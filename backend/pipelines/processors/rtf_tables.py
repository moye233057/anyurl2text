# -*- coding: utf-8 -*-
"""
RTF 表格 → 制表符块：把「只有右边界」的格规格还原成列。

RTF 的表格控制字只给每格的**右边界**（`\\cellx`），不写列号，`\\cell` / `\\row` 也不说这一格占几列，
所以「一格占哪几列」只能从全表的边界反推：把全表出现过的右边界按容差聚成若干列簇，一格的右边界
落在第几个簇，它的列区间就到那儿为止。算法照 anydoc（Rust）的 `src/shared/grid.rs:build_edge_table`，
顺序不能改：

1. 逐行把右边界归一化成严格递增（值不增就顶成上一个 +1）；
2. 全表边界聚类（与**簇的种子**比，不是与前一个值比）；
3. 逐行折叠横向合并（`\\clmgf` + 紧随其后的 `\\clmrg` 并成一个 origin，文本首尾直接拼）；
4. 逐行接纵向合并（`\\clvmgf` / `\\clvmrg`，按**精确列区间**匹配上一行）；
5. 交给共用的格位网格（`grid.py`）渲成制表符分隔的行——合并区文本只出现一次、覆盖位占列位、
   超行数上限时截断并说明，与 docx / odf 同一份实现。

两处反直觉的地方写在各自的函数上：**左边界不使用**（一个格的左边界就是游标），
**无主的纵向继续格变成可见格**而不是丢掉。
"""
from bisect import bisect_left
from typing import List, NamedTuple, Set, Tuple

import configs.limits as limits
from pipelines.processors.grid import GridBuilder, fill_from_specs, render_tsv

# 边界聚类的容差：10 twips ≈ 0.5pt，同一列的两格边界只差一两个 twip 时仍算同一列。
# 带类型标注是有意的：`tests/test_structure.py` 判据 6 按**值**比对模块级的裸赋值，10 与
# `configs/limits.py` 里 `rendered_page_max` 的值相同，裸赋值会被误报成「第二处上界定义」——
# 它是判定阈值而不是上界（同 `NON_LIMIT_THRESHOLDS` 里那三条）。
EDGE_TOLERANCE: int = 10

class CellSpec(NamedTuple):
    """
    RTF 一格的规格：`rtf.py` 从控制字流里收出来，本模块只消费。

    属性：
        text — 这一格的文字（格内换行与制表符已压成单个空格）。
        right — 右边界 twips（`\\cellx`）；缺 `\\cellx` 时是 0，由 `_normalize` 顶成独立一列。
        merge_first / merge_cont — `\\clmgf` / `\\clmrg`：横向合并区的首格与继续格。
        vmerge_first / vmerge_cont — `\\clvmgf` / `\\clvmrg`：纵向合并区的首格与继续格。
    """

    text: str = ""
    right: int = 0
    merge_first: bool = False
    merge_cont: bool = False
    vmerge_first: bool = False
    vmerge_cont: bool = False

class RtfRow(NamedTuple):
    """一行 RTF 表格：它的格规格，以及源有没有给这一行标 `\\trhdr`（表头行）。"""

    cells: List[CellSpec]
    header: bool = False

def _normalize(cells: List[CellSpec]) -> List[CellSpec]:
    """
    把一行的右边界归一化成严格递增。

    功能：右边界不大于上一格时顶成「上一格 +1」。缺 `\\cellx` 的格按 0 进来，于是被顶成 0、1、2…
          ——各自成列，既不报错也不去和前一格抢同一列；`last` 从 -1 起，所以整行都没写 `\\cellx`
          时得到的是 0、1、2 而不是 1、2、3。
    应用场景：聚类与折叠之前逐行调用。
    输入：cells — 一行从 `\\cellx` 收来的格规格。
    输出：新的格规格列表（`_replace` 出新对象，不改调用方那一份）。
    """
    last = -1
    normalized = []
    for cell in cells:
        right = cell.right if cell.right > last else last + 1
        last = right
        normalized.append(cell._replace(right=right))
    return normalized

def _cluster(values: List[int]) -> List[int]:
    """
    把全表的右边界贪心聚成列簇，返回簇种子（升序）。

    功能：**与簇的种子比**，相差超过 EDGE_TOLERANCE 才开新簇。不是与前一个值比：写出器给出的
          `\\cellx` 常有 1-2 twips 抖动，一串 1000,1002,1004,1006 按相邻值比会一步步并成一簇
          （每步只差 2），一列都分不出来；按种子比它们同属 1000±10，要到 1000 与 3000 之间才断开。
          簇种子两两相差超过容差，`_col_of` 的 bisect_left 结果因此恰好是簇下标。
    应用场景：`build_lines` 在折叠横向合并**之前**调用——被合并吞掉的格的右边界也要进簇，
               否则「合并区该跨几列」会因为少了一个边界而算窄。
    输入：values — 全表归一化后的右边界（可含重复）。
    输出：升序的簇种子列表。
    """
    clusters: List[int] = []
    for value in sorted(values):
        if not clusters or value - clusters[-1] > EDGE_TOLERANCE:
            clusters.append(value)
    return clusters

