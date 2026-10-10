# -*- coding: utf-8 -*-
"""
索引与「连续段」的换算：把一串 0 基下标压成半开区间，以及列号 ↔ 列字母。

为什么单独一个文件：xlsx / ods 的可见性读取、`.xls` 的 xlrd 映射、以及 notes 里的登记文案
都要这两件事——`<col min="1" max="16384" hidden="1"/>` 这类写法逐个展开会有上万个数字，
区间只跟元素个数同阶。放在这里是为了让「谁都用、谁都不改」的换算只有一份。

本模块不 import 项目内任何东西，是叶子模块（ADR-0007）。
"""
from typing import Iterable, List, Tuple

def column_letters(index: int) -> str:
    """0 基列号 → 列字母：0 → A、25 → Z、26 → AA。"""
    letters = ""
    value = index + 1
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters

def to_runs(indices: Iterable[int]) -> List[Tuple[int, int]]:
    """
    一串 0 基下标 → 半开区间的连续段：`[1, 2, 3, 5]` → `[(1, 4), (5, 6)]`。

    功能：去重、排序后把相邻下标并成一段；段是半开区间（与合并矩形同一口径）。
    应用场景：隐藏行列的登记、anydoc 侧不需要的稀疏下标压缩。
    输入：indices — 0 基下标（可重复、可乱序）。
    输出：`[(起, 止), ...]`，起含止不含，按起点升序。
    """
    runs: List[Tuple[int, int]] = []
    for index in sorted(set(indices)):
        if runs and index == runs[-1][1]:
            runs[-1] = (runs[-1][0], index + 1)
        else:
            runs.append((index, index + 1))
    return runs
