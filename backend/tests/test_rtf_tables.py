# -*- coding: utf-8 -*-
r"""
缝 A 的 RTF 表格分支：`\trowd` / `\cellx` / `\cell` / `\row` 还原成制表符分隔的行。

为什么单独一个文件：表格列位走的是 `processors/rtf.py` 里那套「全表右边界聚类、一格占几列
由边界反推」，与 `test_processors_by_format.py` 的按格式矩阵不是一件事；这里的样例是手写 RTF
源文本，预期值直接从源文本读得出来（哪一格写了什么、哪几格被合并吞掉），不依赖对实现的理解。

接线现状：`plaintext.extract_plaintext` 的 rtf 分支**在读盘之前**就交给 `rtf.extract_rtf(path)`，
所以 `dispatch("rtf", …)` 与 `extract_rtf` 给的是同一份结果（内容、列位、`table_header_rows`、
`limits_hit`）。本文件里两边都断言过：内容与列位大多走 `dispatch`（真实交付路径），
表头行数另有直接对 `extract_rtf` 的断言（同一函数，两处入口都钉住）。
"""
import pytest

import configs.limits as limits
from pipelines.processors.rtf import extract_rtf
from utils.processor_schema import ExtractContext
from pipelines.steps.step4_extract import dispatch

pytestmark = pytest.mark.capability_table

# ---------- 手写 RTF 源文本（每个样例的预期值都从这些文字里读得出来） ----------

# 两列三行的普通表，表格前后各有一段正文；每行两个 \cellx
TWO_COL_THREE_ROW = (
    r"{\rtf1\ansi\ansicpg936\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0\fs24 表格前正文\par"
    r"\trowd\cellx2000\cellx4000\pard\intbl 甲一\cell 乙一\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 甲二\cell 乙二\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 甲三\cell 乙三\cell\row"
    r"\pard 表格后正文\par}"
)

# 第一行三格；第二行前两格横向合并（\clmgf 首格 + \clmrg 继续格），合并区的文字只写在前一格
H_MERGE = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 合并表\par"
    r"\trowd\cellx2000\cellx4000\cellx6000\pard\intbl 左一\cell 中一\cell 右一\cell\row"
    r"\trowd\clmgf\cellx2000\clmrg\cellx4000\cellx6000\pard\intbl 合并文字\cell\cell 右二\cell\row"
    r"\pard 合并表结束\par}"
)

# 第一列纵向合并：第一行 \clvmgf（首格）、第二行 \clvmrg（继续格，且继续格里不写文字）
V_MERGE = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 纵向合并\par"
    r"\trowd\clvmgf\cellx2000\cellx4000\pard\intbl 跨行\cell 第一\cell\row"
    r"\trowd\clvmrg\cellx2000\cellx4000\pard\intbl\cell 第二\cell\row"
    r"\pard 结束\par}"
)

# 第一行三个 \cellx；第二行只写两个，第三格的边界缺失
MISSING_CELLX = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 某行少一个边界\par"
    r"\trowd\cellx2000\cellx4000\cellx6000\pard\intbl A一\cell B一\cell C一\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl A二\cell B二\cell C二\cell\row"
    r"\pard 结束\par}"
)

# 整张表一个 \cellx 都没写，只有 \trowd 与 \cell
NO_CELLX = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 整表都没写边界\par"
    r"\trowd\pard\intbl A甲\cell B甲\cell\row"
    r"\trowd\pard\intbl A乙\cell B乙\cell\row"
    r"\pard 结束\par}"
)

# 第一行带 \trhdr（源自己标出的表头行），第二行没标
HEADER = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 带表头\par"
    r"\trowd\trhdr\cellx2000\cellx4000\pard\intbl 名称\cell 数量\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 甲\cell 1\cell\row"
    r"\pard 结束\par}"
)

