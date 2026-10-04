# -*- coding: utf-8 -*-
"""
ODS 包内 XML 里的隐藏状态读取：哪张工作表整表隐藏、哪些行列被隐藏。

为什么自己解包读：`pyexcel-ods` 只给值矩阵，样式一概丢掉——而 ODF 的隐藏**全在样式里**
（表格用 `table:display="false"`、行列用 `table:visibility="collapse"`），元素属性上通常什么都没有。
值那条路必须保持只给值（它同时被 ods 与 csv 共用），所以隐藏状态在这里单独读。

隐藏**不是**丢弃内容的理由（用户 2026-10-04 拍板）：本模块的产出只用于 `notes` 登记。
与 `xlsx_visibility.py` 同形：{工作表名: {"state", "hidden_rows", "hidden_cols"}}，行列是 0 基半开区间。

本模块引用两个叶子模块（`index_runs` 的区间换算、`odf_table_rows` 的行收集），自己不是叶子。
"""
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from typing import Dict, List

import configs.limits as limits
from pipelines.processors.index_runs import to_runs
from pipelines.processors.odf_table_rows import table_rows

OFFICE_NS = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
STYLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
VISIBLE = "visible"
HIDDEN = "hidden"
# 「不显示」的取值：表格是 `display="false"`、行列是 `visibility="collapse"`
HIDDEN_VALUES = ("collapse", "false", "hidden")

def _marked_hidden(element) -> bool:
    """元素自己写没写隐藏：`table:visibility` 或 `table:display` 取了「不显示」的值。"""
    return any(element.get(TABLE_NS + name) in HIDDEN_VALUES for name in ("visibility", "display"))

def _style_names(root) -> Dict[str, bool]:
    """
    自动样式表 → {样式名: 是否隐藏}。

    功能：逐个 `style:style` 看它的属性子元素里有没有「不显示」——`table:display="false"`
            （整表不显示）与 `table:visibility="collapse"`（行列折叠）都算隐藏。
    应用场景：`read_visibility` 解析表格 / 行列前调用一次。
    输入：root — content.xml 根元素。
    输出：{样式名: 是否隐藏}；没被样式表登记的名字查不到，按「可见」处理。
    """
    styles: Dict[str, bool] = {}
    for style in root.iter(STYLE_NS + "style"):
        name = style.get(STYLE_NS + "name") or ""
        if name:
            styles[name] = any(_marked_hidden(properties) for properties in style)
    return styles

def _is_hidden(element, styles: Dict[str, bool]) -> bool:
    """元素自身写了隐藏，或它引用的样式是隐藏样式。"""
    return _marked_hidden(element) or bool(styles.get(element.get(TABLE_NS + "style-name") or ""))

def _repeat(element, attribute: str) -> int:
    """读「重复次数」属性：缺失或非法按 1，钳到 `MAX_REPEATED_SLOTS`（与 odf.py / ods_merges.py 同口径）。"""
    try:
        value = int(element.get(TABLE_NS + attribute, "1"))
    except (TypeError, ValueError):
        return 1
    return min(limits.MAX_REPEATED_SLOTS, max(1, value))

def _sheets(root) -> list:
    """工作簿里的工作表：`office:spreadsheet` 的**直属** `table:table`（嵌套表不是工作表，且常常没有名字）。"""
    spreadsheet = root.find(OFFICE_NS + "body/" + OFFICE_NS + "spreadsheet")
    if spreadsheet is not None:
        return [child for child in spreadsheet if child.tag == TABLE_NS + "table"]
    return list(root.iter(TABLE_NS + "table"))

def read_visibility(path: Path) -> Dict[str, dict]:
    """
    读每个工作表的隐藏状态与隐藏行列。

    功能：解包读 `content.xml`，先收自动样式表，再逐工作表按 `table:name` 建索引：
           整表隐藏看表元素；行看 `table:table-row`（含表头行组与行组容器）并按 `number-rows-repeated`
           展开行号；列看 `table:table-column` 并按 `number-columns-repeated` 展开列号。
    应用场景：`excel.py` 读 ods 时调用一次，产出用于 `notes` 登记。
    输入：path — ods 路径。
    输出：{工作表名: {"state": "visible"|"hidden", "hidden_rows": [(起, 止)], "hidden_cols": [...]}}；
           包读不动时返回空字典，调用方按「全部可见」处理（内容读取不受影响）。
    """
    try:
        with zipfile.ZipFile(path) as package:
            root = ElementTree.fromstring(package.read("content.xml"))
    except Exception:
        return {}
    styles = _style_names(root)
    visibility: Dict[str, dict] = {}
    for table in _sheets(root):
        rows, _ = table_rows(table, lambda row: _repeat(row, "number-rows-repeated"))
        hidden_rows: List[int] = []
        row_index = 0
        for row in rows:
            repeat = _repeat(row, "number-rows-repeated")
            if _is_hidden(row, styles):
                hidden_rows.extend(range(row_index, row_index + repeat))
            row_index += repeat
        hidden_cols: List[int] = []
        column_index = 0
        for column in table.findall(TABLE_NS + "table-column"):
            repeat = _repeat(column, "number-columns-repeated")
            if _is_hidden(column, styles):
                hidden_cols.extend(range(column_index, column_index + repeat))
            column_index += repeat
        visibility[table.get(TABLE_NS + "name") or ""] = {
            "state": HIDDEN if _is_hidden(table, styles) else VISIBLE,
            "hidden_rows": to_runs(hidden_rows),
            "hidden_cols": to_runs(hidden_cols),
        }
    return visibility
