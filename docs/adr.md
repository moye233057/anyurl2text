# 架构决策（现行）

> **用途**：只记仍有效的「现在为什么这样」。现行约束与落点看 [architecture.md](./architecture.md)。
> **关联**：[HISTORY.md](./HISTORY.md)（被取代或废弃的选定） · [CONTEXT.md](../CONTEXT.md)（术语表） · [API.md](./API.md)（字段契约）
> **基线**：2026-10-02

- 编号不复用：删掉一条就不再出现该编号。
- 日期是该条**内容最后一次实质变更**的日期。2026-10-02 把原 `docs/adr/*.md` 合并进本文件，结论未变的条目保留原日期；0003 与 0007 按当时的现状重写，日期改为当天。
- 现状落点与状态不在本文：那在 [architecture.md](./architecture.md)。

## ADR-0001 · 2026-09-11 · 响应契约另起一套，摘要不与正文混放

响应恒为 7 个字段 `type / content / summary / meta / usage / timings / error`：`summary` 只由 understand 产出，`content` 是抽取出的原文、任何接口任何参数下都不被覆盖、也不追加实现细节前缀，`error` 恒存在（成功为 `null`）——调用方不必判两种响应形状。`error` 非空时是**结构化的** `{code, message, param}`：`message` 是给使用者看的文案、会改；`code` 是给程序看的稳定机器名（码表在 `configs/errors.py`，不再靠字符串匹配错误类型）。这样「同一份源、两种代价」才能逐字节对比，摘要与原文也各有各的位置。否掉 `{type, content, inputTokens, outputTokens}` 那套契约：那条契约带 query 时用摘要覆盖正文（原文永久丢失）、把引擎实现细节硬编码进正文、成功 4 键失败 5 键、`usage` 里塞的不是 token。

## ADR-0002 · 2026-09-12 · understand 复用 extract 的抽取内核，只做增量

