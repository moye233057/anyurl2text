# -*- coding: utf-8 -*-
"""
表格族的处理器：xlsx / xlsm / xls / ods / csv。

文件名按「一看就懂」取成 excel：这一族在本项目里统一按行列渲染成制表符表格，所以放在同一个文件里；
ODS 与 CSV / TSV 也在这里，不必为了名字精确把它再拆开。合并区 / 可见性按容器分家读（本文件后半：OOXML 与 ODF 两套），都归到同一份格位网格。

设计要点：
- **只有一套表格方言**：`=== SHEET: 名 ===` 加制表符分隔的行。给不同容器各造一套标记
  （`<table>` / `<row>` 那样）会让下游按容器写两套解析。
- **保留空单元格以对齐列**：把空单元格跳过再用 " | " 连接，列一错位整张表就读不出
  对应关系；这里按列位置输出制表符，空列留空。
- 不引入 pandas：整表读取用各格式的原生库就够，少一个重依赖。
"""
import csv
import io
import re
import xml.etree.ElementTree as ElementTree
import zipfile
from itertools import islice
from pathlib import Path
from typing import Dict, List, Tuple

import configs.limits as limits
from utils.excel import cell_text, table_kind
from utils.excel.grid import GridBuilder, grid_notes, merge_rect, render_tsv
from utils.excel.index_runs import column_letters, to_runs
from utils.excel.odf_table_rows import table_rows
from utils.processor_schema import ExtractContext, ExtractResult, TableGrid
from utils.textfile import read_text_file

# 老版 .xls 的 BOUNDSHEET 可见性编码（xlrd 的 `sheet.visibility`）
XLS_SHEET_STATES = {0: "visible", 1: "hidden", 2: "veryHidden"}

def _render_rows(sheets: list, pad_short_rows: bool = True) -> tuple:
    """
    把「(表名, 行列表, 合并矩形)」渲染成统一的文本表格。

    功能：逐表输出 `=== SHEET: 名 ===` 加制表符分隔的行；表格先装配成格位网格再渲染——
          合并区覆盖的空列不被裁掉、合并文本只出现一次；跳过整行全空的行；超行数上限时截断并说明。
    应用场景：xlsx / xls / ods / csv 四条路径共用，避免各写一份渲染规则。
    输入：sheets — [(表名, [[单元格文本, ...], ...], [(行起, 列起, 行止, 列止), ...]), ...]；
          pad_short_rows — 见 `grid.render_tsv`：结构化表格补行尾缺格，CSV / TSV 传 False。
    输出：(拼接后的文本, 命中的上界名列表, 每张非空表的渲染行, 网格登记的 notes)——
          行另交回是为了判表类型，notes 交回是为了孤儿覆盖位 / 续格文字这两条登记。
    """
    blocks = []
    hits = []
    tables = []
    notes: list = []
    for name, rows, merges in sheets:
        grid, grid_hits, sheet_notes = _sheet_grid(rows, merges)
        lines = render_tsv(grid, limits.MAX_ROWS_PER_SHEET, pad_short_rows=pad_short_rows)
        hits.extend(grid_hits)
        notes.extend(sheet_notes)
        if len(rows) > limits.MAX_ROWS_PER_SHEET and "max_rows_per_sheet" not in hits:
            hits.append("max_rows_per_sheet")
        if lines:
            blocks.append(f"=== SHEET: {name} ===\n" + "\n".join(lines))
            tables.append(lines)
    return "\n\n".join(blocks), hits, tables, notes

def _cell_text(cell) -> str:
    """
    单元格值 → 文本。

    功能：空单元格返回空串——openpyxl 的空单元格是 None，直接 str() 会写出字面量
          "None"，把整列内容污染掉；浮点按 15 位有效数字格式化（`str()` 会把二进制误差的
          长尾整条写出来，0.1+0.2 就成了 0.30000000000000004，那是实现细节不是表里的内容），
          整数与字符串一律原样不动。
    格内换行/制表符：换行写成 `<br>`（一个格里也能多段）、制表符换成空格——格位方言见
          `processors/cell_text.py`（真制表符会凭空多一列、真换行会把这一行拆成两行）。
    应用场景：渲染表格时逐单元格调用。
    输入：cell — 各解析库给出的原始值（可能是 None、整数、浮点、日期或字符串）。
    输出：清理后的文本。
    """
    if cell is None:
        return ""
    text = f"{cell:.15g}" if isinstance(cell, float) else str(cell)
    return cell_text.from_text(text)

