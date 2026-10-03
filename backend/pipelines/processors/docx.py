# -*- coding: utf-8 -*-
"""
DOCX 抽取。

设计要点：
- body 块级顺序用 python-docx 自带的 `iter_inner_content()` 取，不自己维护一份
  element→对象映射表——那张表要自己保证永远正确，是纯粹多出来的出错面。
- 图片占位符用中性的 `[图片 N]`（内容级信息），不用 `<img_text>` 这类把引擎实现
  写进正文的标记；识别文本由 understand 放进 meta.ocr_text。
- 文本框单独成节：它们通常锚定在浮动位置，硬插进正文反而会给出错误的阅读顺序，
  但直接丢掉又会掉信息，所以如实标注成独立一节。
"""
import re
from io import BytesIO
from itertools import islice
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from PIL import Image

import configs.settings as settings
from pipelines.processors.grid import GridBuilder, fill_from_specs, render_tsv
from pipelines.processors.schema import EmbeddedAsset, ExtractContext, ExtractResult

# 单个表格最多渲染的行数，避免广表把 content 淹掉
MAX_TABLE_ROWS = 2000

def _textbox_texts(document: "Document") -> list:
    """收集正文之外文本框里的文字（Word 三类文本框容器）。"""
    namespaces = (
        qn("w:txbxContent"),
        "{urn:schemas-microsoft-com:vml}textbox",
        "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}txbx",
    )
    texts = []
    for container in document.element.body.iter():
        if container.tag not in namespaces:
            continue
        paragraphs = []
        for paragraph in container.iter(qn("w:p")):
            run_text = "".join(node.text or "" for node in paragraph.iter(qn("w:t")))
            if run_text.strip():
                paragraphs.append(run_text.strip())
        if paragraphs:
            texts.append("\n".join(paragraphs))
    return texts

def _row_specs(row) -> list:
    """一行的格位规格：[(文本, 横向跨度, 是否纵向继续格)]，行首缺格补空。"""
    specs = [("", 1, False) for _ in range(row.grid_cols_before)]
    for tc in row._tr.tc_lst:
        text = re.sub(r"\s+", " ", "\n".join(paragraph.text for paragraph in tc.p_lst)).strip()
        specs.append((text, tc.grid_span or 1, tc.vMerge not in (None, "restart")))
    return specs

def _render_table(table) -> str:
    """
    把表格渲染成制表符分隔的行。

    功能：按 `w:tc` 收成格位规格（**不用 `row.cells`**：合并区会被返回多次，文本因此重复），
          再交给格位网格填充与渲染——纵向合并的行跨度由网格从继续格数出来。
    应用场景：extract_docx 遇到表格时调用。
    输入：table — python-docx 的表格对象。
    输出：制表符分隔的多行文本；合并区文本只出现一次、覆盖位占列位。
    """
    total_rows = len(table.rows)
    specs_by_row = [_row_specs(row) for row in islice(table.rows, MAX_TABLE_ROWS)]
    lines = render_tsv(fill_from_specs(GridBuilder(), specs_by_row), MAX_TABLE_ROWS)
    if total_rows > MAX_TABLE_ROWS:
        lines.append(f"... 其余 {total_rows - MAX_TABLE_ROWS} 行未列出")
    return "\n".join(lines)

def _save_embedded_images(document: "Document", ctx: ExtractContext) -> list:
    """把前若干张内嵌图转存成 PNG 素材，返回素材列表。"""
    assets = []
    used = 0
    for blip in document.element.body.iter(qn("a:blip")):
        if used >= settings.MAX_EMBEDDED_IMAGES:
            break
        rel_id = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
        if not rel_id:
            continue
        part = document.part.related_parts.get(rel_id)
        blob = getattr(part, "blob", None)
        if not blob:
            continue
        used += 1
        target = ctx.work_dir / f"docx_img{used}.png"
        try:
            with Image.open(BytesIO(blob)) as image:
                image.convert("RGB").save(target, format="PNG")
        except Exception:
            used -= 1
            continue
        assets.append(
            EmbeddedAsset(kind="image", label=f"内嵌图{used}", path=target, order=used)
        )
    return assets

def _declared_pages(document: "Document") -> int:
    """
    从 docProps/app.xml 读文档自报的页数。

    功能：遍历 OPC 包里的部件找 app.xml，用正则取 <Pages>；任何一步失败返回 0。
    应用场景：DOCX 没有真正的分页概念，这个值只是保存时的缓存渲染结果，
              所以读到之后要在 notes 里说明来源，避免被当成精确页数。
    输入：document — python-docx 文档对象。
    输出：页数；读不到返回 0（由调用方转成 None）。
    """
    try:
        props = document.part.package.parts
    except AttributeError:
        return 0
    for part in props:
        if not str(getattr(part, "partname", "")).endswith("docProps/app.xml"):
            continue
        try:
            match = re.search(rb"<Pages>(\d+)</Pages>", part.blob)
        except Exception:
            return 0
        if match:
            return int(match.group(1))
    return 0

def _count_embedded_images(document: "Document") -> int:
    """数一数文档里一共嵌了多少张图（含未落盘的）。"""
    return sum(1 for _ in document.element.body.iter(qn("a:blip")))

def extract_docx(path: Path, ctx: ExtractContext) -> ExtractResult:
    """
    抽取 DOCX 的正文、表格、文本框与内嵌图信息。

    功能：按 body 块级顺序输出段落与表格；文本框单独成节；内嵌图在需要时落盘为素材。
    应用场景：dispatch 分派 DOCX 时调用。
    输入：path — 文件路径；ctx — 抽取上下文。
    输出：ExtractResult，content 为按阅读顺序拼接的文本。
    异常：不抛——打开失败时返回降级结果并说明原因。
    """
    try:
        document = Document(str(path))
    except Exception as exc:
        return ExtractResult(
            degraded=True,
            degrade_reason=f"DOCX 打开失败: {exc}",
            engines=["python-docx"],
        )

    blocks = []
    image_marker = 0
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            rendered = _render_table(item)
            if rendered:
                blocks.append(rendered)
            continue
        text = (item.text or "").strip()
        if not text:
            continue
        # 段落里带图片时用中性占位符标出位置，正文本身不动
        if item._element.findall(".//" + qn("a:blip")):
            image_marker += 1
            text = f"{text} [图片 {image_marker}]"
        blocks.append(text)

    declared_pages = _declared_pages(document)
    result = ExtractResult(
        content="\n".join(blocks),
        engines=["python-docx"],
        pages=declared_pages or None,
        images=_count_embedded_images(document),
        images_used=0,
    )
    if declared_pages:
        result.notes.append("页数取自文档自带的 docProps/app.xml，是保存时的缓存值，可能不精确")

    textboxes = _textbox_texts(document)
    if textboxes:
        result.content += "\n\n=== 文本框内容 ===\n" + "\n---\n".join(textboxes)
        result.notes.append(f"正文之外另有 {len(textboxes)} 个文本框，已单独成节")

    if ctx.prepare_assets:
        assets = _save_embedded_images(document, ctx)
        result.assets = assets
        result.images_used = len(assets)
    return result
