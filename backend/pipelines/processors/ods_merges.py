# -*- coding: utf-8 -*-
"""
ODS 包内 XML 里的合并区读取。

为什么要单独解包读：`pyexcel-ods` 只给值矩阵，`table:number-columns-spanned` /
`table:number-rows-spanned` / `covered-table-cell` 一概丢掉（2026-10-04 实测：该库源码里
`spanned` / `covered` / `number-columns` / `number-rows` 零命中）。于是 ODS 的合并区与
xlsx 走同一条路——**从包内 XML 自己读**，只是容器内的写法不同（ODF 用元素属性，OOXML 用
`<mergeCell ref="A1:C1">`）。

与 `odf.py`（ODT/ODP）的分工：那边是文本类文档，表格只是正文的一部分，合并区在正文遍历里顺带
填网格；这边是表格类文档，值走 `pyexcel-ods`、合并区在这里单独取，两边都归到同一份格位网格。

坐标口径：**半开区间** (行起, 列起, 行止, 列止)，与本项目合并矩形的唯一输入形态一致
（见 `excel.py` 的 `_sheet_grid`）。ODF 的 `number-*-repeated` 必须参与列游标，否则后面的
合并区坐标整体左移——这也是本模块要用 `limits.MAX_REPEATED_SLOTS` 的地方（上界只有一个定义处）。
"""
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

import configs.limits as limits
from pipelines.processors.odf_table_rows import table_rows

TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"

def _repeat(element: ElementTree.Element, attribute: str) -> int:
    """读一个「重复次数」属性：缺失或非法按 1，并钳到 `MAX_REPEATED_SLOTS`（与 odf.py 同口径）。"""
    try:
        value = int(element.get(TABLE_NS + attribute, "1"))
    except (TypeError, ValueError):
        return 1
    return min(limits.MAX_REPEATED_SLOTS, max(1, value))

def _table_rects(table: ElementTree.Element) -> List[Tuple[int, int, int, int]]:
    """
    一张表的合并区。

    功能：按行展开 `number-rows-repeated`，按列展开 `number-columns-repeated`，把带跨度的单元格
           换算成半开矩形；显式 `covered-table-cell` 只占列位、不产生矩形。
    应用场景：`read_merge_rects` 逐表调用。
    输入：table — `table:table` 元素。
    输出：半开矩形列表，顺序即文档顺序。
    """
    rects: List[Tuple[int, int, int, int]] = []
    row_index = 0
    rows, _ = table_rows(table, lambda row: _repeat(row, "number-rows-repeated"))
    for row in rows:
        cells: List[Tuple[str, int, int]] = []
        for cell in row:
            if cell.tag == TABLE_NS + "covered-table-cell":
                cells.append(("covered", 1, 1))
                continue
            if cell.tag != TABLE_NS + "table-cell":
                continue
            col_span = _repeat(cell, "number-columns-spanned")
            row_span = _repeat(cell, "number-rows-spanned")
            for _ in range(_repeat(cell, "number-columns-repeated")):
                cells.append(("cell", col_span, row_span))
        for offset in range(_repeat(row, "number-rows-repeated")):
            column = 0
            for kind, col_span, row_span in cells:
                if kind == "cell" and (col_span > 1 or row_span > 1):
                    rects.append((row_index + offset, column, row_index + offset + row_span, column + col_span))
                column += col_span
            row_index += 1
    return rects

def align_values(rows: list, rects: List[Tuple[int, int, int, int]]) -> list:
    """
    把「不给覆盖位留位置」的值行对齐到网格列序。

    为什么需要这一步：`pyexcel-ods` 遇到 `covered-table-cell` **不产出任何值**，于是含纵向合并
    的行比别的行少若干项；直接按列号落位会让后面的值整体左移（实测：`b3` 丢失、`c3` 挤到第 2 列）。
    openpyxl 相反——被覆盖的格会给出 `None` 占位，所以只有 ODS 需要对齐。对齐之后两条读取路径
    对同一形状给出相同的值矩阵，`excel._sheet_grid` 一份逻辑就够。

    功能：按矩形算出覆盖位集合，逐行把值填到非覆盖列上，覆盖列插空串。
    应用场景：`excel._read_ods` 读到值与合并矩形之后调用。
    输入：rows — pyexcel-ods 给的值行；rects — 该表的半开矩形。
    输出：对齐后的值行（覆盖位是空串，与 openpyxl 的 `None` 等价）。
    """
    covered = set()
    for row_start, col_start, row_end, col_end in rects:
        for row in range(row_start, row_end):
            for col in range(col_start, col_end):
                if (row, col) != (row_start, col_start):
                    covered.add((row, col))
    if not covered:
        return rows
    aligned = []
    for row_index, row in enumerate(rows):
        values = list(row)
        fixed: list = []
        value_index = 0
        column = 0
        while value_index < len(values) or (row_index, column) in covered:
            if (row_index, column) in covered:
                fixed.append("")
            else:
                fixed.append(values[value_index])
                value_index += 1
            column += 1
        aligned.append(fixed)
    return aligned

def read_merge_rects(path: Path) -> Dict[str, List[Tuple[int, int, int, int]]]:
    """
    读每个工作表的合并区，按**表名**索引。

    功能：解包读 `content.xml`，逐 `table:table` 取 `table:name` 与它的合并矩形。
    为什么按表名而不是按序号：`pyexcel-ods` 只回值字典，空表可能被它丢掉，序号对齐会错位；
    表名是两边都有的稳定键（拿不到名字时返回空字典，调用方按「没有合并区」处理）。
    应用场景：`excel.py` 读 ods 时调用一次，与行值一起交给格位网格。
    输入：path — ods 路径。
    输出：{表名: [半开矩形, ...]}；包读不动或解析失败时返回空字典。
    """
    try:
        with zipfile.ZipFile(path) as package:
            root = ElementTree.fromstring(package.read("content.xml"))
    except Exception:
        return {}
    rects: Dict[str, List[Tuple[int, int, int, int]]] = {}
    for table in root.iter(TABLE_NS + "table"):
        name = table.get(TABLE_NS + "name") or ""
        rects[name] = _table_rects(table)
    return rects
