# -*- coding: utf-8 -*-
"""
格位网格：合并单元格的唯一表示与唯一渲染实现（机制借自 anydoc 的 `src/model/table.rs`）。

**不变式：每个逻辑格位恰好出现一次**——内容与跨度只挂在 origin 上，被跨度覆盖的格位是
`CoveredSlot` 并指回覆盖它的 origin。三类脏输入（显式写覆盖位的 ODF、完全不写覆盖位的
OOXML、真实重叠的跨度）都由 `GridBuilder` 吸收，下游只按列位置读：「哪一格是重复」这个
判断因此被结构性消灭，而不是靠每个格式自己小心。

为什么不放进 `schema.py`：三个格式都要填它、而渲染只能有一份实现，塞进契约文件会顶到
单文件预算（`tests/test_structure.py` 判据 2）；它与处理器同层、只 import 同层 `schema`，
依赖方向不受影响（ADR-0007）。

超限不抛异常：停止展开并记 `limit_hit`，由处理器写进 `notes`——「降级是正常结果」（ADR-0003），
网格超限不该让整条请求变成错误。
"""
from typing import Dict, List, Optional, Tuple

import configs.limits as limits
from pipelines.processors.schema import CoveredSlot, GridSpan, TableGrid

def _holds_place(slot) -> bool:
    """这个格位是否占住一个位置：覆盖位、有文字的 origin、带跨度的 origin 都算。"""
    return isinstance(slot, CoveredSlot) or bool(slot.text) or slot.col_span > 1 or slot.row_span > 1

class GridBuilder:
    """网格的唯一构造者：逐行 `next_row()`，一行内按列序 `place()` / `covered()`，最后 `finish()`。"""

    def __init__(self, limit: int = limits.MAX_GRID_EXPANSION) -> None:
        self.grid: TableGrid = []
        self.limit_hit: Optional[str] = None
        self._pending: Dict[Tuple[int, int], Tuple[int, int]] = {}
        self._expansion = 0
        self._limit = limit

    def next_row(self) -> None:
        """开一行。行内格位必须按列序落：列号由本类维护，调用方不要自己数。"""
        self.grid.append([])

    def place(self, text: str, col_span: int = 1, row_span: int = 1) -> None:
        """
        落一个 origin 格位。

        功能：面积先记账（超限退化成 1x1 并记 `limit_hit`），与本行或下方已被别的跨度认领的
              位置重叠时就地钳制，再把跨度覆盖的其余位置登记进待覆盖账本。
        应用场景：各格式处理器读到非覆盖格时调用。
        输入：text — 已清理的单元格文本；col_span / row_span — 横向与纵向跨度。
        输出：无。
        """
        col_span, row_span = max(1, int(col_span)), max(1, int(row_span))
        area = col_span * row_span
        self._expansion += area - 1
        if self._limit and self._expansion > self._limit:
            self._expansion -= area - 1
            self.limit_hit = "table_expansion"
            col_span = row_span = 1
        row = len(self.grid) - 1
        self._skip_pending(row)
        col = len(self.grid[row])
        col_span = self._clamp_cols(row, col, col_span)
        row_span = self._clamp_rows(row, col, col_span, row_span)
        self.grid[row].append(GridSpan(text=text, col_span=col_span, row_span=row_span))
        for row_offset in range(row_span):
            for col_offset in range(col_span):
                if (row_offset, col_offset) != (0, 0):
                    self._pending[(row + row_offset, col + col_offset)] = (row, col)

    def covered(self) -> bool:
        """消费一个显式覆盖位；无人认领时退化成空格并返回 False（产出方少写了格子）。"""
        row = len(self.grid) - 1
        origin = self._pending.pop((row, len(self.grid[row])), None)
        if origin is None:
            self.grid[row].append(GridSpan())
            return False
        self.grid[row].append(CoveredSlot(origin_row=origin[0], origin_col=origin[1]))
        return True

    def finish(self) -> TableGrid:
        """
        收尾并交出网格。

        功能：把跨度认领过、产出方却没写覆盖位的位置补齐成 `CoveredSlot`（必要时补行补列），
              再裁掉尾部全空行。重复调用是幂等的。
        应用场景：一张表的格位全部落完后调用一次。
        输出：可直接交给 `render_tsv` 的网格。
        """
        for (row, col), origin in sorted(self._pending.items()):
            while len(self.grid) <= row:
                self.grid.append([])
            while len(self.grid[row]) < col:
                self.grid[row].append(GridSpan())
            self.grid[row].append(CoveredSlot(origin_row=origin[0], origin_col=origin[1]))
        self._pending.clear()
        while len(self.grid) > 1 and not any(_holds_place(slot) for slot in self.grid[-1]):
            self.grid.pop()
        return self.grid

    def _skip_pending(self, row: int) -> None:
        """落新格之前，先把此前跨度在本行认领过的位置补成覆盖位。"""
        while (row, len(self.grid[row])) in self._pending:
            self.covered()

    def _clamp_cols(self, row: int, col: int, col_span: int) -> int:
        """横向跨度不得盖住本行已被认领的位置：重叠时钳到那一列之前。"""
        for offset in range(1, col_span):
            if (row, col + offset) in self._pending:
                return offset
        return col_span

    def _clamp_rows(self, row: int, col: int, col_span: int, row_span: int) -> int:
        """纵向跨度不得盖住下方已被认领的位置：重叠时钳到那一行之前。"""
        for offset in range(1, row_span):
            for col_offset in range(col_span):
                if (row + offset, col + col_offset) in self._pending:
                    return offset
        return row_span

