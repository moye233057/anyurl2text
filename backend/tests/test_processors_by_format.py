# -*- coding: utf-8 -*-
r"""
缝 A：按文件类型的确定性抽取。

缝：`pipelines/steps/step4_extract.dispatch(ext, path, ctx)`——类型识别之后、响应组装之前，
决定「这个文件交给哪个处理器、产出什么」。不调模型、不联网、不起服务。

为什么选它：项目按「一种格式一个处理器」组织，`dispatch()` 是这张表唯一的入口，
也是 `docs/EXTENDING.md` 让加格式时改的那个函数。类型识别的行为在 `test_detect.py` 里验，
响应契约与状态码在 `test_extract_contract.py`（HTTP 缝）里验，这里只管处理器产出。

预期值来源（都不是「跑一遍看输出」）：
- `README.md`「支持哪些输入」表：承诺完整解析的格式、只给结构信息的格式、降级的格式
- 样例的生成参数（`scripts/make_fixtures.py` 各阶段写死的页数 / 条目数）：
  manual.pdf 10 页有文字层、scanned.pdf 3 页无文字层、deck.pptx 6 页、report.docx 内嵌 1 张图、
  bundle.zip 12 条目、demo.apk 6 条目、sample.epub 3 章（spine 顺序与 manifest / zip 顺序都不同）
- `CONTEXT.md` 的术语：「基础文件信息」「交付文本」「降级」
"""
import pytest

from configs import errors
from pipelines.processors.schema import ExtractContext
from pipelines.steps.step2_detect import extension_from_name
from pipelines.steps.step4_extract import dispatch