两个接口共用同一段下载、识别、解析代码，差异被限制在「是否调用模型」这一件事上。不共用就会出现两条各自演进的解析实现——同一份 PDF 在两条路下抽出不同文本，而「两条路可对比」正是本项目存在的理由。`extract_*` 不调模型、也不感知调用方是谁。**understand 的正文语义**（何时与 extract 相同、何时合并模型转写）见 [ADR-0011](#adr-0011--2026-10-10--understand-正文可含模型转写双池并发与全链路预算)。否掉「单一接口 + `mode` 参数」（调用方从 URL 上看不出代价量级，两条路也无法各自限流与超时）与「understand 独立实现端到端」。

## ADR-0003 · 2026-10-04 · 覆盖范围以「纯 Python 库能否做到」划线

纯 Python 库能解析的一律完整实现；做不到的不报错，而是**降级**为基础文件信息（文件类型 / 大小 / 扩展名 / 文件头），HTTP 仍是 200，`meta.degraded` 与 `degrade_reason` 说明缺的是什么——降级是**能力边界**而不是故障，混进 `error` 会让监控把已知边界当成事故。容器格式必须往里看一层，否则「完整解析」是空头承诺：OLE 头读复合文档目录里的**流名**分家族——`WordDocument` → doc、`Workbook` / `Book` → xls、`PowerPoint Document` → ppt（读法见 `pipelines/steps/ole_directory.py`）；目录读不出、或没有一个流名认识时才退回扩展名与 Content-Type，所以扩展名与 Content-Type 依旧撒谎不起作用。ZIP 头读中央目录指纹定 docx / xlsx（带 `xl/vbaProject.bin` 则 xlsm）/ pptx / odt / ods / odp / epub / ofd / apk / ipa，认不出才维持「这是个 ZIP」。今天真正缺**不可替代**依赖的只剩老版 Office（doc / ppt / wps，缺 LibreOffice 转换器）——音视频已按 ADR-0008 接上本机 ASR，但 `soundfile` / `imageio-ffmpeg` 是可选依赖，缺它们或音轨抽不出时同样降级。否掉「不支持的类型直接报错」：诚实但不可用，且逼迫调用方自己做类型预判。

## ADR-0004 · 2026-09-11 · extract 对图片只给结构信息，不内置本地 OCR

`/extract` 处理图片返回格式、尺寸、色彩模式、帧数与白名单 EXIF，`content` 为空串、`meta.ocr_text` 为 `null`；认字是 understand 的职责。这条边界让两条路的差别可检验（毫秒级结构信息 vs 秒级内容理解）；在纯代码那一路塞个本地 OCR，差别就退化成「慢一点的 OCR vs 快一点的 OCR」，还会把「本地起两个服务就能用」变成「先下几个模型」（权重动辄数百 MB）。否掉内置 PaddleOCR / RapidOCR / Tesseract 与调用系统自带 OCR（跨平台能力与结果格式不一致，无法稳定验收）。

## ADR-0005 · 2026-10-10 · 下载层四条安全基线不可协商

服务接受用户可控 URL，这四条是必答题，任何为「提高成功率」放松它们的改动都应被拒绝：

1. **SSRF 防护**：每次实际连接前判定 DNS 返回的每一个地址，命中私网 / 回环 / 链路本地 / 保留 / 组播一律拒；地址无法识别亦拒（fail-closed）；**解析一次并把连接钉到该 IP**（Host / SNI 仍用原主机名），关掉 DNS rebinding 时间窗；重定向**逐跳**重新校验；**禁止 https → http** 协议降级。
2. **下载大小上限**：流式写盘时累计字节数，超 `MAX_DOWNLOAD_BYTES` 立刻中断并删半成品——不是下载完再检查。
3. **TLS 不降级**：证书问题直接失败并说明原因，绝不用 `verify=False` 换成功率。
4. **请求体早拒**：有 `Content-Length` 且已超限 → 一字节都不收；没有声明或声明偏小 → 中间件边收边数，超限立刻 413（`utils/body_limit.py`）。只在路由里查的话，框架往往已经把整包缓冲到系统临时目录，磁盘那时已被写满，413 来得太晚。生产前再在反代上设请求体上限（见 RUNBOOK）。

## ADR-0006 · 2026-09-11 · 扫描件 PDF 在 understand 里走页面渲染，但要设上限

`/extract` 遇到没有文字层的 PDF 只声明「无文字层」并给出空正文；`/understand` 把页面渲染成位图交给视觉模型（PyMuPDF，纯 Python，不需要系统工具）。是否启用由 `.env` 的 `RENDERED_PAGE_OCR_ENABLED` 控制，页数上限由独立的 `RENDERED_PAGE_MAX`（默认 10）控制——**开关与上限是两项**，超出部分在 `meta.notes` 里显式声明。上限不是性能优化而是成本护栏（逐页一次视觉调用，成本与页数线性相关）。两条降本手段都是确定性的、先于模型调用：**空白页用 64×64 灰度图的亮度极差判掉**——空白页返回的短文本还会连带触发两次切半重试，一次白跑变三次；**清晰度走自适应**，整页识别为空或明显过短才切上下两半，只有合计更长才采用。否掉「不设上限」与「每页固定切两张」（那会让所有扫描件的成本翻倍）。

## ADR-0007 · 2026-10-02 · 后端按「流程步骤 + 功能域」分包

处理流程按序号排成步骤文件（`pipelines/steps/step1_download` … `step6_assemble`），两条管线只做「按顺序调这几步」；一种格式一个文件收在 `pipelines/processors/`（确定性的 `extract_*` 与它自己的 `enhance_*` 同处一室，调试一种格式只开一个文件）；分派入口就放在用它的那一步（`step4_extract.py` 的 `dispatch()` / `enhance()`），从类型识别跳过去一眼能看出这件事属于哪条管线；静态清单（扩展名 / MIME / 魔数 / ZIP 指纹 / 降级分类）统一进 `configs/formats.py`，代码里不留散落字面量；通用件（文本解码、`LlmError`、计价记账）统一进 `utils/`；**契约跟着它的主人走**且都做成叶子模块（不 import 实现层，任何层都能拿它当数据结构用而不成环）。落点与状态表见 [architecture.md](./architecture.md)。否掉「建独立 `contracts/` 包」（层次最干净，但 `contracts` 不是日常 FastAPI 项目里的词，读接口的人第一眼找不到请求体与响应体）、「保留 `core/` 只拆文件」（改动最小，但名字继续不提供信息，循环依赖也还在）、「按技术角色严格分层 `api/` `service/` `domain/` `infra/`」（十来个实现文件不值四层抽象）。

## ADR-0008 · 2026-10-10 · 音视频接本机 ASR：纯代码给音轨参数，转写文字进 meta.asr_text

把音视频划进退化的两条理由都不成立了：本机起了 Qwen3-ASR 服务（第二条「所用模型没有语音识别能力」失效），转码用 pip 装得上的 `imageio-ffmpeg`（自带静态 ffmpeg 二进制）就能绕开（第一条「缺系统级转码工具」失效）。所以 `/extract` 报音轨参数（时长 / 采样率 / 声道 / 音轨格式；视频报的是**它音轨**的参数，容器里没有可确定性抽取的文本），`content` 为空；`/understand` 把音轨交给 `models/qwen3_asr.py`，转写文字进 `meta.asr_text`，**不进 `content`**——否则 ADR-0002 的逐字节可对比立刻不成立。音轨取自一处：音频文件本身就是音轨，视频与 m4a / aac / wma 先抽一条 16kHz 单声道 wav，抽出的音轨登记进 `result.assets`（`kind="audio_track"`）供增强阶段复用，**一次请求只转码一次**。送音频走 `/asr-b64` 传字节，不把 URL 交给上游代下载（上游没有本项目的超时、体积与 SSRF 基线）。加 `ASR_MAX_AUDIO_SECONDS`（默认 600 秒）上限：ASR 单次输出有 token 上限，长音轨尾部会被静默丢掉，而本项目不静默截断——超限则跳过转写；抽音轨前先探时长，已超限不写完整临时 wav，仍抽取时用 ffmpeg `-t` 封顶。否掉「只为音频接 ASR、视频继续降级」（视频恰恰最需要转写）与「转写文字复用 `meta.ocr_text`」（`ocr_text` 的定义是「模型从图像读出的文字」，塞进语音会让术语边界糊掉）。

## ADR-0010 · 2026-10-10 · 废除单文件行数门禁；processors 一格式族一文件；共享按是否调模型分流

为贴「总行 ≤500 / 代码行 ≤300」把本属同一格式族的实现拆成 `odf.py` + `ods_merges.py` + `ods_visibility.py` 等碎片，并让 `reading.py` 这类跨格式模型逻辑挤进 `processors/`，直接违背 ADR-0007「一格式一文件、调试只开一个文件」。决定：**废除**结构判据 2（全仓不再查单文件行数）；`pipelines/processors/` **只保留格式族文件**（族粒度与今日 `dispatch` 一致，如 `excel.py` 覆盖表格族），为预算拆出的兄弟模块并回所属族文件。共享落点：

- 多格式表格公共件 → `utils/excel/`（`grid` / `cell_text` / `index_runs` / `table_kind` / `html_tables` 等）
- 原 `processors/schema.py` → `utils/processor_schema.py`（含 Detector 等碎片）；HTTP 主体 → `utils/convert_contract.py`；`routers/convert/schema.py` 再导出
- 模型侧公共（原 `reading.py`）→ `models/reading.py`
- 表格公共 → `utils/excel/`
- 预算碎片并回：`xlsx_*`/`ods_*` → `excel.py`；`docx_links` → `docx.py`；`rtf_tables` → `rtf.py`
- `odf_table_rows` → `utils/excel/odf_table_rows.py`（`odf` 正文与 `excel` 的 ODS 路径共用；不并进单一格式族文件）

否掉「只对 processors 豁免」「抬高上限仍留门禁」「新建 `pipelines/shared/`」。
现状：行数门禁已删；公共件与契约已迁出；预算碎片已并回（2026-10-10）；原 `utils/rag/` 与 RAG 口已下线（见 HISTORY）。

## ADR-0011 · 2026-10-10 · understand 正文可含模型转写；双池并发与全链路预算

**正文（`content`）**：`/extract` 只含纯代码抽取段。`/understand` 在同一段之上，把 OCR/ASR/页面识别/语义描述等**模型转写**并进 `content`（`utils/delivery_content.merge_understand_delivery`）；同一转写仍写入 `meta.ocr_text` / `meta.asr_text` 供溯源。若某类源纯代码即可完整交付（如 plain/csv/xlsx 正文），两条路 `content` **仍逐字节相同**。模型不可用时不伪造正文，走降级与 `meta.notes`（ADR-0003）。

**对比产品目标**：前端固定展示文件基础信息；**比正文**看有无模型时的差异；**摘要仅 understand** 产出。

**编排**：模型增强分派在 `pipelines/understand.py` 的 `_run_model_enhance()`，不在 `step4_extract` 做自动发现。

**请求生命周期**：`REQUEST_BUDGET_SEC`（默认 120）为**全链路**单调时钟预算，任一步超时终止；`DOWNLOAD_BUDGET_SEC`（默认 20）仅约束下载层且不超过剩余全链路时间。路由入口设预算（`utils/request_budget.py`）；LLM / OCR / ASR / ffmpeg 单次调用超时取 `min(配置上限, 剩余预算)`（`call_timeout`）。客户端断开取消在途调用不在首版范围。

**并发**：扩展名在图片/音视频集合内走 **media 慢池**（`CONCURRENCY_MEDIA`），其余走 **fast 快池**（`CONCURRENCY_FAST`）；池满 HTTP **429**（`request_concurrency_limit`）。实现见 `utils/concurrency.py`。

否掉「两路 content 永远逐字节相同且模型字只进 meta」（2026-10-10 前文案）与「单一全局 Semaphore」。

## ADR-0012 · 2026-10-10 · 对外部署由下载者自配；仓内只给说明、不附托管脚手架

本仓默认是本机开发形态（`RELOAD` 默认开、单进程、`start.py` + Vite dev）。生产机上的「标明当前是生产环境、再加载另一套配置」属于部署方自己的约定；本服务**不读**单独的「生产开关」环境变量，只认已有项（如 `RELOAD`、`ANYURL2TEXT_HOST`）。公开仓库只在 [RUNBOOK](./RUNBOOK.md) 写清建议怎么关热重载、多 worker、静态前端与反代体积上限，**不附带** Dockerfile / systemd / 完整反代样板——避免把运维成本绑进本仓、也避免假装本仓负责别人的生产。否掉「仓内做一套生产默认进程模型与容器」：下载者的环境与编排各不相同，硬编码一套只会过时且误导。

## ADR-0013 · 2026-10-10 · 后端环境与依赖由 uv 管

后端「装什么、用哪个解释器」统一走仓库根的 `pyproject.toml` + `uv.lock` + 本仓 `.venv`：`uv sync` 装齐，`uv run …` 跑 `start.py` / `backend/app.py` / pytest。`start.py` 若不在本仓 `.venv` 内且 PATH 有 uv，会自动 `uv run` 重入，避免静默用到装错包的 conda/全局 Python。前端仍用 npm，不并进 uv。否掉「任意已激活环境 + `pip install -r backend/requirements.txt`」：多环境并存时最常见的故障是「起得来但缺包 / 起错解释器」，把环节收口到 uv 后路径唯一、锁文件可复现。`backend/requirements.txt` 只留迁移提示，不再当安装清单。