def _col_of(right: int, clusters: List[int]) -> int:
    """右边界落在第几个列簇：簇种子两两相差超过容差，所以「第一个 ≥ 右边界−容差 的种子」就是它。"""
    return bisect_left(clusters, right - EDGE_TOLERANCE)

def _origins(cells: List[CellSpec]) -> List[Tuple[str, int, bool, bool]]:
    """
    把一行的格折成 origin，返回 [(文本, 右边界, vmerge_first, vmerge_cont)]。

    功能：`\\clmgf` 之后紧随的连续 `\\clmrg` 都并入这一格——文本**首尾直接拼、不插分隔符**
          （合并区本来就是一个逻辑格的文字被拆在几格里，插分隔符会凭空多出空白），右边界取最后一个
          并入格的（合并区跨几列由它决定）；被并入的格不再单独产出。
    左边界不使用：一个格的左边界就是游标（上一个 origin 的右边界之后），而 RTF 里根本没有左边界
          控制字可以读。
    应用场景：`_specs` 逐行调用。
    输入：cells — 已归一化右边界的格。
    输出：这一行的 origin 列表。
    """
    origins = []
    index = 0
    while index < len(cells):
        cell = cells[index]
        text, right = cell.text, cell.right
        if cell.merge_first:
            while index + 1 < len(cells) and cells[index + 1].merge_cont:
                index += 1
                text += cells[index].text
                right = cells[index].right
        origins.append((text, right, cell.vmerge_first, cell.vmerge_cont))
        index += 1
    return origins

def _specs(cells_by_row: List[List[CellSpec]], clusters: List[int]) -> List[list]:
    """
    逐行算出格位规格：每行 [(文本, 横向跨度, 是否纵向继续格), ...]。

    功能：游标从 0 起，横向跨度 = 右边界所在簇的下一列 − 游标（**不按「并了几格」算**：边界抖动或
          缺 `\\cellx` 时格数会骗人，边界簇才是列位）；纵向按**精确列区间相等**去上一行的纵向合并区
          里查，命中就把这一格标成继续格——行跨度由 `fill_from_specs` 从继续格自己数出来，
          形态与 `docx._row_specs` 一致。
    为什么这里不需要 anydoc 那份 origin 坐标：那边要拿它在 origin 上把行跨度 +1，而本仓的行跨度是
          `fill_from_specs` 按「下一行这一列是不是继续格」数出来的，所以只要记住上一行有哪些列区间
          是纵向合并区就够。
    应用场景：`build_lines` 的最后一步。
    输入：cells_by_row — 已归一化右边界的格；clusters — 全表列簇。
    输出：每行的格位规格，可直接交给 `fill_from_specs`。
    """
    specs_by_row = []
    active: Set[Tuple[int, int]] = set()  # 上一行的纵向合并区：(col_l, col_r)
    for cells in cells_by_row:
        specs = []
        next_active: Set[Tuple[int, int]] = set()
        col = 0
        for text, right, vmerge_first, vmerge_cont in _origins(cells):
            col_r = max(_col_of(right, clusters) + 1, col + 1)
            key = (col, col_r)
            if vmerge_cont and key in active:
                specs.append((text, col_r - col, True))
                next_active.add(key)
            else:
                # 无主的继续格（上一行没有同区间的格）**变成可见格**：丢一个字比多一格糟得多
                specs.append((text, col_r - col, False))
                if vmerge_first:
                    next_active.add(key)
            col = col_r
        specs_by_row.append(specs)
        active = next_active
    return specs_by_row

def build_lines(rows: List[RtfRow]) -> Tuple[str, List[str], int]:
    """
    把一张表的行规格渲成制表符块。

    功能：右边界归一化 → 全表边界聚类 → 逐行折叠横向合并 → 逐行接纵向合并 → 交给格位网格；
          行数超 `limits.MAX_TABLE_ROWS` 时只渲前若干行，并在末行说明还剩多少行。
    应用场景：`rtf.py` 收完一张表的**全部**行后调用一次（聚类要看到全表边界，不能一行一行渲）。
    输入：rows — 每行是 RtfRow（格规格 + 源标的表头标记）。
    输出：(制表符分隔的多行文本, 命中的上界名列表, 源标出的表头行数)；没有可渲染内容时文本为空串。
    """
    # 表头行数按**全部**行数出来：它是源标出来的事实，行数截断只影响渲染多少行
    header_rows = sum(1 for row in rows if row.header)
    kept = rows[: limits.MAX_TABLE_ROWS]
    edges = [_normalize(row.cells) for row in kept]
    clusters = _cluster([cell.right for row in edges for cell in row])
    builder = GridBuilder()
    lines = render_tsv(fill_from_specs(builder, _specs(edges, clusters)), limits.MAX_TABLE_ROWS)
    hits = list(builder.limit_hits)
    if len(rows) > limits.MAX_TABLE_ROWS:
        lines.append(f"... 其余 {len(rows) - limits.MAX_TABLE_ROWS} 行未列出")
        hits.append("max_table_rows")
    return "\n".join(lines), hits, header_rows
