# -*- coding: utf-8 -*-
r"""
A 组：按文件类型的确定性抽取。

测的是：`pipelines/steps/step4_extract.dispatch(ext, path, ctx)`——类型识别之后、响应组装之前，
决定「这个文件交给哪个处理器、产出什么」。不调模型、不联网、不起服务。

为什么选它：项目按「一种格式一个处理器」组织，`dispatch()` 是这张表唯一的入口，
加格式时改的就是这个函数。类型识别的行为在 `test_detect.py` 里验，
响应字段约定与状态码在 `test_extract_contract.py`（HTTP 接口测试）里验，这里只管处理器产出。

预期值来源（都不是「跑一遍看输出」）：
- `README.md`「支持哪些输入」表：承诺完整解析的格式、只给结构信息的格式、降级的格式
- 样例的生成参数（`scripts/make_fixtures.py` 各阶段写死的页数 / 条目数）：
  manual.pdf 3 页有文字层、scanned.pdf 3 页无文字层、deck.pptx 6 页、report.docx 内嵌 1 张图、
  bundle.zip 12 条目、demo.apk 6 条目、sample.epub 3 章（spine 顺序与 manifest / zip 顺序都不同）
- `CONTEXT.md` 的术语：「基础文件信息」「交付文本」「降级」
"""
import zipfile

import pytest

from configs import errors
from utils.excel.grid import CoveredSlot, GridBuilder, check_grid, grid_notes, render_tsv
from utils.processor_schema import ExtractContext
from pipelines.steps.step2_detect import extension_from_name
from pipelines.steps.step4_extract import dispatch

# README「支持哪些输入」里列为「完整解析」的文档格式，每种都有配套样例
DOCUMENT_FIXTURES = (
    "plain.txt",
    "notes.md",
    "table.csv",
    "sample.tsv",
    "sample.json",
    "feed.xml",
    "sample.rtf",
    "report.docx",
    "inventory.xlsx",
    "deck.pptx",
    "sample.odt",
    "sample.odp",
    "sample.epub",
    "sample.ofd",
    "manual.pdf",
)

# 音视频样例的生成参数（`scripts/make_fixtures.py` 阶段 11/12 写死）：
# 16kHz 单声道 3 秒的 wav，mp4 由这段音频封成，所以两者报出的音轨参数应当一样。
AUDIO_SECONDS = 3.0
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1


@pytest.fixture
def ctx(work_dir):
    """抽取路径的上下文：不准备模型素材，与两条路共用的抽取一致。"""
    return ExtractContext(work_dir=work_dir, prepare_assets=False)


@pytest.fixture
def asset_ctx(work_dir):
    """准备模型素材的上下文：只有它会落盘内嵌图，与 understand 那条路一致。"""
    return ExtractContext(work_dir=work_dir, prepare_assets=True)


def run(name, ctx, fixture_copy, page_url: str = ""):
    """把样例复制进工作目录并按它的扩展名跑一次 dispatch。"""
    path, ext = fixture_copy(name)
    return dispatch(ext, path, ctx, page_url=page_url)


# ---------- 承诺「完整解析」的格式 ----------

@pytest.mark.capability_document
@pytest.mark.parametrize("name", DOCUMENT_FIXTURES)
def test_document_formats_are_fully_parsed(name, ctx, fixture_copy):
    """
    行为：README 承诺完整解析的格式都抽出非空正文、不当成降级、并报出实际用的引擎。

    来源：README「支持哪些输入」的文档行 + 「完整解析（版面顺序、表格、文本框、内嵌图位置）」。
    """
    result = run(name, ctx, fixture_copy)
    assert result.degraded is False, f"{name} 不该降级：{result.degrade_reason}"
    assert result.content.strip(), f"{name} 应抽出非空正文"
    assert result.engines, f"{name} 应报出实际使用的引擎"


@pytest.mark.capability_document
def test_plain_text_is_delivered_verbatim(ctx, work_dir):
    """
    行为：交付文本就是源文本本身，不加工、不加前缀。

    来源：CONTEXT「交付文本：从源抽取出的文本本身…也不被追加实现细节前缀」。
    预期值来源：本用例自己写入的内容——不依赖对实现的任何理解。
    写字节而不是 `write_text`：后者在 Windows 上会把 `\\n` 翻成 `\\r\\n`，
    那样验的就不是「源文本原样」而是「我以为是的那段文本」了。
    """
    text = "第一行：中文内容\n第二行：ascii only\n第三行：{braces} 与 $dollar\n"
    path = work_dir / "known.txt"
    path.write_bytes(text.encode("utf-8"))
    result = dispatch("txt", path, ctx)
    assert result.content == text


@pytest.mark.capability_document
def test_line_endings_are_delivered_unchanged(ctx, work_dir):
    """
    行为：行尾原样交付，CRLF 不被归一成 LF。

    来源：README「两条路产出的正文逐字节一致」——归一化行尾就会让这条不成立。
    """
    raw = "第一行\r\n第二行\r\n".encode("utf-8")
    path = work_dir / "crlf.txt"
    path.write_bytes(raw)
    result = dispatch("txt", path, ctx)
    assert result.content == raw.decode("utf-8")


# ---------- 各类型报出的结构信息 ----------

@pytest.mark.parametrize(
    ("name", "pages", "images"),
    [("report.docx", 1, 1), ("deck.pptx", 6, 1)],
)
@pytest.mark.capability_document
def test_office_documents_report_page_and_embedded_image_counts(name, pages, images, ctx, fixture_copy):
    """
    行为：Office 文档报出页数与内嵌图张数。

    来源：样例生成参数（report.docx 1 页内嵌 1 张图、deck.pptx 6 页内嵌 1 张图）
    + README「文档内嵌图：标出位置占位」。
    """
    result = run(name, ctx, fixture_copy)
    assert result.pages == pages
    assert result.images == images


@pytest.mark.capability_table
def test_spreadsheet_reports_its_sheet_count(ctx, fixture_copy):
    """行为：表格类报出工作表数。来源：inventory.xlsx 是单工作表样例。"""
    result = run("inventory.xlsx", ctx, fixture_copy)
    assert result.pages == 1


@pytest.mark.capability_document
def test_text_pdf_reports_every_page(ctx, fixture_copy):
    """
    行为：有文字层的 PDF 逐页抽出正文，页数与内嵌图张数如实报出，且三节按文档顺序出现。

    来源：样例生成参数（manual.pdf 3 页，纯文本无图，标题加六节、两节一页）。
    """
    result = run("manual.pdf", ctx, fixture_copy)
    assert result.pages == 3
    assert result.images == 0
    assert result.content.strip()
    assert result.degraded is False
    # 阅读顺序：页与页正文不同，顺序错了才看得出来（正文相同的样本验不了这一条）
    marks = ["一、主办与承办", "三、组别与关门时间", "五、报名与缴费"]
    positions = [result.content.find(mark) for mark in marks]
    assert all(position >= 0 for position in positions), result.content
    assert positions == sorted(positions), result.content


@pytest.mark.capability_document
def test_scanned_pdf_declares_missing_text_layer_without_degrading(ctx, fixture_copy):
    """
    行为：没有文字层的 PDF 正文为空、页数与图张数照报，但**不降级**，并在现场说明里讲清原因。

    来源：README「扫描件 PDF：正文为空，并在 meta 里声明『没有文字层』」
    + 「降级是能力边界而非故障」的反面——这一条属于能力边界内，所以 degraded 为假。
    样例生成参数：scanned.pdf 3 页、每页一张图。
    """
    result = run("scanned.pdf", ctx, fixture_copy)
    assert result.content == ""
    assert result.degraded is False
    assert result.pages == 3
    assert result.images == 3
    assert any("文字层" in note for note in result.notes), result.notes


@pytest.mark.capability_limits
def test_unprocessed_pages_are_named_when_the_render_limit_bites(asset_ctx, fixture_copy, monkeypatch):
    """
    行为：扫描件页数超过整页渲染上限时，`unprocessed_pages` 精确列出没处理的页码，
          而不是只留一句「其余未渲染」——机器要读得出「缺了哪几页」。

    来源：ADR-0003（能力边界也要机器可读）——降级与未处理声明进 `meta`，保持机器可读。
    做法：把上限临时压到 1，用 3 页的 scanned.pdf 触发——不新增样例、不动全局配置。
    预期值来源：样例生成参数（scanned.pdf 3 页）+ 上限 1 → 没处理的是第 2、3 页。
    """
    from configs import settings

    monkeypatch.setattr(settings, "RENDERED_PAGE_MAX", 1)
    monkeypatch.setattr(settings, "RENDERED_PAGE_OCR_ENABLED", True)
    result = run("scanned.pdf", asset_ctx, fixture_copy)

    assert result.pages == 3
    assert result.pages_used == 1
    assert result.unprocessed_pages == [2, 3]
    assert "rendered_page_max" in result.limits_hit


@pytest.mark.capability_media
@pytest.mark.parametrize("name", ["screenshot.png", "photo.png"])
def test_image_extract_yields_structure_only(name, ctx, fixture_copy):
    """
    行为：extract 对图片的全部产出是结构信息，没有正文，也没有音轨信息。

    来源：README「图片：只给尺寸、格式、EXIF 等结构信息」+ ADR-0004（extract 不内置 OCR）。
    """
    result = run(name, ctx, fixture_copy)
    assert result.content == ""
    assert result.degraded is False
    image = result.image_meta
    assert image is not None, "图片应给出结构信息"
    assert image.width and image.height
    assert image.format
    assert result.audio_meta is None


@pytest.mark.capability_media
def test_audio_reports_its_track_structure(ctx, fixture_copy):
    """
    行为：音频的确定性产出是音轨参数（时长 / 采样率 / 声道），没有正文，也不降级。

    来源：README「音频 → 结构信息（时长 / 采样率 / 声道 / 音轨格式）」+ 样例生成参数
    （`sample.wav` 写死 3 秒、16kHz、单声道）。音频文件本身就是音轨，所以不该出现 ffmpeg。
    """
    result = run("sample.wav", ctx, fixture_copy)
    assert result.content == "", "音频没有可确定性抽取的正文"
    assert result.degraded is False, f"音轨读得出来就不该降级：{result.degrade_reason}"
    assert result.engines == ["soundfile"], "音频不需要先抽音轨"
    assert result.audio_meta is not None, "音频应给出音轨参数"
    assert result.audio_meta.duration_seconds == pytest.approx(AUDIO_SECONDS, abs=0.05)
    assert result.audio_meta.sample_rate == AUDIO_SAMPLE_RATE
    assert result.audio_meta.channels == AUDIO_CHANNELS


@pytest.mark.capability_media
def test_video_reports_the_parameters_of_its_extracted_track(ctx, fixture_copy):
    """
    行为：视频先抽出音轨，报出的是**音轨**的参数（统一转成 16kHz 单声道），正文同样为空且不降级。

    来源：README「视频 → 同上，报的是它音轨的参数」+ 样例生成参数（`sample.mp4` 由 `sample.wav`
    封成，时长同为 3 秒）+ `utils/ffmpeg.py` 把音轨统一成 16kHz 单声道。
    若哪天改成直接报容器的原始参数，采样率与声道就对不上——这条同时钉住「报的是抽出的那条音轨」。
    """
    result = run("sample.mp4", ctx, fixture_copy)
    assert result.content == "", "视频容器里没有可确定性抽取的正文"
    assert result.degraded is False, f"音轨抽得出来就不该降级：{result.degrade_reason}"
    assert result.engines == ["ffmpeg"], "视频要先抽音轨"
    assert result.audio_meta is not None, "视频报的是它音轨的参数"
    assert result.audio_meta.duration_seconds == pytest.approx(AUDIO_SECONDS, abs=0.1)
    assert result.audio_meta.sample_rate == AUDIO_SAMPLE_RATE, "报的应是抽出的音轨参数"
    assert result.audio_meta.channels == AUDIO_CHANNELS
    assert result.notes, "抽过音轨这件事要说明，不能静默"


@pytest.mark.capability_container
@pytest.mark.parametrize(("name", "entries"), [("bundle.zip", 12), ("demo.apk", 6)])
def test_archive_family_lists_entries_without_extracting(name, entries, ctx, fixture_copy):
    """
    行为：压缩包与安装包只列条目清单，清单进交付文本，条目数如实报出。

    来源：README「压缩包：条目清单（不解压内容）」+ 样例生成参数（12 / 6 条目）。
    """
    result = run(name, ctx, fixture_copy)
    assert result.entry_count == entries
    assert result.degraded is False
    assert result.content.strip(), "条目清单应作为交付文本给出"


# ---------- 合并单元格：表格逻辑格子必须始终成立的规则 ----------

# 样例生成参数：三个样例都是 3 列，横向合并在第一行、纵向合并在第一列；
# 合并文本是脚本阶段 13 写死的固定串（见 `scripts/make_fixtures.py`）。
MERGED_FIXTURES = ("merged.docx", "merged.xls", "merged.xlsx", "merged.pptx", "merged.ods", "merged_tail.docx")
MERGED_COLUMNS = 3
MERGED_TITLE = "合并标题"
MERGED_VERTICAL = "纵向合并"


def _table_lines(content: str) -> list:
    """只取表格行：滤掉空行与 `=== ... ===` 这类分节标记。"""
    return [line for line in content.splitlines() if line.strip() and not line.startswith("===")]


@pytest.mark.capability_table
@pytest.mark.parametrize("name", MERGED_FIXTURES)
def test_merged_text_appears_exactly_once(name, ctx, fixture_copy):
    """
    行为：合并区的文本在交付文本里只出现一次——合并只影响坐标，不复制内容。

    来源：CONTEXT「交付文本」+ 样例生成参数（A1:B1 横向、A2:A3 纵向各一处）。
    实测（2026-10-02）：docx 走 `row.cells` 时同一个逻辑格子被返回多次，合并标题与纵向合并各出现
    两次，下游看到的是「两列同一个值」，列对应关系因此失真。pptx 侧 python-pptx 对覆盖格返回
    空串、本来就正确，这条用例在它上面是回归锁。
    """
    result = run(name, ctx, fixture_copy)
    assert result.content.count(MERGED_TITLE) == 1, f"横向合并的文本重复：{result.content!r}"
    assert result.content.count(MERGED_VERTICAL) == 1, f"纵向合并的文本重复：{result.content!r}"


@pytest.mark.capability_table
@pytest.mark.parametrize("name", MERGED_FIXTURES)
def test_merged_rows_keep_the_same_column_count(name, ctx, fixture_copy):
    """
    行为：同一张表里每行的制表符个数相同——合并区覆盖的空列也要占列位。

    来源：`excel.py` 模块 docstring「按列位置输出制表符，空列留空；列一错位整张表就读不出
    对应关系」+ 样例生成参数（`merged.xlsx` 第二张表只有 A1:C1 一处合并，覆盖列没有任何别的值）。
    实测（2026-10-02）：原来「列宽从内容反推」，这张表被读成 1 列（0 个制表符），坐标随之丢失。
    """
    lines = _table_lines(run(name, ctx, fixture_copy).content)
    widths = {line.count("\t") for line in lines}
    assert widths == {MERGED_COLUMNS - 1}, f"表内行宽不一致（覆盖列被裁掉）：{sorted(widths)}，{lines!r}"


