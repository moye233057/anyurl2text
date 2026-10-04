# -*- coding: utf-8 -*-
"""
表格族的处理器：xlsx / xlsm / xls / ods / csv。

文件名按「一看就懂」取成 excel：这一族在本项目里统一按行列渲染成制表符表格，所以放在同一个文件里；
ODS 与 CSV / TSV 也在这里，不必为了名字精确把它再拆开。合并区的读取按容器分家：OOXML 在
`xlsx_merges.py`、ODF 在 `ods_merges.py`，两者都归到同一份格位网格。

设计要点：
- **只有一套表格方言**：`=== SHEET: 名 ===` 加制表符分隔的行。给不同容器各造一套标记
  （`<table>` / `<row>` 那样）会让下游按容器写两套解析。
- **保留空单元格以对齐列**：把空单元格跳过再用 " | " 连接，列一错位整张表就读不出
  对应关系；这里按列位置输出制表符，空列留空。
- 不引入 pandas：整表读取用各格式的原生库就够，少一个重依赖。
"""
import csv
import io
from itertools import islice
from pathlib import Path

import configs.limits as limits
from pipelines.processors.grid import GridBuilder, merge_rect, render_tsv
from pipelines.processors.ods_merges import align_values as align_ods_values
from pipelines.processors.ods_merges import read_merge_rects as read_ods_merge_rects
from pipelines.processors.schema import ExtractContext, ExtractResult, TableGrid
from pipelines.processors.xlsx_merges import read_merge_rects
from utils.textfile import read_text_file

def _render_rows(sheets: list, pad_short_rows: bool = True) -> tuple:
    """
    把「(表名, 行列表, 合并矩形)」渲染成统一的文本表格。

    功能：逐表输出 `=== SHEET: 名 ===` 加制表符分隔的行；表格先装配成格位网格再渲染——
          合并区覆盖的空列不被裁掉、合并文本只出现一次；跳过整行全空的行；超行数上限时截断并说明。
    应用场景：xlsx / xls / ods / csv 四条路径共用，避免各写一份渲染规则。
    输入：sheets — [(表名, [[单元格文本, ...], ...], [(行起, 列起, 行止, 列止), ...]), ...]；
          pad_short_rows — 见 `grid.render_tsv`：结构化表格补行尾缺格，CSV / TSV 传 False。
    输出：(拼接后的文本, 命中的上界名列表)。
    """
    blocks = []
    hits = []
    for name, rows, merges in sheets:
        grid, grid_hits = _sheet_grid(rows, merges)
        lines = render_tsv(grid, limits.MAX_ROWS_PER_SHEET, pad_short_rows=pad_short_rows)
        hits.extend(grid_hits)
        if len(rows) > limits.MAX_ROWS_PER_SHEET and "max_rows_per_sheet" not in hits:
            hits.append("max_rows_per_sheet")
        if lines:
            blocks.append(f"=== SHEET: {name} ===\n" + "\n".join(lines))
    return "\n\n".join(blocks), hits

def _cell_text(cell) -> str:
    """
    单元格值 → 文本。

    功能：空单元格返回空串——openpyxl 的空单元格是 None，直接 str() 会写出字面量
          "None"，把整列内容污染掉；浮点按 15 位有效数字格式化（`str()` 会把二进制误差的
          长尾整条写出来，0.1+0.2 就成了 0.30000000000000004，那是实现细节不是表里的内容），
          整数与字符串一律原样不动。
    应用场景：渲染表格时逐单元格调用。
    输入：cell — 各解析库给出的原始值（可能是 None、整数、浮点、日期或字符串）。
    输出：清理后的文本。
    """
    if cell is None:
        return ""
    text = f"{cell:.15g}" if isinstance(cell, float) else str(cell)
    return text.replace("\t", " ").replace("\n", " ").strip()

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
    return builder.finish(), list(builder.limit_hits)

def _read_xlsx(path: Path) -> list:
    """用 openpyxl 读 xlsx/xlsm；read_only 失败时退回常规模式（部分文件的样式表会让只读模式报错）。"""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception:
        workbook = openpyxl.load_workbook(str(path), data_only=True)
    try:
        merges = read_merge_rects(path)
        sheets = []
        for index, sheet in enumerate(workbook.worksheets):
            if getattr(sheet, "sheet_state", "visible") != "visible":
                continue
            # 边读边停：整表先读进内存的话，几十万行的表会把内存打爆，
            # 渲染层的行数上限那时已经来不及了
            rows = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) >= limits.MAX_ROWS_TO_READ:
                    break
            sheets.append((sheet.title, rows, merges[index] if index < len(merges) else []))
        return sheets
    finally:
        workbook.close()

def _read_xls(path: Path) -> list:
    """
    用 xlrd 读老版 .xls，合并区取它的 `merged_cells`。

    两个坑都实测过：① 坐标是**开区间且行在前**（`(rlo, rhi, clo, chi)`），要过 `grid.merge_rect`
    换算成本项目统一的半开形态；② `merged_cells` **只在 `formatting_info=True` 时才有值**——
    默认打开时它恒为空列表（这一点谁也不会从签名上看出来）。万一某份文件的格式化记录带不动，
    退回默认模式：值照读，只是合并区丢失。
    """
    import xlrd

    try:
        book = xlrd.open_workbook(str(path), formatting_info=True)
    except Exception:
        book = xlrd.open_workbook(str(path))
    sheets = []
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
    return sheets

def _read_ods(path: Path) -> list:
    """
    用 pyexcel_ods 读 ODS 的值；合并区另从包内 XML 读，值行再按覆盖位对齐。

    两步都不能省：该库丢跨度（合并区要自己解包读），而且它对 `covered-table-cell` 不产出值
    （含纵向合并的行会少项、后面的值整体左移，要按覆盖位插空对齐）。
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
    return sheets

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

def _read_csv(path: Path) -> list:
    """读 CSV / TSV：先按编码探测解码，再嗅探分隔符（.tsv 会在这一步选中制表符），最后交给 csv 解析一次。"""
    text, hits = read_text_file(path)
    rows = list(islice(csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text)), limits.MAX_ROWS_TO_READ))
    return [(path.stem or "csv", rows, tuple(hits))]

def extract_excel(path: Path, ext: str, ctx: ExtractContext) -> ExtractResult:
    """
    抽取表格类文件的全部可见单元格。

    功能：按扩展名选解析器，统一渲染成制表符分隔的文本表格。
    应用场景：dispatch 分派表格组时调用。
    输入：path — 文件路径；ext — 已识别的小写扩展名；ctx — 抽取上下文（本类不使用）。
    输出：ExtractResult，content 为按表分块的文本。
    异常：不抛——解析失败时返回降级结果并说明原因。
    """
    try:
        if ext in ("xlsx", "xlsm"):
            sheets, engine = _read_xlsx(path), "openpyxl"
        elif ext == "xls":
            sheets, engine = _read_xls(path), "xlrd"
        elif ext == "ods":
            sheets, engine = _read_ods(path), "pyexcel-ods"
        elif ext in ("csv", "tsv"):
            sheets, engine = _read_csv(path), "csv"
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

    content, hits = _render_rows(sheets, pad_short_rows=ext not in ("csv", "tsv"))
    notes = []
    if not content:
        notes.append("表内没有可见单元格内容（可能是空表或整表被隐藏）")
    return ExtractResult(
        content=content,
        engines=[engine],
        notes=notes,
        pages=len(sheets),
        pages_used=len(sheets),
        limits_hit=hits,
    )