# 外层两列；第一格里嵌一张 \itap2 的两行小表（它自己写了 \cellx1000 / \cellx2000）
NESTED = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 嵌套表\par"
    r"\trowd\cellx2000\cellx4000\pard\intbl 外左\par"
    r"\itap2\trowd\cellx1000\cellx2000\pard\intbl 内甲\cell 内乙\cell\nestrow"
    r"\itap2\trowd\cellx1000\cellx2000\pard\intbl 内丙\cell 内丁\cell\nestrow"
    r"\itap1 尾\cell 外右\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 外左二\cell 外右二\cell\row"
    r"\pard 结束\par}"
)

# 嵌套行收掉后不再写 \itap1：外层 \cell / \row 直接跟在中文后面，\itap 仍停在第 2 层
NESTED_STALE_ITAP = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 嵌套表后没把 itap 降回来\par"
    r"\trowd\cellx2000\cellx4000\pard\intbl 外左\par"
    r"\itap2\trowd\cellx1000\cellx2000\pard\intbl 内甲\cell 内乙\cell\nestrow"
    r"尾\cell 外右\cell\row"
    r"\pard 结束\par}"
)

# 控制字后面直接跟中文（不写分隔空格）：RTF 的控制字以第一个非字母字符结束，这是合法写法
ABUTTING_CJK = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 控制字后面直接跟中文\par"
    r"\trowd\cellx2000\cellx4000\pard\intbl甲\cell乙\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl丙\cell丁\cell\row"
    r"\pard 结束\par}"
)

# 两张表各一行，中间隔一个 \pard 中间段落\par；两张表的第一行都标了 \trhdr
TWO_TABLES = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 第一段\par"
    r"\trowd\trhdr\cellx2000\cellx4000\pard\intbl 表一甲\cell 表一乙\cell\row"
    r"\pard 中间段落\par"
    r"\trowd\trhdr\cellx2000\cellx4000\pard\intbl 表二甲\cell 表二乙\cell\row"
    r"\pard 结尾\par}"
)

# 五行两列的表：用来验行数上限（上限由用例按 2 收紧）
OVER_LIMIT = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil Arial;}}"
    r"\pard\f0 五行表\par"
    r"\trowd\cellx2000\cellx4000\pard\intbl 一甲\cell 一乙\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 二甲\cell 二乙\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 三甲\cell 三乙\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 四甲\cell 四乙\cell\row"
    r"\trowd\cellx2000\cellx4000\pard\intbl 五甲\cell 五乙\cell\row"
    r"\pard 结束\par}"
)


@pytest.fixture
def ctx(work_dir):
    """抽取路径的上下文：不准备模型素材，与两条路共用的抽取一致。"""
    return ExtractContext(work_dir=work_dir, prepare_assets=False)


def write_rtf(work_dir, name, source):
    """把手写 RTF 源文本按 UTF-8 落到工作目录，返回路径（写字节，避免 Windows 上换行被翻动）。"""
    path = work_dir / name
    path.write_bytes(source.encode("utf-8"))
    return path


def test_plain_rows_come_back_one_line_per_row(ctx, work_dir):
    """
    行为：两列三行的普通表还原成三行、每行一个制表符，表格前后的正文段落仍在各自的位置。

    来源：手写源文本 `TWO_COL_THREE_ROW`——三行 `\\trowd`…`\\row`，每行两个 `\\cellx`。
    预期值来源：源文本写了「甲一/乙一」「甲二/乙二」「甲三/乙三」三对格，交付文本因此是 5 行、
          第 2-4 行各一个制表符（改前整张表塌成一行、0 个制表符：`\\cell` 与 `\\row` 被丢掉）。
    """
    path = write_rtf(work_dir, "two_col_three_row.rtf", TWO_COL_THREE_ROW)
    lines = dispatch("rtf", path, ctx).content.split("\n")
    assert len(lines) == 5, lines
    assert [line.count("\t") for line in lines] == [0, 1, 1, 1, 0]
    assert lines[0] == "表格前正文" and lines[4] == "表格后正文"
    assert lines[1:4] == ["甲一\t乙一", "甲二\t乙二", "甲三\t乙三"]


