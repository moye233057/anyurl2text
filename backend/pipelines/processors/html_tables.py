# -*- coding: utf-8 -*-
"""
HTML 表格 → 制表符块：网页与 EPUB 章节共用这一份。

为什么要有它：网页与电子书里的 `<table>` 原本被 `get_text()` 拉平成文本行，列对应关系（谁属于哪一列、
哪些格被合并吞掉）在那一刻就没了——而这是「尽量保留真实信息」在这一类上最直接的损失。
实现上不另起一套：`colspan` / `rowspan` 直接交给格位网格（`grid.py`）的 `place()`，
被合并吞掉的格由网格按 `pending` 规则自动物化，渲染也只有 `render_tsv` 一份。

**回填机制**（顺序不能颠倒）：调用方先让 `render_tables(soup)` 把 `<table>` 换成**哨兵行**，
再清洗正文（`_clean_text` 会 strip 每一行，制表符行首的空列会被 strip 掉），最后用
`restore_blocks(text, blocks)` 把哨兵行换回制表符块。

已知边界：单元格里的**嵌套表**的文字会并进外层那个格位（只取直属单元格，不把内层表拆成外层列）。
"""
from bs4 import BeautifulSoup, NavigableString

import configs.limits as limits
from pipelines.processors.grid import GridBuilder, render_tsv

# 哨兵行长这样：清洗会 strip 每一行，它因此必须是一个「strip 前后完全一样」的短标记
SENTINEL = "@@TABLE-{index}@@"

def _span(cell, attribute: str, fallback: int = 1) -> int:
    """
    读 `colspan` / `rowspan`。

    功能：缺失或非法按 1；`rowspan="0"` 按 HTML 规范是「跨到本行组末尾」，这里按 `fallback`
           （= 这张表还剩几行）处理；其余值不得小于 1。
    应用场景：`table_to_tsv` 逐格调用。
    输入：cell — `<td>` / `<th>`；attribute — "colspan" 或 "rowspan"；fallback — 0 的替代值。
    输出：至少为 1 的跨度。
    """
    try:
        value = int((cell.get(attribute) or "").strip())
    except (TypeError, ValueError):
        return 1
    if value == 0:
        return max(1, fallback)
    return max(1, value)

def _cell_text(cell) -> str:
    """单元格文本：块级元素之间补空格，再压掉换行——制表符方言里一个格位只能占一行。"""
    return " ".join(cell.get_text(separator=" ").split())

def _rows_of(table) -> list:
    """这张表的**直属**行：嵌套表的 `tr` 不属于外层，否则行号会整体错位。"""
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]

def table_to_tsv(table) -> tuple:
    """
    一个 `<table>` → (制表符块, 命中的上界名列表)。

    功能：按 `tr` 逐行、按行内**直属** `td` / `th` 逐格落位；`colspan` / `rowspan` 交给格位网格，
           被吞掉的格由网格自动补覆盖位；行数超 `max_table_rows` 时截断并说明。
    应用场景：`html.extract_html` 与 `epub._chapter_body` 处理表格时调用。
    输入：table — BeautifulSoup 的 `<table>` 元素。
    输出：(制表符分隔的多行文本, 上界名列表)；没有行或没有可渲染内容时返回 ("", [])。
    """
    rows = _rows_of(table)
    if not rows:
        return "", []
    builder = GridBuilder()
    for row_index, row in enumerate(rows):
        builder.next_row()
        for cell in row.find_all(["td", "th"], recursive=False):
            col_span = _span(cell, "colspan")
            row_span = _span(cell, "rowspan", fallback=len(rows) - row_index)
            builder.place(_cell_text(cell), col_span, row_span)
    lines = render_tsv(builder.finish(), limits.MAX_TABLE_ROWS)
    return "\n".join(lines), list(builder.limit_hits)

def render_tables(soup) -> tuple:
    """
    把 soup 里的 `<table>` 就地换成哨兵行。

    功能：逐个表格渲成制表符块、把元素换成 `@@TABLE-N@@`（`NavigableString`，`get_text()` 会在
           原位置带出它，于是表格落在文档顺序里该在的位置）；空表直接删掉。
    应用场景：`html.py` / `epub.py` 在取正文文本**之前**调用。
    输入：soup — 已经剥掉 script / style 的 soup。
    输出：({哨兵: 表块}, 命中的上界名列表)。
    """
    blocks, hits = {}, []
    for index, table in enumerate(soup.find_all("table")):
        block, table_hits = table_to_tsv(table)
        hits.extend(table_hits)
        if not block:
            table.decompose()
            continue
        sentinel = SENTINEL.format(index=index)
        blocks[sentinel] = block
        table.replace_with(NavigableString(f"\n{sentinel}\n"))
    return blocks, hits

def restore_blocks(text: str, blocks: dict) -> str:
    """把哨兵行换回制表符块（**必须在清洗之后**：清洗会 strip 每一行，先换回会把行首空列吃掉）。"""
    for sentinel, block in blocks.items():
        text = text.replace(sentinel, block)
    return text