# README「支持哪些输入」里列为「完整解析」的文档格式，每种都有配套样例
DOCUMENT_FIXTURES = (
    "plain.txt",
    "notes.md",
    "table.csv",
    "sample.json",
    "feed.xml",
    "sample.rtf",
    "report.docx",
    "inventory.xlsx",
    "deck.pptx",
    "sample.odt",
    "sample.odp",
    "sample.epub",
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
    行为：有文字层的 PDF 逐页抽出正文，页数与内嵌图张数如实报出。

    来源：样例生成参数（manual.pdf 10 页，纯文本无图）。
    """
    result = run("manual.pdf", ctx, fixture_copy)
    assert result.pages == 10
    assert result.images == 0
    assert result.content.strip()
    assert result.degraded is False


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

    来源：借鉴设计 P1-6「meta 加结构化未处理声明」+ ADR-0003（能力边界也要机器可读）。
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


# ---------- 合并单元格：格位不变式 ----------

# 样例生成参数：三个样例都是 3 列，横向合并在第一行、纵向合并在第一列；
# 合并文本是脚本阶段 13 写死的固定串（见 `scripts/make_fixtures.py`）。
MERGED_FIXTURES = ("merged.docx", "merged.xlsx", "merged.pptx")
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
    实测（2026-10-02）：docx 走 `row.cells` 时同一个格位被返回多次，合并标题与纵向合并各出现
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

    来源：`docs/PIPELINES.md`「内嵌图：一次请求带上全部图（受 MAX_EMBEDDED_IMAGES 限制）」
    + 样例生成参数（阶段 14：4 处引用、2 张不同的图，上限 3）。
    实测（2026-10-04）：修前三个格式都按「出现次序」占名额——前 3 处引用全是同一张 photo.png，
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

    来源：`docs/PIPELINES.md` 的 odt 行 + `借鉴设计/01-表格格位建模.md`「odf.py 将来怎么接」
    （跨度 → `place(span)`、`covered-table-cell` → `covered()`、
    `number-columns-repeated` → 循环落位并记账）。
    实测（2026-10-04）：原来全局 `root.iter()` 收 `text:p`，每个单元格各自成一行、不含制表符，
    列对应关系完全丢失；接上格位网格后同一张表每行都是 3 列。
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


# ---------- CSV 分隔符嗅探 ----------

CSV_DIALECTS = ((";", "分号"), ("\t", "制表符"), ("|", "竖线"))


@pytest.mark.capability_table
@pytest.mark.parametrize(("delimiter", "label"), CSV_DIALECTS)
def test_csv_delimiter_is_sniffed_instead_of_assumed_comma(delimiter, label, ctx, work_dir):
    """
    行为：分号 / 制表符 / 竖线分隔的 CSV 也按列渲成制表符表格，而不是被读成一列。

    来源：`excel.py` 模块 docstring「按列位置输出制表符，空列留空；列一错位整张表就读不出
    对应关系」+ `docs/PIPELINES.md` 的嗅探规则。
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

    来源：`docs/PIPELINES.md` 的嗅探规则「列数 > 1 且各行列数最一致，平局逗号优先」。
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


# ---------- docx 的超链接 ----------

@pytest.mark.capability_links
def test_docx_hyperlinks_land_in_content_with_text_and_url(ctx, fixture_copy):
    """
    行为：docx 里的超链接按「文字 -> URL」进末尾的 `## 链接` 段，按地址去重，
          没有文字的那条只给 URL。

    来源：`docs/PIPELINES.md` 的 docx 段 + `processors/html.py` 的链接段写法（同一种输出格式）。
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

    来源：ADR-0002「两条路逐字节一致」的前提是 `content` 只由抽取决定；给无链接文档
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

    来源：ADR-0001「响应形状只有一份定义」+ `configs/limits.py` 的 note 模板。
    实测（2026-10-04）：这条链以前不通——上界命中只有处理器自己写的一句自由文案，
    既没有结构化名字，也没有统一模板，改说法要动十几个文件。
    """
    from configs import limits
    from pipelines.processors.schema import PreparedSource
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
    行为：模型侧的两条上界都交回调用方——输出被 `llm_max_tokens` 截断（账本的
          `output_truncated`）、送模型的输入被 `max_model_input_chars` 截断。

    来源：`configs/limits.py` 的两条 + ADR-0003「边界要机器可读」。这两条以前接不上：
    强制点在模型层与步骤 5 里，拿不到 `result`；现在由账本标记、`summarize` 一次交回。
    做法：假客户端 + 压小输入上限；不联网、不调模型。
    """
    from pathlib import Path

    from configs import settings
    from pipelines.processors.schema import PreparedSource
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


# ---------- EPUB：按 spine 顺序拼章节 + 文档级链接消歧 ----------

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

    来源：本仓 EPUB 约定「spine 是阅读顺序的唯一依据」（`docs/PIPELINES.md` 的 EPUB 段）
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
    （本格式最大的难点）；决定记在 `docs/PIPELINES.md` 的 EPUB 段。
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

    来源：样式不是这份文档的内容；这条决定记在 `docs/PIPELINES.md` 的 EPUB 段。
    预期值来源：样例生成参数（阶段 17 把 CSS 特征串只写进 style.css 与 ch2 的内联 `<style>`）。
    实测：不丢 `<style>` 时这一串会作为正文出现在 ch2 里。
    """
    assert EPUB_CSS_SENTINEL not in run(EPUB_FIXTURE, ctx, fixture_copy).content


@pytest.mark.capability_epub
def test_epub_extraction_is_byte_identical_on_repeat(ctx, fixture_copy):
    """
    行为：同一个文件两次抽取逐字节相同——不依赖 dict 顺序，也不依赖 zip 内的文件顺序。

    来源：ADR-0002「两条路逐字节可对比」以「抽取本身是确定的」为前提。
    预期值来源：同一个源抽取两次。
    """
    path, _ = fixture_copy(EPUB_FIXTURE)
    assert dispatch("epub", path, ctx).content == dispatch("epub", path, ctx).content


@pytest.mark.capability_epub
def test_epub_content_is_identical_on_both_paths(ctx, asset_ctx, fixture_copy):
    """
    行为：`prepare_assets` 两种取值下 `content` 逐字节相同——两个接口共用同一份抽取。

    来源：ADR-0002「understand 复用 extract 的抽取内核，只做增量」。
    预期值来源：同一个源的两次抽取；本格式没有模型素材，差异只可能来自「感知调用方是谁」。
    做法：缝 A 里各跑一次 `dispatch` + `build_response`——`tests/test_extract_contract.py`
    只覆盖 extract 那一口，epub 没有 HTTP 双路用例（understand 那一路要真模型，属贵缝）。
    """
    from pipelines.processors.schema import PreparedSource
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