@pytest.mark.capability_table
def test_grid_invariant_holds_for_a_real_merged_docx(fixture_copy):
    """
    行为：真实样例（最后一行还有缺格）构造出的网格满足不变式——每个 origin 声明的每个位置都有
          一个指回它的覆盖位。

    来源：`01-表格逻辑格子建模.md` 的 D.1 把 `check_grid` 列为「必须抄的一件」；本条把它变成可核查判据。
    预期值来源：样例生成参数（A1:B1 横向、A2:A3 纵向各一处）——网格若错，跨度就没有覆盖位背书。
    """
    from docx import Document

    from pipelines.processors import docx as docx_processor
    from utils.excel.grid import GridBuilder, check_grid, fill_from_specs

    path, _ = fixture_copy("merged_tail.docx")
    table = Document(str(path)).tables[0]
    rows = docx_processor._row_elements(table._tbl)
    grid = fill_from_specs(GridBuilder(), [docx_processor._row_specs(row, table) for row in rows])
    assert check_grid(grid) == [], f"网格不自洽：{check_grid(grid)}"


@pytest.mark.capability_table
def test_grid_invariant_checker_reports_a_contradictory_grid():
    """
    行为：手工造一个「跨度没有覆盖位背书」的矛盾网格，校验函数必须报出来。

    来源：与 `test_structure.py` 的 `test_the_rules_are_looking_at_real_modules` 同一种自检手法——
          校验函数自己假绿（永远返回空列表）就等于没加。
    预期值来源：手搭的网格常量（一个声明 2 列跨度、却没有任何覆盖位的 origin）。
    """
    from utils.excel.grid import GridSpan, check_grid

    contradictory = [[GridSpan(text="跨两列", col_span=2)]]
    assert check_grid(contradictory), "跨度声明了 2 列却没有覆盖位，校验函数必须报出来"


# ---------- 表格逻辑格子的边界形状（01 号专题列的五个「难构造」用例）----------

@pytest.mark.capability_table
def test_late_span_is_clamped_to_positions_it_can_own():
    """
    行为：同一行里晚到的跨度不得盖住已被认领的位置——重叠时就地压到上限内，而不是产出矛盾网格。

    来源：`01-表格逻辑格子建模.md` 列的 `overlapping_spans_clamp_the_late_origin`
          （anydoc `src/model/table.rs:399-413`）；它的注释说明「重叠会破坏『每个逻辑格子恰好出现一次』，
          存储的跨度必须与覆盖位一一对应」。
    预期值来源：手搭的逻辑格子序列（两处纵向跨度认领了第 1 行的第 0 / 第 2 列，晚到的横向跨度只能落 1 列）。
    """
    builder = GridBuilder()
    builder.next_row()
    builder.place("纵一", 1, 2)  # 认领 (1,0)
    builder.place("中间", 1, 1)
    builder.place("纵二", 1, 2)  # 认领 (1,2)
    builder.next_row()
    builder.covered()  # 消费 (1,0)
    builder.place("晚到跨两列", 2, 1)  # 起点在第 1 列，但第 2 列已被认领 → 钳成 1 列
    builder.covered()  # 消费 (1,2)
    grid = builder.finish()

    assert grid[1][1].text == "晚到跨两列"
    assert grid[1][1].col_span == 1, "晚到的跨度必须被钳到它能真正占有的列数"
    assert check_grid(grid) == [], f"网格不自洽：{check_grid(grid)}"
    assert builder.limit_hits == []


@pytest.mark.capability_table
def test_nested_spans_and_a_stray_covered_marker_stay_consistent():
    """
    行为：横向跨度与纵向跨度交织、且出现一个「没有跨度认领的覆盖位」时，网格仍自洽——孤立的覆盖位
          退化成空格而不是报错，并把这件事记一笔（`orphan_covered`，由 `grid_notes` 翻成 notes）。

    来源：anydoc 的 `covered()` 语义（`src/model/table.rs:216-232`）：`false` 表示「没有跨度认领这个
          位置，孤立的标记就变成空格子」；本仓 `GridBuilder.covered()` 同构，返回值改成**认领到的
          origin**（None = 没人认领），并多记一个计数。
    预期值来源：手搭的逻辑格子序列（第 1 行的第 0 列无人认领、第 2 列被上方的纵向跨度认领）。
    """
    builder = GridBuilder()
    builder.next_row()
    builder.place("横跨两列", 2, 1)
    builder.place("纵跨两行", 1, 2)  # 认领 (1,2)
    builder.next_row()
    assert builder.covered() is None, "第 1 行第 0 列没有跨度认领，应退化成空格"
    builder.place("晚到", 2, 1)  # 第 2 列被认领 → 钳成 1 列
    builder.covered()  # 消费 (1,2)
    grid = builder.finish()

    assert grid[1][0].text == "" and grid[1][0].col_span == 1, "孤立覆盖位应成一个空格子"
    assert grid[1][1].col_span == 1
    assert isinstance(grid[1][2], CoveredSlot), "被纵向跨度认领的位应是覆盖位"
    assert (grid[1][2].origin_row, grid[1][2].origin_col) == (0, 2)
    assert check_grid(grid) == [], f"网格不自洽：{check_grid(grid)}"
    assert builder.orphan_covered == 1, builder.orphan_covered
    assert grid_notes(builder) == ["有 1 个覆盖位没有对应的合并起点，已按空格处理（列位不受影响）"]


@pytest.mark.capability_table
def test_trailing_covered_rows_stay_backed_and_render_nothing():
    """
    行为：只有覆盖位的尾行**不从网格里裁掉**（跨度仍要有覆盖位背书），但渲染时不出空行。

    来源：anydoc 用 `keep_covered_tail` 让电子表格保住这类行、其余来源裁掉
          （`src/model/table.rs:137-143`、`:258-266`）；本仓刻意只保留在网格里、渲染层跳过整行无文字的
          行（`render_tsv`）——这是 `00-总览` §4.3-4 记下的有意差异。
    预期值来源：手搭的逻辑格子序列（一处跨 3 行的纵向跨度，后两行只被它认领）。
    """
    builder = GridBuilder()
    builder.next_row()
    builder.place("纵跨三行", 1, 3)
    grid = builder.finish()

    assert len(grid) == 3, "只被跨度认领的尾行要留在网格里（否则跨度没有覆盖位背书）"
    assert isinstance(grid[2][0], CoveredSlot)
    assert (grid[2][0].origin_row, grid[2][0].origin_col) == (0, 0)
    assert check_grid(grid) == [], f"网格不自洽：{check_grid(grid)}"
    assert render_tsv(grid, 10) == ["纵跨三行"], "只被覆盖的整行不该出现在渲染结果里"


@pytest.mark.capability_table
def test_huge_span_is_rejected_before_expanding():
    """
    行为：巨大跨度在**展开之前**就被预算拒掉——退化成 1×1 并记 `table_expansion`，不会真的去建
          百万个逻辑格子。

    来源：anydoc 的 `huge_span_hits_the_expansion_budget_before_expanding`
          （`src/model/table.rs:446-452`，`u32::MAX × u32::MAX` 的跨度在展开前被拒）；本仓
          `GridBuilder.place` 先按面积记账、超限即退化成 1×1。
    预期值来源：手搭的逻辑格子序列（100 万 × 100 万的跨度）与 `MAX_GRID_EXPANSION` 的默认值。
    """
    builder = GridBuilder()
    builder.next_row()
    builder.place("巨", 10**6, 10**6)
    grid = builder.finish()

    assert builder.limit_hits == ["max_grid_expansion"]
    assert grid[0][0].col_span == 1, "超限的跨度应退化成 1×1"
    assert len(grid[0]) == 1, "不能为超限跨度真的建出格位"
    assert check_grid(grid) == [], f"网格不自洽：{check_grid(grid)}"


@pytest.mark.capability_table
def test_covered_tail_behind_a_short_row_gap_materializes():
    """
    行为：第 0 行第 5 列起有 2 行跨度、第 1 行只写到第 0 列时，`finish()` 要把中间的空缺补出来，
          让第 5 列上出现指回 `(0,5)` 的覆盖位。

    来源：anydoc 的 `covered_tail_behind_short_row_gap_materializes`（`src/model/table.rs:466-483`）：
          该行的长度必须补到 6，且第 5 位是指回 origin 的覆盖位——「补位」不是渲染层的事，是网格的事。
    预期值来源：手搭的逻辑格子序列（第 0 行 6 列、第 1 行 1 列 + 一处纵向跨度）。
    """
    builder = GridBuilder()
    builder.next_row()
    for index in range(5):
        builder.place(f"c{index}", 1, 1)
    builder.place("纵跨两行", 1, 2)  # 认领 (1,5)
    builder.next_row()
    builder.place("只有一列", 1, 1)
    grid = builder.finish()

    assert len(grid[1]) == 6, f"第 1 行应补到 6 位（实际 {len(grid[1])}）"
    assert isinstance(grid[1][5], CoveredSlot), "第 5 位应是覆盖位"
    assert (grid[1][5].origin_row, grid[1][5].origin_col) == (0, 5)
    assert check_grid(grid) == [], f"网格不自洽：{check_grid(grid)}"


# ---------- 网页与 EPUB 的表格：走表格逻辑格子，不再拉平 ----------

@pytest.mark.capability_table
def test_html_table_is_rendered_as_columns(ctx, fixture_copy):
    """
    行为：网页里的 `<table>` 按行列渲成制表符行，而不是每个单元格各占一行。

    来源：README「支持哪些输入」的网页行 + 「尽量保留真实信息」；`html_tables.py` 的模块 docstring
          记了为什么（`get_text()` 拉平之后列对应关系就没了）。
    预期值来源：样例 `backend/data/page.html` 的表格是写死的 3 行 × 2 列
          （表头「组别 / 关门时间」，两行数据「十公里 / 3 小时」「二十一公里 / 6 小时」）。
    实测（2026-10-04）：改前这 6 个单元格各占一行，列对应关系丢失。
    """
    path, _ = fixture_copy("page.html")
    content = dispatch("html", path, ctx).content
    assert "组别\t关门时间" in content, f"表头应渲成一行两列：{content!r}"
    assert "十公里\t3 小时" in content
    assert "二十一公里\t6 小时" in content
    assert "组别\n关门时间" not in content, "不应该再是「一个单元格一行」的拉平形状"


@pytest.mark.capability_table
def test_html_table_spans_go_through_the_grid(ctx, work_dir):
    """
    行为：`colspan` / `rowspan` 交给表格逻辑格子——合并文本只出现一次，被吞掉的逻辑格子占列位（行首空列保住）。

    来源：`html_tables.table_to_tsv` 的 docstring（`rowspan="0"` 按「跨到本行组末尾」处理）；
          `01-表格逻辑格子建模.md` 的不变式（内容与跨度只挂在 origin 上）。
    预期值来源：本用例自己写的 HTML（横向合并一处、跨两行的纵向合并一处）。
    """
    path = work_dir / "spanned.html"
    path.write_text(
        "<html><body><table>"
        "<tr><td colspan='2'>合并标题</td><td>c1</td></tr>"
        "<tr><td rowspan='2'>纵向</td><td>b2</td><td>c2</td></tr>"
        "<tr><td>b3</td><td>c3</td></tr>"
        "</table></body></html>",
        encoding="utf-8",
    )
    content = dispatch("html", path, ctx).content
    assert content.count("合并标题") == 1, f"横向合并的文本重复了：{content!r}"
    assert content.count("纵向") == 1, f"纵向合并的文本重复了：{content!r}"
    table_lines = [line for line in content.splitlines() if "\t" in line]
    assert table_lines[0] == "合并标题\t\tc1", table_lines
    assert {line.count("\t") for line in table_lines} == {2}, f"同一张表行宽不一致：{table_lines}"
    assert "\tb3\tc3" in table_lines, "被纵向合并吞掉的列位要占住位置（行首空列不能丢）"


@pytest.mark.capability_table
def test_epub_chapter_table_goes_through_the_grid():
    """
    行为：EPUB 章节里的 `<table>` 与网页同一条路——`_chapter_body` 交出制表符块，而不是拉平的文本。

    来源：`html_tables.py` 是网页与 EPUB 共用的那一份（同一能力只在最靠近它的测试入口验一次，
          所以这里直接测章节处理器，不另起一个 epub 样例）。
    预期值来源：本用例自己写的章节 XHTML（表 2 行 × 2 列，前后各一段）。
    """
    from bs4 import BeautifulSoup

    from pipelines.processors.epub import _chapter_body

    soup = BeautifulSoup(
        "<html><body><p>前一段</p>"
        "<table><tr><th>甲</th><th>乙</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<p>后一段</p></body></html>",
        "html.parser",
    )
    body, hits, headers, kinds = _chapter_body(soup)
    assert "甲\t乙" in body and "1\t2" in body, f"章节表格应渲成制表符行：{body!r}"
    assert "前一段" in body and "后一段" in body, "表格前后的段落不能丢"
    assert headers == [1] and kinds == ["data"], (headers, kinds)
    assert hits == []
    assert headers == [1], f"章节里唯一一张表的表头行数应报出来：{headers}"


@pytest.mark.capability_table
def test_table_header_rows_come_from_the_source_markup(ctx, fixture_copy, work_dir):
    """
    行为：`meta.table_header_rows` 报的是**源自己标出**的表头行数（`<th>` / `<thead>`）——三种情况
          各一条：标的（1）、没标的（0）、表不止一张（null）。

    来源：`routers/convert/schema.py` 的 `table_header_rows` 字段说明（null 与 0 的区别就写在那里）
          + ADR-0002（字段约定只有一个事实源）。
    预期值来源：`page.html` 的表首行是 `<th>`（1 行表头）；本用例另写两张页：一张全 `<td>`、一张两张表。
    """
    marked, _ = fixture_copy("page.html")
    assert dispatch("html", marked, ctx).table_header_rows == 1

    unmarked = work_dir / "unmarked.html"
    unmarked.write_text(
        "<html><body><table><tr><td>甲</td><td>乙</td></tr><tr><td>1</td><td>2</td></tr></table></body></html>",
        encoding="utf-8",
    )
    assert dispatch("html", unmarked, ctx).table_header_rows == 0, "源没标表头就是 0，不能拿首行当表头"

    two = work_dir / "two.html"
    two.write_text(
        "<html><body><table><tr><th>甲</th></tr></table><table><tr><th>乙</th></tr></table></body></html>",
        encoding="utf-8",
    )
    result = dispatch("html", two, ctx)
    assert result.table_header_rows is None, "交付文本不止一张表时不给值"
    assert any("2 张表" in note for note in result.notes), result.notes


