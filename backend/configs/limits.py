# -*- coding: utf-8 -*-
"""
限额的单一定义处。

为什么要有这个文件：上界原本散在 `settings.py`（环境变量）与十来个处理器（模块级常量）里，
谁都能再加一个，于是「这份文档为什么被截断」既没有一处能读全，也没有统一的说法。
这里把每一条上界登记成一个 `Limit`：值、单位、命中行为、在哪强制、命中时写什么文案。

两个数值来源在这里汇合：可配的读 `settings`（环境变量），写死的直接定义在本文件——
**不再有第三个来源**。

规则：

- 命中时处理器只把**名字**记进 `result.limits_hit`，不自己写文案；文案只有 `note` 一处模板。
- `LIMIT_NAMES` 是名字白名单，`meta.limits_hit` 里出现表外的名字就是漂移。
- 每条登记的上界都要有**报告链路**：命中时名字必须能到 `meta.limits_hit`（RAG 那口报进它自己的
  `limits_hit`）。只有代码知道的上界，用户与调用方都看不见。
- 阈值（判定用的下限，如「合计不足 30 字算没有文字层」）不是上界，不进表，见 `NON_LIMIT_THRESHOLDS`。

为什么压得这么密：判据 2 是「总行 ≤ 500 且代码行 ≤ 300」（`tests/test_structure.py`；2026-10-04 从
250/150 抬起），一条上界写成多行对象会顶穿预算——这是本方案里唯一有真实预算压力的文件。
"""
from typing import Dict, NamedTuple, Optional, Union

import configs.settings as settings

# 命中后的三种结果：请求失败 / 截断后继续 / 静默钳到上界
ERROR = "error"
TRUNCATE = "truncate"
CLAMP = "clamp"

# ---------- 写死的上界（原本散在各处理器里，收进这里） ----------
# 单个表格最多渲染的行数；docx 与 odf 原本各有一份同值常量，合并成这一处
MAX_TABLE_ROWS = 2000
# 单个表格的格位展开（跨度额外占用的格位）总量上限
MAX_GRID_EXPANSION = 200_000
# 单张表的**格位数**上限：实测一个格位对象约 100 字节，100 万格位约 100 MB，
# 而「行上限 5000 × 200 列」正好落在预算内——它挡的是列数无上限的畸形表。
MAX_GRID_SLOTS = 1_000_000
# 单张表的**文字总字符数**上限：它直接界定渲染出的中间字符串（实测 2000×50×500 字符 = 5000 万字符、
# 峰值 60 MB）。交付文本另有 `max_content_chars` 截断，但那是**组装时事后截断**，峰值已经发生；
# 真实大表（5000 行 × 40 列 × 10 字 ≈ 200 万字符）在预算内，留了 2.5 倍余量。
MAX_GRID_TEXT_CHARS = 5_000_000
# ODF 的 `number-*-repeated` 游程上限：它常被用来把一行补到上千列
MAX_REPEATED_SLOTS = 100
# 压缩包 / 安装包的条目清单上限
MAX_ENTRIES = 500
# 单张工作表的行数上限；读取时多读一行，用来区分「还有后续行」与「这是最后一行」
MAX_ROWS_PER_SHEET = 5000
MAX_ROWS_TO_READ = MAX_ROWS_PER_SHEET + 1
# 链接清单上限：html 抽 `<a href>`、docx 抽 `w:hyperlink`，两者共用这一条
MAX_LINKS = 100
# 隐藏行列的**登记段数**上限：限制的只是 notes 的篇幅（`第 2-4、6 行` 这样一段一段写）；
# 隐藏内容本身一律照抽，不因隐藏少读一个格子（用户 2026-10-04 拍板，见 01 §1.2）。
# 取值 24 是**挑过的**：判据 6 按值比对，20 会撞 `excel.py` 的 `CSV_SNIFF_LINES`、
# 30 会撞 `pdf.py` 的 `TEXT_LAYER_MIN_CHARS`——那两条与本条无关，撞上就是假红。
MAX_VISIBILITY_RANGES = 24
# 文本类（txt/md/json/xml/csv…）读取上限
MAX_TEXT_BYTES = 20 * 1024 * 1024
# RAG 预览取前几页
PREVIEW_FIRST_PAGES = 15
# 送模型前图片长边缩到多大（不是截断，是编码前的等比缩放）
MAX_LONG_EDGE = 1600

class Limit(NamedTuple):
    """
    一条上界。

    属性：
        value — 上界本身；文案里的 {value} 默认换成它，也可由调用方传实际值覆盖。
        unit — 单位，给文档与人看（bytes / chars / rows / count / seconds）。
        behavior — 命中后的结果：ERROR / TRUNCATE / CLAMP 三者之一。
        configurable — 值是否可经环境变量调整（True 读 settings，False 写死在本文件）。
        enforced_at — 在哪强制，`文件:行` 形式，多处用 " / " 分隔。
        note — 命中时写进 `meta.notes` 的模板，用 str.format 填 {value}。
    """

    value: Union[int, float]
    unit: str
    behavior: str
    configurable: bool
    enforced_at: str
    note: str