def _sheet_grid(rows: list, merges: list) -> tuple:
    """
    一个表的行值 + 合并矩形 → 格位网格。

    功能：合并区只让左上格落位（带横纵跨度），其余位置交给覆盖位；矩形超出读到范围的部分裁掉，
          免得为没读进来的行补出格位。
    应用场景：`_render_rows` 逐表调用。
    输入：rows — 行值列表；merges — 半开矩形 (行起, 列起, 行止, 列止)。
    输出：(可渲染的格位网格, 命中的上界名列表)——网格预算命中时要能上报，不能只交网格。
    """
    usable = [
        (row_start, col_start, min(row_end, len(rows)), col_end)
        for row_start, col_start, row_end, col_end in merges
        if row_start < row_end and col_start < col_end and row_start < len(rows)
    ]
    covered = set()
    spans = {}
    for row_start, col_start, row_end, col_end in usable:
        spans[(row_start, col_start)] = (col_end - col_start, row_end - row_start)
        for row in range(row_start, row_end):
            for col in range(col_start, col_end):
                if (row, col) != (row_start, col_start):
                    covered.add((row, col))
    builder = GridBuilder()
    for row_index, row in enumerate(rows):
        builder.next_row()
        for col_index in range(len(row)):
            if (row_index, col_index) in covered:
                builder.covered()
                continue
            col_span, row_span = spans.get((row_index, col_index), (1, 1))
            builder.place(_cell_text(row[col_index]), col_span, row_span)
    return builder.finish(), list(builder.limit_hits), grid_notes(builder)

def _read_xlsx(path: Path) -> tuple:
    """
    用 openpyxl 只读模式读 xlsx/xlsm；失败则抛给 `extract_excel` 降级，不回退整本加载。

    功能：整表逐行读出，另交一份隐藏状态（工作表 / 行列）供 `notes` 登记。
    应用场景：`extract_excel` 读 xlsx / xlsm 时调用。
    输入：path — 文件路径。
    输出：(sheets, visibility)——sheets 为 [(表名, 行列表, 合并矩形), ...]，
          visibility 形态见 `read_xlsx_visibility`（本路径经 `_xlsx_package_meta` 一次解包拿到）。
    说明：**隐藏工作表照读**（隐藏只是界面上没显示，不是没内容；用户 2026-10-04 拍板）。
          实测只读模式同样会吐出隐藏行、隐藏列的值，所以这条读取路径不必改。
          只读失败若回退 `data_only=True` 整本加载，行数上限形同虚设，可被畸形 xlsx 打爆内存。
    """
    import openpyxl

    workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        # 合并区与隐藏状态共用一次 Zip 打开（不再各开一次）
        merges, visibility = _xlsx_package_meta(path)
        sheets = []
        for index, sheet in enumerate(workbook.worksheets):
            # 边读边停：整表先读进内存的话，几十万行的表会把内存打爆，
            # 渲染层的行数上限那时已经来不及了
            rows = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) >= limits.MAX_ROWS_TO_READ:
                    break
            sheets.append((sheet.title, rows, merges[index] if index < len(merges) else []))
        return sheets, visibility
    finally:
        workbook.close()

