# 想改东西？先看这里

> **用途**：最常见的六类改动各自该动哪些文件、按什么顺序、怎么算改完。
> **关联**：[architecture.md](./architecture.md)（模块落点与四条铁律） · [adr.md](./adr.md)（别推翻的为什么） · [API.md](./API.md)（契约） · [PIPELINES.md](./PIPELINES.md)（现有各类型的处理链路）
> **基线**：2026-09-12

---

每节的结构相同：**动哪些文件 → 顺序 → 完成标准**。完成标准是可核查的判据，别凭「主要路径通了」收工。

改完任何一处，先跑这两条命令，再谈人工验收（清单见 [RUNBOOK.md](./RUNBOOK.md#5-验收清单)）：

```bash
cd backend
cd backend
uv run python -m pytest tests -q            # 默认含 HTTP 缝（13 条），**后端须在 8020 应答**；只要进程内自检则加 --ignore=tests/test_extract_contract.py
uv run python -m pytest tests -m model -q   # 贵缝：ADR-0011 正文对比 + 模型增强产出（服务已起，真调模型与本机 OCR / ASR）
```

进程内自检（结构判据 + 各格式抽取 + 判型 + 通用件）本身不联网、不调模型；但 `pytest tests -q` 若不加 `--ignore`，会连同 HTTP 缝一起跑。

---

## 1. 加一种格式的解析

例：让 `.webp` 之外还想支持 `.avif`。

**动四个地方**：

1. `backend/pipelines/processors/` —— 新建一个文件（文件名按格式取，一眼能懂：`excel.py`、`html.py`、`docx.py`……），写 `extract_<格式>(path, ctx) -> ExtractResult`。
   需要模型素材就在 `ctx.prepare_assets` 为真时把图落盘并填进 `result.assets`；不需要就忽略 `ctx`。
   落盘要**按来源部件去重**（docx 用关系 id、pptx 用图片部件 `sha1`、pdf 用 xref）：同一张图被引多次
   只落一次，`MAX_EMBEDDED_IMAGES` 才算在「不同的图」上，否则重复引用会把后面的图挤出上限。
   如果这种格式的**模型处理与通用素材读取不同**（例如独立图片要先判型），再在同一个文件里写
   `enhance_<格式>(client, …) -> EnhanceOutcome`；通用情况不用写，`models/reading.py` 的 `enhance_assets()` 已经覆盖。
2. `backend/pipelines/steps/step4_extract.py` 的 `dispatch()` —— 在对应分组里加一行指向 `extract_*`。
3. 若该格式有**专属**模型逻辑（与 `image.enhance_image` / `media.enhance_media` / `enhance_assets` 不同），在 `backend/pipelines/understand.py` 的 `_run_model_enhance()` 里**显式加一支**；出现第 4 个这类分支时再考虑注册表（ADR-0011）。
4. `backend/configs/formats.py` —— 把扩展名加进对应的格式集合（`IMAGE_EXTENSIONS` 等）；如果是容器格式，`MAGIC_PREFIXES` 里补一行魔数。**容器头常常分不出家族成员**（OLE 里的 doc/xls/ppt、ZIP 里的 docx/xlsx/odt/apk），这种情况还要补一条容器内部指纹表——ZIP 补 `ZIP_ENTRY_FLAVORS` 或 `ODF_EXTENSION_BY_MIMETYPE`，OLE 补 `pipelines/steps/ole_directory.py` 的 `STREAM_EXTENSIONS`（目录流名 → 扩展名），并在 `resolve_extension()` 里接上退回分支；漏了这步，新格式会被判成容器本身（`zip` / `ole`），处理器永远进不去（ADR 0003）。

**完成标准**：

- `cd backend` 后 `uv run python -m pytest tests -q` 全过（后端在跑；或加 `--ignore=tests/test_extract_contract.py` 只跑进程内）：结构判据（`tests/test_structure.py`）与各格式抽取、判型、通用件；其中「扩展名覆盖」就是替你比对两个集合，别靠肉眼。
- 用真实文件跑一次两个接口：`type` 正确；extract 的 `content` 符合 ADR-0004（图片类 extract 可为空）；understand 按 ADR-0011 合并模型转写。
- `meta.ext` 是具体格式而不是容器名（`zip` / `ole`），`meta.engine` 是该格式的解析库——只看「请求成功」会漏掉整族格式被判成压缩包这类错误。
- 若新格式**纯代码即可完整交付正文**，两条路 `content` 仍应相同；若需模型转写，understand 更长且 OCR/ASR 双写 `meta.*`（见 `test_understand_stubbed.py` / 贵缝）。
- 在 `backend/tests/test_processors_by_format.py` 的 `DOCUMENT_FIXTURES` 里加一行，并在该文件里补一条断言，让这个格式以后每次都被自动验到。

## 2. 加一次模型调用

例：给某类源再加一次「提取关键字段」。

**动三个地方**：

1. `backend/models/prompts/` —— 加一个提示词模板文件。要求模型输出 JSON 时，模板里**必须出现 `json` 字样并给结构示例**，否则模型可能一直吐空白。
2. `backend/pipelines/understand.py`（编排）或某个处理器的 `enhance_*`（按格式的模型处理）——调 `client.chat(...)`，产出进 `EnhanceOutcome`；组装前由 `merge_understand_delivery()` 并进 understand 的 `content`，且仍写 `meta.ocr_text` / `meta.asr_text`（ADR-0011）。
3. 需要新字段就按第 3 节走契约改动流程。

**注意**：

- 每次调用都必须显式给 `max_tokens`——不给的话非思考模式的默认上限会把长输出静默截断。
- 思考模式默认是关的。要开就开在真正受益的阶段（如按 query 作答），别全局开：思维链按输出侧计费。
- 耗时记进 `timings.model_ms`，用量由 `client.ledger` 自动记账（`models/deepseek.py`），不要另写一套统计。

**完成标准**：

- 一次调用后 `usage.calls` 加一、`cost_cny` 大于 0。
- 单条请求的成本在 `backend/data/` 里的样例上量一次，确认在可接受范围。
- 不要改 `ExtractResult.content` 的语义；understand 的正文合并只在 `UnderstandPipeline` 组装前做。

## 3. 改响应契约

**顺序不能颠倒**，否则会留下半套新名。

```
routers/convert/schema.py    改字段定义（唯一事实源）
  ↓
pipelines/steps/step6_assemble.py   build_response() 里填进去
  ↓
routers/                     （一般不用动，路由只透传）
  ↓
frontend/src/types.ts        同步镜像类型，字段名逐个对齐
  ↓
frontend/src/ResultPanel.tsx 界面渲染
```

**完成标准**：

- `npx tsc --noEmit` 与 `npx vite build` 都通过。
- 界面里新字段真的显示出来了（别改了类型忘了渲染）。

## 4. 改界面

先读 [architecture.md](./architecture.md) 的四条铁律。界面**只渲染契约字段**，不发明字段、不改写 `content`。

本项目界面有两条自己的约束：

- **固定区域 + 单滚动条**：区域按屏幕分配高度、底色铺满，整页不滚动；只有真的超出时才出滚动条。给每个子块各自加滚动条会让固定块吃光高度，把最该读的长文本压成两行。
- **动作归属到各自的区域**：每栏有自己的运行按钮与 loading，不要合并成一个全局按钮——两栏的结果与耗时才能互不干扰地对比。

**完成标准**：在浏览器里量几何而不是凭印象——整页不滚动、每个区域只在自己溢出时出滚动条、零横向溢出；每个改动的交互都真点过一遍。

## 5. 换模型供应商

例：从 DeepSeek 换成另一家 OpenAI 兼容的服务。

**动两个地方**：`.env` 里的三件套（`DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` / `DEEPSEEK_API_KEY`），以及 `backend/models/deepseek.py`（换模型名时它自己的事实依据注释也要跟着改）。

**要确认的三件事**：

1. 目标模型**能读图**。本项目只有一个模型同时吃文本与图片；换成一个纯文本模型，图片与扫描件那两条链会失败。
2. **单价表**在 `backend/models/deepseek.py` 的 `PRICE_TABLE` 里（第三方 DeepSeek 专属）。换了模型要新增一条记录（**不要改旧记录**，旧记录按生效日留着历史可复核）。没配的记录成本折算会返回 0，`usage` 里的金额就不可信。单请求费用/token 闸用 `DEEPSEEK_COST_LIMIT_ENABLED` / `DEEPSEEK_TOKEN_LIMIT_ENABLED` 与对应阈值控制；本机 OCR/ASR 不走这道闸。
3. **图片传入方式**：本项目统一自己下载后传 base64（`models/images.py`），不把 URL 交给服务端代下载——服务端代下载对鉴权、防盗链、内网、慢跳转都不可靠。

**完成标准**：

- 两个接口各跑一次含图与不含图的源，`usage` 里的 token 与金额都非零。
- 跑一次扫描件，确认 `meta.ocr_text` 有内容且 `meta.notes` 里的页数声明正确。
- 成本折算与目标供应商的价目表核对一次。单价一个季度改一次不稀奇，表里记着生效日就是为这个。

## 6. 加一个模型出口

例：再接一个本机模型服务。Paddle OCR 就是按这一节接进来的（见下），可当范本。

**动两个地方**：

1. `backend/models/<模型>.py`（新建）—— 一个模型一个文件，这是 ADR 0007 定的形态。它只做出口该做的事：把输入发给服务、拿回结果、出错时抛 `utils/errors.py` 的 `LlmError`。**不写业务编排**——什么时候调它是处理器的事。
   计价与记账写在该模型文件里（DeepSeek 即 `deepseek.py`）；本机服务不计 token 费用（如 OCR）时不记账、也不伪造 token 数，`usage` 只反映 DeepSeek 调用。
2. `backend/pipelines/processors/<格式>.py` 的 `enhance_*()` —— 由它决定什么时候调这个出口，并把「走了哪条分支」写进 `EnhanceOutcome.notes`。

**已接好的 Paddle OCR**：出口在 `backend/models/paddle_ocr.py`（本机 `POST /ocr-b64`，纵向超长图在本地切片并发识别；地址与超时见 `.env` 的 `OCR_API_URL` / `OCR_TIMEOUT_SEC`）；编排在 `pipelines/processors/image.py` 的 `enhance_image()`——独立图片先用视觉模型判型（提示词 `models/prompts/classify_image.txt`）：判为语义图片就用那次调用给出的描述，判为文字图片就交 Paddle OCR 读字；判型输出解析不出来时按文字图片处理。

**已接好的 Qwen3-ASR（音视频）**：出口在 `backend/models/qwen3_asr.py`（本机 `POST /asr-b64`，自己抽音轨后传字节，不用服务的 `/asr-url`；地址与超时见 `.env` 的 `ASR_API_URL` / `ASR_TIMEOUT_SEC`）；编排在 `pipelines/processors/media.py`：`extract_media()` 取到音轨（音频直接读，视频与容器音频用 `utils/ffmpeg.py` 抽）并报出 `meta.audio`，音轨同时登记进 `result.assets`（`kind="audio_track"`），`enhance_media()` 拿它转写，文字进 `meta.asr_text`。抽音轨靠 `imageio-ffmpeg`，缺它或抽不出音轨时退回降级（`meta.degraded` 为 `true`）并在 `meta.notes` 里说明；ASR 服务没起只是转写失败，原因同样写进 `meta.notes`，`meta.degraded` 仍为 `false`。

**完成标准**：

- `cd backend` 后 `uv run python -m pytest tests -q` 全过：结构判据（`tests/test_structure.py`）查依赖方向等；**不再**查单文件行数（ADR-0010）。新格式进 `processors/` 一族一文件，跨格式表格逻辑进 `utils/excel/`。
- 外部服务连不上不该让整条请求 500：`tests/test_understand_contract.py` 里已有独立图片判型与音视频转写两类用例；再手工把服务停掉打一张含文字的图 / 一段音频，`meta.notes` 应说明原因且 HTTP 仍是 200。
- 确认 understand 的正文合并符合 ADR-0011：纯代码段保留，模型转写并进 `content` 且双写 `meta.ocr_text` / `meta.asr_text`。