LIMITS: Dict[str, Limit] = {
    # ---------- 下载层（可配置） ----------
    "max_download_bytes": Limit(settings.MAX_DOWNLOAD_BYTES, "bytes", ERROR, True,
        "app.reject_oversized_body / step1_download._stream_to_file / step3_prepare.prepare_from_upload",
        "文件超过 {value} 字节上限已拒绝（调大等于放宽防护）"),
    "max_redirects": Limit(settings.MAX_REDIRECTS, "count", ERROR, True, "step1_download.download",
        "跳转超过 {value} 次已停止"),
    # ---------- 交付文本与模型输入（可配置） ----------
    "max_content_chars": Limit(settings.MAX_CONTENT_CHARS, "chars", TRUNCATE, True, "step6_assemble.build_response",
        "content 超过 {value} 字符已截断"),
    "max_model_input_chars": Limit(settings.MAX_MODEL_INPUT_CHARS, "chars", TRUNCATE, True,
        "step5_enhance.summarize / understand.run_from_upload, run_from_url",
        "送模型的文本超过 {value} 字符，摘要只覆盖前一部分"),
    "llm_max_tokens": Limit(settings.LLM_MAX_TOKENS, "tokens", TRUNCATE, True, "deepseek.chat",
        "模型单次输出上限 {value} token，超出部分被截断"),
    # ---------- 素材与页面（可配置） ----------
    "max_embedded_images": Limit(settings.MAX_EMBEDDED_IMAGES, "count", TRUNCATE, True,
        "docx._save_embedded_images / pdf._save_embedded_images / pptx.extract_pptx",
        "内嵌图超过 {value} 张，其余未送模型识读"),
    "rendered_page_max": Limit(settings.RENDERED_PAGE_MAX, "count", TRUNCATE, True, "pdf.extract_pdf",
        "扫描件超过 {value} 页，其余未渲染送模型"),
    "asr_max_audio_seconds": Limit(settings.ASR_MAX_AUDIO_SECONDS, "seconds", TRUNCATE, True, "media.enhance_media",
        "音轨超过 {value} 秒，超出部分未转写"),
    # ---------- 各格式的读取上限（写死在本文件） ----------
    "max_entries": Limit(MAX_ENTRIES, "count", TRUNCATE, False, "archive.entries_to_content",
        "容器内条目超过 {value} 条，清单已截断"),
    "max_rows_per_sheet": Limit(MAX_ROWS_PER_SHEET, "rows", TRUNCATE, False, "excel._render_rows",
        "工作表超过 {value} 行，其余行未读出"),
    # 不加模块前缀（设计文档 03 写的是 `docx_max_table_rows`）：docx 与 odf **共用**这一条限额，
    # 值与强制点都只有一处，带模块前缀反而要拆成两个名字、两份值。命名以本文件为准。
    "max_table_rows": Limit(MAX_TABLE_ROWS, "rows", TRUNCATE, False,
        "docx._render_table / odf._render_table / html_tables.table_to_tsv / rtf_tables.build_lines",
        "表格超过 {value} 行，其余行未列出"),
    "max_grid_expansion": Limit(MAX_GRID_EXPANSION, "count", TRUNCATE, False, "grid.GridBuilder.place",
        "表格跨度展开超过 {value} 个格位，超出的合并跨度按 1 格处理"),
    "max_grid_slots": Limit(MAX_GRID_SLOTS, "count", TRUNCATE, False, "grid.GridBuilder.place",
        "单张表的格位数超过 {value}，其余单元格未落位"),
    "max_grid_text_chars": Limit(MAX_GRID_TEXT_CHARS, "chars", TRUNCATE, False, "grid.GridBuilder.place",
        "单张表的文字超过 {value} 字符，其余单元格未落位"),
    "max_repeated_slots": Limit(MAX_REPEATED_SLOTS, "count", CLAMP, False, "odf._render_table",
        "ODF 的 number-*-repeated 超过 {value} 次，按 {value} 次处理"),
    "max_links": Limit(MAX_LINKS, "count", TRUNCATE, False, "html.extract_html / docx.extract_docx",
        "链接超过 {value} 条，清单已截断"),
    "max_visibility_ranges": Limit(MAX_VISIBILITY_RANGES, "count", CLAMP, False, "excel._visibility_notes",
        "隐藏行列的连续段超过 {value} 段，只登记前 {value} 段（内容照抽，不影响正文）"),
    "max_text_bytes": Limit(MAX_TEXT_BYTES, "bytes", TRUNCATE, False, "textfile.read_text_file",
        "文本超过 {value} 字节，超出部分未读取"),
    "preview_first_pages": Limit(PREVIEW_FIRST_PAGES, "count", TRUNCATE, False, "rag_extract._try_catalogue",
        "预览只取前 {value} 页"),
    "max_long_edge": Limit(MAX_LONG_EDGE, "count", CLAMP, False, "images.encode_image",
        "图片长边缩到 {value} 像素后再编码"),
}

# 名字白名单：meta.limits_hit 里出现表外的名字就是漂移
LIMIT_NAMES = frozenset(LIMITS)

# 判定用的阈值不是上界：命中它们不产生截断，也不该出现在 limits_hit 里
NON_LIMIT_THRESHOLDS = frozenset({
    "text_layer_min_chars",            # 合计不足 30 字 → 判定为「没有文字层」
    "rendered_page_retry_min_chars",   # 整页识别结果短于 20 字 → 切半重试
    "blank_page_luma_tolerance",       # 判空白页的灰度容差
})

def note_for(name: str, actual: Optional[Union[int, float]] = None) -> str:
    """
    按表里的模板生成命中说明。

    功能：把「给使用者看的文案」收敛到本文件一处——处理器只报名字，改文案不必动十几个文件。
    应用场景：处理器与组装层命中上界时调用。
    输入：name — LIMITS 里的键；actual — 实际值，省略则用表里的上界。
    输出：说明文案。键不在表里 → KeyError（漂移应当当场炸掉，不是静默放过）。
    """
    limit = LIMITS[name]
    return limit.note.format(value=limit.value if actual is None else actual)
