# 想改东西？先看这里

> **用途**：最常见的六类改动各自该动哪些文件、按什么顺序、怎么算改完。
> **关联**：[architecture.md](./architecture.md)（模块落点与四条铁律） · [adr.md](./adr.md)（别推翻的为什么） · [API.md](./API.md)（契约） · [PIPELINES.md](./PIPELINES.md)（现有各类型的处理链路）
> **基线**：2026-09-12

---

每节的结构相同：**动哪些文件 → 顺序 → 完成标准**。完成标准是可核查的判据，别凭「主要路径通了」收工。

改完任何一处，先跑这两条命令，再谈人工验收（清单见 [RUNBOOK.md](./RUNBOOK.md#5-验收清单)）：

```bash
cd backend
python -m pytest tests -q            # 自检缝：结构判据（tests/test_structure.py）+ 各格式抽取 + 判型 + 通用件；免费、不起服务、不联网、不调模型
python -m pytest tests -m model -q   # 贵缝：两条路 content 逐字节一致 + 模型增强产出（服务已起，真调模型与本机 OCR / ASR）
```

---

## 1. 加一种格式的解析

例：让 `.webp` 之外还想支持 `.avif`。

**动三个地方**：

1. `backend/pipelines/processors/` —— 新建一个文件（文件名按格式取，一眼能懂：`excel.py`、`html.py`、`docx.py`……），写 `extract_<格式>(path, ctx) -> ExtractResult`。
   需要模型素材就在 `ctx.prepare_assets` 为真时把图落盘并填进 `result.assets`；不需要就忽略 `ctx`。
   如果这种格式的**模型处理与通用素材读取不同**（例如独立图片要先判型），再在同一个文件里写
   `enhance_<格式>(client, …) -> EnhanceOutcome`；通用情况不用写，`pipelines/processors/reading.py` 的 `enhance_assets()` 已经覆盖。
2. `backend/pipelines/steps/step4_extract.py` 的 `dispatch()` —— 在对应分组里加一行指向它；有专属模型增强时不用改这里（`enhance()` 会按格式找到 `<格式>.enhance_*`，没有的走通用读素材）。
3. `backend/configs/formats.py` —— 把扩展名加进对应的格式集合（`IMAGE_EXTENSIONS` 等）；如果是容器格式，`MAGIC_PREFIXES` 里补一行魔数。**容器头常常分不出家族成员**（OLE 里的 doc/xls/ppt、ZIP 里的 docx/xlsx/odt/apk），这种情况还要补一条容器内部指纹表——ZIP 补 `ZIP_ENTRY_FLAVORS` 或 `ODF_EXTENSION_BY_MIMETYPE`，OLE 补 `pipelines/steps/step2_detect.py` 的 `resolve_extension()` 里的分支；漏了这步，新格式会被判成容器本身（`zip` / `ole`），处理器永远进不去（ADR 0003）。

**完成标准**：

- `python -m pytest tests -q` 全过：结构判据（`tests/test_structure.py`）与各格式抽取、判型、通用件；其中「扩展名覆盖」就是替你比对两个集合，别靠肉眼。
- 用真实文件跑一次两个接口：`type` 正确、`content` 非空（或对图片类为空的语义正确，见 [adr.md](./adr.md) 的 ADR-0004）。
- `meta.ext` 是具体格式而不是容器名（`zip` / `ole`），`meta.engine` 是该格式的解析库——只看「请求成功」会漏掉整族格式被判成压缩包这类错误。
- 同一个文件两条路的 `content` 逐字节相同（`tests/test_understand_contract.py` 覆盖；它是贵缝、要真模型，新加的格式自己再验一次）。
- 在 `backend/tests/test_processors_by_format.py` 的 `DOCUMENT_FIXTURES` 里加一行，并在该文件里补一条断言，让这个格式以后每次都被自动验到。

## 2. 加一次模型调用

例：给某类源再加一次「提取关键字段」。

**动三个地方**：

1. `backend/models/prompts/` —— 加一个提示词模板文件。要求模型输出 JSON 时，模板里**必须出现 `json` 字样并给结构示例**，否则模型可能一直吐空白。
2. `backend/pipelines/understand.py`（编排）或某个处理器的 `enhance_*`（按格式的模型处理）——调 `client.chat(...)`，结果只往四处放：`meta.ocr_text`、`meta.asr_text`、`summary`、`meta.notes`，**绝不动 `content`**。`tests/test_structure.py` 的 `test_enhance_functions_never_touch_content` 会查 `enhance_*` 有没有碰它。
3. 需要新字段就按第 3 节走契约改动流程。

**注意**：

- 每次调用都必须显式给 `max_tokens`——不给的话非思考模式的默认上限会把长输出静默截断。
- 思考模式默认是关的。要开就开在真正受益的阶段（如按 query 作答），别全局开：思维链按输出侧计费。
- 耗时记进 `timings.model_ms`，用量由 `client.ledger` 自动记账（`utils/usage.py`），不要另写一套统计。

**完成标准**：

- 一次调用后 `usage.calls` 加一、`cost_cny` 大于 0。
- 单条请求的成本在 `backend/data/` 里的样例上量一次，确认在可接受范围。
- 两条路的 `content` 仍然逐字节相同（这是本节最容易踩坏的地方）。

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
2. **单价表**在 `backend/configs/settings.py` 的 `PRICE_TABLE` 里。换了模型要新增一条记录（**不要改旧记录**，旧记录按生效日留着历史可复核）。没配的记录成本折算会返回 0，`usage` 里的金额就不可信。
3. **图片传入方式**：本项目统一自己下载后传 base64（`models/images.py`），不把 URL 交给服务端代下载——服务端代下载对鉴权、防盗链、内网、慢跳转都不可靠。

**完成标准**：

- 两个接口各跑一次含图与不含图的源，`usage` 里的 token 与金额都非零。
- 跑一次扫描件，确认 `meta.ocr_text` 有内容且 `meta.notes` 里的页数声明正确。
- 成本折算与目标供应商的价目表核对一次。单价一个季度改一次不稀奇，表里记着生效日就是为这个。

## 6. 加一个模型出口

例：再接一个本机模型服务。Paddle OCR 就是按这一节接进来的（见下），可当范本。

**动两个地方**：

1. `backend/models/<模型>.py`（新建）—— 一个模型一个文件，这是 ADR 0007 定的形态。它只做出口该做的事：把输入发给服务、拿回结果、出错时抛 `utils/errors.py` 的 `LlmError`。**不写业务编排**——什么时候调它是处理器的事。
   计价与记账共用 `utils/usage.py`；服务不计 token 费用（如 OCR）时不记账、也不伪造 token 数，`usage` 只反映 DeepSeek 调用。
2. `backend/pipelines/processors/<格式>.py` 的 `enhance_*()` —— 由它决定什么时候调这个出口，并把「走了哪条分支」写进 `EnhanceOutcome.notes`。

**已接好的 Paddle OCR**：出口在 `backend/models/paddle_ocr.py`（本机 `POST /ocr-b64`，纵向超长图在本地切片并发识别；地址与超时见 `.env` 的 `OCR_API_URL` / `OCR_TIMEOUT_SEC`）；编排在 `pipelines/processors/image.py` 的 `enhance_image()`——独立图片先用视觉模型判型（提示词 `models/prompts/classify_image.txt`）：判为语义图片就用那次调用给出的描述，判为文字图片就交 Paddle OCR 读字；判型输出解析不出来时按文字图片处理。

**已接好的 Qwen3-ASR（音视频）**：出口在 `backend/models/qwen3_asr.py`（本机 `POST /asr-b64`，自己抽音轨后传字节，不用服务的 `/asr-url`；地址与超时见 `.env` 的 `ASR_API_URL` / `ASR_TIMEOUT_SEC`）；编排在 `pipelines/processors/media.py`：`extract_media()` 取到音轨（音频直接读，视频与容器音频用 `utils/ffmpeg.py` 抽）并报出 `meta.audio`，音轨同时登记进 `result.assets`（`kind="audio_track"`），`enhance_media()` 拿它转写，文字进 `meta.asr_text`。抽音轨靠 `imageio-ffmpeg`，缺它或抽不出音轨时退回降级（`meta.degraded` 为 `true`）并在 `meta.notes` 里说明；ASR 服务没起只是转写失败，原因同样写进 `meta.notes`，`meta.degraded` 仍为 `false`。

**完成标准**：

- `python -m pytest tests -q` 全过：结构判据（`tests/test_structure.py`）里的新文件同样受单文件预算与依赖方向约束。
- 外部服务连不上不该让整条请求 500：`tests/test_understand_contract.py` 里已有独立图片判型与音视频转写两类用例；再手工把服务停掉打一张含文字的图 / 一段音频，`meta.notes` 应说明原因且 HTTP 仍是 200。
- 确认模型增强那条路的 `content` 仍然逐字节等于纯代码那条路——OCR 读出的文字进 `meta.ocr_text`，语音转写的文字进 `meta.asr_text`，两者都不进 `content`。

## 7. 改 RAG 抽取

检索入库口在 `POST /rag/extract/file`，契约是 `routers/rag/schema.py`，编排是 `pipelines/rag_extract.py`。印刷目录规则在 `pipelines/processors/catalogue.py`，分页在 `pdf_pages.py`。

**不要**把页列表或目录区间写进 ConvertResponse。改完跑自检缝（`cd backend && python -m pytest tests -q`，结构判据在 `tests/test_structure.py`），再用真实手册 PDF 打一次该口，确认 `catalogue_ranges` 非空。
