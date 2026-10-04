# -*- coding: utf-8 -*-
"""
ODF 家族中「文本类」文档的抽取：odt（文本文档）与 odp（演示文稿）。

这里直接读 ZIP 里的 content.xml 并用标准库 ElementTree 取文本，而不是引入 odfpy：
ODF 的正文全在 text:p / text:h 元素里，按文档结构走一遍就能拿到，
用标准库可控性更高，也少一层对第三方 API 形状的依赖。
（ods 不在这里——表格必须按行列位置还原，见 excel.py。）

**按结构递归，不用全局 `root.iter()`**：`root.iter()` 会把所有 `text:p` 拉平，于是表格坍成
「一段一行」（列对应关系丢失）、嵌套列表的层级也没了。改成从 body 往下走，遇到
`table:table` 与 `text:list` 就交给各自的渲染：

- 表格 → 格位网格（`grid.py`），与 docx / pptx / xlsx 共用同一份不变式与渲染：
  合并区文本只出现一次、覆盖位占列位、超行数上限时截断并说明。
- 列表 → Markdown 记号，每深一层多缩进两个空格。

已知边界：只还原文字内容，不还原样式、分栏与列宽；ODP 幻灯片里的形状仍按出现顺序取段落。
"""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import configs.limits as limits
from pipelines.processors import cell_text, odf_table_rows, table_kind
from pipelines.processors.grid import GridBuilder, grid_notes, render_tsv
from pipelines.processors.schema import ExtractResult

TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
OFFICE_NS = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
# ODF 里承载正文的元素：p 是普通段落，h 是标题
TEXT_ELEMENTS = (TEXT_NS + "p", TEXT_NS + "h")
# 列表项与列表表头：ODF 用 text:list-header 放列表的第一项
LIST_ITEMS = (TEXT_NS + "list-item", TEXT_NS + "list-header")
# 行组容器：ODF 允许把行再包一层（`table:table-rows` 收正文行、`table:table-row-group` 两组都收）
ROW_GROUPS = (TABLE_NS + "table-rows", TABLE_NS + "table-row-group")
# 表头行组：里面的行既是**内容**（要照渲），也是「源自己标出的表头」（要计数）
HEADER_ROWS_GROUP = TABLE_NS + "table-header-rows"

# 单张 ODF 表格最多渲染的行数，与 docx 同源（值在 `configs/limits.py`）
# 列表每深一层的缩进
LIST_INDENT = "  "

def _text_of(element) -> str:
    """取元素里的全部文字：itertext 会把 text:span 等内联元素的文字一起带出来。"""
    return "".join(element.itertext()).strip()

def _heading_line(element) -> str:
    """标题渲成 Markdown 记号；outline-level 缺失或非法时按一级。"""
    level = element.get(TEXT_NS + "outline-level", "1")
    try:
        marks = "#" * max(1, min(6, int(level)))
    except ValueError:
        marks = "#"
    return f"{marks} {_text_of(element)}"

def _count(element, attribute: str, cap: int = 0) -> int:
    """读一个正整数属性：缺失或非法按 1；给了 cap 就钳到 cap。"""
    try:
        value = int(element.get(TABLE_NS + attribute, "1"))
    except (TypeError, ValueError):
        return 1
    value = max(1, value)
    return min(cap, value) if cap else value

def _cell_text(cell) -> str:
    """
    取单元格里的文字：把其中的段落（含嵌套列表项里的段落）拼成一行，段落之间是 `<br>`。

    格位方言见 `processors/cell_text.py`：格内**不能**留真换行（会把这一行 TSV 切两行），
    段落边界用 `<br>` 记号保住（与 anydoc 的 `render_cell` 同形）。
    """
    return cell_text.join_lines(_text_of(p) for p in cell.iter(TEXT_NS + "p"))

