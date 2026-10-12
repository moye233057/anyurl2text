# anyurl2text

把任意 URL 或本地文件转成文本的服务。默认靠规则抽正文；OCR、ASR、LLM 是可选扩展，由请求开关和扩展是否可用共同决定开不开。

## Language

**转换接口**：
对外唯一业务入口：`POST /convert` 与 `/convert/file`。请求里带 `ocr` / `asr` / `llm`，决定要不要叠加扩展结果。
_Avoid_: extract 接口、understand 接口、对比接口、双路

**确定性抽取**：
不调用模型、只靠规则完成的解析（认格式、还原版面、抽文字、判编码）。文档类关掉扩展时，仍靠这一步拿正文。
_Avoid_: 本地解析、离线解析、extract（作接口名时）

**扩展能力**（extension）：
可选的 OCR / ASR / LLM。远程 API 与本机部署都算扩展；没装、没下权重或开关关着时，不启用。
_Avoid_: 模型服务（作总称时）、插件、侧车、sidecar（作产品名时）

**能力开关**：
请求（或等价配置）上分别控制是否启用 OCR、ASR、LLM。只有「开关开」且「扩展可用」时才会真正调用。
_Avoid_: mode、understand 标志、增强开关（单布尔）

**第三方服务**（`services/`）：
仓库根下、各自独立进程的模型服务：`services/<专栏>/<实现名>/`（专栏如 `ocr`、`asr`）。同一专栏可挂多套实现；**一套实现一个端口**。不装进核心 `uv` 环境。
_Avoid_: optional/、ModelServer（作本仓默认路径时）、侧车仓、sidecar 仓

**扩展出口**（`backend/extensions/`）：
核心里用来调用第三方服务或远程 API 的客户端（DeepSeek / OCR·ASR HTTP 等）。**不含**服务进程与模型权重。
_Avoid_: models/（作包名时）、把服务代码放进 extensions

**服务权重目录**：
每个第三方服务实现自己的 `models/`（如 `services/asr/qwen3-asr/models/`）。远程仓库不收录权重。
_Avoid_: backend/extensions/models、共享权重总目录

**交付文本**（`content`）：
对外主正文。文档类含规则抽出的段落；开启且可用的 OCR/ASR/看图转写按规则并入，并**同时写入** `meta.ocr_text` / `meta.asr_text` 留对照。图片与音视频在对应扩展未启用时只有基础文件信息，`content` 可为空。摘要进 `summary`，不替换 `content`。
_Avoid_: 原文、正文、result

**摘要**（`summary`）：
LLM 对交付文本（或图片客观描述）加工后的结果，与交付文本并存。不带 query 时是通用摘要，带 query 时是针对该问题的回答。LLM 未启用或不可用时为 `null`。
_Avoid_: 总结、answer

**基础文件信息**：
类型、大小、扩展名、文件头，以及图片/音轨等纯结构字段。图片与音视频在未开对应扩展时的正常结果；缺依赖只好浅解析时的降级结果也属此类。是正常结果，不是错误。
_Avoid_: 兜底信息、fallback、占位

**识别文本**（`meta.ocr_text`）：
从图像中读出的字（本机 OCR；文档内嵌图与扫描件还可走视觉/LLM）。启用时并进 `content`，本字段再留一份对照。
_Avoid_: OCR 结果、识别内容、正文

**转写文本**（`meta.asr_text`）：
从音频/视频音轨听出的字（本机 ASR）。启用时并进 `content`，本字段再留一份对照。
_Avoid_: 语音识别结果、ASR 文本、字幕

**音轨**：
音频文件本身，或从视频与 m4a/aac/wma 等容器抽出的 16kHz 单声道声音。规则侧只读参数；ASR 开着时送去转写。抽出的音轨放在请求临时目录，请求结束即清。
_Avoid_: 声道、音频流、sound

**判型**：
独立图片在启用相关扩展时的第一步：区分「文字图片」与「语义图片」。文字图交 OCR；语义图用客观描述；判不准时按文字图片处理。
_Avoid_: 分类、路由、semantic router

**页面渲染**：
把 PDF 页画成图片再交给视觉/LLM，用于没有文字层的扫描件。按页整页画，费用大致随页数增加。
_Avoid_: 全页 OCR、整页识别

**内嵌图**：
文档里嵌着的图片对象（PDF xref、docx 关系、pptx shape）。只处理图本身，与「整页画成图」不同。同一对象被引用多次仍算一张；落盘与 `MAX_EMBEDDED_IMAGES` 按对象计，`meta.images` 记引用次数。
_Avoid_: 文档图片、插图

**处理器**：
处理**同一类格式**的单元（`pipelines/processors/` 下**一类格式一个文件**）。先做确定性抽取；需要扩展时再在同文件里写增强入口，方便一起调试。跨格式的表格公共逻辑进 `utils/excel/`；处理器字段约定进 `utils/processor_schema.py`；扩展调用公共逻辑进 `backend/extensions/`。
_Avoid_: 解析器、抽取器、handler、为预算拆出的碎片文件

**格位**（表格里的逻辑格子）：
表格里一个逻辑位置。内容与跨度只记在起点格子上，被合并盖住的位置只记「指向哪个起点」，合并单元格既不重复文字也不错列。多格式共用 `utils/excel/`。
_Avoid_: 单元格矩阵、merge 表、span 表、格位网格（单独当术语时）

**模态**（响应里的类型）：
响应 `type` 字段的唯一维度：document / image / audio / video / webpage / archive / install_package / file / error。只回答「这是什么」，不回答「用了哪家扩展」。
_Avoid_: 类型、文件类型（作 `type` 字段的别称时）

**上界**（限额）：
「最多允许多少」：字节、字符、行、页、张、条。命中后失败（`error`）、截断（`truncate`）或压到上限内（`clamp`）。判定用的阈值不是上界。全部登记在 `configs/limits.py`，命中名进 `meta.limits_hit`。
_Avoid_: 限制、门限、上限值

**资源防护公共件**：
不解析具体格式、只在资源被吃光前拦住的 `utils/` 模块。上界数值与提示文案仍只在 `configs/limits.py`；模块负责怎么拦住。现例：`zip_read`、`body_limit`、文本字节封顶、下载时边写边计数。
_Avoid_: 防护器、安全中间件、limit helper

**用量**（`usage`）：
本次真实产生的计费扩展（如远程 LLM）的 token 与折算费用。本机 OCR/ASR 默认不计费。没有用正文长度冒充 token 的字段。
_Avoid_: inputTokens / outputTokens

**耗时**（`timings`）：
分段实际经过的时间（下载 / 解析 / 模型），用来区分慢在网络还是慢在扩展。
_Avoid_: cost、elapsed、墙钟
