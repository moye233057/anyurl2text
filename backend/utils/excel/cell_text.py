# -*- coding: utf-8 -*-
"""
格内文本的**方言**：一个逻辑格子只能占交付文本的一行一列，格内换行写成 `<br>`。

为什么要有这个文件：六条表格路径（docx / pptx / excel / odf / rtf / 网页）都要把「格内多段」收进
同一个逻辑格子，规则必须一模一样——否则同一个 Word 文档经不同路径（例如 docx 与网页导出）会给出
两种文本。规则来自 anydoc 的 `render_cell`（`src/render/markdown/table.rs:87-101`）：段与段之间用
`<br>` 连接，纯空白段丢掉，段内空白仍压成单空格。

两条硬约束（违反任何一条都会把表格拆坏）：
- 格内**绝不能**留真的制表符或换行：制表符凭空多出一列、换行把这一行 TSV 切成两行；
- `<br>` 是**行内记号**、不是真换行，所以列位与行数都不受影响。

已知歧义（照抄 anydoc，不做转义）：源里本来就写着字面 `<br>` 的文本与「格内换行」在交付文本里
长得一样。要区分就得引入转义规则，那是另一件事。

本模块不 import 项目内任何东西，是叶子模块（ADR-0007）。
"""
from typing import Iterable

# 格内换行的记号（与 anydoc 的 Markdown 渲染同一个记号）
LINE_BREAK = "<br>"

def join_lines(parts: Iterable[str]) -> str:
    """
    段落片段 → 格内文本。

    功能：每段剥掉首尾空白、段内空白压成单空格，丢掉空段，再用 `LINE_BREAK` 连起来。
    应用场景：docx 的 `w:p` 序列、ODF 的 `text:p` 序列、网页格里的块级子元素。
    输入：parts — 段落文本序列。
    输出：格内文本（可能是空串）。
    """
    cleaned = [" ".join(part.split()) for part in parts]
    return LINE_BREAK.join(part for part in cleaned if part)

def from_text(text: str) -> str:
    """
    一段自带换行的文字 → 格内文本。

    功能：`\\r\\n` / `\\r` / `\\n` / `\\v`（Word 与 PowerPoint 的软换行）都算换行，
          制表符换成空格，然后交给 `join_lines`。
    应用场景：pptx 的 `cell.text`、excel 的单元格值、`\\par` / `\\line` 之后的 RTF 格文字。
    输入：text — 原始格文本。
    输出：格内文本（可能是空串）。
    """
    normalized = text.replace("\t", " ").replace("\v", "\n").replace("\r\n", "\n").replace("\r", "\n")
    return join_lines(normalized.split("\n"))