@pytest.mark.capability_table
def test_docx_header_rows_come_from_tbl_header(ctx, fixture_copy):
    """
    行为：docx 的 `w:trPr/w:tblHeader` 行算表头——`header.docx` 报 1，且这一行的文字照旧在正文里。

    来源：`01-表格逻辑格子建模.md` §1.1 的剩余项（anydoc 的表是 `{grid, header_rows, kind}`，本仓只缺
          「其余格式的源文件里标明的表头」）；`routers/convert/schema.py` 的字段说明（只认源自己标的）。
    预期值来源：`scripts/make_fixtures.py` 阶段 22 的生成参数——3 行表、只把首行标上 `w:tblHeader`。
    """
    path, ext = fixture_copy("header.docx")
    result = dispatch(ext, path, ctx)
    assert result.table_header_rows == 1, result.table_header_rows
    assert "季度\t金额" in result.content, result.content


@pytest.mark.capability_table
def test_docx_without_a_marked_row_reports_zero(ctx, fixture_copy):
    """
    行为：docx 没标 `w:tblHeader` 时是 **0**（查过、源明确没标），而不是拿首行当表头。

    来源：字段说明里 `0` 与 `null` 的区别（`routers/convert/schema.py`）；本仓取向「宁可回落也不猜」。
    预期值来源：`report.docx`（阶段 2 生成）的表没有写行属性——python-docx 只写 `w:tc`，不写 `w:tblHeader`。
    """
    path, ext = fixture_copy("report.docx")
    result = dispatch(ext, path, ctx)
    assert result.table_header_rows == 0, "源没标表头就是 0，不能拿首行当表头"


@pytest.mark.capability_table
def test_odt_header_row_group_is_rendered_and_counted(ctx, fixture_copy):
    """
    行为：ODF 的表头是**行组**（`table:table-header-rows`）——那一组的文字进正文，且报成 1 行表头。

    来源：`01-表格逻辑格子建模.md` §1.1 的剩余项。修前 `odf._render_table` 只取 `table:table` 的
          **直属** `table:table-row`，整个表头行组连同文字一起消失（实测 `header.odt` 的
          `项目\t数量` 一个字都没进正文，字段也恒为 null）。
    预期值来源：阶段 22 的生成参数——3 行表、首行放进表头行组。
    """
    path, ext = fixture_copy("header.odt")
    result = dispatch(ext, path, ctx)
    assert result.table_header_rows == 1, result.table_header_rows
    assert "项目\t数量" in result.content, f"表头行组的内容不能丢：{result.content!r}"
    assert "甲\t3" in result.content, result.content


@pytest.mark.capability_table
def test_odf_row_group_wrapper_keeps_its_rows(ctx, work_dir):
    """
    行为：行被 `table:table-rows` 包一层时照样渲出来（ODF 允许把正文行再包一层）。

    来源：`odf._table_rows` 的容器清单（`table:table-rows` / `table:table-row-group`）；
          只取直属行的话这类文件会整表变空。
    预期值来源：本用例用 odfpy 写的一张单列表——一行在 `table:table-rows` 里、一行在表下直属。
    """
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow, TableRows
    from odf.text import P

    document = OpenDocumentText()
    table = Table(name="行组样例")
    group = TableRows()
    for text in ("组里的行A", "组里的行B"):
        row = TableRow()
        cell = TableCell(valuetype="string")
        cell.addElement(P(text=text))
        row.addElement(cell)
        group.addElement(row)
    table.addElement(group)
    row = TableRow()
    cell = TableCell(valuetype="string")
    cell.addElement(P(text="直属的行C"))
    row.addElement(cell)
    table.addElement(row)
    document.text.addElement(table)
    path = work_dir / "row_group.odt"
    document.save(str(path))

    content = dispatch("odt", path, ctx).content
    for text in ("组里的行A", "组里的行B", "直属的行C"):
        assert text in content, f"{text} 没进正文：{content!r}"


@pytest.mark.capability_table
def test_docx_with_two_tables_leaves_the_header_count_empty(ctx, work_dir):
    """
    行为：一份 docx 里有**两张**表时不给 `table_header_rows`，并在 `notes` 里说明有几张。

    来源：字段说明（交付文本不止一张表时 `null`）+ 网页那一路同形的 `notes` 文案。
    预期值来源：本用例写两张各一行的表（都标了 `w:tblHeader`），必然落到「不止一张表」这条分支。
    """
    from docx import Document
    from docx.oxml.ns import qn

    document = Document()
    for label in ("甲表头", "乙表头"):
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = label
        tr_pr = table.rows[0]._tr.get_or_add_trPr()
        marker = tr_pr.makeelement(qn("w:tblHeader"), {})
        marker.set(qn("w:val"), "true")
        tr_pr.append(marker)
    path = work_dir / "two_tables.docx"
    document.save(str(path))

    result = dispatch("docx", path, ctx)
    assert result.table_header_rows is None, "交付文本不止一张表时不给值"
    assert any("2 张表" in note for note in result.notes), result.notes


# ---------- 两处「未核实」的实测固化（01 §4.1 / §4.5） ----------

@pytest.mark.capability_table
def test_xlsx_without_a_covered_cell_element_keeps_its_column(ctx, work_dir):
    """
    行为：产出方**不给覆盖位写 `<c>`** 时，合并区后面的值仍落在源列上（`合并标题\\t\\tc1`）。

    来源：`01-表格逻辑格子建模.md` §4.1 的未核实项——本仓 `_sheet_grid` 按列号落位，隐含依赖
          「覆盖位在值矩阵里占一个位置」。实测：openpyxl 自己写出来的 xlsx **就不写** B1 的 `<c>`
          （本用例先断言这一前提），而读取侧仍按合并矩形把 B1 标成覆盖位，所以列位不左移。
    预期值来源：本用例写 A1:B1 合并 + C1、A2/B2，形状即预期（第一行第 3 列是 c1）。
    """
    import openpyxl
    import zipfile

    path = work_dir / "no_covered_cell.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet["A1"] = "合并标题"
    sheet["C1"] = "c1"
    sheet["A2"] = "a2"
    sheet["B2"] = "b2"
    sheet.merge_cells("A1:B1")
    book.save(str(path))

    with zipfile.ZipFile(path) as package:
        sheet_xml = package.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert 'r="B1"' not in sheet_xml, "前提变了：产出方开始给覆盖位写 <c> 了，这条核实要重做"

    lines = [line for line in dispatch("xlsx", path, ctx).content.splitlines() if "\t" in line]
    assert lines[0] == "合并标题\t\tc1", lines
    assert lines[1] == "a2\tb2\t", lines


@pytest.mark.capability_table
def test_ods_repeated_columns_keep_the_merge_coordinates(ctx, work_dir):
    """
    行为：多张表 + `number-columns-repeated` 补位时，合并区坐标仍按展开后的列号算——
          「补位」重复 3 次后，后面的文字落在第 4 列；跨列表的覆盖位不左移。

    来源：`01-表格逻辑格子建模.md` §4.5 的未核实项（ODS 合并区的重复列游标假设）；`align_ods_values`
          依赖 `read_merge_rects` 的坐标与 pyexcel-ods 的展开口径一致。
    预期值来源：本用例写两张表——表一「A1:B1 合并 + C1」，表二「3 个重复空格 + 四列」「尾合并 跨 2 列 + d2」。
    """
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import CoveredTableCell, Table, TableCell, TableRow
    from odf.text import P

    def cell(text, span=1, repeat=1):
        attributes = {"valuetype": "string"} if text else {}
        if span > 1:
            attributes["numbercolumnsspanned"] = span
        if repeat > 1:
            attributes["numbercolumnsrepeated"] = repeat
        node = TableCell(**attributes)
        if text:
            node.addElement(P(text=text))
        return node

    document = OpenDocumentSpreadsheet()
    first = Table(name="表一")
    row = TableRow()
    row.addElement(cell("合并标题", span=2))
    row.addElement(CoveredTableCell())
    row.addElement(cell("c1"))
    first.addElement(row)
    document.spreadsheet.addElement(first)
    second = Table(name="表二")
    row = TableRow()
    row.addElement(cell("补位", repeat=3))
    row.addElement(cell("四列"))
    second.addElement(row)
    row = TableRow()
    row.addElement(cell("尾合并", span=2))
    row.addElement(CoveredTableCell())
    row.addElement(cell("d2"))
    second.addElement(row)
    document.spreadsheet.addElement(second)
    path = work_dir / "repeat_columns.ods"
    document.save(str(path))

    lines = [line for line in dispatch("ods", path, ctx).content.splitlines() if "\t" in line]
    assert lines[0] == "合并标题\t\tc1", lines
    assert lines[1] == "补位\t补位\t补位\t四列", lines
    assert lines[2] == "尾合并\t\td2\t", lines


# ---------- 隐藏内容：docx 隐藏文字 / 隐藏幻灯片 / 网页隐藏元素 ----------

@pytest.mark.capability_document
def test_hidden_docx_text_is_extracted_and_registered(ctx, work_dir):
    """
    行为：docx 的隐藏文字（`w:rPr/w:vanish`）照进正文，并在 `notes` 里登记处数与字数。

    来源：用户 2026-10-04 拍板「隐藏内容一律照抽」（`01-表格逻辑格子建模.md` §1.2）；
          python-docx 的 `run.font.hidden = True` 写的就是 `w:vanish`。
    预期值来源：本用例写 1 段普通文字 + 1 处隐藏文字「隐藏文字样本XYZ」（9 字）。
    """
    from docx import Document

    path = work_dir / "hidden_text.docx"
    document = Document()
    document.add_paragraph("普通段落")
    document.add_paragraph().add_run("隐藏文字样本XYZ").font.hidden = True
    document.save(str(path))

    result = dispatch("docx", path, ctx)
    assert "隐藏文字样本XYZ" in result.content
    assert any("1 处隐藏文字" in note and "共 9 字" in note for note in result.notes), result.notes


@pytest.mark.capability_document
def test_hidden_pptx_slide_is_extracted_and_registered(ctx, work_dir):
    """
    行为：隐藏幻灯片（`p:sld@show="0"`）的正文照进交付文本，`notes` 里登记是哪一张。

    来源：同 §1.2——隐藏只是放映时跳过，不是没有内容。
    预期值来源：本用例写 3 页、只把第 2 页标成隐藏，所以登记的应当是「第 2 张」且第 2 页正文仍在。
    """
    from pptx import Presentation

    path = work_dir / "hidden_slide.pptx"
    presentation = Presentation()
    for index in range(3):
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = f"第{index + 1}页标题"
        if index == 1:
            slide.element.set("show", "0")
    presentation.save(str(path))

    result = dispatch("pptx", path, ctx)
    assert "第2页标题" in result.content
    assert any("第 2 张幻灯片是隐藏的" in note for note in result.notes), result.notes


@pytest.mark.capability_document
def test_hidden_html_content_is_extracted_and_registered(ctx, work_dir):
    """
    行为：`display:none` 与 `hidden` 属性里的文字照进正文，`notes` 登记处数与字数（嵌套只算一处）。

    来源：同 §1.2。预期值来源：本用例写 1 处 `display:none`（外层 div 套内层 span，只算一处、9 字）
          + 1 处 `hidden` 属性（「属性隐藏的段落」7 字），合计 2 处 16 字。
    """
    path = work_dir / "hidden.html"
    path.write_text(
        "<html><body><p>普通段落</p>"
        "<div style='display:none'><span>隐藏文字样本XYZ</span></div>"
        "<p hidden>属性隐藏的段落</p></body></html>",
        encoding="utf-8",
    )

    result = dispatch("html", path, ctx)
    assert "隐藏文字样本XYZ" in result.content
    assert "属性隐藏的段落" in result.content
    assert any("2 处隐藏文字" in note and "共 16 字" in note for note in result.notes), result.notes


# ---------- 表类型：data / layout ----------

@pytest.mark.capability_table
def test_single_cell_table_is_reported_as_layout(ctx, work_dir):
    """
    行为：1×1 的单格表算 `layout`（排版壳），多格表算 `data`——口径照 anydoc 的 `TableKind`。

    来源：`processors/table_kind.py` 的模块 docstring（anydoc 的 `Layout` 只在它自己的单元测试里
          被构造过，唯一判据是 `is_single_cell()`）；`routers/convert/schema.py` 的字段说明。
    预期值来源：本用例写两份 docx——一份 1×1、一份 2×2，形状是手写的常量。
    """
    from docx import Document

    single = work_dir / "single_cell.docx"
    document = Document()
    document.add_table(rows=1, cols=1).cell(0, 0).text = "只有一个格子"
    document.save(str(single))
    assert dispatch("docx", single, ctx).table_kind == "layout"

    grid = work_dir / "two_by_two.docx"
    document = Document()
    table = document.add_table(rows=2, cols=2)
    for row_index, values in enumerate((("甲", "乙"), ("1", "2"))):
        for column_index, value in enumerate(values):
            table.cell(row_index, column_index).text = value
    document.save(str(grid))
    assert dispatch("docx", grid, ctx).table_kind == "data"


@pytest.mark.capability_table
def test_table_kind_comes_from_the_web_table_shape(ctx, work_dir):
    """
    行为：网页那一路同样给表类型——单张多格表 `data`，单张单格表 `layout`。

    来源：同 `table_kind.py`；`html_tables.table_to_tsv` 用渲染好的行判，四路（docx / odf / 网页 /
          EPUB）口径一致。
    预期值来源：本用例手写两张页面（2×2 与 1×1），形状即预期。
    """
    path = work_dir / "grid.html"
    path.write_text(
        "<html><body><table><tr><td>甲</td><td>乙</td></tr><tr><td>1</td><td>2</td></tr></table></body></html>",
        encoding="utf-8",
    )
    assert dispatch("html", path, ctx).table_kind == "data"

    single = work_dir / "single.html"
    single.write_text("<html><body><table><tr><td>只有一个格子</td></tr></table></body></html>", encoding="utf-8")
    assert dispatch("html", single, ctx).table_kind == "layout"


@pytest.mark.capability_table
def test_table_kind_is_empty_when_the_text_has_two_tables(ctx, work_dir):
    """
    行为：交付文本里有**两张**表时不给表类型（`null`）——「唯一一张表」这条门槛与表头行数同源。

    来源：`routers/convert/schema.py` 的字段说明（没有表、有多张表时为 null）。
    预期值来源：本用例写两张 2×2 的 docx 表，必然落到「不止一张表」这条分支。
    """
    from docx import Document

    path = work_dir / "two_tables.docx"
    document = Document()
    for label in ("甲", "乙"):
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = label
        table.cell(1, 1).text = "1"
    document.save(str(path))

    result = dispatch("docx", path, ctx)
    assert result.table_kind is None, result.table_kind
    assert result.table_header_rows is None, "同一门槛：两张表时表头行数也不给值"


