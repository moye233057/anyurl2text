# anyurl2text

把任意 URL 或本地文件转成文本的服务。同一个源有两条路：一条只靠代码确定性抽取，一条在抽取结果之上叠加模型的理解能力。两条路共用同一份请求体与响应契约，代价与产出因此可以直接对比。

## Language

**extract**：
只依赖代码的确定性抽取接口。产出可核查的原始文本与结构信息，不调用任何模型。
_Avoid_: 接口一、纯代码接口、deterministic API

**understand**：
在 extract 的产出之上叠加模型能力的接口。产出摘要，并保留 extract 的全部内容。
_Avoid_: 接口二、AI 接口、增强接口

**确定性抽取**：
不需要模型的解析行为本身（格式识别、版面还原、文本提取、编码判定）。extract 是它的对外接口，understand 复用它。
_Avoid_: 本地解析、离线解析

**模型增强**：
在确定性抽取结果之上、由模型产出的语言加工（看图取字、内容描述、按 query 回答、通用摘要）。
_Avoid_: 模型解析、AI 处理

**处理器**：
一种**格式族**在两条路下的处理单元（`pipelines/processors/` 下**一个族一个文件**，例如 `excel.py` 覆盖 xlsx/xls/ods/csv）。
它总是提供确定性产出，需要模型处理时再提供该格式的模型增强入口——两者在同一份实现里，调试只开一个文件。
不为过行数门禁再拆兄弟文件。跨格式表格公共件进 `utils/excel/`；处理器契约进 `utils/processor_schema.py`；涉及模型调用的公共逻辑进 `models/`（如 `models/reading.py`）。
_Avoid_: 解析器、抽取器、handler、为预算拆出的 `*_merges.py` / `*_visibility.py` 式碎片

**格位**（格位网格）：
表格里一个逻辑位置的表示。内容与跨度只挂在 origin 上，被跨度覆盖的位置只记「指向哪个 origin」，
所以合并单元格既不会让文本重复、也不会让列错位——多个表格格式共用一份结构与渲染（`utils/excel/`）。
_Avoid_: 单元格矩阵、merge 表、span 表

**模态**：
响应 `type` 字段的唯一维度，取值 document / image / audio / video / webpage / archive / install_package / file / error。它只回答「这是什么」，不回答「用什么做的」——引擎与策略一律进 `meta`。
_Avoid_: 类型、文件类型

**交付文本**（`content`）：
对外主正文。`/extract` 只含纯代码抽取段；`/understand` 在同一段之上合并模型/OCR/ASR 等转写（仍双写进 `meta.ocr_text` / `meta.asr_text`）。纯代码即可完整交付的类型，两路 `content` 仍相同。摘要进 `summary`，不替换 `content`。
_Avoid_: 原文、正文、result

**摘要**（`summary`）：
模型对交付文本的加工结果，与交付文本并存。不带 query 时是通用摘要，带 query 时是针对该 query 的回答。
_Avoid_: 总结、answer

**基础文件信息**：
缺少依赖、无法深入解析时返回的降级产出：文件类型、大小、扩展名、magic 头。它是正常结果，不是错误。
_Avoid_: 兜底信息、fallback、占位

**识别文本**（`meta.ocr_text`）：
从图像中读出的文字：独立图片走本机 Paddle OCR，文档内嵌图与扫描件页面走视觉模型。understand 路同时并进 `content` 与 `meta.ocr_text`（溯源副本）。
_Avoid_: OCR 结果、识别内容、正文

**转写文本**（`meta.asr_text`）：
从音频/视频的音轨里听出的文字，由本机 Qwen3-ASR 产出，只有 understand 那一路有值。understand 路同时并进 `content` 与 `meta.asr_text`；与 `ocr_text` 分字段是为区分来源。
_Avoid_: 语音识别结果、ASR 文本、字幕

**音轨**：
音频文件本身，或从视频与 m4a/aac/wma 这类容器里抽出来的一条 16kHz 单声道声音。它是音视频这一类在两条路之间传递的东西：纯代码读它的参数得到结构信息，understand 把它送语音识别。抽出的音轨落在本次请求的临时目录里，随请求一起清理。
_Avoid_: 声道、音频流、sound

**判型**：
独立图片在模型增强里的第一步：用视觉模型判断这张图是「文字图片」还是「语义图片」。文字图片交本机 Paddle OCR 读字，语义图片直接用同一次调用给出的客观描述；判不准时按文字图片处理。
_Avoid_: 分类、路由、semantic router

**页面渲染**：
把 PDF 页画成位图再交给视觉模型，用于没有文字层的扫描件。按页全幅，成本与页数成正比。
_Avoid_: 全页 OCR、整页识别

**内嵌图**：
文档内部承载的图片对象（PDF xref、docx 关系、pptx shape）。只覆盖图中的内容，与页面渲染不同。
同一个图片对象被引用多次仍是**一张**内嵌图——它是对象，不是出现位置；所以落盘素材数与
`MAX_EMBEDDED_IMAGES` 都按对象算，而 `meta.images` 记的是引用次数。
_Avoid_: 文档图片、插图

**上界**（限额）：
一个「最多允许多少」的上限：字节、字符、行、页、张、条。命中后要么失败（`error`）、要么截断
（`truncate`）、要么静默钳到上界（`clamp`）。**阈值不是上界**——「不足 30 字算没有文字层」是判定用的
下限，命中它不产生截断。全部上界登记在 `configs/limits.py`，命中后名字进 `meta.limits_hit`。
_Avoid_: 限制、门限、上限值

**用量**（`usage`）：
本次调用真实的模型 token 与折算成本，按模型分别记账。不存在用内容长度冒充 token 的字段。
_Avoid_: inputTokens / outputTokens

**耗时**（`timings`）：
分段墙钟（下载 / 解析 / 模型），用于区分「慢在网络上」与「慢在模型上」。
_Avoid_: cost、elapsed
