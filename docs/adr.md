# 架构决策（现行）

> **用途**：只记仍有效的「现在为什么这样」。现行约束与落点看 [architecture.md](./architecture.md)。
> **关联**：[HISTORY.md](./HISTORY.md)（被取代或废弃的选定） · [CONTEXT.md](../CONTEXT.md)（术语表） · [API.md](./API.md)（字段契约）
> **基线**：2026-10-02

- 编号不复用：删掉一条就不再出现该编号。
- 日期是该条**内容最后一次实质变更**的日期。2026-10-02 把原 `docs/adr/*.md` 合并进本文件，结论未变的条目保留原日期；0003 与 0007 按当时的现状重写，日期改为当天。
- 现状落点与状态不在本文：那在 [architecture.md](./architecture.md)。

## ADR-0001 · 2026-09-11 · 响应契约另起一套，摘要不与正文混放

响应恒为 7 个字段 `type / content / summary / meta / usage / timings / error`：`summary` 只由 understand 产出，`content` 是抽取出的原文、任何接口任何参数下都不被覆盖、也不追加实现细节前缀，`error` 恒存在（成功为 `null`）——调用方不必判两种响应形状。这样「同一份源、两种代价」才能逐字节对比，摘要与原文也各有各的位置。否掉 `{type, content, inputTokens, outputTokens}` 那套契约：那条契约带 query 时用摘要覆盖正文（原文永久丢失）、把引擎实现细节硬编码进正文、成功 4 键失败 5 键、`usage` 里塞的不是 token。

## ADR-0002 · 2026-09-12 · understand 复用 extract 的抽取内核，只做增量

两个接口共用同一段下载、识别、解析代码，差异被限制在「是否调用模型」这一件事上。不共用就会出现两条各自演进的解析实现——同一份 PDF 在两条路下抽出不同文本，而「两条路可对比」正是本项目存在的理由。代价落成两条不可破的规矩：`extract_*` 不调模型、也不感知调用方是谁；`enhance_*` 只能在抽取产出之上追加，**不得改动 `content`**（由 `backend/scripts/check_structure.py` 的静态判据守着）。否掉「单一接口 + `mode` 参数」（调用方从 URL 上看不出代价量级，两条路也无法各自限流与超时）与「understand 独立实现端到端」。

## ADR-0003 · 2026-10-02 · 覆盖范围以「纯 Python 库能否做到」划线

纯 Python 库能解析的一律完整实现；做不到的不报错，而是**降级**为基础文件信息（文件类型 / 大小 / 扩展名 / 文件头），HTTP 仍是 200，`meta.degraded` 与 `degrade_reason` 说明缺的是什么——降级是**能力边界**而不是故障，混进 `error` 会让监控把已知边界当成事故。容器格式必须往里看一层，否则「完整解析」是空头承诺：OLE 头靠扩展名或 Content-Type 分 doc / xls / ppt / wps，ZIP 头读中央目录指纹定 docx / xlsx（带 `xl/vbaProject.bin` 则 xlsm）/ pptx / odt / ods / odp / apk / ipa，认不出才维持「这是个 ZIP」。今天真正缺依赖的只剩老版 Office（doc / ppt / wps，缺 LibreOffice 转换器）——音视频已按 ADR-0008 接上本机 ASR，不再降级。否掉「不支持的类型直接报错」：诚实但不可用，且逼迫调用方自己做类型预判。

## ADR-0004 · 2026-09-11 · extract 对图片只给结构信息，不内置本地 OCR

`/extract` 处理图片返回格式、尺寸、色彩模式、帧数与白名单 EXIF，`content` 为空串、`meta.ocr_text` 为 `null`；认字是 understand 的职责。这条边界让两条路的差别可检验（毫秒级结构信息 vs 秒级内容理解）；在纯代码那一路塞个本地 OCR，差别就退化成「慢一点的 OCR vs 快一点的 OCR」，还会把「本地起两个服务就能用」变成「先下几个模型」（权重动辄数百 MB）。否掉内置 PaddleOCR / RapidOCR / Tesseract 与调用系统自带 OCR（跨平台能力与结果格式不一致，无法稳定验收）。

## ADR-0005 · 2026-09-11 · 下载层四条安全基线不可协商

服务接受用户可控 URL，这四条是必答题，任何为「提高成功率」放松它们的改动都应被拒绝：

1. **SSRF 防护**：每次实际连接前判定 DNS 返回的每一个地址，命中私网 / 回环 / 链路本地 / 保留 / 组播一律拒；重定向**逐跳**重新校验目标，否则外部域名可以 302 到内网。
2. **下载大小上限**：流式写盘时累计字节数，超 `MAX_DOWNLOAD_BYTES` 立刻中断并删半成品——不是下载完再检查。
3. **TLS 不降级**：证书问题直接失败并说明原因，绝不用 `verify=False` 换成功率。
4. **请求体早拒**：上传超限在读取请求体之前按 `Content-Length` 拒掉；只在路由里查的话，Starlette 已经把整个 multipart 缓冲到临时文件，磁盘那时已被写满，413 来得太晚。