def _read_xls(path: Path) -> tuple:
    """
    用 xlrd 读老版 .xls，合并区取它的 `merged_cells`，隐藏状态取它的 visibility / rowinfo / colinfo。

    两个坑都实测过：① 坐标是**开区间且行在前**（`(rlo, rhi, clo, chi)`），要过 `grid.merge_rect`
    换算成本项目统一的半开形态；② `merged_cells` **只在 `formatting_info=True` 时才有值**——
    默认打开时它恒为空列表（这一点谁也不会从签名上看出来）。万一某份文件的格式化记录带不动，
    退回默认模式：值照读，只是合并区与隐藏行列都丢失（内容不少，只是不登记）。

    输出：(sheets, visibility)——隐藏行列在这里也换算成 0 基半开区间，与 xlsx 侧同一形态。
    """
    import xlrd

    try:
        book = xlrd.open_workbook(str(path), formatting_info=True)
    except Exception:
        book = xlrd.open_workbook(str(path))
    sheets = []
    visibility = {}
    for sheet in book.sheets():
        rows = [sheet.row_values(index) for index in range(min(sheet.nrows, limits.MAX_ROWS_TO_READ))]
        # xlrd 给的是 (rlo, rhi, clo, chi)：**行在前、开区间**，且列不是紧挨着行的那个位置——
        # 直接 `merge_rect(*item)` 会把「行止」当成「列起」（实测：A2:A3 被换算成 A1:B2、A1:B1
        # 因退化被整个丢掉）。所以按名字取，不按位置展开。
        rects = [
            rect
            for rect in (
                merge_rect(item[0], item[2], item[1], item[3]) for item in sheet.merged_cells
            )
            if rect
        ]
        sheets.append((sheet.name, rows, rects))
        visibility[sheet.name] = {
            "state": XLS_SHEET_STATES.get(sheet.visibility, "visible"),
            "hidden_rows": to_runs([key for key, info in sheet.rowinfo_map.items() if info.hidden]),
            "hidden_cols": to_runs([key for key, info in sheet.colinfo_map.items() if info.hidden]),
        }
    return sheets, visibility

def _read_ods(path: Path) -> tuple:
    """
    用 pyexcel_ods 读 ODS 的值；合并区另从包内 XML 读，值行再按覆盖位对齐。

    两步都不能省：该库丢跨度（合并区要自己解包读），而且它对 `covered-table-cell` 不产出值
    （含纵向合并的行会少项、后面的值整体左移，要按覆盖位插空对齐）。

    隐藏表照读（实测 `table:display="false"` 的表也在返回里），可见性从包内样式读（`read_ods_visibility`）——
    该库只给值矩阵，样式一概丢掉。输出 (sheets, visibility)。
    """
    from pyexcel_ods import get_data

    # 必须按 (表名, 行) 解包：写成 `for x in data.items()` 会把元组当成行序列，输出是乱的
    # （表名另有用途，不能丢）。pyexcel-ods 没有流式接口，只能读完后立刻截断。
    data = get_data(str(path))
    merges = read_ods_merge_rects(path)
    sheets = []
    for name, rows in data.items():
        rects = merges.get(name, [])
        sheets.append((name, align_ods_values(rows, rects)[: limits.MAX_ROWS_TO_READ], rects))
    return sheets, read_ods_visibility(path)

# CSV 分隔符嗅探：候选顺序即平局时的优先级，逗号在最前——普通逗号 CSV 的产出因此逐字节不变
CSV_DELIMITERS = (",", ";", "\t", "|")
# 只看前若干**非空**行：够判一个文件的方言，又不会被超长文件拖慢
CSV_SNIFF_LINES = 20

def _sniff_delimiter(text: str) -> str:
    """
    在前若干非空行上挑一个 CSV 分隔符。

    功能：逐个候选分隔符解析样本行，只考虑「确实把行列开了」（某行多于 1 列）的候选，
          在其中选**列数最一致**的那个（一致 = 与出现次数最多的列数相同的行最多）；
          平局按候选顺序决定（逗号优先），一个都不满足就退回逗号。
    应用场景：`_read_csv` 读文件前调用一次——分号 / 制表符 / 竖线分隔的 CSV 用默认逗号会读成一列。
    输入：text — 已解码的 CSV 文本。
    输出：选中的分隔符，恒为 CSV_DELIMITERS 之一，所以规则是确定性的、不依赖 locale。
    """
    sample = [line for line in text.splitlines() if line.strip()][:CSV_SNIFF_LINES]
    best, best_score = ",", 0
    for delimiter in CSV_DELIMITERS:
        counts = [len(row) for row in csv.reader(sample, delimiter=delimiter) if len(row) > 1]
        if not counts:
            continue
        score = max(counts.count(count) for count in set(counts))
        if score > best_score:
            best, best_score = delimiter, score
    return best

def _read_csv(path: Path) -> tuple:
    """读 CSV / TSV：先按编码探测解码，再嗅探分隔符（.tsv 会在这一步选中制表符），最后交给 csv 解析一次。"""
    text, hits = read_text_file(path)
    rows = list(islice(csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text)), limits.MAX_ROWS_TO_READ))
    return [(path.stem or "csv", rows, tuple(hits))], {}

