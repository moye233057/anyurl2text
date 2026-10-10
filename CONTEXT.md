# anyurl2text

把任意 URL 或本地文件转成文本的服务。默认做确定性抽取；OCR、ASR、LLM 都是可选扩展能力，由开关与是否具备对应扩展共同决定是否启用。

## Language

**转换接口**：
对外唯一业务入口：`POST /convert` 与 `/convert/file`。请求体携带 `ocr` / `asr` / `llm`，决定是否叠加扩展产出。实现迁移前代码里可能仍暂挂旧名。
_Avoid_: extract 接口、understand 接口、对比接口、双路

**确定性抽取**：
不需要模型的解析行为本身（格式识别、版面还原、文本提取、编码判定）。文档类在关扩展时仍走这条路拿正文。
_Avoid_: 本地解析、离线解析、extract（作接口名时）

**扩展能力**（extension）：
可选的 OCR / ASR / LLM 能力。远程 API 与本机部署都算扩展；未安装、未下载权重或开关关闭时，不启用对应能力。
_Avoid_: 模型服务（作总称时）、插件、sidecar（作产品名时）

**能力开关**：
请求（或等价配置）上分别控制是否启用 OCR、ASR、LLM。只有「开关开」且「扩展可用」时才真正调用。
_Avoid_: mode、understand 标志、增强开关（单布尔）

**扩展模块**：
落在本仓 `optional/` 下、由本仓拉起的 OCR / ASR 实现（代码与进程归属本仓，不依赖使用者另起外部 ModelServer）。
_Avoid_: 外部 ModelServer、辅服务仓、第三方进程（作默认部署名时）

**扩展权重目录**：
第三方模型文件的本机落点：`backend/extensions/models/`。远程仓库不收录权重；该目录勿放 Python 包。
_Avoid_: backup/extensions、ModelScope 缓存路径（作本仓约定时）

**交付文本**（`content`）：
对外主正文。文档类含纯代码抽取段；开启且可用的 OCR/ASR/视觉转写按规则并入，并双写 `meta.ocr_text` / `meta.asr_text`。图片与音视频在对应扩展未启用时只有基础文件信息，`content` 可为空。摘要进 `summary`，不替换 `content`。
_Avoid_: 原文、正文、result

**摘要**（`summary`）：
LLM 扩展对交付文本（或语义图像描述）的加工结果，与交付文本并存。不带 query 时是通用摘要，带 query 时是针对该 query 的回答。LLM 未启用或不可用时为 `null`。
_Avoid_: 总结、answer

**基础文件信息**：
类型、大小、扩展名、magic 头，以及图片/音轨等纯结构字段。图片与音视频在未开对应扩展时的正常产出；缺依赖无法深入解析时的降级产出也属此类。是正常结果，不是错误。
_Avoid_: 兜底信息、fallback、占位

**识别文本**（`meta.ocr_text`）：
从图像中读出的文字（本机 OCR 扩展；文档内嵌图与扫描件页面还可走视觉/LLM 扩展）。启用时并进 `content`，并保留本字段作溯源副本。
_Avoid_: OCR 结果、识别内容、正文

**转写文本**（`meta.asr_text`）：
从音频/视频音轨听出的文字（本机 ASR 扩展）。启用时并进 `content`，并保留本字段作溯源副本。
_Avoid_: 语音识别结果、ASR 文本、字幕

**音轨**：
音频文件本身，或从视频与 m4a/aac/wma 等容器抽出的 16kHz 单声道声音。确定性侧读参数；ASR 扩展启用时送去转写。抽出的音轨落在请求临时目录，随请求清理。
_Avoid_: 声道、音频流、sound

**判型**：
独立图片在启用相关扩展时的第一步：区分「文字图片」与「语义图片」。文字图交 OCR；语义图用客观描述；判不准时按文字图片处理。
_Avoid_: 分类、路由、semantic router

**页面渲染**：
把 PDF 页画成位图再交给视觉/LLM 扩展，用于无文字层的扫描件。按页全幅，成本与页数成正比。
_Avoid_: 全页 OCR、整页识别

**内嵌图**：
文档内部承载的图片对象（PDF xref、docx 关系、pptx shape）。只覆盖图中内容，与页面渲染不同。同一对象被引用多次仍算一张；落盘与 `MAX_EMBEDDED_IMAGES` 按对象计，`meta.images` 记引用次数。
_Avoid_: 文档图片、插图

**处理器**：
一种**格式族**的处理单元（`pipelines/processors/` 下**一个族一个文件**）。提供确定性产出；需要扩展时再提供该格式的增强入口——同文件调试。跨格式表格公共件进 `utils/excel/`；处理器契约进 `utils/processor_schema.py`；扩展调用公共逻辑进 `backend/extensions/`。
_Avoid_: 解析器、抽取器、handler、为预算拆出的碎片文件

**格位**（格位网格）：
表格里一个逻辑位置的表示。内容与跨度只挂在 origin 上，被跨度覆盖的位置只记「指向哪个 origin」，合并单元格既不重复文本也不错列。多格式共用 `utils/excel/`。
_Avoid_: 单元格矩阵、merge 表、span 表

**模态**：
响应 `type` 字段的唯一维度：document / image / audio / video / webpage / archive / install_package / file / error。只回答「这是什么」，不回答「用了哪家扩展」。
_Avoid_: 类型、文件类型

**上界**（限额）：
「最多允许多少」：字节、字符、行、页、张、条。命中后失败（`error`）、截断（`truncate`）或钳制（`clamp`）。阈值不是上界。全部登记在 `configs/limits.py`，命中名进 `meta.limits_hit`。
_Avoid_: 限制、门限、上限值

**资源防护公共件**：
不解析具体格式、只在资源被吃光前挡住的 `utils/` 模块。上界值与文案仍只在 `configs/limits.py`；模块负责强制方式。现例：`zip_read`、`body_limit`、文本字节封顶、下载层流式计数。
_Avoid_: 防护器、安全中间件、limit helper

**用量**（`usage`）：
本次真实产生的计费扩展（如远程 LLM）的 token 与折算成本。本机 OCR/ASR 默认不计费。不存在用内容长度冒充 token 的字段。
_Avoid_: inputTokens / outputTokens

**耗时**（`timings`）：
分段墙钟（下载 / 解析 / 模型），区分慢在网络还是慢在扩展。
_Avoid_: cost、elapsed
