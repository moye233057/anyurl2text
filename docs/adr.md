# 架构决策（现行）

> **用途**：只记仍有效的「现在为什么这样」。现行约束与落点看 [architecture.md](./architecture.md)。
> **关联**：[HISTORY.md](./HISTORY.md) · [CONTEXT.md](../CONTEXT.md) · [API.md](./API.md)
> **基线**：2026-10-02

- 编号不复用：删掉一条就不再出现该编号。
- 日期是该条**内容最后一次实质变更**的日期。

## ADR-0001 · 2026-10-10 · 响应契约另起一套，摘要不与正文混放

响应恒为 7 个字段 `type / content / summary / meta / usage / timings / error`：`summary` 只由 **LLM 扩展**在启用且可用时产出；`content` 不被摘要覆盖、也不追加实现细节前缀；`error` 恒存在（成功为 `null`）。`error` 非空时是结构化 `{code, message, param}`：`message` 给人看、`code` 给程序看（码表在 `configs/errors.py`）。否掉带 query 时用摘要覆盖正文、成功/失败不同键集、以及把引擎细节写进正文的旧契约。

## ADR-0002 · 2026-10-10 · 单一转换接口复用抽取内核；扩展只做增量

下载、识别、确定性解析只有一份实现；差异被限制在「哪些扩展开关打开且可用」。`extract_*` 不调扩展、不感知开关。否掉「understand 独立实现端到端」与「靠双 URL 表达代价」。对外只保留统一转换接口（路径与字段见 API / 蓝图）；旧双路对比产品身份已废（见 HISTORY）。

## ADR-0003 · 2026-10-04 · 覆盖范围以「纯 Python 库能否做到」划线

纯 Python 库能解析的一律完整实现；做不到的不报错，而是**降级**为基础文件信息，HTTP 仍是 200，`meta.degraded` 与 `degrade_reason` 说明缺什么。容器格式必须往里看一层（OLE 流名 / ZIP 指纹）。今天真正缺不可替代依赖的只剩老版 Office（doc / ppt / wps）。音视频在 ASR 扩展可用时转写；缺 `soundfile` / `imageio-ffmpeg` 或抽不出音轨时同样降级。否掉「不支持的类型直接报错」。

## ADR-0004 · 2026-10-10 · 图片默认只给结构信息；OCR 走可选扩展

未启用 OCR（及所需的判型/视觉）扩展时，图片只返回格式、尺寸、色彩模式、帧数与白名单 EXIF，`content` 为空、`meta.ocr_text` 为 `null`。认字与语义描述是扩展职责，且权重不进远程仓。否掉把 OCR 打进核心必装依赖，以及调用系统自带 OCR（跨平台结果不稳）。

## ADR-0005 · 2026-10-10 · 下载层四条安全基线不可协商

服务接受用户可控 URL，这四条是必答题，任何为「提高成功率」放松它们的改动都应被拒绝：

1. **SSRF 防护**：每次实际连接前判定 DNS 返回的每一个地址，命中私网 / 回环 / 链路本地 / 保留 / 组播一律拒；地址无法识别亦拒（fail-closed）；**解析一次并把连接钉到该 IP**（Host / SNI 仍用原主机名）；重定向逐跳校验；**禁止 https → http** 协议降级。
2. **下载大小上限**：流式写盘累计字节，超 `MAX_DOWNLOAD_BYTES` 立刻中断并删半成品。
3. **TLS 不降级**：证书问题直接失败，绝不用 `verify=False`。
4. **请求体早拒**：有 `Content-Length` 且已超限 → 一字节都不收；否则边收边数，超限 413（`utils/body_limit.py`）。生产前再在反代上设上限（见 RUNBOOK）。

## ADR-0006 · 2026-10-10 · 扫描件 PDF 的页面渲染走视觉/LLM 扩展，且设上限