def _run_text(run: tuple, kind: str) -> str:
    """一个半开区间 → 人读的区间串：行是 `第 2 行` / `第 2-4 行`，列是 `B 列` / `B-D 列`（都按 1 基显示）。"""
    start, end = run
    if kind == "row":
        return f"第 {start + 1} 行" if end - start == 1 else f"第 {start + 1}-{end} 行"
    first, last = column_letters(start), column_letters(end - 1)
    return f"{first} 列" if first == last else f"{first}-{last} 列"

def _runs_text(runs: list, kind: str) -> tuple:
    """
    区间列表 → 登记串，段数超过上界时截断。

    功能：按 `limits.MAX_VISIBILITY_RANGES` 取前若干段拼成 `第 2-4、6 行`；被截断时补一句
          「等 N 段」并报出上界名——登记本身也不能无上限地长（内容另说，内容一律照抽）。
    应用场景：`_visibility_notes` 逐表调用。
    输入：runs — 半开区间列表；kind — "row" 或 "col"。
    输出：(登记串, 是否命中上界)。
    """
    limit = limits.MAX_VISIBILITY_RANGES
    text = "、".join(_run_text(run, kind) for run in runs[:limit])
    if len(runs) <= limit:
        return text, False
    return f"{text} 等 {len(runs)} 段", True

def _visibility_notes(visibility: dict) -> tuple:
    """
    隐藏状态 → `notes` 登记 + 命中的上界名。

    功能：整表隐藏的工作表登记一条（写明 hidden / veryHidden），隐藏行、隐藏列各登记一条
          （按连续段合并，如 `第 2-4、6 行`、`B-D 列`）。**只登记，不扣内容**——
          隐藏只是界面上没显示，不是没有内容（用户 2026-10-04 拍板）。
    应用场景：`extract_excel` 组装 notes 时调用。
    输入：visibility — 见 `read_xlsx_visibility`；xls / ods / csv 的形态一致或为空。
    输出：(notes 列表, 命中的上界名列表)。
    """
    notes = []
    hits = []
    for name, info in visibility.items():
        state = info.get("state", "visible")
        if state != "visible":
            notes.append(f"隐藏内容照抽：工作表「{name}」整表为 {state} 状态")
        parts = []
        for kind, key in (("row", "hidden_rows"), ("col", "hidden_cols")):
            runs = info.get(key) or []
            if not runs:
                continue
            text, truncated = _runs_text(runs, kind)
            parts.append(f"隐藏{'行' if kind == 'row' else '列'}（{text}）")
            if truncated and "max_visibility_ranges" not in hits:
                hits.append("max_visibility_ranges")
        if parts:
            notes.append(f"隐藏内容照抽：工作表「{name}」有" + "、".join(parts))
    return notes, hits

def extract_excel(path: Path, ext: str, ctx: ExtractContext) -> ExtractResult:
    """
    抽取表格类文件的全部单元格（含隐藏工作表与隐藏行列）。

    功能：按扩展名选解析器，统一渲染成制表符分隔的文本表格；隐藏内容照抽，可见性状态进 `notes`。
    应用场景：dispatch 分派表格组时调用。
    输入：path — 文件路径；ext — 已识别的小写扩展名；ctx — 抽取上下文（本类不使用）。
    输出：ExtractResult，content 为按表分块的文本。
    异常：不抛——解析失败时返回降级结果并说明原因。
    """
    try:
        if ext in ("xlsx", "xlsm"):
            sheets, visibility = _read_xlsx(path)
            engine = "openpyxl"
        elif ext == "xls":
            sheets, visibility = _read_xls(path)
            engine = "xlrd"
        elif ext == "ods":
            sheets, visibility = _read_ods(path)
            engine = "pyexcel-ods"
        elif ext in ("csv", "tsv"):
            sheets, visibility = _read_csv(path)
            engine = "csv"
        else:
            return ExtractResult(
                degraded=True,
                degrade_reason=f"未支持的表格格式: .{ext}",
                degrade_code="excel_unsupported",
                engines=["基础文件信息"],
            )
    except Exception as exc:
        return ExtractResult(
            degraded=True,
            degrade_reason=f"表格解析失败: {exc}",
            degrade_code="excel_parse_failed",
            engines=["基础文件信息"],
        )

    content, hits, tables, table_notes = _render_rows(sheets, pad_short_rows=ext not in ("csv", "tsv"))
    notes, visibility_hits = _visibility_notes(visibility)
    hits.extend(visibility_hits)
    notes = list(table_notes) + notes
    if not content:
        notes.append("表内没有单元格内容（空表，或所有格子都是空的）")
    return ExtractResult(
        content=content,
        engines=[engine],
        notes=notes,
        pages=len(sheets),
        pages_used=len(sheets),
        limits_hit=hits,
        table_kind=table_kind.classify(tables[0]) if len(tables) == 1 else None,
    )