def test_horizontal_merge_keeps_its_column_slots(ctx, work_dir):
    """
    行为：横向合并的格只出现一次，被合并吞掉的那一列位仍是空的一格。

    来源：`H_MERGE` 源文本第二行是 `\\clmgf\\cellx2000\\clmrg\\cellx4000\\cellx6000`（前两格合并、
          文字只写在前一格），第一行是三个独立格。
    预期值来源：合并区文字「合并文字」在源文本里只出现一次，两行都是三列，所以两行各 2 个制表符，
          且合并区后面那一格是空串（`合并文字\\t\\t右二`）。
    """
    path = write_rtf(work_dir, "h_merge.rtf", H_MERGE)
    content = dispatch("rtf", path, ctx).content
    assert content.count("合并文字") == 1, content
    lines = content.split("\n")
    assert lines[1] == "左一\t中一\t右一"
    assert lines[2] == "合并文字\t\t右二"


def test_vertical_merge_text_is_not_repeated(ctx, work_dir):
    """
    行为：纵向合并区的文字只在第一行出现，续行那一列是空串、列位还在。

    来源：`V_MERGE` 源文本第一行 `\\clvmgf`（首格）、第二行 `\\clvmrg`（继续格），继续格里没有文字。
    预期值来源：源文本里「跨行」只写在首格，两行各两列，所以续行是「\\t第二」（行首一个制表符），
          而整个交付文本里「跨行」只出现一次。
    """
    path = write_rtf(work_dir, "v_merge.rtf", V_MERGE)
    content = dispatch("rtf", path, ctx).content
    assert content.count("跨行") == 1, content
    lines = content.split("\n")
    assert lines[1] == "跨行\t第一"
    assert lines[2] == "\t第二"


def test_row_missing_one_cellx_keeps_all_three_cells(ctx, work_dir):
    """
    行为：某一行少写一个 `\\cellx` 时，这一行的格一个不少，也不与上一行的列错开。

    来源：`MISSING_CELLX` 源文本第一行三个 `\\cellx`，第二行只写两个（第三格没有边界）。
    预期值来源：源文本第二行写了三个 `\\cell`（A二 / B二 / C二），缺的边界由「上一格 +1」补出，
          所以两行都是三列、都是 2 个制表符。
    """
    path = write_rtf(work_dir, "missing_cellx.rtf", MISSING_CELLX)
    lines = dispatch("rtf", path, ctx).content.split("\n")
    assert [line.count("\t") for line in lines[1:3]] == [2, 2]
    assert lines[1] == "A一\tB一\tC一"
    assert lines[2] == "A二\tB二\tC二"


def test_table_without_any_cellx_still_gets_columns(ctx, work_dir):
    """
    行为：整张表一个 `\\cellx` 都没写时，每行的格仍各自成列——不报错、也不挤成一格。

    来源：`NO_CELLX` 源文本两行都只有 `\\trowd` 与两个 `\\cell`，没有边界控制字。
    预期值来源：源文本每行两个 `\\cell`，缺的边界按 0、1、2… 递增补出，所以每行 1 个制表符，
          两格文字分开落位。
    """
    path = write_rtf(work_dir, "no_cellx.rtf", NO_CELLX)
    lines = dispatch("rtf", path, ctx).content.split("\n")
    assert lines[1] == "A甲\tB甲"
    assert lines[2] == "A乙\tB乙"


