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

from pipelines.processors.schema import ExtractContext, ExtractResult
from utils.textfile import read_text_file

# 单表最多渲染的行数上限
MAX_ROWS_PER_SHEET = 5000
# 读取上限比渲染上限多一行：多出来的那一行是给渲染层看的信号——
# 「还有后续行没读」和「这就是最后一行」必须能区分开。
MAX_ROWS_TO_READ = MAX_ROWS_PER_SHEET + 1

def _render_rows(sheets: list) -> str:
    """
    把「(表名, 行列表)」渲染成统一的文本表格。

    功能：逐表输出 `=== SHEET: 名 ===` 加制表符分隔的行；裁掉整表都为空的尾列；
          跳过整行全空的行；超过行数上限时截断并说明。
    应用场景：xlsx / xls / ods / csv 四条路径共用，避免各写一份渲染规则。
    输入：sheets — [(表名, [[单元格文本, ...], ...]), ...]。
    输出：拼接后的文本。
    """
    blocks = []
    for name, rows in sheets:
        width = 0
        for row in rows:
            for index in range(len(row) - 1, -1, -1):
                if _cell_text(row[index]):
                    width = max(width, index + 1)
                    break
        lines = []
        for row in rows:
            cells = [_cell_text(cell) for cell in row[:width]]
            if not any(cells):
                continue
            if len(lines) >= MAX_ROWS_PER_SHEET:
                lines.append(f"... 其余行未列出（本表超过 {MAX_ROWS_PER_SHEET} 行）")
                break
            lines.append("\t".join(cells))
        if lines:
            blocks.append(f"=== SHEET: {name} ===\n" + "\n".join(lines))
    return "\n\n".join(blocks)

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

def _read_xlsx(path: Path) -> list:
    """用 openpyxl 读 xlsx/xlsm；read_only 失败时退回常规模式（部分文件的样式表会让只读模式报错）。"""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception:
        workbook = openpyxl.load_workbook(str(path), data_only=True)
    try:
        sheets = []
        for sheet in workbook.worksheets:
            if getattr(sheet, "sheet_state", "visible") != "visible":
                continue
            # 边读边停：整表先读进内存的话，几十万行的表会把内存打爆，
            # 渲染层的行数上限那时已经来不及了
            rows = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) >= MAX_ROWS_TO_READ:
                    break
            sheets.append((sheet.title, rows))
        return sheets
    finally:
        workbook.close()

def _read_xls(path: Path) -> list:
    """用 xlrd 读老版 .xls。"""
    import xlrd

    book = xlrd.open_workbook(str(path))
    sheets = []
    for sheet in book.sheets():
        rows = [sheet.row_values(index) for index in range(min(sheet.nrows, MAX_ROWS_TO_READ))]
        sheets.append((sheet.name, rows))
    return sheets

def _read_ods(path: Path) -> list:
    """用 pyexcel_ods 读 ODS。"""
    from pyexcel_ods import get_data

    # 必须按 (表名, 行) 解包：写成 `for x in data.items()` 会把元组当成行序列，输出是乱的
    # （表名另有用途，不能丢）。
    # pyexcel-ods 没有流式接口，只能读完后立刻截断。
    data = get_data(str(path))
    return [(name, rows[:MAX_ROWS_TO_READ]) for name, rows in data.items()]

def _read_csv(path: Path) -> list:
    """读 CSV：先按编码探测解码，再交给 csv 解析一次（不二次拼接）。"""
    text = read_text_file(path)
    rows = list(islice(csv.reader(io.StringIO(text)), MAX_ROWS_TO_READ))
    return [(path.stem or "csv", rows)]

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
                engines=["基础文件信息"],
            )
    except Exception as exc:
        return ExtractResult(
            degraded=True,
            degrade_reason=f"表格解析失败: {exc}",
            engines=["基础文件信息"],
        )

    content = _render_rows(sheets)
    notes = []
    if not content:
        notes.append("表内没有可见单元格内容（可能是空表或整表被隐藏）")
    return ExtractResult(
        content=content,
        engines=[engine],
        notes=notes,
        pages=len(sheets),
        pages_used=len(sheets),
    )