# ===== OOXML：合并区（原 xlsx_merges）=====
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

# ===== OOXML：合并区 + 可见性（一次解包）=====
# 工作表可见性的取值（OOXML 的 `<sheet state="...">`）
VISIBLE = "visible"
HIDDEN = "hidden"
VERY_HIDDEN = "veryHidden"
OOXML_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
OOXML_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

def _xlsx_sheet_part(targets: dict, sheet, rel: str) -> str:
    """从 workbook rels 解析工作表部件路径；相对路径补上 `xl/` 前缀。"""
    part = targets.get(sheet.get(f"{rel}id"), "").lstrip("/")
    if part and not part.startswith("xl/"):
        part = f"xl/{part}"
    return part

def _merge_rects_from_root(root: ElementTree.Element, main: str) -> list:
    """一个工作表 XML 根上的合并矩形；半开区间。"""
    rects = []
    for merge in root.iter(f"{main}mergeCell"):
        start, _, end = (merge.get("ref") or "").partition(":")
        if end:
            row_start, col_start = cell_position(start)
            row_end, col_end = cell_position(end)
            rects.append((row_start, col_start, row_end + 1, col_end + 1))
    return rects

def _hidden_spans_from_root(root: ElementTree.Element, main: str) -> dict:
    """一个工作表 XML 根上的隐藏行与隐藏列（0 基半开区间）。"""
    hidden_rows = []
    for position, row in enumerate(root.iter(f"{main}row"), start=1):
        if _xlsx_is_hidden(row):
            # `r` 是 1 基行号，缺省时按元素出现次序数（span 之类不写 r 的行）
            hidden_rows.append(int(row.get("r") or position) - 1)
    hidden_cols = []
    for col in root.iter(f"{main}col"):
        if not _xlsx_is_hidden(col):
            continue
        first = int(col.get("min") or 1)
        last = max(first, int(col.get("max") or first))
        hidden_cols.extend(range(first - 1, last))
    return {"hidden_rows": to_runs(hidden_rows), "hidden_cols": to_runs(hidden_cols)}

def _xlsx_is_hidden(node) -> bool:
    """OOXML 的布尔属性：`1` / `true` 都算隐藏（Excel 两种都写）。"""
    return (node.get("hidden") or "").strip().lower() in ("1", "true")

def _load_sheet_root(package: zipfile.ZipFile, part: str):
    """读并解析一张工作表 XML；部件缺失或解析不动时返回 None。"""
    if not part:
        return None
    try:
        return ElementTree.fromstring(package.read(part))
    except Exception:
        return None

def _xlsx_package_meta(path: Path) -> Tuple[List[list], Dict[str, dict]]:
    """
    一次解包同时取出合并区与隐藏状态。

    功能：打开 Zip 一次，读 `workbook.xml` / rels，再按表各读一次 sheet XML——
          合并矩形与隐藏行列都从同一份根上算，避免原先 `read_merge_rects` 与
          `read_xlsx_visibility` 各开一次包、每张表 XML 读两遍。
    应用场景：`_read_xlsx` 主路径；对外的 `read_merge_rects` / `read_xlsx_visibility` 也走这里。
    输入：path — xlsx / xlsm 路径。
    输出：(merges, visibility)。merges 按 `<sheet>` 顺序；visibility 按表名。
          包级读不动 → `([], {})`；单表部件坏了 → 该表合并为空、隐藏为空（不拖垮整簿）。
    """
    try:
        with zipfile.ZipFile(path) as package:
            workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
            rels = ElementTree.fromstring(package.read("xl/_rels/workbook.xml.rels"))
            targets = {node.get("Id"): (node.get("Target") or "") for node in rels}
            merges = []
            visibility = {}
            for sheet in workbook.iter(f"{OOXML_MAIN}sheet"):
                root = _load_sheet_root(package, _xlsx_sheet_part(targets, sheet, OOXML_REL))
                merges.append(_merge_rects_from_root(root, OOXML_MAIN) if root is not None else [])
                visibility[sheet.get("name") or ""] = {
                    "state": sheet.get("state") or VISIBLE,
                    **(
                        _hidden_spans_from_root(root, OOXML_MAIN)
                        if root is not None
                        else {"hidden_rows": [], "hidden_cols": []}
                    ),
                }
            return merges, visibility
    except Exception:
        return [], {}

