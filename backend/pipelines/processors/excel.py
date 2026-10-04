# -*- coding: utf-8 -*-
"""
表格族的处理器：xlsx / xlsm / xls / ods / csv。

文件名按「一看就懂」取成 excel：这一族在本项目里统一按行列渲染成制表符表格，所以放在同一个文件里；
ODS 与 CSV 也在这里，不必为了名字精确把它再拆开。

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
from pipelines.processors.grid import GridBuilder, render_tsv
from pipelines.processors.schema import ExtractContext, ExtractResult, TableGrid
from pipelines.processors.xlsx_merges import read_merge_rects
from utils.textfile import read_text_file

def _render_rows(sheets: list) -> tuple:
    """
    把「(表名, 行列表, 合并矩形)」渲染成统一的文本表格。

    功能：逐表输出 `=== SHEET: 名 ===` 加制表符分隔的行；表格先装配成格位网格再渲染——
          合并区覆盖的空列不被裁掉、合并文本只出现一次；跳过整行全空的行；超行数上限时截断并说明。
    应用场景：xlsx / xls / ods / csv 四条路径共用，避免各写一份渲染规则。
    输入：sheets — [(表名, [[单元格文本, ...], ...], [(行起, 列起, 行止, 列止), ...]), ...]。
    输出：(拼接后的文本, 命中的上界名列表)。
    """
    blocks = []
    hits = []
    for name, rows, merges in sheets:
        lines = render_tsv(_sheet_grid(rows, merges), limits.MAX_ROWS_PER_SHEET)
        if len(rows) > limits.MAX_ROWS_PER_SHEET and "max_rows_per_sheet" not in hits:
            hits.append("max_rows_per_sheet")
        if lines:
            blocks.append(f"=== SHEET: {name} ===\n" + "\n".join(lines))
    return "\n\n".join(blocks), hits

def _cell_text(cell) -> str:
    """
    单元格值 → 文本。

    功能：空单元格返回空串——openpyxl 的空单元格是 None，直接 str() 会写出字面量
          "None"，把整列内容污染掉。
    应用场景：渲染表格时逐单元格调用。
    输入：cell — 各解析库给出的原始值（可能是 None、数字、日期或字符串）。
    输出：清理后的文本。
    """
    if cell is None:
        return ""
    return str(cell).replace("\t", " ").replace("\n", " ").strip()

def _sheet_grid(rows: list, merges: list) -> TableGrid:
    """
    一个表的行值 + 合并矩形 → 格位网格。

    功能：合并区只让左上格落位（带横纵跨度），其余位置交给覆盖位；矩形超出读到范围的部分裁掉，
          免得为没读进来的行补出格位。
    应用场景：`_render_rows` 逐表调用。
    输入：rows — 行值列表；merges — 半开矩形 (行起, 列起, 行止, 列止)。
    输出：可渲染的格位网格。
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
    return builder.finish()

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
    """用 xlrd 读老版 .xls。"""
    import xlrd

    book = xlrd.open_workbook(str(path))
    sheets = []
    for sheet in book.sheets():
        rows = [sheet.row_values(index) for index in range(min(sheet.nrows, limits.MAX_ROWS_TO_READ))]
        sheets.append((sheet.name, rows, ()))
    return sheets

def _read_ods(path: Path) -> list:
    """用 pyexcel_ods 读 ODS。"""
    from pyexcel_ods import get_data

    # 必须按 (表名, 行) 解包：写成 `for x in data.items()` 会把元组当成行序列，输出是乱的
    # （表名另有用途，不能丢）。
    # pyexcel-ods 没有流式接口，只能读完后立刻截断。
    data = get_data(str(path))
    return [(name, rows[: limits.MAX_ROWS_TO_READ], ()) for name, rows in data.items()]

def _read_csv(path: Path) -> list:
    """读 CSV：先按编码探测解码，再交给 csv 解析一次（不二次拼接）。"""
    text, hits = read_text_file(path)
    rows = list(islice(csv.reader(io.StringIO(text)), limits.MAX_ROWS_TO_READ))
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
        elif ext == "csv":
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

    content, hits = _render_rows(sheets)
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
