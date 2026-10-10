# -*- coding: utf-8 -*-
"""
RTF 控制字解析器：把 RTF 源码还原成纯文本与表格。

按控制字逐字符解析，不用正则剥控制字——正则会把 `\\uN` 转义一起丢掉，中文 RTF 抽出来
几乎没有内容。字体表 / 颜色表 / 图片等可忽略目的地整组跳过；这个解析器单独成文件，
不和 txt / md / json / xml 那四条直读路径挤在一起。

表格是另一件事：RTF 只给每格的**右边界**（`\\cellx`），不写列号，一格占哪几列要从全表边界反推
（表格列位算法见本文件后半 `build_lines`）。本文件前半把控制字流收成「每行一串格规格」：`\\trowd` 开一行、
`\\cellx` 定一格、`\\cell` 收一格文字、`\\row` 收一行。这几个控制字以前不处理，整张表因此塌成一行
——它们被当成未知控制字丢掉，所有格的文字首尾相接，列位与行位一起消失。
"""
from bisect import bisect_left
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple, Union

import configs.limits as limits
from utils.excel import cell_text
from utils.excel.grid import GridBuilder, fill_from_specs, grid_notes, render_tsv
from utils.processor_schema import ExtractResult
from utils.textfile import read_text_file

# RTF 里这些目的地装的是字体表、颜色表、图片、页眉页脚等，不是正文，遇到就整组跳过。
# 不跳过的话，字体表里的字体名会混进正文（这是「正则剥控制字」做法的典型症状）。
RTF_IGNORABLE_DESTINATIONS = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header",
    "footer", "footnote", "annotation", "fldinst", "listtable",
    "listoverridetable", "rsidtbl", "generator", "themedata",
    "colorschememapping", "latentstyles", "datastore", "xmlnstbl",
    "filetbl", "revtbl", "pntext", "nonshppict", "shppict",
})

# 只有这四种格属性参与合并：`\clmgf`/`\clmrg` 是横向合并区的首格/继续格，
# `\clvmgf`/`\clvmrg` 是纵向的。其余 `\cl*`（边框、垂直对齐…）与列位无关，解析器不认。
MERGE_FLAGS = ("clmgf", "clmrg", "clvmgf", "clvmrg")

# ===== 表格列位（原 rtf_tables）=====
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