def test_trhdr_row_count_reaches_the_result(ctx, work_dir):
    """
    行为：源用 `\\trhdr` 标出的表头行数进 `table_header_rows`；源没标时是 0。

    来源：`HEADER` 源文本第一行是 `\\trowd\\trhdr`、第二行没有；`rtf.extract_rtf` 是填这个字段的入口
          （`dispatch` 的 rtf 分支仍只交文本，所以字段值在这里断言）。
    预期值来源：源文本里只有一个 `\\trhdr`，所以那张表给 1；`TWO_COL_THREE_ROW` 一个都没标，
          按字段语义给 0——0 是「查过、源明确没标」，与「没有表 / 多张表」的 None 不是一回事。
    """
    marked = extract_rtf(write_rtf(work_dir, "header.rtf", HEADER))
    assert marked.table_header_rows == 1
    plain = extract_rtf(write_rtf(work_dir, "plain.rtf", TWO_COL_THREE_ROW))
    assert plain.table_header_rows == 0


def test_two_tables_give_no_header_rows_and_a_note(ctx, work_dir):
    """
    行为：交付文本里有**两张**表时不给 `table_header_rows`（None），并在 notes 里说明张数。

    来源：`TWO_TABLES` 源文本两张表各一行、各标了 `\\trhdr`，中间隔一个 `\\pard 中间段落\\par`。
    预期值来源：源文本里确实是两张表，而字段只在「交付文本只有一张表」时给值，所以是 None；
          文案与 docx / odf 是同一句；两行各 1 个制表符，中间的正文段落还在。
    """
    result = extract_rtf(write_rtf(work_dir, "two_tables.rtf", TWO_TABLES))
    assert result.table_header_rows is None
    assert result.notes == ["正文里有 2 张表，meta.table_header_rows 只在交付文本只有一张表时给值"]
    assert [line.count("\t") for line in result.content.split("\n")] == [0, 1, 0, 1, 0]


def test_nested_table_does_not_pollute_the_outer_grid(ctx, work_dir):
    """
    行为：外层单元格里的嵌套表不改变外层的列数，内层的文字一个也不丢。

    来源：`NESTED` 源文本外层两列，第一格里嵌了一张 `\\itap2` 的两行小表（它自己写了 `\\cellx1000`
          与 `\\cellx2000`，与外层边界完全不同）。
    预期值来源：外层每行两个 `\\cellx`，所以两行外层行各 1 个制表符（嵌套边界不外泄）；内层四个格
          的文字留在外层第一格里，按本仓的嵌套表方言串成 `甲 / 乙`、格内换行写成 `<br>`（见 `processors/cell_text.py`）。
    """
    path = write_rtf(work_dir, "nested.rtf", NESTED)
    lines = dispatch("rtf", path, ctx).content.split("\n")
    assert [line.count("\t") for line in lines[1:3]] == [1, 1]
    assert lines[1] == "外左<br>内甲 / 内乙 内丙 / 内丁 尾\t外右"
    assert lines[2] == "外左二\t外右二"


def test_outer_row_still_closes_when_the_writer_leaves_itap_at_two(ctx, work_dir):
    """
    行为：嵌套表收掉后写出器没把 `\\itap` 降回 1 时，外层那一行仍然收得住、列位照旧。

    来源：`NESTED_STALE_ITAP` 源文本——嵌套行被 `\\nestrow` 收掉后，外层的 `\\cell` / `\\row`
          直接跟在中文后面，`\\itap` 还停在第 2 层（`\\itap` 是状态，写出器不一定重写它）。
    预期值来源：外层每行两个 `\\cellx`，所以外层是 1 个制表符；内层两格与「外左」「尾」的文字
          都留在外层第一格里（内层两格按嵌套表方言串成 `内甲 / 内乙`、格内换行是 `<br>`）。
    """
    path = write_rtf(work_dir, "nested_stale_itap.rtf", NESTED_STALE_ITAP)
    lines = dispatch("rtf", path, ctx).content.split("\n")
    assert [line.count("\t") for line in lines[1:2]] == [1]
    assert lines[1] == "外左<br>内甲 / 内乙 尾\t外右"


