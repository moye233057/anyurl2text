# -*- coding: utf-8 -*-
"""
表格逻辑格子：合并单元格的唯一表示与唯一渲染实现（机制借自 anydoc 的 `src/model/table.rs`）。

**不变式：每个逻辑格位恰好出现一次**——内容与跨度只挂在 origin 上，被跨度覆盖的逻辑格子是
`CoveredSlot` 并指回覆盖它的 origin。三类脏输入（显式写覆盖位的 ODF、完全不写覆盖位的
OOXML、真实重叠的跨度）都由 `GridBuilder` 吸收，下游只按列位置读：「哪一格是重复」这个
判断因此被结构性消灭，而不是靠每个格式自己小心。

为什么不放进 `schema.py`：三个格式都要填它、而渲染只能有一份实现，塞进字段约定文件会顶到
（目标迁入 `utils/excel/`，见 ADR-0007）；它与处理器同层、只 import 同层 `schema`，
依赖方向不受影响（ADR-0007）。

超限不抛异常：命中预算时**不落这一格**（或把跨度退化成 1×1），把上界名记进 `limit_hits`，由处理器写进
`limits_hit` / `notes`——「降级是正常结果」（ADR-0003），网格超限不该让整条请求变成错误。
三条预算各管一件事：**展开**（跨度额外占的逻辑格子数）、**格位数**（本表落了多少格）、
**文字量**（落进来的文字总字符数，直接界定渲染出的中间字符串大小）。
"""
from typing import Dict, List, Optional, Tuple

import configs.limits as limits
from utils.processor_schema import CoveredSlot, GridSpan, TableGrid

def _holds_place(slot) -> bool:
    """这个逻辑格子是否占住一个位置：覆盖位、有文字的 origin、带跨度的 origin 都算。"""
    return isinstance(slot, CoveredSlot) or bool(slot.text) or slot.col_span > 1 or slot.row_span > 1