def build_lines(rows: List[RtfRow]) -> Tuple[str, List[str], int, List[str]]:
    """
    把一张表的行规格渲成制表符块。

    功能：右边界归一化 → 全表边界聚类 → 逐行折叠横向合并 → 逐行接纵向合并 → 交给格位网格；
          行数超 `limits.MAX_TABLE_ROWS` 时只渲前若干行，并在末行说明还剩多少行。
    应用场景：`rtf.py` 收完一张表的**全部**行后调用一次（聚类要看到全表边界，不能一行一行渲）。
    输入：rows — 每行是 RtfRow（格规格 + 源标的表头标记）。
    输出：(制表符分隔的多行文本, 命中的上界名列表, 源标出的表头行数, 网格登记的 notes)；
          没有可渲染内容时文本为空串。
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
    return "\n".join(lines), hits, header_rows, grid_notes(builder)

class RtfTable(NamedTuple):
    """
    一张已经渲好的表。

    属性：
        lines — 制表符分隔的行（已按 `max_table_rows` 截断）。
        limits_hit — 这张表命中的上界名。
        header_rows — 源自己标出的表头行数（`\\trhdr`）。
        notes — 网格登记的 notes（孤儿覆盖位 / 并回的续格文字）。
    """

    lines: List[str]
    limits_hit: List[str]
    header_rows: int
    notes: List[str]

def _rtf_at_group_start(source: str, backslash_pos: int) -> bool:
    """判断某个控制字是不是「某个组的第一个控制字」——可忽略目的地都是这种形态。"""
    cursor = backslash_pos - 1
    while cursor >= 0 and source[cursor] in " \t\r\n":
        cursor -= 1
    return cursor >= 0 and source[cursor] == "{"

def _int_param(param: str, fallback: int = 0) -> int:
    """控制字的数字参数；缺失或写法非法时用 fallback（一个畸形控制字不该把整篇解析炸掉）。"""
    try:
        return int(param)
    except ValueError:
        return fallback

def _cell_text(pieces: List[str]) -> str:
    """
    把一格的文字收干净。

    功能：格内累积的文字按格位方言（`processors/cell_text.py`）收成一行——`\\par` / `\\line` / `\\tab`
          与源码里的换行都变成**记号**（换行 → `<br>`、制表符 → 空格），段内空白压成单空格。
    为什么必须收：一格只能占交付文本的一行一列，格内留一个真制表符就凭空多出一列、留一个真换行就把
          这一行 TSV 拆成两行。
    应用场景：`\\cell` / `\\row` 收格时调用。
    输入：pieces — 这一格累积的文字片段。
    输出：单行文字（段落边界是 `<br>`）。
    """
    return cell_text.from_text("".join(pieces))

def _row_cells(defs: List[CellSpec], texts: List[str]) -> List[CellSpec]:
    """
    把一行的 `\\cellx` 定义与 `\\cell` 文字按**序号**配对成这一行的格规格。

    功能：第 i 个 `\\cell` 的文字贴到第 i 个 `\\cellx` 的格上；多出来的文字得到一格 `right=0`
          （由 `_normalize` 顶成独立一列），多出来的定义得到空格。
    为什么按序号：RTF 的 `\\cellx` 全写在格文字之前（Word 的输出形态），两者之间没有别的键可用。
    应用场景：`\\row` 收行时调用。
    输入：defs — 来自 `\\cellx` 的格（含合并标志）；texts — 依次 `\\cell` 收下的文字。
    输出：这一行的格规格。
    """
    cells = []
    for index in range(max(len(defs), len(texts))):
        if index < len(defs):
            cells.append(defs[index]._replace(text=texts[index] if index < len(texts) else ""))
        else:
            cells.append(CellSpec(text=texts[index]))
    return cells

def _table_block(rows: List[RtfRow]) -> RtfTable:
    """一行规格交给 `build_lines` 渲成表格段。"""
    text, hits, header_rows, notes = build_lines(rows)
    return RtfTable(text.splitlines() if text else [], hits, header_rows, notes)

def _assemble(blocks: List[Union[str, RtfTable]]) -> str:
    """
    把解析出的段落拼成交付文本。

    功能：正文段按原来的方式归一（逐行 strip、连续空行折成一个），表格段**整行原样插入**。
    为什么表格行不能跟着 strip：贴到列位的行尾制表符就是列位本身（空列也是列），strip 掉列数就变了。
    应用场景：`rtf_to_text` 与 `extract_rtf` 共用。
    输入：blocks — `_rtf_blocks` 的产出。
    输出：交付文本。
    """
    lines: List[str] = []
    for block in blocks:
        if isinstance(block, RtfTable):
            lines.extend(block.lines)
            continue
        for raw in block.splitlines():
            line = raw.strip()
            if line or (lines and lines[-1]):
                lines.append(line)
    return "\n".join(lines).strip("\n")

def _close_nested_row(pieces: List[str]) -> None:
    """
    收一条嵌套行：去掉行尾多出来的那个格间分隔符，再用一个空格接下一条。

    功能：`\\cell` 是「这一格到此结束」，所以格间分隔符 ` / ` 写在**一格之后**，行尾因此多出一个；
          这里先 rstrip 再判断，源码折行留下的换行不影响判断。
    为什么要有它：不处理的话外层那一格会以「内甲 / 内乙 /」结尾——一个没有下文的斜杠。
    应用场景：嵌套表的 `\\row` / `\\nestrow` 收行时调用（`docx._nested_table_text` 用 `" / ".join`
              得到同样的字样：格间 ` / `、行间一个空格）。
    输入：pieces — 当前文字缓冲。
    输出：无（就地改 pieces）。
    """
    trimmed = "".join(pieces).rstrip()
    if trimmed.endswith("/"):
        pieces[:] = [trimmed[:-1].rstrip()]
    pieces.append(" ")

def _skip_fallback(source: str, index: int, count: int) -> int:
    """
    跳过 `\\uN` 后面的 fallback 字符，返回新的下标。

    功能：按 `\\ucN` 声明的个数（默认 1）往前吃；一个 `\\'hh` 十六进制转义算**一个** fallback 单位，
    一个控制字也按一个单位整词吃掉，其余字符一个一个吃。
    为什么不能只跳 `?`：Word 常写 `?`，但 `\\uc1` 配 `\\'hh` 或直接跟普通字符（`\\uc1` + `c1`）
    都是合法写法——漏跳会让这些字符漏进正文（`data/sample.rtf` 里就漏出过一个「c1」）。
    应用场景：`_rtf_blocks` 处理 `\\uN` 时调用。
    输入：source — RTF 源码；index — `\\uN` 之后的下标；count — 要跳几个单位。
    输出：新的下标（不会超过 len(source)）。
    """
    while count > 0 and index < len(source):
        if source[index] == "\\" and index + 1 < len(source) and source[index + 1] == "'":
            index += 2
            digits = 0
            while digits < 2 and index < len(source) and source[index] in "0123456789abcdefABCDEF":
                index += 1
                digits += 1
        elif source[index] == "\\" and index + 1 < len(source) and source[index + 1].isascii() and source[index + 1].isalpha():
            index += 2
            while index < len(source) and source[index].isascii() and source[index].isalpha():
                index += 1
            while index < len(source) and (source[index].isdigit() or source[index] == "-"):
                index += 1
            if index < len(source) and source[index] == " ":
                index += 1
        else:
            index += 1
        count -= 1
    return index

def _rtf_blocks(source: str) -> List[Union[str, RtfTable]]:
    """
    把 RTF 源码解析成「正文段 / 表格段」的顺序列表。

    功能：处理 `\\uN`（含负值的 UTF-16 码位）与 `\\'hh` 十六进制转义，剥掉控制字与花括号，
          跳过字体表 / 颜色表 / 图片等可忽略目的地，`\\par` 还原成换行、`\\tab` 还原成制表符；
          表格按 `\\trowd` / `\\cellx` / `\\cell` / `\\row` 收成行规格，交 `build_lines` 渲成表格段。
    应用场景：`rtf_to_text`（只要文本）与 `extract_rtf`（还要表头行数与上界名）共用这一次解析。
    输入：source — RTF 源码文本。
    输出：段落列表，元素是 str（正文）或 RtfTable（已渲好的表格）。
    """
    blocks: List[Union[str, RtfTable]] = []
    pieces: List[str] = []
    hex_buffer = bytearray()
    depth = 0
    skip_depth = None
    unicode_skip = 1                # `\ucN` 声明 `\uN` 后面跟几个 fallback 字符；RTF 默认 1
    index = 0
    length = len(source)

    # ---------- 表格状态 ----------
    itap = 1                        # `\itapN` 是**状态**、写一次留到下次改写；1 = 外层表
    nested = 0                      # 已经开着、还没关的嵌套行数
    outer_open = False              # 有一行外层表开着（`\trowd` 之后、`\row` 之前）
    table_rows: Optional[List[RtfRow]] = None  # 当前这张表收好的行；None = 没有开着的表
    row_defs: List[CellSpec] = []   # 本行从 `\cellx` 收来的格（带合并标志）
    flags: Dict[str, bool] = {}     # 下一个 `\cellx` 的格属性：RTF 把格属性写在它自己的 `\cellx` 之前
    cell_texts: List[str] = []      # 本行已经 `\cell` 收下的格文字
    row_header = False              # 本行有没有 `\trhdr`
    saw_par = False                 # 见过 `\par`：用来判「表被正文打断」（源码里的换行不算，见 start_table）

    def flush_hex() -> None:
        """把累积的 \\'hh 字节按 utf-8 → gbk → cp1252 依次尝试解码。"""
        if not hex_buffer:
            return
        raw = bytes(hex_buffer)
        hex_buffer.clear()
        for encoding in ("utf-8", "gbk", "cp1252"):
            try:
                pieces.append(raw.decode(encoding))
                return
            except UnicodeDecodeError:
                continue
        pieces.append(raw.decode("latin-1"))

    def start_table() -> None:
        """
        确保现在有一张开着的表：把表前（或两张表之间）的正文收进 blocks。

        功能：出现正文（非空白文字，或一个 `\\par`）就认定上一张表结束，先把它渲好的段落收进去；
              只有空白时那是行与行之间的分隔，丢掉，表继续。
        为什么用 `\\par` 而不是源码里的换行判打断：写出器普遍会把 RTF 源码折行，那些换行只是排版，
              照它们判会把一张好好的表拆成几张。
        应用场景：`\\trowd`（以及没写 `\\trowd` 就冒出来的 `\\cell` / `\\row`）开表时调用。
        输入：无（读外层的 pieces / table_rows / saw_par）。
        输出：无（就地改 blocks 与 table_rows）。
        """
        nonlocal table_rows, saw_par
        text = "".join(pieces)
        pieces.clear()
        if table_rows is not None and (text.strip() or saw_par):
            blocks.append(_table_block(table_rows))
            table_rows = None
        if table_rows is None:
            if text:
                blocks.append(text)
            table_rows = []
        saw_par = False

    while index < length:
        char = source[index]

        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            if skip_depth is not None and depth <= skip_depth:
                skip_depth = None
            depth = max(0, depth - 1)
            index += 1
            continue

        if char != "\\":
            if skip_depth is None:
                flush_hex()
                pieces.append(char)
            index += 1
            continue

        backslash_pos = index
        index += 1
        if index >= length:
            break
        symbol = source[index]

        if symbol == "'":
            try:
                hex_buffer.append(int(source[index + 1 : index + 3], 16))
            except ValueError:
                pass
            index += 3
            continue

        flush_hex()

        if symbol == "u":
            index += 1
            sign = 1
            if index < length and source[index] == "-":
                sign = -1
                index += 1
            digits = ""
            while index < length and source[index].isdigit():
                digits += source[index]
                index += 1
            if not digits:
                # 走到这里说明这不是 `\uN`，而是以 u 开头的**别的控制字**——`\ucN` 最常见。
                # 改前这里直接 continue，`\uc1` 的 "c1" 就留在源码里当成正文吐出来了
                # （`data/sample.rtf` 的第一行原本是「c1」）。
                word = "u"
                while index < length and source[index].isalpha() and source[index].isascii():
                    word += source[index]
                    index += 1
                param = ""
                while index < length and (source[index].isdigit() or source[index] == "-"):
                    param += source[index]
                    index += 1
                if index < length and source[index] == " ":
                    index += 1
                if word == "uc":
                    # `\ucN`：`\uN` 后面跟几个 fallback 字符（0 = 没有）；RTF 里它是状态，一直生效
                    unicode_skip = max(0, _int_param(param, 1))
                continue
            # `\uN` 后面跟的是「当前代码页的替代字符」，按 `\ucN` 声明的个数跳过（默认 1 个）——
            # 只认 `?` 是不够的：`\uc1` 配 `\'hh`、或直接跟一个普通字符都合法，漏跳会漏进正文。
            index = _skip_fallback(source, index, unicode_skip)
            if skip_depth is None:
                code = sign * int(digits)
                pieces.append(chr(code + 65536 if code < 0 else code))
            continue

        if symbol in "\\{}":
            if skip_depth is None:
                pieces.append(symbol)
            index += 1
            continue

        if symbol == "*":
            skip_depth = depth
            index += 1
            continue

        word = ""
        # 控制字只由 ASCII 字母组成。这里的 isascii() 不能省：Python 的 isalpha() 认中日韩文字，
        # 而 RTF 的控制字以「第一个非字母字符」结束，`\cell中文` 是完全合法的写法——省掉这一条，
        # 「中文」会被吞进控制字里整段丢掉（改前 `\intbl甲\cell乙` 两格文字都会消失）。
        while index < length and source[index].isalpha() and source[index].isascii():
            word += source[index]
            index += 1
        param = ""
        while index < length and (source[index].isdigit() or source[index] == "-"):
            param += source[index]
            index += 1
        # 控制字后面那一个空格是分隔符，不属于正文
        if index < length and source[index] == " ":
            index += 1

        if word in RTF_IGNORABLE_DESTINATIONS and _rtf_at_group_start(source, backslash_pos):
            skip_depth = depth
            continue
        if skip_depth is not None:
            continue

        if word == "itap":
            # `\itap0` 是「不在表里」，本解析器按外层表（1）处理
            itap = max(1, _int_param(param, 1))
        elif word == "trhdr":
            # 表头是**行**属性（写在 `\trowd` 之后、`\cellx` 之前）；嵌套行标它不算外层表头
            if not nested:
                row_header = True
        elif word == "trowd":
            # 一行外层表还开着时再来的 `\trowd` 只可能是它里面的嵌套表：外层行没收到 `\row`，
            # 谁也开不了新的一行。`\itap >= 2` 是同一条判断的备用信号。
            if outer_open or itap >= 2:
                nested += 1
            else:
                start_table()
                row_defs, flags, cell_texts, row_header = [], {}, [], False
                outer_open = True
        elif word == "cellx":
            if not nested:
                row_defs.append(
                    CellSpec(
                        right=_int_param(param),
                        merge_first=flags.get("clmgf", False),
                        merge_cont=flags.get("clmrg", False),
                        vmerge_first=flags.get("clvmgf", False),
                        vmerge_cont=flags.get("clvmrg", False),
                    )
                )
                flags = {}
        elif word in ("cell", "nestcell"):
            if nested or (word == "nestcell" and itap >= 2):
                # 嵌套表的格边界：格与格之间写 ` / `（本仓的嵌套表方言，与 `docx._nested_table_text`
                # 同形），内层的文字因此既不粘成一个词，也不进外层网格
                pieces.append(" / ")
            else:
                if table_rows is None:
                    start_table()
                cell_texts.append(_cell_text(pieces))
                pieces.clear()
                saw_par = False
        elif word in ("row", "nestrow"):
            if nested:
                nested -= 1
                _close_nested_row(pieces)
            elif word == "nestrow" and itap >= 2:
                pass
            else:
                if table_rows is None:
                    start_table()
                leftover = _cell_text(pieces)  # `\row` 前少写一个 `\cell` 的写出件不少见：补成最后一格
                pieces.clear()
                saw_par = False
                if leftover:
                    cell_texts.append(leftover)
                table_rows.append(RtfRow(_row_cells(row_defs, cell_texts), row_header))
                row_defs, flags, cell_texts, row_header = [], {}, [], False
                outer_open = False
        elif word in MERGE_FLAGS:
            flags[word] = True
        elif word in ("par", "line", "sect"):
            if word == "par":
                saw_par = True  # 只有 `\par` 是段落分隔，`\line` / `\sect` 不断表
            pieces.append("\n")
        elif word == "tab":
            pieces.append("\t")

    flush_hex()
    text = "".join(pieces)
    if table_rows is not None:
        blocks.append(_table_block(table_rows))
    if text:
        blocks.append(text)
    return blocks

