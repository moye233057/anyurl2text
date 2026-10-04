# -*- coding: utf-8 -*-
"""
OFD（GB/T 33190 开放版式文档）的抽取：容器 → 文档根 → 逐页正文。

OFD 与 ODF 都是 ZIP + XML，但结构不同，所以另起一份处理器：入口是根级 `OFD.xml`，
它指向文档根（通常是 `Doc_0/Document.xml`），文档根里的 `Pages/Page` 再逐页给出 `BaseLoc`
——相对文档根所在目录的 `Content.xml` 路径。正文在每页的 `TextCode` 里，一个 `TextObject`
通常是一行（长行会被拆成多个 TextCode 片段、各自带 X 坐标），所以这里**按 TextObject 成行**、
把它的片段直接拼起来，页与页之间用与 PDF 相同的页标记分开。

为什么不用第三方 ofd 库：正文全在 XML 里，标准库 ElementTree 足够，少一层对第三方 API 形状与
版本兼容的依赖（与 `odf.py` 同一个理由）。元素按 local-name 匹配：规范里前缀是 `ofd:`，
但厂商导出时的前缀与命名空间声明并不统一。

已知边界：只还原文字与页序，不还原版面坐标、字形与矢量图形；图像对象只计数，不进交付文本
（ADR-0004：`extract` 不内置 OCR）。
"""
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pipelines.processors.schema import ExtractResult

KIND = "OFD 版式文档"
ENTRY_NAME = "OFD.xml"
PAGE_MARK = "=== 第{}页 ==="

def _local(tag) -> str:
    """取元素的 local-name：前缀各厂商不统一，只按名字尾部匹配。"""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

def _named(element, name: str) -> list:
    """按 local-name 取元素自身与它下面所有层级的同名元素（含代码块内部的挂载点）。"""
    return [item for item in element.iter() if _local(item.tag) == name]

def _text_of(element) -> str:
    """取一个元素里的全部文字并去掉首尾空白。"""
    return "".join(element.itertext()).strip()

def _natural_key(name: str) -> list:
    """名字里的数字段按数值比较，免得 Page_10 排在 Page_2 前面。"""
    return [(1, int(part)) if part.isdigit() else (0, part) for part in re.split(r"(\d+)", name)]

def _document_root(archive: zipfile.ZipFile) -> str:
    """从 `OFD.xml` 取文档根路径（形如 `Doc_0/Document.xml`）；没写返回空串。"""
    entry = ET.fromstring(archive.read(ENTRY_NAME))
    roots = _named(entry, "DocRoot")
    return _text_of(roots[0]) if roots else ""

def _page_locations(archive: zipfile.ZipFile, doc_root: str, doc_bytes: bytes) -> list:
    """
    取逐页 `Content.xml` 的容器内路径。

    功能：读文档根的 `Pages/Page` 序列——`BaseLoc` 按规范相对文档根，但导出件也有按容器根写的，
           两种都认（优先规范那一种）；一个都没写、或写出来的路径在容器里全不存在时，退回
           「容器里所有以 Content.xml 结尾的条目」，按名字里的数字升序，保证页序确定。
    应用场景：extract_ofd 打开容器后调用一次。
    输入：archive — 已打开的容器；doc_root — 文档根路径；doc_bytes — 文档根 XML 字节。
    输出：容器内路径列表，顺序即页序。
    异常：ET.ParseError — 文档根不是合法 XML，由调用方降级。
    """
    names = set(archive.namelist())
    base_dir = doc_root.rsplit("/", 1)[0] + "/" if "/" in doc_root else ""
    locations = []
    for page in _named(ET.fromstring(doc_bytes), "Page"):
        base_loc = (page.get("BaseLoc") or "").strip()
        if not base_loc:
            continue
        relative = base_dir + base_loc
        locations.append(relative if relative in names else base_loc if base_loc in names else relative)
    if any(name in names for name in locations):
        return locations
    # 路径写法不在这两种之内：按条目名兜底，免得整份报「页面全部读不出来」
    return sorted((name for name in names if name.endswith("Content.xml")), key=_natural_key)

def _page_lines(content: bytes) -> tuple:
    """
    一页的 Content.xml → (行列表, 图像对象数)。

    功能：按文档顺序取 `TextObject`，把它的 `TextCode` 片段拼成一行；没有文字的 TextObject
           不产出行（空文本框在版式文档里很常见）。
    应用场景：extract_ofd 逐页调用。
    输入：content — 该页 Content.xml 的字节。
    输出：(行列表, 图像对象数)。
    异常：ET.ParseError — 该页 XML 非法，由调用方决定跳过还是整份降级。
    """
    root = ET.fromstring(content)
    lines = []
    for element in _named(root, "TextObject"):
        text = "".join(_text_of(code) for code in _named(element, "TextCode"))
        if text:
            lines.append(text)
    return lines, len(_named(root, "ImageObject"))

def _degraded(reason: str, code: str) -> ExtractResult:
    """降级产出：只说明原因与机器名，`content` 为空（ADR-0003）。"""
    return ExtractResult(
        content="", engines=["ElementTree"], degraded=True, degrade_reason=reason, degrade_code=code
    )

def extract_ofd(path: Path) -> ExtractResult:
    """
    抽取 OFD 的正文文字。

    功能：容器 → `OFD.xml` → 文档根 → 逐页正文，页间用 `=== 第N页 ===` 分开；单页坏了只跳过
           那一页并留下说明（整份降级会把其余页也丢掉），一页都读不出才整份降级。
    应用场景：dispatch 分派 ofd 时调用。
    输入：path — 文件路径。
    输出：ExtractResult，content 为按页拼接的文本，pages 为声明的页数、pages_used 为真读到的页数。
    异常：不抛——容器损坏或 XML 非法时返回降级结果并说明原因。
    """
    blocks = []
    notes = []
    locations = []
    skipped = []
    images = 0
    try:
        with zipfile.ZipFile(path) as archive:
            doc_root = _document_root(archive)
            if not doc_root:
                raise KeyError(f"{ENTRY_NAME} 里没有 DocRoot")
            locations = _page_locations(archive, doc_root, archive.read(doc_root))
            for page_no, name in enumerate(locations, start=1):
                try:
                    lines, page_images = _page_lines(archive.read(name))
                except (KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
                    skipped.append(page_no)
                    notes.append(f"第{page_no}页读不出来已跳过: {exc}")
                    continue
                images += page_images
                if lines:
                    blocks.append(PAGE_MARK.format(page_no) + "\n" + "\n".join(lines))
    except (zipfile.BadZipFile, KeyError) as exc:
        return _degraded(f"{KIND}容器解析失败: {exc}", "ofd_parse_failed")
    except ET.ParseError as exc:
        return _degraded(f"{KIND}的 {ENTRY_NAME} 或文档根不是合法 XML: {exc}", "ofd_content_failed")

    if not locations:
        return _degraded(f"{KIND}的文档根里没有任何页面", "ofd_content_failed")
    if not blocks and len(skipped) == len(locations):
        return _degraded(f"{KIND}的页面全部读不出来", "ofd_content_failed")

    if images:
        notes.append(f"含 {images} 个图像对象，图像内容不进交付文本")
    if not blocks:
        notes.append("页面里没有可提取的文字（可能是图形页或纯扫描件）")
    return ExtractResult(
        content="\n\n".join(blocks),
        engines=["ElementTree"],
        notes=notes,
        pages=len(locations),
        pages_used=len(locations) - len(skipped),
    )
