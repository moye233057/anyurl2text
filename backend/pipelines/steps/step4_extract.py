# -*- coding: utf-8 -*-
"""
步骤 4：找到对应格式的处理器并执行——纯代码路与模型增强路两个入口都在这里。

- `run_extraction()`：两条路共用的确定性抽取，产出 `content`（纯代码交付文本）与素材清单。

扩展增强分派在 `pipelines/convert.py`（ADR-0011 / 0014）。
`extract_*` 不调模型、也不感知调用方是哪个接口（ADR 0002/0007）。认不出的扩展名与缺依赖的类型
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
from pipelines.processors import (
    archive,
    docx,
    epub,
    excel,
    html,
    image,
    install_package,
    media,
    odf,
    ofd,
    pdf,
    plaintext,
    pptx,
)
from utils.processor_schema import ExtractContext, ExtractResult, PreparedSource
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

# 系统噪音文件：它们不是任何文档的容器，命中就按降级处理
NOISE_FILE_NAMES = (".ds_store", "thumbs.db")

def _is_temporary_noise(name: str) -> bool:
    """这个文件名是不是噪音：Office 临时锁文件（`~$` 前缀）或系统噪音文件（.DS_Store / Thumbs.db）。"""
    lowered = (name or "").strip().lower()
    return lowered.startswith("~$") or lowered in NOISE_FILE_NAMES

def extract_file_info(path: Path, reason: str, degrade_code: str, extra_notes: list = None) -> ExtractResult:
    """
    产出降级的基础文件信息（ADR 0003）。

    功能：只声明「这是什么、多大」，把降级原因写进 degrade_reason、机器名写进 degrade_code，供调用方区分
          「能力边界」与「故障」；不往 content 写自然语言模板句——模板句机器没法解析，
          事实全部进 meta。
    应用场景：dispatch 遇到覆盖不了的扩展名，或该类型缺依赖时调用。
    输入：path — 文件路径；reason — 降级原因（给人看）；degrade_code — 降级的稳定机器名
          （`configs/errors.py` 的 DEGRADE_CODES，必填：每个降级点都得自己声明是哪个码，
          免得「忘了传」被悄悄标成 `unsupported_type`）；extra_notes — 附加上下文说明。
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
        degrade_code=degrade_code,
    )

def dispatch(ext: str, path: Path, ctx: ExtractContext, page_url: str = "", source_name: str = "") -> ExtractResult:
    """
    按扩展名把文件交给对应的处理器。

    功能：先拦掉不是文档本体的噪音文件（Office 锁文件与系统噪音文件），再处理必须降级的类型，
          最后按「文档 / 网页 / 图片 / 压缩包与安装包」分派；认不出的扩展名落到基础文件信息。
    应用场景：`run_extraction()` 调用；加一种格式就在下面的链条里加一行。
    输入：ext — 已识别的小写扩展名；path — 落盘文件；ctx — 处理器上下文；
          page_url — 网页的最终 URL，用于把相对链接补成绝对链接；
          source_name — 原始文件名（下载落盘名会被改成 `dl_xxxx.docx`，噪音判定要看它）。
    输出：ExtractResult。
    """
    # 噪音按**原始文件名**判：`~$report.docx` 的扩展名是正常的 docx，落盘后名字却已经被换掉
    if _is_temporary_noise(source_name) or _is_temporary_noise(path.name):
        return extract_file_info(
            path,
            "这是 Office 锁文件（~$ 前缀）或系统噪音文件（.DS_Store / Thumbs.db），不是文档本体",
            degrade_code="temporary_lock_file",
        )

    if not ext:
        return extract_file_info(
            path, "无法识别文件类型，只能给出文件信息", degrade_code="unsupported_type"
        )

    if ext in OFFICE_LEGACY_EXTENSIONS:
        return extract_file_info(
            path, _degrade_reason(ext), degrade_code="legacy_office_requires_converter"
        )

    # ---------- 文档 ----------
    if ext == "pdf":
        return pdf.extract_pdf(path, ctx)
    if ext == "docx":
        return docx.extract_docx(path, ctx)
    if ext == "pptx":
        return pptx.extract_pptx(path, ctx)
    if ext in ("xlsx", "xlsm", "xls", "ods", "csv", "tsv"):
        return excel.extract_excel(path, ext, ctx)
    if ext in ("odt", "odp"):
        return odf.extract_odf(path, ext)
    if ext == "epub":
        return epub.extract_epub(path, ctx)
    if ext == "ofd":
        return ofd.extract_ofd(path)
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

    return extract_file_info(
        path, f"没有 .{ext} 的解析路径，只能给出文件信息", degrade_code="unsupported_type"
    )

def run_extraction(prepared: PreparedSource, prepare_assets: bool) -> Tuple[ExtractResult, int]:
    """
    步骤 4 的入口：跑一次确定性抽取（两条路共用）。

    功能：建处理器上下文 → 分派 → 计时；不碰模型，也不碰响应组装。
    应用场景：`pipelines/convert.py` 的第 4 步。
    输入：prepared — 步骤 1-3 产出的源；prepare_assets — 是否准备模型素材。
    输出：(ExtractResult, 解析耗时毫秒)。
    """
    ctx = ExtractContext(work_dir=prepared.path.parent, prepare_assets=prepare_assets)
    started = time.monotonic()
    result = dispatch(
        prepared.ext, prepared.path, ctx,
        page_url=prepared.page_url,
        source_name=prepared.file_name,
    )
    parse_ms = int((time.monotonic() - started) * 1000)
    return result, parse_ms
