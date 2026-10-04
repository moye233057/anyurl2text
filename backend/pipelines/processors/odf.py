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

from pipelines.processors.grid import GridBuilder, render_tsv
from pipelines.processors.schema import ExtractResult

TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
OFFICE_NS = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
# ODF 里承载正文的元素：p 是普通段落，h 是标题
TEXT_ELEMENTS = (TEXT_NS + "p", TEXT_NS + "h")
# 列表项与列表表头：ODF 用 text:list-header 放列表的第一项
LIST_ITEMS = (TEXT_NS + "list-item", TEXT_NS + "list-header")

# 单张 ODF 表格最多渲染的行数，与 docx 的 MAX_TABLE_ROWS 同义
MAX_TABLE_ROWS = 2000
# `number-columns-repeated` / `number-rows-repeated` 是 ODF 的游程写法，尾部补位常写成上千；
# 给重复次数一个上限，免得空行空列把网格撑爆（空格既不带内容也不占可见列宽）
MAX_REPEATED_SLOTS = 100
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
    """取单元格里的文字：把其中的段落（含嵌套列表项里的段落）拼成一行。"""
    return " ".join(part for part in (_text_of(p) for p in cell.iter(TEXT_NS + "p")) if part)

def _render_table(table) -> str:
    """
    把 ODF 表格渲成制表符分隔的行。

    功能：按 `table:table-row` 逐行、按单元格自己写明的跨度与覆盖位落进格位网格——
           `number-columns-spanned` / `number-rows-spanned` → `place(span)`、
           `covered-table-cell` → `covered()`、两个 `number-*-repeated` → 循环落位。
    应用场景：`_body_lines` 遇到 `table:table` 时调用。
    输入：table — `table:table` 元素。
    输出：制表符分隔的多行文本；超 MAX_TABLE_ROWS 时末行说明还剩多少行。
    """
    rows = table.findall(TABLE_NS + "table-row")
    repeats = [_count(row, "number-rows-repeated", MAX_REPEATED_SLOTS) for row in rows]
    total_rows = sum(repeats)
    builder = GridBuilder()
    placed = 0
    for row, repeat in zip(rows, repeats):
        if placed >= MAX_TABLE_ROWS:
            break
        for _ in range(repeat):
            if placed >= MAX_TABLE_ROWS:
                break
            placed += 1
            builder.next_row()
            for cell in row:
                if cell.tag == TABLE_NS + "covered-table-cell":
                    builder.covered()
                    continue
                if cell.tag != TABLE_NS + "table-cell":
                    continue
                col_span = _count(cell, "number-columns-spanned")
                row_span = _count(cell, "number-rows-spanned")
                for _ in range(_count(cell, "number-columns-repeated", MAX_REPEATED_SLOTS)):
                    builder.place(_cell_text(cell), col_span, row_span)
    lines = render_tsv(builder.finish(), MAX_TABLE_ROWS)
    if total_rows > MAX_TABLE_ROWS:
        lines.append(f"... 其余 {total_rows - MAX_TABLE_ROWS} 行未列出")
    return "\n".join(lines)

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

def _body_lines(element) -> list:
    """
    按文档结构递归收集正文行。

    功能：`table:table` 与 `text:list` 交给各自的渲染并**不再往下走**（否则单元格与列表项里的
           段落会被再收一遍），`text:p` / `text:h` 取文本，其余容器继续递归。
    应用场景：extract_odf 从 `office:body` 起调用。
    输入：element — 容器元素（body / text / presentation / draw:page / draw:text-box …）。
    输出：行列表，顺序即文档顺序。
    """
    lines = []
    for child in element:
        if child.tag == TABLE_NS + "table":
            rendered = _render_table(child)
            if rendered:
                lines.extend(rendered.splitlines())
        elif child.tag == TEXT_NS + "list":
            lines.extend(_list_lines(child, 0))
        elif child.tag in TEXT_ELEMENTS:
            text = _text_of(child)
            if not text:
                continue
            lines.append(_heading_line(child) if child.tag == TEXT_NS + "h" else text)
        else:
            lines.extend(_body_lines(child))
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
        )

    try:
        root = ET.fromstring(content_xml)
    except ET.ParseError as exc:
        return ExtractResult(
            content="",
            engines=["标准库"],
            degraded=True,
            degrade_reason=f"{kind}的 content.xml 解析失败: {exc}",
        )

    body = root.find(OFFICE_NS + "body")
    lines = _body_lines(body if body is not None else root)
    # 计数与渲染分开：这里只数标题个数（给 notes 用），不参与上面那次结构遍历
    heading_count = sum(1 for element in root.iter(TEXT_NS + "h") if _text_of(element))

    notes = []
    if heading_count:
        notes.append(f"含 {heading_count} 个标题，已按层级标为 Markdown 记号")
    if not lines:
        notes.append("文档里没有可提取的文字段落")
    return ExtractResult(content="\n\n".join(lines), engines=["ElementTree"], notes=notes)