def fill_from_specs(builder: GridBuilder, specs_by_row: list) -> TableGrid:
    """
    按「格位规格行」填充网格。

    功能：先把纵向继续格沿同列数出来，给 origin 补上行跨度，再逐格落位——纵向继续格自己不写
          行数（OOXML 的 `vMerge` 就是这样），这个换算对 docx / odf 这类格式是同一件事，
          放这里免得每个格式各写一遍。
    应用场景：处理器把原生格位规格交给网格时调用。
    输入：builder — 新建的构造者；specs_by_row — 每行是 [(文本, 横向跨度, 是否纵向继续格), ...]。
    输出：已完成的网格。
    """
    continues = []
    for specs in specs_by_row:
        columns = set()
        column = 0
        for _, col_span, is_continue in specs:
            if is_continue:
                columns.update(range(column, column + col_span))
            column += col_span
        continues.append(columns)
    for row_index, specs in enumerate(specs_by_row):
        builder.next_row()
        column = 0
        for text, col_span, is_continue in specs:
            if is_continue:
                for _ in range(col_span):
                    builder.covered()
            else:
                row_span = 1
                while row_index + row_span < len(continues) and column in continues[row_index + row_span]:
                    row_span += 1
                builder.place(text, col_span, row_span)
            column += col_span
    return builder.finish()

def render_tsv(grid: TableGrid, max_rows: int) -> List[str]:
    """
    把格位网格渲染成制表符分隔的行（各格式共用这一份）。

    功能：覆盖位输出空串（占列位不占内容）；列宽取「最后一个有文字或有覆盖位的列」，所以合并区
          覆盖的空列不会被裁掉；整行没有文字的行跳过（保持现有方言）；超过行数上限时截断并说明。
    应用场景：各处理器的表格渲染段调用。
    输入：grid — 格位网格；max_rows — 最多渲染多少行。
    输出：行列表；`=== SHEET: 名 ===` 这类分节标记由处理器自己拼。
    """
    width = max(
        (index + 1 for row in grid for index, slot in enumerate(row) if _holds_place(slot)),
        default=0,
    )
    lines: List[str] = []
    for index, row in enumerate(grid):
        if len(lines) >= max_rows:
            lines.append(f"... 其余 {len(grid) - index} 行未列出")
            break
        cells = ["" if isinstance(slot, CoveredSlot) else slot.text for slot in row[:width]]
        if not any(cells):
            continue
        lines.append("\t".join(cells))
    return lines
