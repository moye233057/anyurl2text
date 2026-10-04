# -*- coding: utf-8 -*-
"""
表格类型：`data`（真正的数据表）还是 `layout`（排版壳）。

口径照 anydoc 的 `TableKind`（`src/model/table.rs:21-30`）：只有 `Data` 与 `Layout` 两个变体，
而它判 `Layout` 的唯一行为判据是 `is_single_cell()`（`src/model/table.rs:104-108`）——
**1×1 的单格表**。anydoc 自己没有「数据表 vs 印刷目录表」的判定算法（`Layout` 只在它的单元测试里
被构造过一次），所以本仓也不造这种启发式：猜错表类型会误导按它分支的调用方，与本仓
「宁可回落也不猜」的取向相反。

本模块不 import 项目内任何东西，是叶子模块（ADR-0007）。
"""
from typing import List, Optional

DATA = "data"
LAYOUT = "layout"

def classify(lines: List[str]) -> Optional[str]:
    """
    渲染好的制表符表格 → 表类型。

    功能：剥掉行数截断的说明行，按制表符切列；**1×1 的单格表**算 `layout`（排版壳：文本框里
           当容器用的那种表），其余算 `data`；没有可判的行时返回 `None`。
    应用场景：各处理器在「交付文本只有一张表」时调用，值进 `meta.table_kind`。
    输入：lines — 该表的渲染行（`render_tsv` 的产出，可能带「... 其余 N 行未列出」）。
    输出：`"layout"` / `"data"` / `None`。
    """
    rows = [line.split("\t") for line in lines if line.strip() and not line.startswith("...")]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return None
    if len(rows) == 1 and len(rows[0]) == 1:
        return LAYOUT
    return DATA