无文字层的 PDF：未启用相应扩展时只声明「无文字层」并空正文；启用后把页面渲染成位图交给视觉/LLM 扩展。开关与页数上限是两项（如 `RENDERED_PAGE_OCR_ENABLED` / `RENDERED_PAGE_MAX`），超出部分在 `meta.notes` 声明。空白页与清晰度自适应降本手段保留。否掉「不设上限」与「每页固定切两张」。

## ADR-0007 · 2026-10-02 · 后端按「流程步骤 + 功能域」分包

处理流程按序号排成步骤文件；管线只做按序调度；一种格式一个文件收在 `pipelines/processors/`；静态清单进 `configs/formats.py`；通用件进 `utils/`；契约做叶子模块。落点见 [architecture.md](./architecture.md)。否掉独立 `contracts/` 包、保留空洞的 `core/`、以及十来个文件不值的四层 `api/service/domain/infra`。

## ADR-0008 · 2026-10-10 · 音视频：结构信息默认；ASR 扩展启用时转写并进 content

确定性侧报告音轨参数（时长 / 采样率 / 声道 / 格式），`content` 为空。ASR 扩展开启且可用时，音轨交本仓 ASR 模块，转写进 `meta.asr_text` **并并进 `content`**（与 OCR 同源策略，ADR-0011/0014）。音轨只转码一次；送字节不送 URL 代下载；`ASR_MAX_AUDIO_SECONDS` 防静默截断。否掉「只为音频接 ASR、视频继续降级」与「转写复用 `meta.ocr_text`」。

## ADR-0010 · 2026-10-10 · 废除单文件行数门禁；processors 一格式族一文件；共享按是否调扩展分流

废除结构判据 2；`pipelines/processors/` 只保留格式族文件。表格公共 → `utils/excel/`；契约 → `utils/processor_schema.py` / `convert_contract.py`；扩展侧公共 → `extensions/`（或蓝图敲定的出口包名）。否掉「只对 processors 豁免」「抬高上限仍留门禁」「新建 `pipelines/shared/`」。

## ADR-0011 · 2026-10-10 · 扩展转写可并进 content；双池并发与全链路预算

**正文**：在确定性段之上，把本次实际跑过的 OCR/ASR/页面识别/语义描述等转写并进 `content`（`utils/delivery_content`）；并双写 `meta.ocr_text` / `meta.asr_text`。纯代码已完整交付时，不开扩展则 `content` 保持该段。扩展不可用时不伪造正文，走降级与 `meta.notes`。

**编排**：扩展分派在统一管线显式进行，不在 `step4_extract` 自动发现。

**预算**：`REQUEST_BUDGET_SEC` 全链路；`DOWNLOAD_BUDGET_SEC` 仅下载；单次调用超时取 `min(配置上限, 剩余预算)`。

**并发**：图片/音视频走 media 慢池，其余走 fast 快池；池满 429。

否掉「模型字永远只进 meta、永不进 content」与「单一全局 Semaphore」。双路对比产品目标已废（见 HISTORY / ADR-0014）。

## ADR-0012 · 2026-10-10 · 对外部署由下载者自配；仓内只给说明、不附托管脚手架

本仓默认本机开发形态。公开仓只在 RUNBOOK 写建议，**不附带** Dockerfile / systemd / 完整反代样板。否掉「仓内做一套生产默认进程模型与容器」。

## ADR-0013 · 2026-10-10 · 后端核心环境与依赖由 uv 管

核心后端走仓库根 `pyproject.toml` + `uv.lock` + `.venv`。前端仍用 npm。扩展模块可自带环境，不强制并进核心 lock。否掉「任意 conda/pip + requirements.txt 当唯一安装路径」。

## ADR-0014 · 2026-10-10 · 统一转换接口 + 仓内可选扩展（OCR/ASR/LLM）

**产品**：取消双路对比；对外 `POST /convert` 与 `/convert/file`，请求字段 `ocr` / `asr` / `llm`（默认 `false`）。文档类关扩展仍做确定性抽取；图片与音视频在看图/ASR 未实际跑时只给基础文件信息。迁移完成即删除旧 `/extract*` `/understand*`，不留别名。