def rtf_to_text(source: str) -> str:
    """
    把 RTF 源码转成纯文本（表格渲成制表符分隔的行）。

    功能：处理 `\\uN`（含负值的 UTF-16 码位）与 `\\'hh` 十六进制转义，剥掉控制字与花括号，
          跳过字体表/颜色表/图片等可忽略目的地，`\\par` 还原成换行，表格按行列还原。
    应用场景：extract_plaintext 处理 .rtf 时调用（还要表头行数与上界名时用 `extract_rtf`）。
    输入：source — RTF 源码文本。
    输出：还原出的纯文本。
    """
    return _assemble(_rtf_blocks(source))

def extract_rtf(path: Path) -> ExtractResult:
    """
    抽取 RTF 的正文与表格，并报出源自己标出的表头行数。

    功能：读盘解码后走同一次控制字解析；把每张表的命中上界与表头行数汇总进 ExtractResult——
          交付文本只有一张表时给 `table_header_rows`，多张表时给 None 并在 notes 里说张数
          （与 docx / odf 同一条规则与文案）。
    应用场景：dispatch 分派 rtf 时调用（`plaintext.extract_plaintext` 的 rtf 分支接线到这里）；
              只要文本、不要表头行数时仍可用 `rtf_to_text`。
    输入：path — 文件路径。
    输出：ExtractResult，content 为正文与表格拼成的交付文本。
    """
    text, hits = read_text_file(path)
    blocks = _rtf_blocks(text)
    tables = [block for block in blocks if isinstance(block, RtfTable) and block.lines]
    limits_hit = list(hits)
    notes: list = []
    for table in tables:
        limits_hit.extend(table.limits_hit)
        notes.extend(table.notes)
    if len(tables) > 1:
        notes.append(
            f"正文里有 {len(tables)} 张表，meta.table_header_rows 只在交付文本只有一张表时给值"
        )
    return ExtractResult(
        content=_assemble(blocks),
        engines=["RTF 控制字解析"],
        notes=notes,
        limits_hit=limits_hit,
        table_header_rows=tables[0].header_rows if len(tables) == 1 else None,
    )