def test_control_word_abutting_chinese_text_keeps_the_text(ctx, work_dir):
    """
    行为：控制字后面直接跟中文（没写分隔空格）时，中文仍是正文，一个字都不丢。

    来源：`ABUTTING_CJK` 源文本写成 `\\intbl甲\\cell乙`——控制字以第一个非字母字符结束，
          中文与 ASCII 控制字之间不写空格是合法写法。
    预期值来源：源文本四格写了甲 / 乙 / 丙 / 丁，所以两行各 1 个制表符且四个字都在
          （改前这四个字全被吞进控制字，正文里一格都不剩）。
    """
    path = write_rtf(work_dir, "abutting_cjk.rtf", ABUTTING_CJK)
    lines = dispatch("rtf", path, ctx).content.split("\n")
    assert lines[1] == "甲\t乙"
    assert lines[2] == "丙\t丁"


def test_table_over_the_row_limit_is_truncated_and_reported(ctx, work_dir, monkeypatch):
    """
    行为：行数超 `max_table_rows` 的表格只渲前若干行并说明还剩多少行，命中名进 `limits_hit`。

    来源：`OVER_LIMIT` 源文本有五行两列；上限按 `configs/limits.MAX_TABLE_ROWS` 收紧到 2。
    预期值来源：五行里渲前两行，末行是「... 其余 3 行未列出」（5 − 2 = 3），命中名是 max_table_rows。
    """
    monkeypatch.setattr(limits, "MAX_TABLE_ROWS", 2)
    path = write_rtf(work_dir, "over_limit.rtf", OVER_LIMIT)
    lines = dispatch("rtf", path, ctx).content.split("\n")
    assert lines[1:4] == ["一甲\t一乙", "二甲\t二乙", "... 其余 3 行未列出"]
    assert "max_table_rows" in extract_rtf(path).limits_hit


# ---------- `\ucN`：`\uN` 后面的 fallback 字符要按声明跳过 ----------

def test_unicode_fallback_is_skipped_by_the_uc_declaration(ctx, work_dir):
    """
    行为：`\\uN` 后面的 fallback 字符按 `\\ucN` 声明的个数跳过——问号、`\\'hh`、普通字符都不漏进正文。

    来源：RTF 规范（`\\ucN` 声明 `\\uN` 之后有几个「当前代码页的替代字符」，默认 1）。
    预期值来源：四份手写源文本——`\\uc1 \\u31179?`（问号）、`\\uc1 \\u31179\\'c4`（十六进制转义）、
          `\\uc0 \\u31179`（声明 0 个）、默认不写 `\\uc` 时跟一个普通字符；四种都只该得到「秋」。
    """
    cases = {
        "question.rtf": r"{\rtf1\ansi\uc1 \u31179?}",
        "hex.rtf": r"{\rtf1\ansi\uc1 \u31179\'c4}",
        "none.rtf": r"{\rtf1\ansi\uc0 \u31179}",
        "default.rtf": r"{\rtf1\ansi \u31179x}",
    }
    for name, source in cases.items():
        content = dispatch("rtf", write_rtf(work_dir, name, source), ctx).content
        assert content.strip() == "秋", f"{name} 的正文是 {content!r}，fallback 没跳干净"


def test_sample_rtf_does_not_leak_the_uc_parameter(ctx, fixture_copy):
    """
    行为：真实样例 `data/sample.rtf` 的正文第一行不再是控制字参数「c1」。

    来源：该样例开头的 `{\\rtf1\\ansi\\uc1`——改前解析器把 `\\uc1` 当成「`\\u` 没跟数字」直接跳过，
          `c1` 留在源码里被当正文吐出来（第一行就是「c1」）。
    预期值来源：样例里第一段正文是「秋季越野赛 2026 报名说明」，所以首行必须是它。
    """
    path, _ = fixture_copy("sample.rtf")
    assert dispatch("rtf", path, ctx).content.splitlines()[0] == "秋季越野赛 2026 报名说明"