## ADR-0006 · 2026-09-11 · 扫描件 PDF 在 understand 里走页面渲染，但要设上限

`/extract` 遇到没有文字层的 PDF 只声明「无文字层」并给出空正文；`/understand` 把页面渲染成位图交给视觉模型（PyMuPDF，纯 Python，不需要系统工具），**默认最多 10 页**、由 `.env` 开关控制，超出部分在 `meta.notes` 里显式声明。上限不是性能优化而是成本护栏（逐页一次视觉调用，成本与页数线性相关）。两条降本手段都是确定性的、先于模型调用：**空白页用 64×64 灰度图的亮度极差判掉**——空白页返回的短文本还会连带触发两次切半重试，一次白跑变三次；**清晰度走自适应**，整页识别为空或明显过短才切上下两半，只有合计更长才采用。否掉「不设上限」与「每页固定切两张」（那会让所有扫描件的成本翻倍）。

## ADR-0007 · 2026-10-02 · 后端按「流程步骤 + 功能域」分包

处理流程按序号排成步骤文件（`pipelines/steps/step1_download` … `step6_assemble`），两条管线只做「按顺序调这几步」；一种格式一个文件收在 `pipelines/processors/`（确定性的 `extract_*` 与它自己的 `enhance_*` 同处一室，调试一种格式只开一个文件）；分派入口就放在用它的那一步（`step4_extract.py` 的 `dispatch()` / `enhance()`），从类型识别跳过去一眼能看出这件事属于哪条管线；静态清单（扩展名 / MIME / 魔数 / ZIP 指纹 / 降级分类）统一进 `configs/formats.py`，代码里不留散落字面量；通用件（文本解码、`LlmError`、计价记账）统一进 `utils/`；**契约跟着它的主人走**且都做成叶子模块（不 import 实现层，任何层都能拿它当数据结构用而不成环）。落点与状态表见 [architecture.md](./architecture.md)。否掉「建独立 `contracts/` 包」（层次最干净，但 `contracts` 不是日常 FastAPI 项目里的词，读接口的人第一眼找不到请求体与响应体）、「保留 `core/` 只拆文件」（改动最小，但名字继续不提供信息，循环依赖也还在）、「按技术角色严格分层 `api/` `service/` `domain/` `infra/`」（十来个实现文件不值四层抽象）。

## ADR-0008 · 2026-09-12 · 音视频接本机 ASR：纯代码给音轨参数，转写文字进 meta.asr_text

把音视频划进退化的两条理由都不成立了：本机起了 Qwen3-ASR 服务（第二条「所用模型没有语音识别能力」失效），转码用 pip 装得上的 `imageio-ffmpeg`（自带静态 ffmpeg 二进制）就能绕开（第一条「缺系统级转码工具」失效）。所以 `/extract` 报音轨参数（时长 / 采样率 / 声道 / 音轨格式；视频报的是**它音轨**的参数，容器里没有可确定性抽取的文本），`content` 为空；`/understand` 把音轨交给 `models/qwen3_asr.py`，转写文字进 `meta.asr_text`，**不进 `content`**——否则 ADR-0002 的逐字节可对比立刻不成立。音轨取自一处：音频文件本身就是音轨，视频与 m4a / aac / wma 先抽一条 16kHz 单声道 wav，抽出的音轨登记进 `result.assets`（`kind="audio_track"`）供增强阶段复用，**一次请求只转码一次**。送音频走 `/asr-b64` 传字节，不把 URL 交给上游代下载（上游没有本项目的超时、体积与 SSRF 基线）。加 `ASR_MAX_AUDIO_SECONDS`（默认 600 秒）上限：ASR 单次输出有 token 上限，长音轨尾部会被静默丢掉，而本项目不静默截断。否掉「只为音频接 ASR、视频继续降级」（视频恰恰最需要转写）与「转写文字复用 `meta.ocr_text`」（`ocr_text` 的定义是「模型从图像读出的文字」，塞进语音会让术语边界糊掉）。

## ADR-0009 · 2026-09-28 · RAG 抽取用独立契约，不进 ConvertResponse

检索入库需要页列表与目录区间，而 ConvertResponse 的 `content` 必须在 extract / understand 下逐字节一致、不能为 RAG 改形状，所以新增 `POST /rag/extract/file`，响应 `{kind, pages?, text?, catalogue_ranges?, error}`、`error` 恒存在、**不调用模型**。PDF 抽出印刷目录才走 pymupdf4llm 分页加目录区间，抽不出则复用 extract 内核的交付文本。实现落点：路由 `routers/rag/`，编排 `pipelines/rag_extract.py`，目录规则 `processors/catalogue.py`，分页 `processors/pdf_pages.py`，契约 `routers/rag/schema.py`。代价是多一份契约与一条管线，对比界面不渲染此口。
