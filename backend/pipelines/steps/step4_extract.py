# -*- coding: utf-8 -*-
"""
步骤 4：找到对应格式的处理器并执行——纯代码路与模型增强路两个入口都在这里。

- `run_extraction()`：两条路共用的确定性抽取，产出 `content`（交付文本）与素材清单。
- `enhance()`：只有 understand 走，按格式把素材交给模型读——独立图片读字与描述、
  扫描页逐页识别、内嵌图批量识读；读素材的共用实现在 `pipelines/processors/reading.py`。

`extract_*` 不调模型、也不感知调用方是哪个接口（ADR 0002/0007），所以 `content` 只有一个来源；
`enhance_*` 只能在抽取产出之上追加，**永远不碰 `content`**。认不出的扩展名与缺依赖的类型
走 `extract_file_info()` 的降级产出——那是正常结果，不是错误（ADR 0003）。
"""
import time
from pathlib import Path
from typing import Tuple

from configs.formats import (
    ARCHIVE_EXTENSIONS,
    IMAGE_EXTENSIONS,
    INSTALL_PACKAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    OFFICE_LEGACY_EXTENSIONS,
    WEBPAGE_EXTENSIONS,
)
from models.deepseek import DeepSeekClient
from pipelines.processors import (
    archive,
    docx,
    excel,
    html,
    image,
    install_package,
    media,
    odf,
    pdf,
    plaintext,
    pptx,
)
from pipelines.processors.reading import enhance_assets
from pipelines.processors.schema import (
    EnhanceOutcome,
    ExtractContext,
    ExtractResult,
    PreparedSource,
)
from utils.textfile import read_header_hex

def _degrade_reason(ext: str) -> str:
    """
    给出降级原因。

    功能：如实说明缺的是什么——调用方需要能区分「能力边界」与「故障」。
    应用场景：dispatch 判定需要降级时调用。
    输入：ext — 小写扩展名。
    输出：中文原因。
    """
    if ext in OFFICE_LEGACY_EXTENSIONS:
        return "老版 Office 格式需要 LibreOffice 转换器，本机不具备，只能给出文件信息"
    return "没有可用于该格式的纯代码解析路径，只能给出文件信息"

def extract_file_info(path: Path, reason: str, extra_notes: list = None) -> ExtractResult:
    """
    产出降级的基础文件信息（ADR 0003）。

    功能：只声明「这是什么、多大」，把降级原因写进 degrade_reason，供调用方区分
          「能力边界」与「故障」；不往 content 写自然语言模板句——模板句机器没法解析，
          事实全部进 meta。
    应用场景：dispatch 遇到覆盖不了的扩展名，或该类型缺依赖时调用。
    输入：path — 文件路径；reason — 降级原因；extra_notes — 附加上下文说明。
    输出：ExtractResult，content 为空，degraded=True。
    """
    notes = list(extra_notes or [])
    header = read_header_hex(path)
    if header:
        notes.append(f"文件头: {header}")
    return ExtractResult(
        content="",
        engines=["基础文件信息"],
        notes=notes,
        degraded=True,
        degrade_reason=reason,
    )

def dispatch(ext: str, path: Path, ctx: ExtractContext, page_url: str = "") -> ExtractResult:
    """
    按扩展名把文件交给对应的处理器。

    功能：先处理必须降级的类型，再按「文档 / 网页 / 图片 / 压缩包与安装包」分派；
          认不出的扩展名落到基础文件信息。
    应用场景：`run_extraction()` 调用；加一种格式就在下面的链条里加一行。
    输入：ext — 已识别的小写扩展名；path — 落盘文件；ctx — 处理器上下文；
          page_url — 网页的最终 URL，用于把相对链接补成绝对链接。
    输出：ExtractResult。
    """
    if not ext:
        return extract_file_info(path, "无法识别文件类型，只能给出文件信息")

    if ext in OFFICE_LEGACY_EXTENSIONS:
        return extract_file_info(path, _degrade_reason(ext))

    # ---------- 文档 ----------
    if ext == "pdf":
        return pdf.extract_pdf(path, ctx)
    if ext == "docx":
        return docx.extract_docx(path, ctx)
    if ext == "pptx":
        return pptx.extract_pptx(path, ctx)
    if ext in ("xlsx", "xlsm", "xls", "ods", "csv"):
        return excel.extract_excel(path, ext, ctx)
    if ext in ("odt", "odp"):
        return odf.extract_odf(path, ext)
    if ext in ("txt", "md", "markdown", "json", "xml", "rtf"):
        return plaintext.extract_plaintext(path, ext)

    # ---------- 网页 ----------
    if ext in WEBPAGE_EXTENSIONS:
        return html.extract_html(path, page_url)

    # ---------- 图片 ----------
    if ext in IMAGE_EXTENSIONS:
        return image.extract_image(path, ext)

    # ---------- 音频与视频 ----------
    if ext in MEDIA_EXTENSIONS:
        return media.extract_media(path, ext, ctx)

    # ---------- 压缩包与安装包 ----------
    if ext in ARCHIVE_EXTENSIONS:
        return archive.extract_archive(path, ext)
    if ext in INSTALL_PACKAGE_EXTENSIONS:
        return install_package.extract_install_package(path, ext)

    return extract_file_info(path, f"没有 .{ext} 的解析路径，只能给出文件信息")

def enhance(prepared: PreparedSource, result: ExtractResult, client: DeepSeekClient) -> EnhanceOutcome:
    """
    模型增强路的处理器入口：按格式决定怎么用模型。

    功能：独立图片（源本身是图片、没有素材清单）交给 `pipelines/processors/image.py` 处理——
          它负责判型后的分流位置与「文字 + 客观描述」的取法；
          音频与视频交给 `pipelines/processors/media.py`——它拿步骤 4 抽好的音轨去问本机 ASR；
          其余格式读 `result.assets`（内嵌图批量识读、扫描页逐页识别），共用 `enhance_assets`。
    应用场景：understand 的第 4 步（`run_extraction` 之后）调用一次。
    输入：prepared — 源；result — 确定性产出；client — 模型客户端。
    输出：EnhanceOutcome；没有可增强内容时各项为空。
    异常：LlmError — 模型调用失败，由调用方转成 notes。
    """
    if prepared.ext in IMAGE_EXTENSIONS and not result.assets:
        return image.enhance_image(client, prepared)
    if prepared.ext in MEDIA_EXTENSIONS:
        return media.enhance_media(result)
    return enhance_assets(client, result)

def run_extraction(prepared: PreparedSource, prepare_assets: bool) -> Tuple[ExtractResult, int]:
    """
    步骤 4 的入口：跑一次确定性抽取（两条路共用）。

    功能：建处理器上下文 → 分派 → 计时；不碰模型，也不碰响应组装。
    应用场景：`pipelines/extract.py` 与 `pipelines/understand.py` 的第 4 步。
    输入：prepared — 步骤 1-3 产出的源；prepare_assets — 是否准备模型素材。
    输出：(ExtractResult, 解析耗时毫秒)。
    """
    ctx = ExtractContext(work_dir=prepared.path.parent, prepare_assets=prepare_assets)
    started = time.monotonic()
    result = dispatch(prepared.ext, prepared.path, ctx, page_url=prepared.page_url)
    parse_ms = int((time.monotonic() - started) * 1000)
    return result, parse_ms
