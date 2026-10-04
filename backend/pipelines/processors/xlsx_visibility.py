# -*- coding: utf-8 -*-
"""
xlsx / xlsm 包内 XML 里的**隐藏状态**读取：哪张工作表整表隐藏、哪些行列被隐藏。

为什么单独一个文件：`read_only=True` 的 worksheet **没有** `row_dimensions` / `column_dimensions`
（实测同一份文件：常规模式给出 `hidden_rows=[2]` / `hidden_cols=['B']`，只读模式两者都是空的），
而值仍必须走只读模式（「边读边停」的内存护栏，理由同 `xlsx_merges.py`）。所以隐藏状态自己解包读。
`sheet_state` 是例外——只读模式也有值，但既然已经解包，就一处读全，免得两个来源对不上。

隐藏**不是**丢弃内容的理由（用户 2026-10-04 拍板：隐藏只是界面上没显示）；本模块产出的信息
只用于 `notes` 登记，让调用方知道这份产出的可见性状态。

本模块只引用叶子模块 `index_runs`（区间换算），不 import 其它项目内代码。
"""
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

from pipelines.processors.index_runs import to_runs

# 工作表可见性的取值（OOXML 的 `<sheet state="...">`）
VISIBLE = "visible"
HIDDEN = "hidden"
VERY_HIDDEN = "veryHidden"

def read_visibility(path: Path) -> Dict[str, dict]:
    """
    读每个工作表的隐藏状态与隐藏行列。

    功能：解包读 `xl/workbook.xml` 拿 `<sheet state="hidden|veryHidden">`，按 rels 定位各表 XML，
          再读 `<row hidden="1" r="N">` 与 `<col hidden="1" min max>`；行列一律换算成 0 基半开区间。
    应用场景：`excel.py` 读 xlsx / xlsm 时调用一次，产出用于 `notes` 登记。
    输入：path — xlsx / xlsm 路径。
    输出：{工作表名: {"state": ..., "hidden_rows": [(起, 止), ...], "hidden_cols": [...]}}；
          包里读不动时返回空字典，调用方按「全部可见」处理（内容读取不受影响）。
    """
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    try:
        with zipfile.ZipFile(path) as package:
            workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
            rels = ElementTree.fromstring(package.read("xl/_rels/workbook.xml.rels"))
            targets = {node.get("Id"): (node.get("Target") or "") for node in rels}
            visibility = {}
            for sheet in workbook.iter(f"{main}sheet"):
                part = targets.get(sheet.get(f"{rel}id"), "").lstrip("/")
                if part and not part.startswith("xl/"):
                    part = f"xl/{part}"
                visibility[sheet.get("name") or ""] = {
                    "state": sheet.get("state") or VISIBLE,
                    **_hidden_spans(package, part, main),
                }
            return visibility
    except Exception:
        return {}

def _hidden_spans(package: zipfile.ZipFile, part: str, main: str) -> dict:
    """一个工作表 XML 里的隐藏行与隐藏列（0 基半开区间）；部件缺失或解析不动时按「没有隐藏」处理。"""
    if not part:
        return {"hidden_rows": [], "hidden_cols": []}
    try:
        root = ElementTree.fromstring(package.read(part))
    except Exception:
        return {"hidden_rows": [], "hidden_cols": []}
    hidden_rows = []
    for position, row in enumerate(root.iter(f"{main}row"), start=1):
        if _is_hidden(row):
            # `r` 是 1 基行号，缺省时按元素出现次序数（span 之类不写 r 的行）
            hidden_rows.append(int(row.get("r") or position) - 1)
    hidden_cols = []
    for col in root.iter(f"{main}col"):
        if not _is_hidden(col):
            continue
        first = int(col.get("min") or 1)
        last = max(first, int(col.get("max") or first))
        hidden_cols.extend(range(first - 1, last))
    return {"hidden_rows": to_runs(hidden_rows), "hidden_cols": to_runs(hidden_cols)}

def _is_hidden(node) -> bool:
    """OOXML 的布尔属性：`1` / `true` 都算隐藏（Excel 两种都写）。"""
    return (node.get("hidden") or "").strip().lower() in ("1", "true")
