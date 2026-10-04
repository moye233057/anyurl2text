# -*- coding: utf-8 -*-
"""
DOCX 抽取。

设计要点：
- body 块级顺序用 python-docx 自带的 `iter_inner_content()` 取，不自己维护一份
  element→对象映射表——那张表要自己保证永远正确，是纯粹多出来的出错面。
- 图片占位符用中性的 `[图片 N]`（内容级信息），不用 `<img_text>` 这类把引擎实现
  写进正文的标记；识别文本由 understand 放进 meta.ocr_text。
- 超链接的**地址**也要抽出来：正文里只剩链接文字的话，「这个文字指向哪儿」这条信息就丢了；
  输出成与网页那一路同形的 `## 链接` 段。
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

import configs.limits as limits
import configs.settings as settings
from pipelines.processors import cell_text, table_kind
from pipelines.processors.docx_links import hyperlink_lines
from pipelines.processors.grid import GridBuilder, fill_from_specs, grid_notes, render_tsv
from pipelines.processors.schema import EmbeddedAsset, ExtractContext, ExtractResult
from pipelines.processors import table_kind

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

def _row_elements(tbl) -> list:
    """
    表格的行元素，按文档顺序——含被 `w:sdt` 包着的行。

    为什么不能直接用 python-docx 的 `table.rows`：它取的是 `w:tbl` 的**直属** `w:tr`，
    而 Word 的「重复表行内容控件」把行包在 `w:sdt/w:sdtContent` 里，那些行会**整行消失**。
    （实测过：改动前那份文档的行一个都没进正文。）
    应用场景：`_row_specs` / `_header_rows` / `_nested_table_text` 三处取行。
    输入：tbl — `w:tbl` 元素。
    输出：`w:tr` 元素列表。
    """
    rows = []
    for child in tbl:
        if child.tag == qn("w:tr"):
            rows.append(child)
        elif child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                rows.extend(node for node in content if node.tag == qn("w:tr"))
    return rows

def _inline_text(element) -> str:
    """
    段落里的行内文字：`w:t` 取文本、`w:tab` 变空格、`w:br` / `w:cr`（软换行）变换行。

    为什么按元素逐个走而不是 `paragraph.text`：软换行与制表符都不是 `w:t`，
    只拼 `w:t` 会把它们整个丢掉（本仓 `_cell_text` 以前就是这么丢的）。
    """
    chunks = []
    for node in element.iter():
        if node.tag == qn("w:t"):
            chunks.append(node.text or "")
        elif node.tag == qn("w:tab"):
            chunks.append(" ")
        elif node.tag in (qn("w:br"), qn("w:cr")):
            chunks.append("\n")
    return "".join(chunks)

def _nested_table_text(tbl) -> str:
    """
    格内嵌套表的文字。

    功能：逐行把格文字用 ` / ` 连起来、**行与行用 `<br>` 连**（与 anydoc 的嵌套表渲染同形：
          行内 ` / `、行间 `<br>`）；格位方言的规则见 `processors/cell_text.py`。
    为什么必须取：`w:tc` 里的 `w:tbl` 是**正常内容**，只看 `w:tc` 的直属段落会让整块文字消失。
    应用场景：`_cell_text` 遇到 `w:tc` 里的 `w:tbl` 时调用（可递归任意层）。
    输入：tbl — `w:tbl` 元素。
    输出：嵌套表的文字；空表返回空串。
    """
    lines = []
    for row in _row_elements(tbl):
        cells = [_cell_text(tc) for tc in row.findall(qn("w:tc"))]
        if any(cells):
            lines.append(" / ".join(cells))
    return cell_text.join_lines(lines)

def _cell_text(tc) -> str:
    """
    单元格文字：段落取文本、`w:sdt` 拆壳、**格内嵌套表也取**，格内换行写成 `<br>`。

    与旧实现（`"\\n".join(paragraph.text for paragraph in tc.p_lst)`）的差别：
    `tc.p_lst` 只认直属 `w:p`，内容控件里的段落与格内嵌套表都被跳过；而格内换行以前被
    `re.sub(r"\\s+", " ", ...)` 压成空格，段落边界（地址、多值、项目符号）因此丢掉。
    输入：tc — `w:tc` 元素。
    输出：格内文本（换行是 `<br>`，没有真换行与真制表符）。
    """
    parts: list = []
    for child in tc:
        if child.tag == qn("w:p"):
            parts.append(cell_text.from_text(_inline_text(child)))
        elif child.tag == qn("w:tbl"):
            parts.append(_nested_table_text(child))
        elif child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is None:
                continue
            for node in content:
                if node.tag == qn("w:p"):
                    parts.append(cell_text.from_text(_inline_text(node)))
                elif node.tag == qn("w:tbl"):
                    parts.append(_nested_table_text(node))
    return cell_text.join_lines(parts)

def _row_specs(row, table) -> list:
    """一行的格位规格：[(文本, 横向跨度, 是否纵向继续格)]，行首缺格补空。"""
    tr_pr = row.find(qn("w:trPr"))
    before = 0
    if tr_pr is not None:
        node = tr_pr.find(qn("w:gridBefore"))
        if node is not None:
            try:
                before = int(node.get(qn("w:val")) or 0)
            except ValueError:
                before = 0
    specs = [("", 1, False) for _ in range(before)]
    for tc in row.findall(qn("w:tc")):
        # `tc` 是 `CT_Tc` 元素：`grid_span` / `vMerge` 是 python-docx 在 XML 层给的属性
        # （不要换成 `_Cell` 包装——那个类没有 vMerge）
        specs.append((_cell_text(tc), tc.grid_span or 1, tc.vMerge not in (None, "restart")))
    return specs

def _header_rows(table) -> int:
    """
    源自己标出的表头行数（`w:trPr/w:tblHeader`）。

    功能：逐行看行属性里有没有 `w:tblHeader`——Word 用它声明「这一行是表头，跨页时重复出现」。
    为什么不用「首行就是表头」这类启发式：导出件常常整表都没有表头行，猜错会吃掉一行数据，
    与本仓「宁可回落也不猜」的取向相反（网页那一路的 `<th>` / `<thead>` 是同一条理由）。
    应用场景：`_render_table` 随正文一起交回调用方，最终进 `meta.table_header_rows`。
    输入：table — python-docx 的表格对象。
    输出：标了表头的行数（可能是 0）。
    """
    count = 0
    for row in _row_elements(table._tbl):
        tr_pr = row.find(qn("w:trPr"))
        if tr_pr is not None and tr_pr.find(qn("w:tblHeader")) is not None:
            count += 1
    return count

def _render_table(table) -> tuple:
    """
    把表格渲染成制表符分隔的行。

    功能：按 `w:tc` 收成格位规格（**不用 `row.cells`**：合并区会被返回多次，文本因此重复），
          再交给格位网格填充与渲染——纵向合并的行跨度由网格从继续格数出来。
    应用场景：extract_docx 遇到表格时调用。
    输入：table — python-docx 的表格对象。
    输出：(制表符分隔的多行文本, 命中的上界名列表, 源标出的表头行数)；合并区文本只出现一次、覆盖位占列位。
    """
    total_rows = len(_row_elements(table._tbl))
    builder = GridBuilder()
    specs_by_row = [_row_specs(row, table) for row in islice(_row_elements(table._tbl), limits.MAX_TABLE_ROWS)]
    lines = render_tsv(fill_from_specs(builder, specs_by_row), limits.MAX_TABLE_ROWS)
    hits = []
    if total_rows > limits.MAX_TABLE_ROWS:
        lines.append(f"... 其余 {total_rows - limits.MAX_TABLE_ROWS} 行未列出")
        hits.append("max_table_rows")
    hits.extend(builder.limit_hits)
    kind = table_kind.classify(lines)
    return "\n".join(lines), hits, _header_rows(table), kind, grid_notes(builder)

def _save_embedded_images(document: "Document", ctx: ExtractContext, result: ExtractResult) -> list:
    """
    把前若干张内嵌图转存成 PNG 素材，返回素材列表。

    功能：按 `a:blip` 的来源关系 id 去重后落盘——同一张图被引 N 次只落一次，
           `MAX_EMBEDDED_IMAGES` 也因而记在**不同的图**上，重复引用不会挤掉后面的图。
           真的因上限少读了图时，把 `max_embedded_images` 记到 result 上（以前这里是静默的）。
    应用场景：`prepare_assets=True` 的 extract 与 understand 两条路。
    输入：document — python-docx 文档对象；ctx — 抽取上下文（用它的 work_dir）；
          result — 抽取产出，只用来记命中的上界名。
    输出：EmbeddedAsset 列表，数量不超过 MAX_EMBEDDED_IMAGES 且文件互不相同。
    """
    assets = []
    seen = set()
    for blip in document.element.body.iter(qn("a:blip")):
        rel_id = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
        if not rel_id or rel_id in seen:
            continue
        # 上限只在遇到**新的**来源时判定：剩下的全是重复引用就不算命中，别误报
        if len(assets) >= settings.MAX_EMBEDDED_IMAGES:
            result.limits_hit.append("max_embedded_images")
            break
        part = document.part.related_parts.get(rel_id)
        blob = getattr(part, "blob", None)
        if not blob:
            continue
        # 先记来源再落盘：同一张图只试一次，转码失败也不回头重试 N 遍
        seen.add(rel_id)
        position = len(assets) + 1
        target = ctx.work_dir / f"docx_img{position}.png"
        try:
            with Image.open(BytesIO(blob)) as image:
                image.convert("RGB").save(target, format="PNG")
        except Exception:
            continue
        assets.append(
            EmbeddedAsset(kind="image", label=f"内嵌图{position}", path=target, order=position)
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

def _hidden_text_note(document) -> str:
    """
    隐藏文字（`w:rPr/w:vanish`）的登记文案。

    功能：数出正文（含表格、文本框里的段落）中带 `w:vanish` 的 run 与它们的字符数。
    为什么只登记、不扣内容：隐藏只是 Word 里没显示，不是没有内容（用户 2026-10-04 拍板：
    隐藏内容一律照抽），登记是为了让调用方知道这份产出里有没有「界面上看不见的字」。
    应用场景：extract_docx 组装 notes 时调用。
    输入：document — python-docx 的 Document。
    输出：一句文案；没有隐藏文字时返回空串。
    """
    runs = 0
    chars = 0
    for element in document.element.body.iter(qn("w:r")):
        properties = element.find(qn("w:rPr"))
        if properties is None:
            continue
        vanish = properties.find(qn("w:vanish"))
        if vanish is None or (vanish.get(qn("w:val")) or "").lower() in ("0", "false", "off"):
            continue
        runs += 1
        chars += len("".join(node.text or "" for node in element.iter(qn("w:t"))))
    if not runs:
        return ""
    return f"隐藏内容照抽：正文里有 {runs} 处隐藏文字（w:vanish），共 {chars} 字"

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
            degrade_code="docx_parse_failed",
            engines=["python-docx"],
        )

    blocks = []
    hits = []
    table_headers = []
    table_kinds = []
    notes: list = []
    image_marker = 0
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            rendered, table_hits, header_rows, kind, table_notes = _render_table(item)
            hits.extend(table_hits)
            notes.extend(table_notes)
            if rendered:
                blocks.append(rendered)
                table_headers.append(header_rows)
                table_kinds.append(kind)
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
        limits_hit=hits,
        notes=notes,
        table_header_rows=table_headers[0] if len(table_headers) == 1 else None,
        table_kind=table_kinds[0] if len(table_kinds) == 1 else None,
    )
    if len(table_headers) > 1:
        result.notes.append(
            f"正文里有 {len(table_headers)} 张表，meta.table_header_rows 只在交付文本只有一张表时给值"
        )
    hidden_note = _hidden_text_note(document)
    if hidden_note:
        result.notes.append(hidden_note)
    if declared_pages:
        result.notes.append("页数取自文档自带的 docProps/app.xml，是保存时的缓存值，可能不精确")

    textboxes = _textbox_texts(document)
    if textboxes:
        result.content += "\n\n=== 文本框内容 ===\n" + "\n---\n".join(textboxes)
        result.notes.append(f"正文之外另有 {len(textboxes)} 个文本框，已单独成节")

    # 链接段与网页那一路同形：末尾一个 `## 链接`，每行 `文字 -> URL`；超上限时只报名字
    links = hyperlink_lines(document)
    if links:
        if len(links) > limits.MAX_LINKS:
            result.limits_hit.append("max_links")
        result.content += "\n\n## 链接\n" + "\n".join(links[: limits.MAX_LINKS])

    if ctx.prepare_assets:
        assets = _save_embedded_images(document, ctx, result)
        result.assets = assets
        result.images_used = len(assets)
    return result
