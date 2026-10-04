# -*- coding: utf-8 -*-
"""
ODF 表格的**行收集**：把 `table:table-row` 从各层容器里按文档顺序取出来。

为什么单独一个文件：`table:table-row` 不只直接挂在 `table:table` 下——`table:table-header-rows`
（表头行组）、`table:table-rows`、`table:table-row-group` 都会再包一层。三处都要这份逻辑：
`odf.py` 渲正文（只取直属行的话整个表头行组会凭空消失）、`ods_merges.py` 算合并区坐标、
`ods_visibility.py` 标隐藏行列。放在这里是为了让「哪些算这一张表的行、哪些算表头」只有一份定义。

本模块不 import 项目内任何东西，是叶子模块（ADR-0007）。
"""
from typing import List, Tuple

TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
# 行组容器：ODF 允许把行再包一层（table:table-rows 收正文行、table:table-row-group 两组都收）
ROW_GROUPS = (TABLE_NS + "table-rows", TABLE_NS + "table-row-group")
# 表头行组：里面的行既是**内容**（要照渲），也是「源自己标出的表头」（要计数）
HEADER_ROWS = TABLE_NS + "table-header-rows"
TABLE = TABLE_NS + "table"
ROW = TABLE_NS + "table-row"

def table_rows(container, repeated=None) -> Tuple[List, int]:
    """
    一个 ODF 表格容器里的行（文档顺序）+ 其中属于表头行组的行数。

    功能：按容器递归收行，遇嵌套表停住（它的行不属于外层，文字已由单元格取文本时收进去）；
           表头行组里的行照收，另外按 `number-rows-repeated` 展开后计数（重复 N 次就是 N 行表头）。
    应用场景：`odf._render_table`、`ods_merges._table_rects`、`ods_visibility.read_visibility`。
    输入：container — `table:table` 元素，或行组容器（递归用）；
          repeated — 读 `number-rows-repeated` 的函数（`(元素) -> int`）；省略时表头计数按每行 1 计。
    输出：(行元素列表, 表头行数)——行元素各出现一次，重复由调用方自己展开。
    """
    rows: List = []
    header_rows = 0
    for child in container:
        if child.tag == ROW:
            rows.append(child)
        elif child.tag == HEADER_ROWS:
            group = [row for row in child if row.tag == ROW]
            rows.extend(group)
            header_rows += sum(repeated(row) if repeated else 1 for row in group)
        elif child.tag in ROW_GROUPS:
            nested_rows, nested_headers = table_rows(child, repeated)
            rows.extend(nested_rows)
            header_rows += nested_headers
    return rows, header_rows