**开关矩阵**：凡看图支路（独立图判型/OCR/语义描述、扫描件整页、内嵌图）要求 `ocr && llm`；音视频转写仅 `asr`；摘要/query 仅 `llm`。缺开关或扩展不可用则跳过并写 `meta.notes`，不静默半套。 **（本条矩阵已由 ADR-0015 取代：看图只看 `llm`；`ocr` 改为提供商单选。）**

**扩展**：OCR/ASR **服务进程**在仓库根 `services/{ocr,asr}/<实现名>/`（专栏下可多实现，一实现一端口）；权重在该实现自己的 `models/`（不进 git）。**出口客户端**只在 `backend/extensions/`。默认由 `start.py` 拉起管理子进程，亦可按 `services/` README 手动起。LLM 走同一客户端：远程 DeepSeek 或本机 OpenAI 兼容端点（换 `BASE_URL`）。

**否掉**：双栏对比主路径；权重进 git；默认依赖外部 ModelServer 仓；旧路由长期别名；看图只开 `ocr` 或只开 `llm` 仍强行半跑。

## ADR-0015 · 2026-10-11 · 扩展按「能力 → 提供商」注册；OCR 改为提供商单选，探活决定谁能用

**背景**：扩展既有本机侧车（Paddle OCR / Qwen3-ASR），也有第三方 API（DeepSeek）。此前「谁能干这件事」是写死的：`ocr` 只是个布尔，独立文字图一律交本机 Paddle——于是配了 API key 也绕不开侧车，侧车没起就整条路失败；而「某项现在能不能用」只能等用户点了运行、由失败信息告诉他。

**决定**：

1. 能力与提供商的**名字**在 `configs/extensions.py` 声明一次（能力 `llm` / `ocr` / `asr`；OCR 有两家：`deepseek` 模型视觉、`paddle` 本机侧车）；探活、单选与能力快照在 `extensions/registry.py`。名单放 `configs/` 是因为契约层与路由层按依赖单向（ADR-0010）都到不了 `extensions/`。
2. 请求契约里 `ocr` 变成**提供商单选**：`false` / `true`（用默认）/ 提供商 id；同一请求只能选一个，非法名字当场 422。
3. `/probe/health` 增加 `capabilities`（每个提供商的 `available` / `reason` / `detail`），前端据此把不可用项置灰并写明原因。
4. **探活口径**：侧车探 `GET <origin>/health`（本机、不花钱）；API 只看密钥是否配置，**绝不发模型请求**——「探针不带副作用」优先于「显示最真实状态」。API 的真实可用性另用记忆的上次调用结果提示，并留出用户主动「测试连接」的位置。
5. **门闩拆开**：看图（判型、语义描述、扫描页、内嵌图）只看 `llm`；`ocr` 只管独立文字图的转写交给谁。
6. 文字转写按**方案 B**：选模型提供商时，文字由判型那次调用一并返回（图本来就在那次调用里），不再单发一次转录请求；只有「被要求转写」时才在提示里要它，选本机 OCR 时不白花这份 token。

**后果**：

- 只配 `DEEPSEEK_API_KEY` 就能覆盖读图的大部分路径，不必起 OCR 侧车；选 `paddle` 则完全不花 token。
- 新增一家实现只需在 `configs/extensions.py` 加一条、在 `extensions/registry.py` 补一个探活分支；路由、界面、契约都不再各写一份名字。
- 侧车的 502 类误报暴露出一件事：本地调用不能走代理。`utils/egress.shared_httpx_client()` 已改为 `trust_env=False`——httpx 在 Windows 上会经 `urllib.request.getproxies()` 读到注册表里的 WinINET 代理（且不认它的 `ProxyOverride` 例外表），把「本机侧车没起」伪装成「上游 502」。
