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
  bundle.zip 12 条目、demo.apk 6 条目
- `CONTEXT.md` 的术语：「基础文件信息」「交付文本」「降级」
"""
import pytest

from pipelines.processors.schema import ExtractContext
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
def test_office_documents_report_page_and_embedded_image_counts(name, pages, images, ctx, fixture_copy):
    """
    行为：Office 文档报出页数与内嵌图张数。

    来源：样例生成参数（report.docx 1 页内嵌 1 张图、deck.pptx 6 页内嵌 1 张图）
    + README「文档内嵌图：标出位置占位」。
    """
    result = run(name, ctx, fixture_copy)
    assert result.pages == pages
    assert result.images == images


def test_spreadsheet_reports_its_sheet_count(ctx, fixture_copy):
    """行为：表格类报出工作表数。来源：inventory.xlsx 是单工作表样例。"""
    result = run("inventory.xlsx", ctx, fixture_copy)
    assert result.pages == 1


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


def test_the_limit_registry_is_self_consistent():
    """
    行为：`configs/limits.py` 的表自洽——值是正数、行为是三种之一、每条模板都能格式化出文案。

    没有这条，名字少打一个字只会在 `meta.limits_hit` 里悄悄漂移，或到生成文案时才炸。
    """
    from configs import limits

    assert limits.LIMIT_NAMES == frozenset(limits.LIMITS)
    # 未接进 limits_hit 的那几个必须是表里的条目，不能是想当然写下的名字
    assert limits.NOT_REPORTED_YET <= limits.LIMIT_NAMES
    for name, limit in limits.LIMITS.items():
        assert limit.value > 0, name
        assert limit.behavior in (limits.ERROR, limits.TRUNCATE, limits.CLAMP), name
        assert limits.note_for(name).strip(), name
        assert limits.note_for(name, 7).strip(), name


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


# ---------- 降级路径 ----------

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
    assert result.engines == ["基础文件信息"]


def test_unknown_extension_degrades_to_basic_file_info(ctx, work_dir):
    """行为：认不出的扩展名走降级，不报错。来源：ADR-0003「做不到的降级而不是报错」。"""
    path = work_dir / "mystery.xyz"
    path.write_bytes(b"not a known format at all")
    result = dispatch("xyz", path, ctx)
    assert result.content == ""
    assert result.degraded is True
    assert result.degrade_reason


def test_missing_extension_degrades_to_basic_file_info(ctx, fixture_copy):
    """行为：连扩展名都没有时同样降级，不抛异常。来源：ADR-0003。"""
    path, _ = fixture_copy("plain.txt")
    result = dispatch("", path, ctx)
    assert result.content == ""
    assert result.degraded is True
    assert result.degrade_reason