def read_merge_rects(path: Path) -> List[list]:
    """
    读每个工作表的合并区，返回按工作表顺序排列的半开矩形。

    功能：解包读 `xl/workbook.xml` 与它的 rels 定位各表 XML，再把 `<mergeCell ref="A1:C1"/>`
          换算成 (行起, 列起, 行止, 列止)——半开区间是本项目里合并矩形的唯一输入形态。
    应用场景：需要单独拿合并区时调用；`_read_xlsx` 走 `_xlsx_package_meta` 一次拿齐。
    输入：path — xlsx / xlsm 路径。
    输出：[[矩形, ...], ...]，与工作簿里 `<sheet>` 的顺序一一对应；包读不动时返回空列表，
          调用方按「没有合并区」处理。
    """
    merges, _ = _xlsx_package_meta(path)
    return merges

def read_xlsx_visibility(path: Path) -> Dict[str, dict]:
    """
    读每个工作表的隐藏状态与隐藏行列。

    功能：解包读 `xl/workbook.xml` 拿 `<sheet state="hidden|veryHidden">`，按 rels 定位各表 XML，
          再读 `<row hidden="1" r="N">` 与 `<col hidden="1" min max>`；行列一律换算成 0 基半开区间。
    应用场景：需要单独拿可见性时调用；`_read_xlsx` 走 `_xlsx_package_meta` 一次拿齐。
    输入：path — xlsx / xlsm 路径。
    输出：{工作表名: {"state": ..., "hidden_rows": [(起, 止), ...], "hidden_cols": [...]}}；
          包里读不动时返回空字典，调用方按「全部可见」处理（内容读取不受影响）。
    """
    _, visibility = _xlsx_package_meta(path)
    return visibility

# ===== ODF/ODS：合并区（原 ods_merges）=====
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
    应用场景：`read_ods_merge_rects` 逐表调用。
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

def align_ods_values(rows: list, rects: List[Tuple[int, int, int, int]]) -> list:
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

def read_ods_merge_rects(path: Path) -> Dict[str, List[Tuple[int, int, int, int]]]:
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

# ===== ODF/ODS：可见性（原 ods_visibility）=====
OFFICE_NS = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
STYLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
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

def _ods_is_hidden(element, styles: Dict[str, bool]) -> bool:
    """元素自身写了隐藏，或它引用的样式是隐藏样式。"""
    return _marked_hidden(element) or bool(styles.get(element.get(TABLE_NS + "style-name") or ""))


def _sheets(root) -> list:
    """工作簿里的工作表：`office:spreadsheet` 的**直属** `table:table`（嵌套表不是工作表，且常常没有名字）。"""
    spreadsheet = root.find(OFFICE_NS + "body/" + OFFICE_NS + "spreadsheet")
    if spreadsheet is not None:
        return [child for child in spreadsheet if child.tag == TABLE_NS + "table"]
    return list(root.iter(TABLE_NS + "table"))

def read_ods_visibility(path: Path) -> Dict[str, dict]:
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
            if _ods_is_hidden(row, styles):
                hidden_rows.extend(range(row_index, row_index + repeat))
            row_index += repeat
        hidden_cols: List[int] = []
        column_index = 0
        for column in table.findall(TABLE_NS + "table-column"):
            repeat = _repeat(column, "number-columns-repeated")
            if _ods_is_hidden(column, styles):
                hidden_cols.extend(range(column_index, column_index + repeat))
            column_index += repeat
        visibility[table.get(TABLE_NS + "name") or ""] = {
            "state": HIDDEN if _ods_is_hidden(table, styles) else VISIBLE,
            "hidden_rows": to_runs(hidden_rows),
            "hidden_cols": to_runs(hidden_cols),
        }
    return visibility