@pytest.mark.capability_table
def test_odf_inline_empty_cells_still_hold_their_columns(ctx, work_dir):
    """
    行为：行内（非行尾）的空单元格仍然落格——它后面的文字落在**源列**上，不被左移。

    来源：`odf._flush_gap` 的策略（anydoc `src/formats/odf/table.rs:324-336`：行内空格的缓冲在遇到
          下一个非平凡格时 flush，行尾的才丢）。这是「尾部空格省掉」不能踩坏的那一半。
    预期值来源：本用例写一行 `空 / 空 / 甲`（两个空 `table:table-cell` 在前），所以「甲」必须出现在
          第 3 列——渲染成 `\\t\\t甲`（两个前导制表符）。
    """
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    document = OpenDocumentText()
    table = Table(name="行内空格")
    row = TableRow()
    for text in ("", "", "甲"):
        cell = TableCell(valuetype="string")
        if text:
            cell.addElement(P(text=text))
        row.addElement(cell)
    table.addElement(row)
    document.text.addElement(table)
    path = work_dir / "inline_gap.odt"
    document.save(str(path))

    line = [item for item in dispatch("odt", path, ctx).content.splitlines() if item.strip()][0]
    assert line == "\t\t甲", f"行内空列必须占住列位：{line!r}"


@pytest.mark.capability_limits
def test_odf_trailing_empty_cells_do_not_consume_the_slot_budget(monkeypatch, ctx, work_dir):
    """
    行为：ODF 行尾的空单元格不落格位——同样的表把 `max_grid_slots` 压到 30 也不命中。

    来源：`01-表格逻辑格子建模.md` §1.4 的省法（anydoc 的 `flush_gap`）；`odf._render_table` 现在按
          「行内缓冲、行尾丢弃」落格。
    预期值来源：本用例写 3 行、每行「1 个有字格 + 20 个行尾空格」= 修前 63 个逻辑格子、修后 3 个；
          预算 30 卡在两者之间，所以命中与否直接区分两种实现。
    """
    import configs.limits as limits
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    document = OpenDocumentText()
    table = Table(name="行尾空格")
    for index in range(3):
        row = TableRow()
        cell = TableCell(valuetype="string")
        cell.addElement(P(text=f"第{index + 1}行"))
        row.addElement(cell)
        for _ in range(20):
            row.addElement(TableCell())
        table.addElement(row)
    document.text.addElement(table)
    path = work_dir / "trailing_gap.odt"
    document.save(str(path))

    monkeypatch.setattr(limits, "MAX_GRID_SLOTS", 30)
    result = dispatch("odt", path, ctx)
    assert "max_grid_slots" not in result.limits_hit, result.limits_hit
    assert "第3行" in result.content, result.content


# ---------- 单元格里的嵌套表 / 内容控件（w:sdt） ----------

@pytest.mark.capability_table
def test_nested_table_text_survives_in_its_cell(ctx, work_dir):
    """
    行为：单元格里再嵌一张表时，嵌套表的文字进正文（每个嵌套行写成 `a / b`），外层列位仍是 2 列。

    来源：`05-对标现状对比.md` 记的「DOCX 嵌套表格文本丢失」——旧实现按 `tc.p_lst` 只取单元格的
          直属段落，而 python-docx 在嵌套表两侧插的空段落正好占掉那些位置，嵌套表一个字都读不到。
    预期值来源：本用例写外层 1×2 表、右格嵌 2×2 表（内甲/内乙、内1/内2），形状即预期。
    """
    from docx import Document

    path = work_dir / "nested.docx"
    document = Document()
    outer = document.add_table(rows=1, cols=2)
    outer.cell(0, 0).text = "外格"
    inner = outer.cell(0, 1).add_table(rows=2, cols=2)
    for row_index, values in enumerate((("内甲", "内乙"), ("内1", "内2"))):
        for column_index, value in enumerate(values):
            inner.cell(row_index, column_index).text = value
    document.save(str(path))

    result = dispatch("docx", path, ctx)
    assert "内甲 / 内乙<br>内1 / 内2" in result.content, f"嵌套表的文字没进正文：{result.content!r}"
    lines = [line for line in result.content.splitlines() if "\t" in line]
    assert {line.count("\t") for line in lines} == {1}, f"外层仍是 2 列：{lines}"


@pytest.mark.capability_table
def test_table_row_wrapped_in_content_control_is_not_dropped(ctx, work_dir):
    """
    行为：被 `w:sdt/w:sdtContent` 包住的表行照样渲染——Word 的「重复表行内容控件」就是这个形状。

    来源：同审计；python-docx 的 `table.rows` 只取 `w:tbl` 的**直属** `w:tr`（本用例实测：XML 里
          1 个直属 `w:tr` + 1 个 `w:sdt`，`table.rows` 只报 1 行）。
    预期值来源：本用例写 2 行 2 列、把第 2 行包进 `w:sdt`，所以两行的文字都要在、且列数一致。
    """
    from docx import Document
    from docx.oxml import OxmlElement

    path = work_dir / "sdt_row.docx"
    document = Document()
    table = document.add_table(rows=2, cols=2)
    for row_index, values in enumerate((("头甲", "头乙"), ("正1", "正2"))):
        for column_index, value in enumerate(values):
            table.cell(row_index, column_index).text = value
    row = table.rows[1]._tr
    container = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    table._tbl.replace(row, container)
    container.append(content)
    content.append(row)
    document.save(str(path))

    result = dispatch("docx", path, ctx)
    assert "正1\t正2" in result.content, f"被内容控件包住的行丢了：{result.content!r}"
    lines = [line for line in result.content.splitlines() if "\t" in line]
    assert {line.count("\t") for line in lines} == {1}, lines


@pytest.mark.capability_document
def test_paragraph_inside_content_control_reaches_the_text(ctx, work_dir):
    """
    行为：单元格里被 `w:sdt` 包住的段落照样进正文。

    来源：同审计——旧实现按 `tc.p_lst` 取单元格的直属段落（本用例实测取到 **0 段**）。
    预期值来源：本用例写 1×1 表、把「控件里的段落」放进 `w:sdt/w:sdtContent`。
    """
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    path = work_dir / "sdt_paragraph.docx"
    document = Document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)._tc
    for paragraph in list(cell.findall(qn("w:p"))):
        cell.remove(paragraph)
    container = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "控件里的段落"
    run.append(text)
    paragraph.append(run)
    content.append(paragraph)
    container.append(content)
    cell.append(container)
    document.save(str(path))

    assert "控件里的段落" in dispatch("docx", path, ctx).content


@pytest.mark.capability_table
def test_pptx_multiline_cell_keeps_the_row_on_one_line(ctx, work_dir):
    """
    行为：一个格里有多个段落时，格内换行压成空格——这一行仍是**一行**，与同表其他行同列数。

    来源：`excel._cell_text` / `docx._cell_text` 都是这么压的（行导向方言里换行会把一行切成两行，
          同表列数就对不上）；实测 python-pptx 的 `cell.text` 在合并后也会带出换行。
    预期值来源：本用例写 2×2 表，左上格两段（「上半」/「下半」），断言它渲染成 `上半<br>下半` 一行。
    """
    from pptx import Presentation
    from pptx.util import Inches

    path = work_dir / "multiline_cell.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "上半"
    table.cell(0, 0).text_frame.add_paragraph().text = "下半"
    table.cell(0, 1).text = "右上"
    table.cell(1, 0).text = "a2"
    table.cell(1, 1).text = "b2"
    presentation.save(str(path))

    lines = [line for line in dispatch("pptx", path, ctx).content.splitlines() if "\t" in line]
    assert {line.count("\t") for line in lines} == {1}, f"同表列数应一致：{lines}"
    assert "上半<br>下半" in lines[0], lines


@pytest.mark.capability_table
def test_pptx_text_left_in_a_covered_cell_is_merged_back(ctx, work_dir):
    """
    行为：合并区非左上格（续格）里若被产出方留下文字，文字并回合并单元格并在 `notes` 里登记。

    来源：`01-表格逻辑格子建模.md` §4.4 的未核实项——anydoc 直接丢弃续格文字，本仓取向是「尽量抽出来」。
    预期值来源：本用例写 2×2 表、把 A1:B1 合并，再把续格单独写上「续格残留字」（并清掉 origin 里的
          同名文字），所以正文里必须出现这两段、且 notes 报 1 处。
    """
    from pptx import Presentation
    from pptx.util import Inches

    path = work_dir / "residue.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "合并标题"
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "合并标题"  # 清掉 python-pptx 搬进来的续格文字，只留续格那份
    table.cell(0, 1).text = "续格残留字"
    table.cell(1, 0).text = "a2"
    table.cell(1, 1).text = "b2"
    presentation.save(str(path))

    result = dispatch("pptx", path, ctx)
    assert "合并标题 续格残留字" in result.content, result.content
    assert any("残留文字已并入合并单元格（1 处）" in note for note in result.notes), result.notes


# ---------- 格内多段：一个逻辑格子里的换行写成 `<br>`（anydoc 的 render_cell 口径） ----------

@pytest.mark.capability_table
def test_docx_multi_paragraph_cell_keeps_its_line_break(ctx, work_dir):
    """
    行为：docx 一个格里有**两段**时，交付文本里是 `<br>`——段落边界保住，列位不动。

    来源：`processors/cell_text.py` 的模块 docstring（格子方言照 anydoc 的 `render_cell`：
          `src/render/markdown/table.rs:87-101`）。改前 `re.sub(r"\\s+", " ", ...)` 把两段压成空格，
          下游分不清「地址的第二行」与「地址里的一个空格」。
    预期值来源：本用例写一张 2×2 表，右上格两段（收货地址 / 邮编），所以第一行是
          `项目\\t北京市海淀区中关村大街 1 号<br>邮编 100080`。
    """
    from docx import Document

    path = work_dir / "multiline_cell.docx"
    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "北京市海淀区中关村大街 1 号"
    table.cell(0, 1).add_paragraph("邮编 100080")
    table.cell(1, 0).text = "联系人"
    table.cell(1, 1).text = "张伟 13800000000"
    document.save(str(path))

    lines = [line for line in dispatch("docx", path, ctx).content.splitlines() if "\t" in line]
    assert lines[0] == "项目\t北京市海淀区中关村大街 1 号<br>邮编 100080", lines
    assert {line.count("\t") for line in lines} == {1}, f"同表列数必须一致：{lines}"


@pytest.mark.capability_table
def test_docx_soft_line_break_and_tab_inside_a_cell(ctx, work_dir):
    """
    行为：格内的软换行（`w:br`）也算一段边界 → `<br>`；格内的制表符仍换成空格（不能多出一列）。

    来源：同 `cell_text.py`（真制表符会凭空多一列，真换行会把这一行拆两行）。
    预期值来源：本用例手写 `w:t` / `w:br` / `w:tab` 三种行内元素，所以预期是
          `上半<br>下半 尾`（软换行成记号、制表符成空格）。
    """
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    path = work_dir / "inline_breaks.docx"
    document = Document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)._tc
    for paragraph in list(cell.findall(qn("w:p"))):
        cell.remove(paragraph)
    paragraph = OxmlElement("w:p")
    for tag, text in (("w:t", "上半"), ("w:br", ""), ("w:t", "下半"), ("w:tab", ""), ("w:t", "尾")):
        node = OxmlElement(tag)
        if text:
            node.text = text
        paragraph.append(node)
    cell.append(paragraph)
    document.save(str(path))

    lines = [line for line in dispatch("docx", path, ctx).content.splitlines() if line.strip()]
    assert lines[0] == "上半<br>下半 尾", lines


@pytest.mark.capability_table
def test_pptx_multi_paragraph_cell_keeps_its_line_break(ctx, work_dir):
    """
    行为：pptx 一个格里有多个段落时同样是 `<br>`，同表列数仍是 2 列。

    来源：同 `cell_text.py`；python-pptx 的 `cell.text` 用 `\\n` 连段落、`\\v` 连软换行。
    预期值来源：本用例写 1×2 表、左格两段（上半 / 下半），所以是 `上半<br>下半\\t右上`。
    """
    from pptx import Presentation
    from pptx.util import Inches

    path = work_dir / "multiline_cell.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "上半"
    table.cell(0, 0).text_frame.add_paragraph().text = "下半"
    table.cell(0, 1).text = "右上"
    presentation.save(str(path))

    lines = [line for line in dispatch("pptx", path, ctx).content.splitlines() if "\t" in line]
    assert lines[0] == "上半<br>下半\t右上", lines


@pytest.mark.capability_table
def test_ods_cell_with_a_newline_keeps_it_as_a_break(ctx, work_dir):
    """
    行为：电子表格一个格里写了换行（`\\n`）时同样是 `<br>`——一个格仍占一列。

    来源：同 `cell_text.py`；openpyxl 读回来的字符串里就带 `\\n`。
    预期值来源：本用例写一张 1×2 表，左格内容 `上半\\n下半`，所以是 `上半<br>下半\\t右列`。
    """
    import openpyxl

    path = work_dir / "newline_cell.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet["A1"] = "上半\n下半"
    sheet["B1"] = "右列"
    book.save(str(path))

    lines = [line for line in dispatch("xlsx", path, ctx).content.splitlines() if "\t" in line]
    assert lines[0] == "上半<br>下半\t右列", lines


@pytest.mark.capability_table
def test_web_cell_line_break_survives_as_a_marker(ctx, work_dir):
    """
    行为：网页表格里 `<td>上半<br>下半</td>` 的换行保住成 `<br>`（不压成空格、也不拆行）。

    来源：同 `cell_text.py`；`html_tables._cell_text` 先把 `<br>` 换成换行再走同一条格子方言。
    预期值来源：本用例手写一行两格，左格带 `<br>`，所以是 `上半<br>下半\\t右格`。
    """
    path = work_dir / "cell_break.html"
    path.write_text(
        "<html><body><table><tr><td>上半<br>下半</td><td>右格</td></tr></table></body></html>",
        encoding="utf-8",
    )

    lines = [line for line in dispatch("html", path, ctx).content.splitlines() if "\t" in line]
    assert lines[0] == "上半<br>下半\t右格", lines


# ---------- 网格的两条兜底：孤儿覆盖位登记 / 续格文字并回 ----------

