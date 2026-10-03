# -*- coding: utf-8 -*-
"""
xlsx / xlsm 包内 XML 里的合并区读取。

为什么单独一个文件：合并区不在 openpyxl 的值接口里——`read_only=True` 的 worksheet 没有
`merged_cells`，而只在常规模式读会让 `excel.py` 的两条读取路径对同一份文件给出不同 `content`
（违反 ADR-0002）。所以值仍走 `read_only`（保住「边读边停」的内存护栏），合并区自己解包读。
与 `pdf` 拆出 `pdf_pages.py` 是同一种理由：同一格式的辅助读取单独成文件，处理器只管流程。

本模块不 import 项目内任何东西，是叶子模块，任何层都能拿它当数据结构用（ADR-0007）。
"""
import re
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from typing import List, Tuple

# A1 记法的单元格引用：列字母 + 行号
A1_PATTERN = re.compile(r"([A-Za-z]+)(\d+)")

def column_index(letters: str) -> int:
    """列号字母 → 0 基列号：A → 0、AA → 26。"""
    value = 0
    for char in letters.upper():
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1

def cell_position(reference: str) -> Tuple[int, int]:
    """A1 记法的单元格引用 → (0 基行, 0 基列)；解析不出时返回 (0, 0)。"""
    match = A1_PATTERN.match(reference.strip())
    if not match:
        return (0, 0)
    letters, digits = match.groups()
    return (int(digits) - 1, column_index(letters))

def read_merge_rects(path: Path) -> List[list]:
    """
    读每个工作表的合并区，返回按工作表顺序排列的半开矩形。

    功能：解包读 `xl/workbook.xml` 与它的 rels 定位各表 XML，再把 `<mergeCell ref="A1:C1"/>`
          换算成 (行起, 列起, 行止, 列止)——半开区间是本项目里合并矩形的唯一输入形态。
    应用场景：`excel.py` 读 xlsx / xlsm 时调用一次，与行值一起交给格位网格。
    输入：path — xlsx / xlsm 路径。
    输出：[[矩形, ...], ...]，与工作簿里 `<sheet>` 的顺序一一对应；包读不动时返回空列表，
          调用方按「没有合并区」处理。
    """
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    try:
        with zipfile.ZipFile(path) as package:
            workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
            rels = ElementTree.fromstring(package.read("xl/_rels/workbook.xml.rels"))
            targets = {node.get("Id"): (node.get("Target") or "") for node in rels}
            sheets = []
            for sheet in workbook.iter(f"{main}sheet"):
                part = targets.get(sheet.get(f"{rel}id"), "").lstrip("/")
                if part and not part.startswith("xl/"):
                    part = f"xl/{part}"
                sheets.append(_sheet_rects(package, part, main))
        return sheets
    except Exception:
        return []

def _sheet_rects(package: zipfile.ZipFile, part: str, main: str) -> list:
    """一个工作表 XML 里的合并矩形；部件缺失或解析不动时返回空列表。"""
    if not part:
        return []
    rects = []
    for merge in ElementTree.fromstring(package.read(part)).iter(f"{main}mergeCell"):
        start, _, end = (merge.get("ref") or "").partition(":")
        if end:
            row_start, col_start = cell_position(start)
            row_end, col_end = cell_position(end)
            rects.append((row_start, col_start, row_end + 1, col_end + 1))
    return rects
