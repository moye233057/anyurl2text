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