@pytest.mark.capability_table
def test_docx_continuation_cell_text_is_merged_back(ctx, work_dir):
    """
    行为：纵向合并的**继续格**自带文字时，文字并回合并起点并在 `notes` 报处数（不再静默丢掉）。

    来源：`01-表格逻辑格子建模.md` §2.1 的决定（用户 2026-10-04：直接改代码兜底）——产出方
          （WPS / LibreOffice）有时把文字留在续格里；PPTX 那条早已改成并回，这里把 docx 对齐。
    预期值来源：本用例写 2 行 2 列、左列纵向合并（起点在第一行），再往第二行（继续格）单独写
          「写在继续格里的字」——所以它必须出现在第一行那一格里，且 notes 报「1 处」。
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import _Cell

    path = work_dir / "continue_text.docx"
    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 1).text = "选手甲"
    table.cell(1, 1).text = "选手乙"
    table.cell(0, 0).merge(table.cell(1, 0))
    rows = table._tbl.findall(qn("w:tr"))
    _Cell(rows[1].findall(qn("w:tc"))[0], table).text = "写在继续格里的字"
    document.save(str(path))

    result = dispatch("docx", path, ctx)
    lines = [line for line in result.content.splitlines() if "\t" in line]
    assert lines[0] == "写在继续格里的字\t选手甲", lines
    assert any("残留文字已并入合并单元格（1 处）" in note for note in result.notes), result.notes


@pytest.mark.capability_table
def test_docx_merge_without_its_origin_is_reported(ctx, work_dir):
    """
    行为：纵向合并块**起点行不在文件里**时（继续格没有 origin），列位照旧、并在 `notes` 报出孤儿数。

    来源：同 §2.1——Word 删掉合并块首行 / 部分复制粘贴 / 我们自己的预算裁跨度都会留下这种形状；
          今天以前它是**全仓唯一一处静默退化**，调用方分不清「文件本来没内容」与「我们没解析对」。
    预期值来源：本用例先正常合并再用 XML 删掉首个 `w:tr`，所以剩两行的左列都是无主继续格
          → 孤儿 2 个；右列两格文字照旧在。
    """
    from docx import Document
    from docx.oxml.ns import qn

    path = work_dir / "orphan_merge.docx"
    document = Document()
    table = document.add_table(rows=3, cols=2)
    for row_index, (left, right) in enumerate((("组别 A", "选手甲"), ("", "选手乙"), ("", "选手丙"))):
        if left:
            table.cell(row_index, 0).text = left
        table.cell(row_index, 1).text = right
    table.cell(0, 0).merge(table.cell(2, 0))
    table._tbl.remove(table._tbl.findall(qn("w:tr"))[0])
    document.save(str(path))

    result = dispatch("docx", path, ctx)
    lines = [line for line in result.content.splitlines() if "\t" in line]
    assert lines == ["\t选手乙", "\t选手丙"], lines
    assert any("有 2 个覆盖位没有对应的合并起点" in note for note in result.notes), result.notes


@pytest.mark.capability_table
def test_odf_covered_cell_without_a_span_is_reported(ctx, work_dir):
    """
    行为：ODS / ODT 里出现「没有跨度认领的 `covered-table-cell`」时同样登记（同一份网格逻辑）。

    来源：同 §2.1——孤儿计数在 `GridBuilder` 上，四条表格路径共用，所以 ODF 侧也报得出来。
    预期值来源：本用例写一张单列表，第一格直接是 `table:covered-table-cell`（没有起点），所以
          第一行第一列是空逻辑格子、notes 报 1 个孤儿。
    """
    from odf.opendocument import OpenDocumentText
    from odf.table import CoveredTableCell, Table, TableCell, TableRow
    from odf.text import P

    document = OpenDocumentText()
    table = Table(name="孤儿覆盖位")
    row = TableRow()
    row.addElement(CoveredTableCell())
    cell = TableCell(valuetype="string")
    cell.addElement(P(text="右格"))
    row.addElement(cell)
    table.addElement(row)
    document.text.addElement(table)
    path = work_dir / "orphan_covered.odt"
    document.save(str(path))

    result = dispatch("odt", path, ctx)
    assert any("有 1 个覆盖位没有对应的合并起点" in note for note in result.notes), result.notes


# ---------- 内嵌图：同一张图被引多次 ----------

# 样例生成参数（`scripts/make_fixtures.py` 阶段 14）：photo.png 被引 3 次、screenshot.png 被引 1 次，
# 即 4 处引用、2 张不同的图。默认上限 `MAX_EMBEDDED_IMAGES=3`，修前正好被重复引用占满。
REPEATED_FIXTURES = ("repeated.docx", "repeated.pptx", "repeated.pdf")
REPEATED_REFERENCES = 4
REPEATED_DISTINCT = 2


@pytest.mark.capability_media
@pytest.mark.parametrize("name", REPEATED_FIXTURES)
def test_repeated_image_is_saved_once_and_does_not_starve_others(name, asset_ctx, fixture_copy):
    """
    行为：同一张图被引 N 次只落一次盘，`MAX_EMBEDDED_IMAGES` 记在**不同的图**上——
           重复引用既不留下内容相同的多份素材，也不把后面的图挤到上限之外。

    来源：`docs/pipelines.md`「内嵌图：一次请求带上全部图（受 MAX_EMBEDDED_IMAGES 限制）」
    + 样例生成参数（阶段 14：4 处引用、2 张不同的图，上限 3）。
    实测（2026-10-04）：修前三个格式都按「出现次序」占张数上限——前 3 处引用全是同一张 photo.png，
    于是落下 3 份内容完全相同的文件，而 screenshot.png 一次都没被读到。
    `images` 仍是**引用次数**（docx 数 `a:blip`、pdf 数 `get_images`、pptx 数图片形状），
    这不在本轮改动范围内，用第一条断言把它钉住。
    """
    result = run(name, asset_ctx, fixture_copy)
    assert result.images == REPEATED_REFERENCES, "内嵌图总数按引用次数报出"
    assert len(result.assets) == REPEATED_DISTINCT, (
        f"同一张图被引 3 次不该落 3 份：实际 {len(result.assets)} 份"
    )
    assert result.images_used == REPEATED_DISTINCT
    blobs = {asset.path.read_bytes() for asset in result.assets}
    assert len(blobs) == REPEATED_DISTINCT, "落盘的素材得是 2 张不同的图，而不是同一张重复"


@pytest.mark.capability_table
def test_merge_rect_normalises_row_first_half_open_input():
    """
    行为：`grid.merge_rect` 把「行在前、开区间」的输入原样归一成半开矩形，起止写反时对调，
          退化矩形返回 None。

    来源：`01-表格逻辑格子建模.md` 的 D.2——openpyxl 是闭区间且列在前、xlrd 是开区间且行在前，
          两条路径不能共用一个换算，所以统一到 `grid.merge_rect` 的半开形态。
    预期值来源：手算常量（xlrd 的 `(rlo, rhi, clo, chi) = (1, 3, 0, 1)` 表示 A2:A3）。
    """
    from utils.excel.grid import merge_rect

    assert merge_rect(1, 0, 3, 1) == (1, 0, 3, 1), "开区间、行在前的输入应原样通过"
    assert merge_rect(3, 1, 1, 0) == (1, 0, 3, 1), "起止写反时应对调"
    assert merge_rect(1, 0, 1, 1) is None, "退化的空矩形应返回 None，由调用方过滤"


# ---------- ODT：按结构走，而不是把段落拉平 ----------

# 样例生成参数（`scripts/make_fixtures.py` 阶段 15）：3 列 4 行的表（A1:B1 横向合并、
# A2:A3 纵向合并、末行末尾是 `number-columns-repeated` 的空格补位）+ 一个两级列表；
# 合并文本沿用阶段 13 那两个固定串。
ODT_STRUCTURED_FIXTURE = "structured.odt"
ODT_STRUCTURED_TABLE_ROWS = 4
ODT_LIST_PARENT = "组别设置"
ODT_LIST_CHILD = "十公里组"


@pytest.mark.capability_table
def test_odt_table_keeps_columns_instead_of_one_paragraph_per_line(ctx, fixture_copy):
    """
    行为：ODT 表格按行列渲成制表符分隔的行，合并区文本只出现一次。

    来源：`docs/pipelines.md` 的 odt 行（ODF 表格格位接入）
    （跨度 → `place(span)`、`covered-table-cell` → `covered()`、
    `number-columns-repeated` → 循环落位并记账）。
    实测（2026-10-04）：原来全局 `root.iter()` 收 `text:p`，每个单元格各自成一行、不含制表符，
    列对应关系完全丢失；接上表格逻辑格子后同一张表每行都是 3 列。
    """
    result = run(ODT_STRUCTURED_FIXTURE, ctx, fixture_copy)
    table_lines = [line for line in _table_lines(result.content) if "\t" in line]
    assert len(table_lines) == ODT_STRUCTURED_TABLE_ROWS, (
        f"表格应渲成 {ODT_STRUCTURED_TABLE_ROWS} 行制表符文本（单元格不该各自成行）：{table_lines!r}"
    )
    assert {line.count("\t") for line in table_lines} == {MERGED_COLUMNS - 1}, (
        f"表内行宽不一致：{sorted(line.count(chr(9)) for line in table_lines)}"
    )
    assert result.content.count(MERGED_TITLE) == 1, "横向合并的文本重复了"
    assert result.content.count(MERGED_VERTICAL) == 1, "纵向合并的文本重复了"


@pytest.mark.capability_document
def test_odt_nested_list_keeps_its_levels(ctx, fixture_copy):
    """
    行为：嵌套列表的子项比父项多一层缩进，层级不因抽取而消失。

    来源：`CONTEXT.md`「交付文本」——层级是这份文档真实的结构信息，拉平就等于丢掉它。
    实测（2026-10-04）：原来全局 `root.iter()` 把所有 `text:p` 拉平，父子项长得一模一样。
    """
    lines = _table_lines(run(ODT_STRUCTURED_FIXTURE, ctx, fixture_copy).content)
    assert f"- {ODT_LIST_PARENT}" in lines, f"父项没按列表渲出来：{lines!r}"
    assert f"  - {ODT_LIST_CHILD}" in lines, f"子项没有深一层：{lines!r}"


# ---------- 上界命中：名字进 limits_hit，文案只有一处模板 ----------

@pytest.mark.capability_limits
def test_html_reports_the_link_limit(ctx, work_dir):
    """
    行为：链接数超上限时，命中的上界名进 `limits_hit`。

    来源：`configs/limits.py` 的 `max_links`（100，强制点 `html.extract_html`）。
    预期值来源：本用例自己写的 HTML——150 条互不相同的链接，不依赖对实现的任何理解。
    """
    anchors = "".join(f'<a href="https://example.com/{index}">链接{index}</a>' for index in range(150))
    path = work_dir / "many_links.html"
    path.write_text(f"<html><body>{anchors}</body></html>", encoding="utf-8")
    assert dispatch("html", path, ctx).limits_hit == ["max_links"]


@pytest.mark.capability_limits
def test_archive_reports_the_entry_limit(ctx, work_dir):
    """
    行为：条目数超上限时名字进 `limits_hit`，条目总数照报。

    来源：`configs/limits.py` 的 `max_entries`（500，强制点 `archive.entries_to_content`）。
    预期值来源：本用例自己写的 ZIP——600 个条目。
    """
    import zipfile

    path = work_dir / "many_entries.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(600):
            archive.writestr(f"entry_{index:04d}.txt", "x")
    result = dispatch("zip", path, ctx)
    assert result.limits_hit == ["max_entries"]
    assert result.entry_count == 600


@pytest.mark.capability_limits
def test_spreadsheet_reports_the_row_limit(ctx, work_dir):
    """
    行为：单表行数超上限时名字进 `limits_hit`。

    来源：`configs/limits.py` 的 `max_rows_per_sheet`（5000，强制点 `excel._render_rows`）。
    预期值来源：本用例自己写的 CSV——6000 行；读取层多读一行（`MAX_ROWS_TO_READ=5001`），
    所以渲染层看到的行数确实超过上限。
    """
    rows = "\n".join(f"{index},值{index}" for index in range(6000))
    path = work_dir / "many_rows.csv"
    path.write_text("序号,值\n" + rows + "\n", encoding="utf-8")
    assert dispatch("csv", path, ctx).limits_hit == ["max_rows_per_sheet"]


@pytest.mark.capability_limits
def test_pptx_reports_the_image_limit_instead_of_failing(monkeypatch, asset_ctx, fixture_copy):
    """
    行为：PPTX 的不同内嵌图超过张数上限时，命中名字进 `limits_hit`，抽取照常完成而不是崩。

    来源：`configs/limits.py` 的 `max_embedded_images` + 「命中上界必须机器可读」。
    预期值来源：样例 `repeated.pptx` 的生成参数（阶段 14：photo 被引 2 次 + screenshot 1 次，
               即 2 张**不同**的图）与把上限压到 1。
    实测：2026-10-04 审计查出的真缺陷——修前这里抛 `NameError`（`pptx.py` 引用了那时还不存在的
    `result`），是「崩」而不是「丢内容」；判定这条是否还有效，把上限压到 1 跑一遍即可。
    """
    from configs import settings

    monkeypatch.setattr(settings, "MAX_EMBEDDED_IMAGES", 1)
    result = run("repeated.pptx", asset_ctx, fixture_copy)
    assert not result.degraded, result.degrade_reason
    assert "max_embedded_images" in result.limits_hit
    assert result.images_used == 1
    assert result.content.strip(), "命中上限不该影响正文"


@pytest.mark.capability_limits
def test_grid_expansion_limit_is_reported(monkeypatch, ctx, fixture_copy):
    """
    行为：格位展开超过上限时只停展开、把名字记进 `limits_hit`，抽取仍是正常结果（不降级）。

    来源：`01-表格逻辑格子建模.md` 的 D.6-4（「把上限临时调成 1 跑一次」）+ ADR-0003（降级是能力边界，
          网格超限不该让整条请求变成错误）。
    预期值来源：样例 `merged.docx` 有横向与纵向各一处跨度（各多占一个覆盖位），上限压到 1 必然命中。
    """
    import configs.limits as limits

    monkeypatch.setattr(limits, "MAX_GRID_EXPANSION", 1)
    result = run("merged.docx", ctx, fixture_copy)
    assert "max_grid_expansion" in result.limits_hit, result.limits_hit
    assert result.degraded is False, result.degrade_reason
    assert result.content.strip(), "命中上限不该把整张表丢掉"


@pytest.mark.capability_limits
def test_grid_expansion_limit_is_absent_when_not_hit(ctx, fixture_copy):
    """
    行为：上限没命中时它不出现在 `limits_hit` 里——免得这条上报退化成常量。

    来源：同上；`limits_hit` 的语义是「本次真的命中了哪条上界」。
    预期值来源：同一个样例在默认上限下跑一次（样例规模远小于上限）。
    """
    result = run("merged.docx", ctx, fixture_copy)
    assert "max_grid_expansion" not in result.limits_hit, result.limits_hit


@pytest.mark.capability_limits
def test_grid_slot_budget_is_reported(monkeypatch, ctx, fixture_copy):
    """
    行为：单张表的逻辑格子数超过 `max_grid_slots` 时，其余单元格不落位、名字进 `limits_hit`，请求仍正常。

    来源：`configs/limits.py` 的 `max_grid_slots`（2026-10-04 新增：实测一个逻辑格子约 100 字节，
          100 万逻辑格子约 100 MB；它挡的是列数无上限的畸形表）+ ADR-0003（降级是能力边界）。
    预期值来源：`merged.xlsx` 第一张表是 4 行 × 3 列 = 12 个逻辑格子，把预算压到 3 必然命中。
    """
    import configs.limits as limits

    monkeypatch.setattr(limits, "MAX_GRID_SLOTS", 3)
    result = run("merged.xlsx", ctx, fixture_copy)
    assert "max_grid_slots" in result.limits_hit, result.limits_hit
    assert result.degraded is False, result.degrade_reason


@pytest.mark.capability_limits
def test_grid_text_budget_is_reported(monkeypatch, ctx, fixture_copy):
    """
    行为：单张表落进去的文字总量超过 `max_grid_text_chars` 时同样截断并报名字。

    来源：`configs/limits.py` 的 `max_grid_text_chars`（2026-10-04 新增：实测 2000 行 × 50 列 × 500 字符
          会渲染出 5000 万字符、峰值 60 MB，而交付侧的 `max_content_chars` 是组装时事后截断）。
    预期值来源：`sample.tsv` 的单元格文字远多于 1 个字符，把预算压到 1 必然命中。
    """
    import configs.limits as limits

    monkeypatch.setattr(limits, "MAX_GRID_TEXT_CHARS", 1)
    result = run("sample.tsv", ctx, fixture_copy)
    assert "max_grid_text_chars" in result.limits_hit, result.limits_hit
    assert result.degraded is False, result.degrade_reason


@pytest.mark.capability_limits
@pytest.mark.parametrize("name", ["merged.xlsx", "merged.pptx"])
def test_spreadsheet_and_slide_paths_report_the_grid_expansion_limit(monkeypatch, name, ctx, fixture_copy):
    """
    行为：`.xlsx` 与 `.pptx` 的表格也要上报 `max_grid_expansion`——今天只有 docx / odf 报。

    来源：`01-表格逻辑格子建模.md` 的 D.5「命中上限必须机器可读」；实测（2026-10-04）发现
          `_sheet_grid` 与 `_table_text` 只交网格、不交命中的名字，于是这两条路的上界**静默**。
    预期值来源：两个样例各有合并区（各一处跨度），把展开预算压到 1 必然命中。
    """
    import configs.limits as limits

    monkeypatch.setattr(limits, "MAX_GRID_EXPANSION", 1)
    result = run(name, ctx, fixture_copy)
    assert "max_grid_expansion" in result.limits_hit, result.limits_hit
    assert result.content.strip(), "命中上界不该把整张表丢掉"


# ---------- 隐藏工作表与隐藏行列：内容照抽、可见性登记 ----------

@pytest.mark.capability_table
def test_hidden_sheets_are_still_extracted(ctx, fixture_copy):
    """
    行为：整表隐藏（hidden / veryHidden）的工作表里的文字照样进正文——隐藏只是界面上没显示。

    来源：用户 2026-10-04 拍板（`01-表格逻辑格子建模.md` §1.2）。修前 `excel.py` 会整张跳过
          `sheet_state != "visible"` 的表：`hidden.xlsx` 的 3 张表只抽到 1 张。
    预期值来源：`scripts/make_fixtures.py` 阶段 21 写死的内容——两张隐藏表各一格文字 + 表数 3。
    """
    result = run("hidden.xlsx", ctx, fixture_copy)
    assert "隐藏表的格子" in result.content
    assert "非常隐藏表的格子" in result.content
    assert result.pages == 3, result.pages
    assert any("「隐藏表」整表为 hidden" in note for note in result.notes), result.notes
    assert any("「非常隐藏表」整表为 veryHidden" in note for note in result.notes), result.notes
    assert result.degraded is False, result.degrade_reason


@pytest.mark.capability_table
def test_hidden_rows_and_columns_are_still_extracted(ctx, fixture_copy):
    """
    行为：隐藏行、隐藏列里的格子照样进正文，并把「哪几行哪几列被隐藏」登记进 notes。

    来源：同 §1.2——隐藏行列本来就是照抽的，缺的是登记（不静默）。
    预期值来源：阶段 21 的样例生成参数：0 基隐藏行 (1, 3, 4) 即 1 基的第 2、4-5 行，隐藏列 (1) 即 B 列。
    """
    result = run("hidden.xlsx", ctx, fixture_copy)
    for text in ("隐藏行里的格子", "隐藏行列交叉", "隐藏连续行一", "隐藏连续行二"):
        assert text in result.content, f"隐藏格子 {text!r} 没进正文：{result.content!r}"
    assert any(
        "隐藏行（第 2 行、第 4-5 行）" in note and "隐藏列（B 列）" in note for note in result.notes
    ), result.notes


@pytest.mark.capability_table
def test_hidden_visibility_is_registered_for_legacy_xls(ctx, fixture_copy):
    """
    行为：老版 .xls 走另一条读取路径（BOUNDSHEET 的可见性编码 + `rowinfo_map` / `colinfo_map`），
          隐藏表与隐藏行列同样照抽、同样登记。

    来源：同 §1.2；`hidden.xls` 与 `hidden.xlsx` 是同一形状的两种容器，产出的正文应当一致。
    预期值来源：阶段 21 的样例生成参数（同 xlsx 那份：隐藏第 2、4-5 行与 B 列、一张隐藏表）。
    """
    result = run("hidden.xls", ctx, fixture_copy)
    assert "隐藏表的格子" in result.content
    assert "隐藏行列交叉" in result.content
    assert result.pages == 2, result.pages
    assert any("「隐藏表」整表为 hidden" in note for note in result.notes), result.notes
    assert any("隐藏行（第 2 行、第 4-5 行）" in note and "隐藏列（B 列）" in note for note in result.notes), result.notes


@pytest.mark.capability_table
def test_hidden_ods_table_content_survives(ctx, fixture_copy):
    """
    行为：ODS 的隐藏是**样式**说了算（整表 `table:display="false"`、行列 `table:visibility="collapse"`）——
          隐藏表与隐藏行列的内容照样进正文，可见性同样登记进 `notes`。

    来源：用户 2026-10-04 拍板「隐藏内容一律照抽」（`01-表格逻辑格子建模.md` §1.2）；
          `pyexcel-ods` 只给值矩阵、样式一概丢掉，所以可见性由 `excel.read_ods_visibility`
          解 `content.xml` 的自动样式表读出来。
    预期值来源：阶段 21 的生成参数——与 `hidden.xlsx` / `hidden.xls` 同一组常量（隐藏第 2、4-5 行与 B 列、
          另有一张 hidden 表）。
    """
    result = run("hidden.ods", ctx, fixture_copy)
    assert "隐藏表的格子" in result.content
    assert "隐藏行列交叉" in result.content
    assert result.pages == 2, result.pages
    assert any("「隐藏表」整表为 hidden" in note for note in result.notes), result.notes
    assert any("隐藏行（第 2 行、第 4-5 行）" in note and "隐藏列（B 列）" in note for note in result.notes), result.notes


@pytest.mark.capability_table
def test_hidden_visibility_matches_across_table_containers(ctx, fixture_copy):
    """
    行为：同一份形状（隐藏第 2、4-5 行与 B 列）在 xlsx / xls / ods 三种容器里给出**同一段正文**
          与同一条隐藏行列登记——列位与可见性口径不因容器而变。

    来源：`excel.py` 的三条读取路径归到同一份渲染（`_sheet_grid` + `render_tsv`）与同一条登记
          （`_visibility_notes`）；三个容器的可见性来源各不相同（包内 XML / xlrd 映射 / ODF 样式）。
    预期值来源：阶段 21 的三个样例由同一组常量生成（`HIDDEN_CELLS` / `HIDDEN_ROW_INDEXES` /
          `HIDDEN_COLUMN_INDEXES`），所以「可见表」那一段必须逐字节相同。
    """
    blocks, notes = {}, {}
    for name in ("hidden.xlsx", "hidden.xls", "hidden.ods"):
        result = run(name, ctx, fixture_copy)
        blocks[name] = result.content.split("=== SHEET: 隐藏表 ===")[0]
        notes[name] = tuple(note for note in result.notes if "隐藏行" in note)
    assert len(set(blocks.values())) == 1, blocks
    assert len(set(notes.values())) == 1, notes


@pytest.mark.capability_limits
def test_hidden_row_registration_is_capped_and_reported(ctx, work_dir):
    """
    行为：隐藏行的连续段超过 `max_visibility_ranges` 时只登记前若干段并报出上界名；正文照抽不受影响。

    来源：`configs/limits.py` 的 `max_visibility_ranges`（2026-10-04 新增——备注的篇幅也要有上界，
          否则「隔行隐藏」这种畸形文件能把 notes 写爆；它钳的只是登记，内容一律照抽）。
    预期值来源：本用例写 25 个「各自单独一段」的隐藏行（第 1、3、5…49 行），必然超过 24 段。
    """
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "隔行隐藏"
    for row_index in range(1, 50, 2):
        sheet.cell(row=row_index, column=1, value=f"第{row_index}行")
        sheet.row_dimensions[row_index].hidden = True
    path = work_dir / "hidden_runs.xlsx"
    book.save(str(path))

    result = dispatch("xlsx", path, ctx)
    assert "max_visibility_ranges" in result.limits_hit, result.limits_hit
    assert "第49行" in result.content, "命中的是登记的上界，正文不该跟着少内容"
    assert any("等 25 段" in note for note in result.notes), result.notes


# ---------- CSV 分隔符嗅探 ----------

CSV_DIALECTS = ((";", "分号"), ("\t", "制表符"), ("|", "竖线"))


@pytest.mark.capability_table
@pytest.mark.parametrize(("delimiter", "label"), CSV_DIALECTS)
def test_csv_delimiter_is_sniffed_instead_of_assumed_comma(delimiter, label, ctx, work_dir):
    """
    行为：分号 / 制表符 / 竖线分隔的 CSV 也按列渲成制表符表格，而不是被读成一列。

    来源：`excel.py` 模块 docstring「按列位置输出制表符，空列留空；列一错位整张表就读不出
    对应关系」+ `docs/pipelines.md` 的嗅探规则。
    预期值来源：本用例自己写入的三行两列内容，与实现无关。
    """
    path = work_dir / "dialect.csv"
    path.write_text(f"名称{delimiter}数量\n甲{delimiter}1\n乙{delimiter}2\n", encoding="utf-8")
    lines = [line for line in dispatch("csv", path, ctx).content.splitlines() if not line.startswith("===")]
    assert lines == ["名称\t数量", "甲\t1", "乙\t2"], f"{label}分隔的 CSV 被读错了"


@pytest.mark.capability_table
def test_a_comma_csv_keeps_its_exact_old_output(ctx, work_dir):
    """
    行为：逗号 CSV 的产出逐字节不变——平局时逗号优先，且字段里出现的制表符 / 分号 / 竖线
          不会把列重新切开（它们就是普通字符）。

    来源：`docs/pipelines.md` 的嗅探规则「列数 > 1 且各行列数最一致，平局逗号优先」。
    预期值来源：本用例自己写入的三行内容（字段故意塞进另外三个候选分隔符）。
    """
    path = work_dir / "comma.csv"
    path.write_text("名称,数量\n甲;乙,1\n丙|丁,2\n", encoding="utf-8")
    assert dispatch("csv", path, ctx).content == (
        "=== SHEET: comma ===\n名称\t数量\n甲;乙\t1\n丙|丁\t2"
    )


@pytest.mark.capability_table
def test_the_comma_sample_keeps_its_output_byte_for_byte(ctx, fixture_copy):
    """
    行为：仓库自带的逗号样例 `table.csv` 产出逐字节不变。

    来源：`excel.py`「只有一套表格方言」+ 嗅探规则「平局逗号优先」。
    预期值来源：样例自身的内容（7 行、每行 5 个逗号字段）与渲染规则（列数取全表最宽——表头
    的 5 个字段就定下了 5 列，所以末尾空字段仍占列位、留下一个制表符）。
    """
    expected = "\n".join(
        [
            "=== SHEET: table ===",
            "货品编号\t名称\t下架原因\t备注\t处理人",
            "60966496933\t春季外套\t其他\t春节打烊\t",
            "76623068631\t帆布鞋\t其他\t春节打烊\t张",
            "98660070537\t保温杯\t质量问题\t批次抽检不合格\t李",
            "90888143999\t双肩包\t\t正常在售\t",
            "84172413150\t运动水壶\t其他\t春节打烊\t王",
            "24217444517\t雨伞\t清仓\t尾货处理\t",
        ]
    )
    assert run("table.csv", ctx, fixture_copy).content == expected


@pytest.mark.capability_table
def test_float_cells_use_fifteen_significant_digits(ctx, work_dir):
    """
    行为：浮点单元格按 15 位有效数字写成文本，不再带二进制误差的长尾；整数与字符串不动。

    来源：CONTEXT「交付文本：源抽取出的文本本身」+ `excel._cell_text` 的约定——
    `str(0.1 + 0.2)` 给出 `0.30000000000000004`，那是浮点实现细节，不是表里的内容。
    预期值来源：本用例自己写进 xlsx 的四个值，按 15 位有效数字手算：
    `0.1 + 0.2` → `0.3`、`1.0` → `1`、`1e20` → `1e+20`、整数 `3` → `3`（不带小数点）。
    """
    import openpyxl

    path = work_dir / "floats.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "数值"
    sheet.append([0.1 + 0.2, 1.0, 1e20, 3])
    workbook.save(str(path))

    assert dispatch("xlsx", path, ctx).content == "=== SHEET: 数值 ===\n0.3\t1\t1e+20\t3"


# ---------- TSV：同一套表格方言，分列靠制表符 ----------

TSV_FIXTURE = "sample.tsv"
# 样例生成参数（`scripts/make_fixtures.py` 阶段 19）：3 列 4 行，其中一格含逗号与分号
TSV_COLUMNS = 3
TSV_HEADER = "名称\t数量\t备注"
TSV_TRICKY_CELL = "含逗号,与分号;的备注"


@pytest.mark.capability_table
def test_tsv_is_split_by_tab_not_by_a_lookalike_delimiter(ctx, fixture_copy):
    """
    行为：`.tsv` 按制表符分列——每行都是 3 列，含逗号与分号的那一格不被切开。

    来源：本仓「只有一套表格方言」（`docs/pipelines.md` §3.4）+ `excel._sniff_delimiter` 的候选顺序。
    预期值来源：样例生成参数（阶段 19 写死 3 列，并在备注列放了逗号与分号）。
    实测：嗅探若挑成逗号或分号，含它们的那一行会多出列，第二条断言立刻失败。
    """
    content = run(TSV_FIXTURE, ctx, fixture_copy).content
    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("=== SHEET:")]
    rows = [line.split("\t") for line in lines]
    assert rows[0] == TSV_HEADER.split("\t"), f"表头应是 3 列：{rows[0]}"
    assert all(len(row) == TSV_COLUMNS for row in rows), f"每行都该是 {TSV_COLUMNS} 列：{rows}"
    assert any(row[-1] == TSV_TRICKY_CELL for row in rows), "含逗号与分号的那一格应完整落在一列里"


# ---------- docx 的超链接 ----------

@pytest.mark.capability_links
def test_docx_hyperlinks_land_in_content_with_text_and_url(ctx, fixture_copy):
    """
    行为：docx 里的超链接按「文字 -> URL」进末尾的 `## 链接` 段，按地址去重，
          没有文字的那条只给 URL。

    来源：`docs/pipelines.md` 的 docx 段 + `processors/html.py` 的链接段写法（同一种输出格式）。
    预期值来源：样例生成参数（`make_fixtures.py` 阶段 16：同一地址写两次、再一条只有地址的链接）。
    """
    content = run("hyperlink.docx", ctx, fixture_copy).content
    assert "## 链接" in content, "链接段没接进交付文本"
    assert "示例站点 -> https://example.com/a" in content
    assert "https://example.com/b" in content, "没有文字的链接也要给出地址"
    assert content.count("https://example.com/a") == 1, "同一地址应去重"


@pytest.mark.capability_links
def test_docx_link_limit_is_reported(monkeypatch, ctx, fixture_copy):
    """
    行为：链接数超过 `MAX_LINKS` 时截断，并把命中的上界名 `max_links` 报到 `limits_hit`。

    来源：`configs/limits.py` 的 `max_links`（网页与 docx 共用这一条，值只有一处）
    + ADR-0003「边界要机器可读」。
    做法：把上限压到 1，用阶段 16 的样例（去重后 2 条）触发——不新增样例、不动全局配置。
    预期值来源：样例去重后的链接数 2 与上限 1。
    """
    from configs import limits

    monkeypatch.setattr(limits, "MAX_LINKS", 1)
    result = run("hyperlink.docx", ctx, fixture_copy)
    assert "max_links" in result.limits_hit
    assert result.content.count(" -> ") == 1, "截断后只应留上限条数的链接行"


@pytest.mark.capability_links
def test_documents_without_hyperlinks_are_not_touched(ctx, fixture_copy):
    """
    行为：没有超链接的文档产出一字不加——`## 链接` 段只在真有链接时出现。

    来源：确定性抽取的 `content` 只由抽取决定；给无链接文档
    塞一个空链接段就等于改了所有 docx 的产出。
    预期值来源：这两个样例都不含超链接（阶段 2 与阶段 13 的生成参数里没有链接）。
    """
    for name in ("report.docx", "merged.docx"):
        assert "## 链接" not in run(name, ctx, fixture_copy).content


@pytest.mark.capability_limits
def test_the_limit_registry_is_self_consistent():
    """
    行为：`configs/limits.py` 的表自洽——值是正数、行为是三种之一、每条模板都能格式化出文案。

    没有这条，名字少打一个字只会在 `meta.limits_hit` 里悄悄漂移，或到生成文案时才炸。
    """
    from configs import limits

    assert limits.LIMIT_NAMES == frozenset(limits.LIMITS)
    for name, limit in limits.LIMITS.items():
        assert limit.value > 0, name
        assert limit.behavior in (limits.ERROR, limits.TRUNCATE, limits.CLAMP), name
        assert limits.note_for(name).strip(), name
        assert limits.note_for(name, 7).strip(), name


@pytest.mark.capability_limits
def test_limits_hit_reaches_meta_with_the_templated_note(ctx, work_dir):
    """
    行为：处理器报的名字经步骤 6 进 `meta.limits_hit`，`meta.notes` 里那句说明取自同一份模板。

    来源：ADR-0002「响应形状只有一份定义」+ `configs/limits.py` 的 note 模板。
    实测（2026-10-04）：这条链以前不通——上界命中只有处理器自己写的一句自由文案，
    既没有结构化名字，也没有统一模板，改说法要动十几个文件。
    """
    from configs import limits
    from utils.processor_schema import PreparedSource
    from pipelines.steps.step6_assemble import build_response
    from routers.convert.schema import Detector

    anchors = "".join(f'<a href="https://example.com/{index}">{index}</a>' for index in range(150))
    path = work_dir / "limited.html"
    path.write_text(f"<html><body>{anchors}</body></html>", encoding="utf-8")
    prepared = PreparedSource(
        path=path,
        source="limited.html",
        file_name="limited.html",
        mime="text/html",
        size_bytes=path.stat().st_size,
        ext="html",
        detector=Detector.EXTENSION,
        page_url="",
    )
    response = build_response(prepared, dispatch("html", path, ctx))
    assert response.meta.limits_hit == ["max_links"]
    assert limits.note_for("max_links") in response.meta.notes


@pytest.mark.capability_limits
def test_model_side_limits_are_reported_by_step5(monkeypatch):
    """
    行为：模型侧的两条上界都交回调用方——输出被 `llm_max_tokens` 截断（用量记账的
          `output_truncated`）、送模型的输入被 `max_model_input_chars` 截断。

    来源：`configs/limits.py` 的两条 + ADR-0003「边界要机器可读」。这两条以前接不上：
    强制点在模型层与步骤 5 里，拿不到 `result`；现在由用量记账标记、`summarize` 一次交回。
    做法：模拟的模型客户端 + 压小输入上限；不联网、不调模型。
    """
    from pathlib import Path

    from configs import settings
    from utils.processor_schema import PreparedSource
    from pipelines.steps import step5_enhance
    from routers.convert.schema import Detector

    class _Ledger:
        output_truncated = True

    class _Client:
        ledger = _Ledger()

        @staticmethod
        def chat(messages, **kwargs):
            return "摘要"

    prepared = PreparedSource(
        path=Path("x.txt"),
        source="x.txt",
        file_name="x.txt",
        mime=None,
        size_bytes=0,
        ext="txt",
        detector=Detector.EXTENSION,
        page_url="",
    )
    monkeypatch.setattr(settings, "MAX_MODEL_INPUT_CHARS", 2)

    summary, hits = step5_enhance.summarize(_Client(), prepared, "正文很长", None, None, None, None)

    assert summary == "摘要"
    assert hits == ["llm_max_tokens", "max_model_input_chars"]


# ---------- EPUB：按 spine 顺序拼章节 + 文档级链接区分清楚 ----------

# 样例生成参数（`scripts/make_fixtures.py` 阶段 17）：三章，manifest 的声明顺序与 zip 的写入顺序
# 都反着 spine（ch1 → ch2 → ch3）；每章一个独有的特征串，用来验「到底按哪个顺序拼」。
EPUB_FIXTURE = "sample.epub"
EPUB_MARKERS = ("=== 章节 1", "=== 章节 2", "=== 章节 3")
EPUB_BODY_MARKS = ("第一章独有字样", "第二章独有字样", "第三章独有字样")
# 阶段 17 把这个串只写进样式里（`<link rel=stylesheet>` 指向的 style.css 与 ch2 的内联 `<style>`）
EPUB_CSS_SENTINEL = "epub-css-sentinel-only-in-stylesheet"
# 三处内部引用提升后的文档级目标：跨章 `ch2.xhtml#sec`、章内自链 `#sec2`、`../` 跨目录回指 `#sec3`
EPUB_LINK_SEC = "OEBPS/ch2.xhtml#sec"
EPUB_LINK_SEC2 = "OEBPS/ch2.xhtml#sec2"
EPUB_LINK_SEC3 = "OEBPS/ch2.xhtml#sec3"


def _epub_link_lines(content: str) -> list:
    """只取 `## 链接` 段里的链接行。"""
    return [line for line in content.split("## 链接", 1)[1].splitlines() if line.strip()]


@pytest.mark.capability_epub
def test_epub_chapters_follow_the_spine_order(ctx, fixture_copy):
    """
    行为：章节按 OPF 的 spine 顺序拼接——manifest 的声明顺序与 zip 的写入顺序都说了不算。

    来源：本仓 EPUB 约定「spine 是阅读顺序的唯一依据」（`docs/pipelines.md` 的 EPUB 段）
    + ADR-0003（容器要往里看一层，读对了才算完整解析）。
    预期值来源：样例生成参数（阶段 17 的 manifest 与 zip 写入顺序都反着 spine）。
    实测：按 manifest 或 zip 顺序拼会把 ch3 排到最前，三个独有特征串的先后立刻对不上。
    """
    content = run(EPUB_FIXTURE, ctx, fixture_copy).content
    positions = [content.index(mark) for mark in EPUB_BODY_MARKS]
    assert positions == sorted(positions), f"章节没按 spine 顺序：{positions}"
    markers = [content.index(mark) for mark in EPUB_MARKERS]
    assert markers == sorted(markers), "分章标记的顺序与正文顺序不一致"


@pytest.mark.capability_epub
def test_epub_titles_come_from_title_then_heading_then_nothing(ctx, fixture_copy):
    """
    行为：分章标记的标题先取 `<title>`、再取第一个 h1–h6、都没有就只写章号。

    来源：样例生成参数（阶段 17：ch1 有 `<title>`、ch2 只有 h1、ch3 两者都没有）。
    预期值来源：样例里写死的那两个标题串与「都没有」这第三种情况。
    """
    content = run(EPUB_FIXTURE, ctx, fixture_copy).content
    assert f"{EPUB_MARKERS[0]}: 第一章 竞赛规程 ===" in content
    assert f"{EPUB_MARKERS[1]}: 第二章 组别设置 ===" in content, "没有 <title> 时应退回第一个 h1"
    assert "=== 章节 3 ===" in content, "两者都没有时分章标记只写章号"


@pytest.mark.capability_epub
def test_epub_internal_links_are_promoted_to_document_level_targets(ctx, fixture_copy):
    """
    行为：章内锚点与相对引用都提升成带章路径的文档级目标——拼成一整篇后每个目标唯一。

    来源：EPUB 的 `#frag` 只在**单章内**有意义，跨章拼接后不带上章路径「指向哪儿」就是歧义
    （本格式最大的难点）；决定记在 `docs/pipelines.md` 的 EPUB 段。
    预期值来源：样例生成参数（阶段 17 的三处内部引用 + 一处外部地址 + 一条应被跳过的 mailto）；
    章路径用它在容器里的完整路径（`OEBPS/ch2.xhtml`），与 zip 成员名同一命名空间。
    """
    lines = _epub_link_lines(run(EPUB_FIXTURE, ctx, fixture_copy).content)
    assert f"第二章要点 -> {EPUB_LINK_SEC}" in lines
    assert f"补充说明 -> {EPUB_LINK_SEC2}" in lines, "章内自链 `#sec2` 没有提升成文档级目标"
    assert f"附录提要 -> {EPUB_LINK_SEC3}" in lines, "`../` 相对引用没有按该章所在目录解析"
    assert "官网 -> https://example.com/race" in lines, "外部 http(s) 链接应原样输出"
    targets = [line.rsplit(" -> ", 1)[-1] for line in lines]
    assert not [target for target in targets if target.startswith("#")], f"还有裸锚点目标：{targets}"
    assert targets.count(EPUB_LINK_SEC) == 1, "同一目标应去重（自链与跨章引用指向同一个位置）"
    assert "mailto:" not in "".join(lines), "mailto 应被跳过"


@pytest.mark.capability_epub
def test_epub_styles_never_reach_the_delivered_text(ctx, fixture_copy):
    """
    行为：`<link rel="stylesheet">` 与内联 `<style>` 的内容都不进交付文本。

    来源：样式不是这份文档的内容；这条决定记在 `docs/pipelines.md` 的 EPUB 段。
    预期值来源：样例生成参数（阶段 17 把 CSS 特征串只写进 style.css 与 ch2 的内联 `<style>`）。
    实测：不丢 `<style>` 时这一串会作为正文出现在 ch2 里。
    """
    assert EPUB_CSS_SENTINEL not in run(EPUB_FIXTURE, ctx, fixture_copy).content


@pytest.mark.capability_epub
def test_epub_extraction_is_byte_identical_on_repeat(ctx, fixture_copy):
    """
    行为：同一个文件两次抽取逐字节相同——不依赖 dict 顺序，也不依赖 zip 内的文件顺序。

    来源：抽取本身是确定的——同一文件两次抽取应付出相同交付文本。
    预期值来源：同一个源抽取两次。
    """
    path, _ = fixture_copy(EPUB_FIXTURE)
    assert dispatch("epub", path, ctx).content == dispatch("epub", path, ctx).content


@pytest.mark.capability_epub
def test_epub_content_is_identical_on_both_paths(ctx, asset_ctx, fixture_copy):
    """
    行为：`prepare_assets` 两种取值下 `content` 逐字节相同——两个接口共用同一份抽取。

    来源：ADR-0001「扩展只做增量，复用同一份抽取内核」。
    预期值来源：同一个源的两次抽取；本格式没有模型素材，差异只可能来自「感知调用方是谁」。
    做法：A 组 里各跑一次 `dispatch` + `build_response`——`tests/test_extract_contract.py`
    只覆盖 extract 那一口，epub 没有 HTTP 双路用例（understand 那一路要真模型，属要花钱的模型测试）。
    """
    from utils.processor_schema import PreparedSource
    from pipelines.steps.step6_assemble import build_response
    from routers.convert.schema import Detector

    path, _ = fixture_copy(EPUB_FIXTURE)
    prepared = PreparedSource(
        path=path,
        source=EPUB_FIXTURE,
        file_name=EPUB_FIXTURE,
        mime="application/epub+zip",
        size_bytes=path.stat().st_size,
        ext="epub",
        detector=Detector.MAGIC,
        page_url="",
    )
    plain = build_response(prepared, dispatch("epub", path, ctx))
    enhanced = build_response(prepared, dispatch("epub", path, asset_ctx))
    assert plain.content == enhanced.content
    assert plain.content.strip(), "两条路都应有非空正文"


@pytest.mark.capability_epub
def test_epub_reports_the_entry_limit(monkeypatch, ctx, fixture_copy):
    """
    行为：spine 章数超过 `MAX_ENTRIES` 时只读前几章，命中名字进 `limits_hit`，并在 `notes` 里说明。

    来源：`configs/limits.py` 的 `max_entries`（容器条目上限，与压缩包共用这一处定义）
    + ADR-0003「不静默截断——少读了用户的章节必须声明」。
    做法：把上限压到 2，用 3 章的样例触发——不新增样例、不动全局配置。
    预期值来源：样例的 spine 有 3 章、上限 2 → 第三章不该出现在正文里。
    """
    from configs import limits

    monkeypatch.setattr(limits, "MAX_ENTRIES", 2)
    result = run(EPUB_FIXTURE, ctx, fixture_copy)
    assert "max_entries" in result.limits_hit
    assert EPUB_BODY_MARKS[1] in result.content
    assert EPUB_BODY_MARKS[2] not in result.content, "超过上限的章节不该被读出来"
    assert any("章" in note for note in result.notes), result.notes


@pytest.mark.capability_epub
def test_a_plain_zip_renamed_to_epub_degrades(ctx, work_dir):
    """
    行为：普通 ZIP 改名成 `.epub` 走 epub 处理器时降级为 `epub_parse_failed`，不抛异常。

    来源：ADR-0003（降级是能力边界，不是故障）+ `configs/errors.py` 的 `epub_parse_failed`
    （触发点写的就是「EPUB 容器 / OPF / spine 读不出来」）。
    预期值来源：本用例自己写的 ZIP——有 zip 结构、没有 `META-INF/container.xml`，
    所以卡的是「定位 OPF」那一步，说明里要能看出是哪个文件缺了。
    """
    import zipfile

    path = work_dir / "plain.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "这不是 EPUB")
    result = dispatch("epub", path, ctx)
    assert result.degraded is True
    assert result.degrade_code == "epub_parse_failed"
    assert result.content == ""
    assert "container.xml" in result.degrade_reason, result.degrade_reason


@pytest.mark.capability_epub
def test_epub_reports_the_zip_uncompressed_limit(monkeypatch, ctx, fixture_copy):
    """
    行为：解压总量超限时已读章节照常交付，停读后续章，命中名进 `limits_hit`。

    来源：A-④ + `configs/limits.py` 的 `max_zip_uncompressed_bytes`（TRUNCATE）。
    做法：把上限压到刚够读完前两章（按样例实测算），第三独有字样不应出现。
    """
    import zipfile

    from configs import limits
    from utils.zip_read import ZipReadBudget, read_member

    path, _ = fixture_copy(EPUB_FIXTURE)
    with zipfile.ZipFile(path) as archive:
        # container + OPF + 前两章（spine 顺序）的解压字节合计，作为刚好切在第三章前的额度
        from pipelines.processors import epub as epub_mod

        budget = ZipReadBudget(limit=10**9)
        opf = epub_mod._opf_path(archive, budget)
        chapters = epub_mod._spine_members(archive, opf, budget)
        for chapter in chapters[:2]:
            read_member(archive, chapter, budget)
        ceiling = budget.used

    monkeypatch.setattr(limits, "MAX_ZIP_UNCOMPRESSED_BYTES", ceiling)
    result = run(EPUB_FIXTURE, ctx, fixture_copy)
    assert "max_zip_uncompressed_bytes" in result.limits_hit
    assert EPUB_BODY_MARKS[0] in result.content
    assert EPUB_BODY_MARKS[2] not in result.content, "超解压额度的章节不该被读出来"


@pytest.mark.capability_document
def test_odt_degrades_when_zip_budget_blocks_content_xml(monkeypatch, ctx, work_dir):
    """
    行为：`content.xml` 声明体积已超解压额度时整份降级为 `odf_parse_failed`，不把残缺 XML 拿去解析。

    来源：A-④；必要成员超限不能走截断（截断后的 XML 不是合法文档）。
    """
    from configs import limits

    monkeypatch.setattr(limits, "MAX_ZIP_UNCOMPRESSED_BYTES", 8)
    path = work_dir / "oversize.odt"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", b"<office:document-content>" + b"x" * 40)
    result = dispatch("odt", path, ctx)
    assert result.degraded is True
    assert result.degrade_code == "odf_parse_failed"
    assert result.content == ""
    assert "超限" in result.degrade_reason, result.degrade_reason


# ---------- OFD：页序取自文档根，逐页取正文 ----------

OFD_FIXTURE = "sample.ofd"
# 样例生成参数（`scripts/make_fixtures.py` 阶段 18）：2 页；zip 写入顺序与页序相反；
# 第 1 页的正文拆成两个 TextCode 片段；第 2 页带 1 个图像对象。
OFD_PAGE_MARKS = ("=== 第1页 ===", "=== 第2页 ===")
OFD_JOINED_LINE = "第一页独有字样：两个文字块拼成一行"
OFD_SECOND_LINE = "第一页第二行"
OFD_PAGE2_LINE = "第二页独有字样：这是第二页"
OFD_MINIMAL_ENTRY = "<OFD><DocRoot>Doc_0/Document.xml</DocRoot></OFD>"


def _write_ofd(path, pages: dict, document: str = "") -> None:
    """造一个最小 OFD 容器：页面路径（相对 Doc_0/，与规范的 BaseLoc 同形）→ 页面 XML。"""
    names = {f"Doc_0/{name}": content for name, content in pages.items()}
    if not document:
        declared = "".join(
            f'<Page ID="{index}" BaseLoc="{name}"/>' for index, name in enumerate(pages, start=1)
        )
        document = f"<Document><Pages>{declared}</Pages></Document>"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("OFD.xml", OFD_MINIMAL_ENTRY)
        archive.writestr("Doc_0/Document.xml", document)
        for name, content in names.items():
            archive.writestr(name, content)


@pytest.mark.capability_document
def test_ofd_pages_follow_the_document_root_order(ctx, fixture_copy):
    """
    行为：页序取自文档根的 `Pages/Page` 声明——zip 的写入顺序说了不算；同一 `TextObject` 里的多个
          `TextCode` 片段拼成一行。

    来源：本仓 OFD 约定「文档根是页序的唯一依据」（`docs/pipelines.md` 的 OFD 段）
     + ADR-0003（容器要往里看一层，读对了才算完整解析）。
    预期值来源：样例生成参数（阶段 18 的 zip 写入顺序反着页序；第 1 页正文拆成两个片段）。
    实测：按容器里的条目顺序拼会把第 2 页排到最前，两个页标记的先后立刻对不上。
    """
    content = run(OFD_FIXTURE, ctx, fixture_copy).content
    assert content.index(OFD_PAGE_MARKS[0]) < content.index(OFD_PAGE_MARKS[1]), "页序不是文档根给出的顺序"
    assert OFD_JOINED_LINE in content, "同一 TextObject 里的多个 TextCode 片段应拼成一行"
    assert content.index(OFD_JOINED_LINE) < content.index(OFD_SECOND_LINE), "同一页内应保持文档顺序"
    assert OFD_PAGE2_LINE in content


@pytest.mark.capability_document
def test_ofd_reports_pages_and_keeps_images_out_of_the_text(ctx, fixture_copy):
    """
    行为：报出声明的页数与真读到的页数，并把「图像对象只计数、图像内容不进交付文本」写进 notes。

    来源：CONTEXT「交付文本」+ ADR-0004（`extract` 不内置 OCR，认字是 understand 的职责）。
    预期值来源：样例生成参数（阶段 18：2 页、第 2 页 1 个图像对象）。
    """
    result = run(OFD_FIXTURE, ctx, fixture_copy)
    assert (result.pages, result.pages_used) == (2, 2)
    assert any("图像对象" in note for note in result.notes), f"notes 应说明图像未进正文：{result.notes}"


@pytest.mark.capability_document
def test_ofd_falls_back_to_entry_names_when_pages_have_no_baseloc(ctx, work_dir):
    """
    行为：导出件不写 `BaseLoc` 时退回容器里的页面条目，并按名字里的数字升序——Page_10 不排在 Page_2 前。

    来源：`step2_detect` 立的同一条做法——判据不止一层，认不出就往下一层退，而不是空手返回。
    预期值来源：本用例自己造的包（三页，条目名刻意让字典序与页序不一致）。
    """
    pages = {
        "Pages/Page_10/Content.xml": "<Page><TextObject><TextCode>第十页</TextCode></TextObject></Page>",
        "Pages/Page_2/Content.xml": "<Page><TextObject><TextCode>第二页</TextCode></TextObject></Page>",
        "Pages/Page_0/Content.xml": "<Page><TextObject><TextCode>第零页</TextCode></TextObject></Page>",
    }
    path = work_dir / "no-baseloc.ofd"
    _write_ofd(path, pages, document="<Document><Pages/></Document>")
    result = dispatch("ofd", path, ctx)
    order = [mark for mark in ("第零页", "第二页", "第十页") if mark in result.content]
    assert order == ["第零页", "第二页", "第十页"], f"条目顺序应按数字升序：{result.content}"
    assert result.pages == 3


@pytest.mark.capability_degrade
def test_ofd_broken_input_reports_the_right_degrade_code(ctx, work_dir):
    """
    行为：入口缺失（没有 `OFD.xml`）报 `ofd_parse_failed`，文档根 XML 非法报 `ofd_content_failed`，
          两条都是降级结果而不是抛异常。

    来源：ADR-0003 + `configs/errors.py` 的两个 OFD 码（与 odf 的两个码同形：容器坏 / 内容 XML 坏）。
    预期值来源：本用例自己造的两种坏法——不依赖对实现的任何理解。
    """
    no_entry = work_dir / "no-entry.ofd"
    with zipfile.ZipFile(no_entry, "w") as archive:
        archive.writestr("Doc_0/Document.xml", "<Document/>")
    result = dispatch("ofd", no_entry, ctx)
    assert result.degraded is True and result.degrade_code == "ofd_parse_failed"
    assert result.degrade_code in errors.DEGRADE_CODES

    bad_root = work_dir / "bad-root.ofd"
    with zipfile.ZipFile(bad_root, "w") as archive:
        archive.writestr("OFD.xml", OFD_MINIMAL_ENTRY)
        archive.writestr("Doc_0/Document.xml", "<Document><Pages>")
    result = dispatch("ofd", bad_root, ctx)
    assert result.degraded is True and result.degrade_code == "ofd_content_failed"


@pytest.mark.capability_document
def test_ofd_skips_one_broken_page_but_keeps_the_rest(ctx, work_dir):
    """
    行为：单页 XML 非法只跳过那一页并在 notes 里说明，其余页照常交付（不是整份降级）。

    来源：EPUB 那一段立的同一条做法——一章坏只跳过该章，一章都读不出才整份降级。
    预期值来源：本用例自己造的包（第 1 页正常、第 2 页 XML 非法）。
    """
    pages = {
        "Pages/Page_0/Content.xml": "<Page><TextObject><TextCode>好页的文字</TextCode></TextObject></Page>",
        "Pages/Page_1/Content.xml": "<Page><TextObject>",
    }
    path = work_dir / "one-bad-page.ofd"
    _write_ofd(path, pages)
    result = dispatch("ofd", path, ctx)
    assert result.degraded is False
    assert "好页的文字" in result.content
    assert (result.pages, result.pages_used) == (2, 1)
    assert any("第2页" in note for note in result.notes), result.notes


# ---------- 降级路径 ----------

@pytest.mark.capability_degrade
def test_legacy_office_degrades_to_basic_file_info(ctx, fixture_copy):
    """
    行为：老版 Office 降级成「基础文件信息」——无正文、说明缺的是什么，但不算错误。

    来源：README「降级：老版 Office（doc/ppt/wps）只返回文件类型与大小」
    + CONTEXT「基础文件信息」+ ADR-0003。
    """
    result = run("legacy.doc", ctx, fixture_copy)
    assert result.content == ""
    assert result.degraded is True
    assert result.degrade_reason, "降级必须说明缺的是什么"
    assert result.degrade_code == "legacy_office_requires_converter"
    assert result.engines == ["基础文件信息"]


@pytest.mark.capability_degrade
def test_unknown_extension_degrades_to_basic_file_info(ctx, work_dir):
    """行为：认不出的扩展名走降级，不报错。来源：ADR-0003「做不到的降级而不是报错」。"""
    path = work_dir / "mystery.xyz"
    path.write_bytes(b"not a known format at all")
    result = dispatch("xyz", path, ctx)
    assert result.content == ""
    assert result.degraded is True
    assert result.degrade_reason
    assert result.degrade_code == "unsupported_type"


@pytest.mark.capability_degrade
def test_missing_extension_degrades_to_basic_file_info(ctx, fixture_copy):
    """行为：连扩展名都没有时同样降级，不抛异常。来源：ADR-0003。"""
    path, _ = fixture_copy("plain.txt")
    result = dispatch("", path, ctx)
    assert result.content == ""
    assert result.degraded is True
    assert result.degrade_reason
    assert result.degrade_code == "unsupported_type"


# Office 锁文件与系统噪音文件：`~$report.docx` 的扩展名是正常的 docx，内容却不是文档本体。
NOISE_FILES = ("~$report.docx", ".DS_Store", "Thumbs.db")


@pytest.mark.capability_degrade
@pytest.mark.parametrize("name", NOISE_FILES)
def test_noise_files_degrade_with_their_own_code(name, ctx, work_dir):
    """
    行为：Office 锁文件（`~$` 前缀）与系统噪音文件（`.DS_Store` / `Thumbs.db`）不交给任何处理器，
          直接返回 `temporary_lock_file` 降级产出，原因里写清「不是文档本体」。

    来源：CONTEXT「基础文件信息」+ ADR-0003（降级是能力边界）；机器侧要能分辨「这不是文档本体」
    与「文档坏了」，所以码表里给它单独一条码，而不是让它落进 `docx_parse_failed` / `unsupported_type`。
    预期值来源：这几个文件名本身；内容只写几个字节，反正不该被解析。
    """
    path = work_dir / name
    path.write_bytes(b"not a real document")
    result = dispatch(extension_from_name(name), path, ctx)
    assert result.content == ""
    assert result.degraded is True
    assert result.degrade_code == "temporary_lock_file"
    assert "不是文档本体" in result.degrade_reason, result.degrade_reason
    assert result.degrade_code in errors.DEGRADE_CODES


@pytest.mark.capability_degrade
def test_noise_is_judged_by_the_original_name_not_the_temp_name(ctx, work_dir):
    """
    行为：噪音判定看**原始文件名**——下载落盘名已被 `step3_prepare._align_extension` 换成
          `dl_xxxx.docx`，只看 `path.name` 的实现在真实下载路径上会漏掉锁文件。

    来源：`step3_prepare` 的落盘名约定（`dl_` 前缀 + 识别出的后缀）。
    预期值来源：本用例自己写的临时名与原始名；原始名不是噪音时不该误伤（垃圾 docx 仍走解析失败）。
    """
    path = work_dir / "dl_0001.docx"
    path.write_bytes(b"not a real document")
    locked = dispatch("docx", path, ctx, source_name="~$report.docx")
    assert locked.degrade_code == "temporary_lock_file"
    assert dispatch("docx", path, ctx, source_name="report.docx").degrade_code == "docx_parse_failed"


# 内容不是对应容器时，每个格式该报的那一个码（`configs/errors.py` 的 DEGRADE_CODES）
BROKEN_INPUT_CODES = (
    ("docx", "docx_parse_failed"),
    ("pptx", "pptx_parse_failed"),
    ("xlsx", "excel_parse_failed"),
    ("odt", "odf_parse_failed"),
    ("ofd", "ofd_parse_failed"),
    ("pdf", "pdf_parse_failed"),
    ("epub", "epub_parse_failed"),
    ("png", "image_decode_failed"),
    ("apk", "install_package_parse_failed"),
)


@pytest.mark.capability_degrade
@pytest.mark.parametrize(("ext", "code"), BROKEN_INPUT_CODES)
def test_broken_input_carries_the_degrade_code(ext, code, ctx, work_dir):
    """
    行为：容器损坏 / 解不开时，降级带上「哪一类失败」的稳定机器名，且该码在码表白名单里。

    来源：`configs/errors.py` 的第 2 组 + ADR-0003（降级是能力边界，但边界也要机器可读）。
    这条盯的是「同一句中文说明由不同分支产生」——只写 degrade_reason 的话，调用方分不出
    「容器坏了」与「压根不支持」。
    """
    path = work_dir / f"broken.{ext}"
    path.write_bytes(f"this is definitely not a {ext} container".encode())
    result = dispatch(ext, path, ctx)
    assert result.degraded is True
    assert result.degrade_code == code, f"{ext} 应报 {code}，实际 {result.degrade_code}"
    assert result.degrade_code in errors.DEGRADE_CODES


@pytest.mark.capability_degrade
def test_every_degrade_branch_declares_a_code(ctx, work_dir):
    """
    行为：降级必然带码——`degraded=True` 却没有 `degrade_code` 就是漏了这条通道。

    来源：`configs/errors.py`「degraded=true 时必有 degrade_code」。上面那条参数化用例只盖
    几个格式，这条用「无扩展名」这个最通用的降级入口把「必有」本身钉住。
    """
    path = work_dir / "nameless"
    path.write_bytes(b"no extension at all")
    result = dispatch("", path, ctx)
    assert result.degraded and result.degrade_code in errors.DEGRADE_CODES