def _flush_gap(builder, pending: int) -> int:
    """
    把缓冲的「行内空格」落成空格格位，返回新的缓冲数（恒为 0）。

    功能：`pending` 个空单元格逐个 `place("")`——它们的唯一作用是**把后续格推到源列坐标**上。
    为什么行尾不 flush：行尾空格之后再无同行的格，列坐标已经没人用（`render_tsv` 会给短行补空），
    而它们一旦落格就要占格位预算。anydoc 的 `flush_gap` 正是这个策略（`src/formats/odf/table.rs:324-336`：
    「Materialize a buffered empty-cell run in full so the next cell lands on its source column.
    Trailing runs are never flushed and stay elided.」）。
    应用场景：`_render_table` 遇到非平凡格或 `covered-table-cell` 前调用。
    输入：builder — 格位网格构造器；pending — 缓冲的空格数。
    输出：0（缓冲已清空）。
    """
    for _ in range(pending):
        builder.place("")
    return 0

def _render_table(table, hits: list) -> tuple:
    """
    把 ODF 表格渲成制表符分隔的行。

    功能：按 `table:table-row` 逐行、按单元格自己写明的跨度与覆盖位落进格位网格——
           `number-columns-spanned` / `number-rows-spanned` → `place(span)`、
           `covered-table-cell` → `covered()`、两个 `number-*-repeated` → 循环落位。
           表头行组里的行**照渲**（见 `_table_rows`），另把表头行数交回调用方。
    应用场景：`_body_lines` 遇到 `table:table` 时调用。
    输入：table — `table:table` 元素；hits — 命中的上界名收集到这一份列表里。
    输出：(制表符分隔的多行文本, 源标出的表头行数)；超行数上限时末行说明还剩多少行。
    """
    rows, header_rows = odf_table_rows.table_rows(
        table, lambda row: _count(row, "number-rows-repeated", limits.MAX_REPEATED_SLOTS)
    )
    repeats = [_count(row, "number-rows-repeated", limits.MAX_REPEATED_SLOTS) for row in rows]
    total_rows = sum(repeats)
    builder = GridBuilder()
    placed = 0
    for row, repeat in zip(rows, repeats):
        if placed >= limits.MAX_TABLE_ROWS:
            break
        for _ in range(repeat):
            if placed >= limits.MAX_TABLE_ROWS:
                break
            placed += 1
            builder.next_row()
            pending = 0
            for cell in row:
                if cell.tag == TABLE_NS + "covered-table-cell":
                    # 结构格优先：它是某个跨度的覆盖位，不能被当成「可省的空格」；
                    # 续格自带的文字（产出方怪癖）并回 origin，认领不到时只剩孤儿计数
                    pending = _flush_gap(builder, pending)
                    claimed = None
                    for _ in range(_count(cell, "number-columns-repeated", limits.MAX_REPEATED_SLOTS)):
                        claimed = builder.covered() or claimed
                    builder.merge_residue(claimed, _cell_text(cell))
                    continue
                if cell.tag != TABLE_NS + "table-cell":
                    continue
                col_span = _count(cell, "number-columns-spanned")
                row_span = _count(cell, "number-rows-spanned")
                cell_repeat = _count(cell, "number-columns-repeated", limits.MAX_REPEATED_SLOTS)
                text = _cell_text(cell)
                if not text and col_span == 1 and row_span == 1:
                    # 平凡空格只记数、不落格；行内空格会在下一个非平凡格之前补上，行尾的直接消失
                    pending += cell_repeat
                    continue
                pending = _flush_gap(builder, pending)
                for _ in range(cell_repeat):
                    builder.place(text, col_span, row_span)
    lines = render_tsv(builder.finish(), limits.MAX_TABLE_ROWS)
    if total_rows > limits.MAX_TABLE_ROWS:
        lines.append(f"... 其余 {total_rows - limits.MAX_TABLE_ROWS} 行未列出")
        hits.append("max_table_rows")
    hits.extend(builder.limit_hits)
    return "\n".join(lines), header_rows, table_kind.classify(lines), grid_notes(builder)

def _list_lines(list_element, depth: int) -> list:
    """
    把一个 `text:list` 渲成带层级的 Markdown 列表行。

    功能：逐个列表项取段落渲成 `- 文本`，遇到内层 `text:list` 就递归并加深一层——
           全局 `root.iter()` 会把所有段落拉平、父子项长得一样，这一段就是为了保住层级。
    应用场景：`_body_lines` 遇到 `text:list` 时调用。
    输入：list_element — `text:list` 元素；depth — 当前层级（0 起）。
    输出：行列表。
    """
    indent = LIST_INDENT * depth
    lines = []
    for item in list_element:
        if item.tag not in LIST_ITEMS:
            continue
        for child in item:
            if child.tag == TEXT_NS + "list":
                lines.extend(_list_lines(child, depth + 1))
            elif child.tag in TEXT_ELEMENTS:
                text = _text_of(child)
                if text:
                    lines.append(f"{indent}- {text}")
    return lines