class GridBuilder:
    """网格的唯一构造者：逐行 `next_row()`，一行内按列序 `place()` / `covered()`，最后 `finish()`。"""

    def __init__(
        self,
        limit: Optional[int] = None,
        slot_limit: Optional[int] = None,
        text_limit: Optional[int] = None,
    ) -> None:
        self.grid: TableGrid = []
        self.limit_hits: List[str] = []
        self._pending: Dict[Tuple[int, int], Tuple[int, int]] = {}
        self._expansion = 0
        self._slots = 0
        self._text_chars = 0
        # 两件要登记的事（`grid_notes` 把它们翻成 notes）：没人认领的覆盖位、并回 origin 的续格文字
        self.orphan_covered = 0
        self.merged_residue = 0
        # 三条预算都在**调用时**读，不用默认参数：默认参数在 import 期就绑定，用例没法 monkeypatch。
        # `configs/limits.py` 仍是唯一事实源，改值只改那一处。
        self._limit = limits.MAX_GRID_EXPANSION if limit is None else limit
        self._slot_limit = limits.MAX_GRID_SLOTS if slot_limit is None else slot_limit
        self._text_limit = limits.MAX_GRID_TEXT_CHARS if text_limit is None else text_limit

    def _charge(self, name: str, amount: int, budget: Optional[int]) -> bool:
        """
        记一笔预算并判断还放不放得下。

        功能：命中时把上界名记进 `limit_hits`（同名只记一次）并返回 False——调用方据此**不落这一格**，
               超限的格既不落位、也不登记它认领的覆盖位，所以网格不会出现「跨度声明了、覆盖位没有」。
        应用场景：`place` / `covered` 每次落位前、`finish` 每次补位前调用。
        输入：name — 上界名；amount — 本次记账量；budget — 该条预算（0 或 None 表示不限）。
        输出：True 表示还放得下；False 表示已超限。
        """
        if name == "max_grid_slots":
            self._slots += amount
            total = self._slots
        else:
            self._text_chars += amount
            total = self._text_chars
        if not budget:
            return True
        if total > budget and name not in self.limit_hits:
            self.limit_hits.append(name)
        return total <= budget

    def next_row(self) -> None:
        """开一行。行内格位必须按列序落：列号由本类维护，调用方不要自己数。"""
        self.grid.append([])

    def place(self, text: str, col_span: int = 1, row_span: int = 1) -> None:
        """
        落一个 origin 格位。

        功能：面积先记账（超限退化成 1x1 并记 `max_grid_expansion`），与本行或下方已被别的跨度认领的
              位置重叠时就地压到不重叠，再把跨度覆盖的其余位置登记进待覆盖登记表。
              另有两条预算：`max_grid_slots`（本表已落格位数）与 `max_grid_text_chars`（落进来的文字
              总字符数）——任一超限就**不落这一格**，也不登记它认领的位置。
        应用场景：各格式处理器读到非覆盖格时调用。
        输入：text — 已清理的单元格文本；col_span / row_span — 横向与纵向跨度。
        输出：无。
        """
        col_span, row_span = max(1, int(col_span)), max(1, int(row_span))
        area = col_span * row_span
        self._expansion += area - 1
        if self._limit and self._expansion > self._limit:
            self._expansion -= area - 1
            if "max_grid_expansion" not in self.limit_hits:
                self.limit_hits.append("max_grid_expansion")
            col_span = row_span = 1
        if not self._charge("max_grid_slots", 1, self._slot_limit):
            return
        if not self._charge("max_grid_text_chars", len(text), self._text_limit):
            return
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

    def covered(self, text: str = ""):
        """
        消费一个显式覆盖位，返回**认领到的 origin 格位**（认领不到返回 None）。

        功能：认领成功就把这一位换成指回 origin 的 `CoveredSlot`；没人认领时退化成空逻辑格子（列位不串、
              内容该空还是空），并把这件事记一笔 `orphan_covered`——它是**唯一一处静默退化**，
              登记出来才能把「文件本来就没内容」与「我们没解析对」分开。
              `text` 是产出方写在**续格**里的文字（PPTX 的残留文字、docx 的 vMerge 继续格）：
              非空时并回 origin（见 `merge_residue`），不丢字、也不改列位。
        应用场景：`fill_from_specs` 与各处理器的逻辑格子循环（odf / excel / pptx）。
        输入：text — 续格自带的文字（可空）。
        输出：认领到的 `GridSpan`，或 None（没人认领）。
        """
        row = len(self.grid) - 1
        origin = self._pending.pop((row, len(self.grid[row])), None)
        if not self._charge("max_grid_slots", 1, self._slot_limit):
            return origin
        if origin is None:
            self.orphan_covered += 1
            self.grid[row].append(GridSpan())
            return None
        self.grid[row].append(CoveredSlot(origin_row=origin[0], origin_col=origin[1]))
        claimed = self.grid[origin[0]][origin[1]]
        if text:
            self.merge_residue(claimed, text)
        return claimed

    def merge_residue(self, origin, text: str) -> bool:
        """
        把续格里的文字并回它的 origin，返回是否真的并了。

        为什么要并：合并区的非左上格只是「继续格」，但产出方（WPS / LibreOffice / 手工改过的文件）
        有时把文字留在续格里。anydoc 直接丢，本仓取向是「尽量抽出来」——并进 origin 的文本末尾
        （空格分隔），既不丢字、也不动列位。
        去重：`python-docx` 的 `merge()` 会把续格文字搬进 origin，两边都有的不并第二次。
        应用场景：`covered(text)` 内部调用；处理器不必自己判。
        输入：origin — `GridSpan`；text — 续格文字。
        输出：True 表示这次真的并入了一段。
        """
        if not text or origin is None or text in origin.text:
            return False
        origin.text = f"{origin.text} {text}".strip()
        self.merged_residue += 1
        return True

    def finish(self) -> TableGrid:
        """
        收尾并交出网格。

        功能：把跨度认领过、产出方却没写覆盖位的位置补齐成 `CoveredSlot`（必要时补行补列），
              再裁掉尾部全空行。重复调用是幂等的。
              **这里的逻辑格子预算是软上限**：已认领的覆盖位必须补齐，否则跨度没有背书、不变式就破了；
              所以宁可多出一点（最多 `max_grid_expansion` 个），也要保住网格自洽。
        应用场景：一张表的逻辑格子全部落完后调用一次。
        输出：可直接交给 `render_tsv` 的网格。
        """
        for (row, col), origin in sorted(self._pending.items()):
            while len(self.grid) <= row:
                self.grid.append([])
            while len(self.grid[row]) < col:
                self.grid[row].append(GridSpan())
                self._charge("max_grid_slots", 1, self._slot_limit)
            self.grid[row].append(CoveredSlot(origin_row=origin[0], origin_col=origin[1]))
            self._charge("max_grid_slots", 1, self._slot_limit)
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
                claimed = None
                for _ in range(col_span):
                    claimed = builder.covered() or claimed
                if text:
                    # 续格自带的文字并回 origin（不丢字、不改列位；认领不到时无处可并，只剩孤儿计数）
                    builder.merge_residue(claimed, text)
            else:
                row_span = 1
                while row_index + row_span < len(continues) and column in continues[row_index + row_span]:
                    row_span += 1
                builder.place(text, col_span, row_span)
            column += col_span
    return builder.finish()

def grid_notes(builder: "GridBuilder") -> List[str]:
    """
    网格构造过程中两件要登记的事，翻成 `notes` 文案。

    功能：把 `builder.orphan_covered`（没人认领的覆盖位）与 `builder.merged_residue`
          （并回 origin 的续格文字）写成句子——文案只有这一处定义，四条表格路径口径一致。
    为什么值得登记：孤儿覆盖位是**全仓唯一一处静默退化**，它往往意味着产出方的合并标记不成对，
          或者我们自己的合并解析有洞（ODF 表头行组、RTF 竖链都是这类），登记出来才能把
          「文件本来就没内容」与「我们没解析对」分开。
    应用场景：docx / odf / excel / pptx / rtf 收尾时各调一次，把结果并进 `notes`。
    输入：builder — 造完网格的构造器。
    输出：note 列表（没有要登记的事时是空列表）。
    """
    notes = []
    if builder.orphan_covered:
        notes.append(
            f"有 {builder.orphan_covered} 个覆盖位没有对应的合并起点，已按空格处理（列位不受影响）"
        )
    if builder.merged_residue:
        notes.append(f"覆盖格里的残留文字已并入合并单元格（{builder.merged_residue} 处）")
    return notes