def _body_lines(element, hits: list, table_headers: list, table_kinds: list, table_notes: list) -> list:
    """
    按文档结构递归收集正文行。

    功能：`table:table` 与 `text:list` 交给各自的渲染并**不再往下走**（否则单元格与列表项里的
           段落会被再收一遍），`text:p` / `text:h` 取文本，其余容器继续递归。
    应用场景：extract_odf 从 `office:body` 起调用。
    输入：element — 容器元素（body / text / presentation / draw:page / draw:text-box …）；
          hits — 命中的上界名收集到这一份列表里；
          table_headers — 逐张表收集「源标出的表头行数」（与 hits 一样就地累加，省一层返回）。
    输出：行列表，顺序即文档顺序。
    """
    lines = []
    for child in element:
        if child.tag == TABLE_NS + "table":
            rendered, header_rows, kind, notes = _render_table(child, hits)
            if rendered:
                lines.extend(rendered.splitlines())
                table_headers.append(header_rows)
                table_kinds.append(kind)
                table_notes.extend(notes)
        elif child.tag == TEXT_NS + "list":
            lines.extend(_list_lines(child, 0))
        elif child.tag in TEXT_ELEMENTS:
            text = _text_of(child)
            if not text:
                continue
            lines.append(_heading_line(child) if child.tag == TEXT_NS + "h" else text)
        else:
            lines.extend(_body_lines(child, hits, table_headers, table_kinds, table_notes))
    return lines

def extract_odf(path: Path, ext: str) -> ExtractResult:
    """
    抽取 odt / odp 的正文文字。

    功能：从 ZIP 容器取出 content.xml，按文档结构递归收集正文——表格渲成制表符分隔的行、
           列表保住层级、标题按层级标出。
    应用场景：dispatch 分派 odt / odp 时调用。
    输入：path — 文件路径；ext — "odt" 或 "odp"。
    输出：ExtractResult，content 为按文档顺序拼接的文本。
    异常：不抛——容器损坏或 XML 非法时返回降级结果并说明原因。
    """
    kind = "ODT 文本文档" if ext == "odt" else "ODP 演示文稿"
    try:
        with zipfile.ZipFile(path) as archive:
            content_xml = archive.read("content.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        return ExtractResult(
            content="",
            engines=["标准库"],
            degraded=True,
            degrade_reason=f"{kind}容器解析失败: {exc}",
            degrade_code="odf_parse_failed",
        )

    try:
        root = ET.fromstring(content_xml)
    except ET.ParseError as exc:
        return ExtractResult(
            content="",
            engines=["标准库"],
            degraded=True,
            degrade_reason=f"{kind}的 content.xml 解析失败: {exc}",
            degrade_code="odf_content_failed",
        )

    body = root.find(OFFICE_NS + "body")
    hits = []
    table_headers = []
    table_kinds = []
    table_notes: list = []
    lines = _body_lines(body if body is not None else root, hits, table_headers, table_kinds, table_notes)
    # 计数与渲染分开：这里只数标题个数（给 notes 用），不参与上面那次结构遍历
    heading_count = sum(1 for element in root.iter(TEXT_NS + "h") if _text_of(element))

    notes = list(table_notes)
    if len(table_headers) > 1:
        notes.append(
            f"文档里有 {len(table_headers)} 张表，meta.table_header_rows 只在交付文本只有一张表时给值"
        )
    if heading_count:
        notes.append(f"含 {heading_count} 个标题，已按层级标为 Markdown 记号")
    if not lines:
        notes.append("文档里没有可提取的文字段落")
    return ExtractResult(
        content="\n\n".join(lines),
        engines=["ElementTree"],
        notes=notes,
        limits_hit=hits,
        table_header_rows=table_headers[0] if len(table_headers) == 1 else None,
        table_kind=table_kinds[0] if len(table_kinds) == 1 else None,
    )