def merge_rect(row_start: int, col_start: int, row_end: int, col_end: int):
    """
    把外来的合并矩形统一成本项目的半开形态 (行起, 列起, 行止, 列止)。

    为什么要这一层：不同库的坐标约定不一样——openpyxl 的 `CellRange` 是**闭区间且列在前**
    （`bounds` 是 `(min_col, min_row, max_col, max_row)`），xlrd 的 `sheet.merged_cells` 是
    **开区间且行在前**（`(rlo, rhi, clo, chi)`）。`.xls` 这条走这里；`.xlsx` 那条在
    `excel.read_merge_rects` 里就地换算（不经本模块，避免循环依赖）。
    功能：起止写反时对调，退化的空矩形返回 `None` 由调用方过滤。
    应用场景：`excel._read_xls` 逐项换算 xlrd 的合并区。
    输入：四个整数（含义见上，行序/列序均可）。
    输出：半开矩形四元组；空矩形返回 None。
    """
    if row_end < row_start:
        row_start, row_end = row_end, row_start
    if col_end < col_start:
        col_start, col_end = col_end, col_start
    if row_end <= row_start or col_end <= col_start:
        return None
    return (row_start, col_start, row_end, col_end)

def check_grid(grid: TableGrid) -> List[str]:
    """
    不变式校验：每个 origin 声明的每个位置，都必须有指回它的覆盖位。

    为什么要有它：01 号专题把这条校验列为「必须抄的一件」——「每个逻辑格位恰好出现一次」如果只靠
    `GridBuilder` 的构造过程正确，那么任何一次构造路径改动（新格式接入、坐标换算写错）都可能悄悄
    产出矛盾网格，而渲染出来只是「某一列少一格」，看不出根因。校验函数把这件事变成可核查判据。
    应用场景：由 `tests/test_processors_by_format.py` 调用。生产路径**不调它**——`GridBuilder` 是
    唯一构造者、`finish()` 会把跨度认领过而产出方没写的覆盖位补齐，所以线上没有第二个构造来源
    （理由记在 `00-总览与优先级.md` §4.3-1）；若将来出现「构造完成后再改网格」的路径，再接线。
    输入：grid — 一张表的表格逻辑格子。
    输出：问题描述列表；空列表表示自洽。
    """
    problems: List[str] = []
    for row_index, row in enumerate(grid):
        for col_index, slot in enumerate(row):
            if isinstance(slot, CoveredSlot):
                continue
            for row_offset in range(slot.row_span):
                for col_offset in range(slot.col_span):
                    if (row_offset, col_offset) == (0, 0):
                        continue
                    target_row, target_col = row_index + row_offset, col_index + col_offset
                    if target_row >= len(grid) or target_col >= len(grid[target_row]):
                        backing = None
                    else:
                        backing = grid[target_row][target_col]
                    if not isinstance(backing, CoveredSlot) or (
                        backing.origin_row,
                        backing.origin_col,
                    ) != (row_index, col_index):
                        problems.append(
                            f"origin ({row_index},{col_index}) 在 ({target_row},{target_col}) 没有覆盖位背书"
                        )
    return problems

def render_tsv(grid: TableGrid, max_rows: int, pad_short_rows: bool = True) -> List[str]:
    """
    把表格逻辑格子渲染成制表符分隔的行（各格式共用这一份）。

    功能：覆盖位输出空串（占列位不占内容）；列宽取「最后一个有文字或有覆盖位的列」，所以合并区
          覆盖的空列不会被裁掉；整行没有文字的行跳过（保持现有方言）；超过行数上限时截断并说明。
          `pad_short_rows`：行尾缺格时补空——OOXML 允许一行提前结束（尾部 `w:tc` 省略，
          python-docx 用 `row.grid_cols_after` 暴露），pptx 的生产者也常省略尾部续格；不补的话
          这一行的制表符个数与同表其他行不同，后面所有列位都错开。
          分隔符文本（CSV / TSV）传 `False`：锯齿行是源文件里的真实形状，保真优先——这与结构化
          表格「列位由结构保住」是两种取向，别混。
    应用场景：各处理器的表格渲染段调用。
    输入：grid — 表格逻辑格子；max_rows — 最多渲染多少行；pad_short_rows — 是否把短行补到网格宽度。
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
        if pad_short_rows and len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        if not any(cells):
            continue
        lines.append("\t".join(cells))
    return lines
