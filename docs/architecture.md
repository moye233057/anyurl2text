# 架构与模块落点（目标态）

> **用途**：项目是什么、明确不做什么、每个模块落在哪个路径、各自什么状态。写代码前读它，与 [adr.md](./adr.md) 一起构成现行约束。
> **关联**：[adr.md](./adr.md)（仍有效的为什么）· [HISTORY.md](./HISTORY.md)（废掉的选定）· [CONTEXT.md](../CONTEXT.md)（术语表）· [API.md](./API.md)（字段契约）· [PIPELINES.md](./PIPELINES.md)（各类型链路）· [RUNBOOK.md](./RUNBOOK.md)（跑起来与验收）
> **基线**：2026-10-02

---

## 1. 系统是什么

把任意 URL 或本地文件转成**尽可能保留原始信息**的文本的本地服务。同一个源有两条**可对比**的路：`/extract` 只用代码做确定性抽取，快、稳、不花钱；`/understand` 在同一份抽取产出之上叠加模型能力，慢且计费。两条路的 `content` **逐字节相同**，所以「同一个源、两种代价」能直接对比。另有一条给检索入库用的独立契约 `/rag/extract/file`，不进这条对比链。

**明确不做**（产品边界，越线要改本文与 ADR）：

- 不做鉴权、多租户、限流、计费——本机自用服务，`ALLOW_PRIVATE_HOSTS=false` 只防 SSRF。
- 不把 URL 交给任何上游代下载：图片自己下载后传 base64，音频自己抽音轨后传字节（ADR-0008）。
- 不在 `/extract` 里放任何 OCR 或语言模型（ADR-0004）；`content` 也不接受任何模型产出。
- 不静默截断：任何截断、跳过、降级都要在 `meta.truncated` / `meta.notes` / `meta.degrade_reason` 里声明。
- 压缩包只列条目，不解压、不递归。
- 老版 Office（doc / ppt / wps）不做深度解析，只降级（ADR-0003）。
- 不为「提高成功率」放松下载层四条安全基线（ADR-0005）。

## 2. 现行约束：四条铁律

写代码前先认这四条，它们决定了各处的写法，由 `backend/tests/test_structure.py` 的静态判据守着。这五条判据覆盖铁律 1 与 2；**铁律 3（状态码语义）与铁律 4（降级仍 200）没有静态判据**，铁律 1 的逐字节一致另由 `tests/test_understand_contract.py` 守（贵缝，要真模型）。

1. **`content` 在两个接口下完全一致**：模型产出只进 `summary` / `meta.ocr_text` / `meta.asr_text` / `meta.notes`，绝不覆盖或追加进 `content`。
2. **确定性产出只有一份实现**：`processors/` 里每个格式的 `extract_*` 不调模型、不感知调用方是谁；`enhance_*` 只能在其之上追加，**不得改动 `content`**（ADR-0002 / 0007）。
3. **失败也走契约**：对比接口不把异常抛给调用方，而是组装成 ConvertResponse 的 `error` 字段并配语义状态码（入参 422 / 超限 413 / 超时 504 / 上游 502）；RAG 抽取走独立契约，同样 `error` 恒存在。
4. **降级是正常结果，不是错误**：缺依赖的类型返回「这是什么、多大」，HTTP 仍是 200。

## 3. 目标态模块、落点与状态

状态含义：**已实现** = 代码里有且位置与目标态相符；**内联** = 逻辑已有但尚未拆开；**未做** = 目标态有、代码没有；**明确不做** = 只标边界。

| 模块 | 落点 | 状态 |
|---|---|---|
| 服务入口：加载 `.env`、装配路由与中间件、请求体早拒 | `backend/app.py` | 已实现 |
| 运行配置、单价表、日志格式 | `backend/configs/settings.py`、`configs/log_config.py` | 已实现 |
| 静态格式清单：扩展名集合 / Content-Type → 扩展名反查表 / 魔数表 / ZIP 指纹 / 降级分类 | `backend/configs/formats.py` | 已实现 |
| 对比接口契约（ConvertResponse） | `backend/routers/convert/schema.py` | 已实现 |
| RAG 抽取契约 | `backend/routers/rag/schema.py` | 已实现 |
| HTTP 层：收参与状态码 | `backend/routers/convert/router.py`、`routers/rag/router.py`、`routers/probe/router.py` | 已实现 |
| 两条对比管线的编排 | `backend/pipelines/extract.py`、`pipelines/understand.py` | 已实现 |
| RAG 抽取编排 | `backend/pipelines/rag_extract.py` | 已实现 |
| 处理流程六步 | `backend/pipelines/steps/step1_download.py` … `step6_assemble.py` | 已实现 |
| 处理器输入产出结构 + 各格式共用的读素材实现 | `backend/pipelines/processors/schema.py`、`processors/reading.py` | 已实现 |
| 一格式一个文件的处理器 | `backend/pipelines/processors/{pdf,docx,pptx,excel,odf,plaintext,rtf,html,image,media,archive,install_package}.py` | 已实现 |
| RAG 分页与印刷目录规则 | `backend/pipelines/processors/pdf_pages.py`、`processors/catalogue.py` | 已实现 |
| 模型出口（一模型一文件）+ 图片编码 + 提示词模板 | `backend/models/deepseek.py`、`paddle_ocr.py`、`qwen3_asr.py`、`images.py`、`prompts/` | 已实现 |
| 通用件：文本解码与文件头、`LlmError`、计价记账、抽音轨 | `backend/utils/textfile.py`、`errors.py`、`usage.py`、`ffmpeg.py` | 已实现 |
| 样例生成与测试缝 | `backend/scripts/make_fixtures.py`；`backend/tests/`（含 pytest 配置 `backend/pytest.ini`） | 已实现 |
| 单页对比界面（三栏、两栏各有运行按钮） | `frontend/src/`（表见 `App.tsx` / `ResultPanel.tsx`）；预设样例数据源 `frontend/public/presets.config.js` | 已实现 |
| 一键拉起前后端两个进程 | `start.py`（项目根） | 已实现 |
| 术语表 | `CONTEXT.md` | 已实现 |

## 4. 依赖方向

单向，由 `tests/test_structure.py` 守着：

```
app → routers → pipelines → pipelines/steps → pipelines/processors → { models , utils } → configs
```

叶子模块（自己不 import 项目内任何东西，所以任何层都能拿它当数据结构用，不会成环）：关键的几个是 `routers/convert/schema.py`、`routers/rag/schema.py`、`configs/formats.py`、`configs/settings.py`、`utils/textfile.py`、`utils/errors.py`；`configs/log_config.py`、`pipelines/processors/catalogue.py`、`pipelines/processors/pdf_pages.py`、`pipelines/processors/rtf.py` 与各 `__init__.py` 同样是叶子，实际共 20 个。完整清单：`python -m pytest tests/test_structure.py -s -k leaf` 会打印出来（`-s` 才看得见被 pytest 捕获的输出）。

另外两条结构性规矩：`__init__.py` 里不放逻辑（只有模块 docstring）；单文件总行数 ≤ 250 且代码行（剥离 docstring / 注释 / 空行）≤ 150。

## 5. 一次请求怎么走（落点视角）

| 步骤 | 落点 | 产出 |
|---|---|---|
| 收参与状态码 | `routers/convert/router.py` | 调下面某条管线，把 `PipelineResult.status` 写进 HTTP |
| 1 下载 / 上传落盘 | `steps/step1_download.py` | 临时目录里的一个文件（SSRF 校验、流式限大小、TLS 不降级） |
| 2 类型识别 | `steps/step2_detect.py` | 扩展名 + `Detector`（文件头 → 文件名扩展名 → Content-Type，容器再看一层） |
| 3 补后缀与组装 | `steps/step3_prepare.py` | `PreparedSource`；请求结束 `cleanup()` 整树删临时目录 |
| 4 确定性抽取 | `steps/step4_extract.py` → `processors/*.extract_*()` | `content`（唯一来源）+ `assets` 素材清单 |
| 4′ 模型增强（仅 understand） | `steps/step4_extract.py` → `processors/*.enhance_*()` / `reading.py` | `meta.ocr_text` / `meta.asr_text` / 描述 |
| 5 摘要（仅 understand） | `steps/step5_enhance.py` | `summary`（带 `query` 时是针对性回答） |
| 6 组装响应 | `steps/step6_assemble.py` → `routers/convert/schema.py` | `ConvertResponse` 七字段一次填齐 |
| RAG 分支 | `pipelines/rag_extract.py` | `{kind, pages?, text?, catalogue_ranges?, error}` |

## 6. 现状缺口

已完成的闭环里还剩这些，不是待办清单，是已知边界与下一步的入口：

- **老版 Office（doc / ppt / wps）没有解析路径**：要补就得引 LibreOffice 转换器或自己解析 OLE 流，两者都超出「纯 Python 库能否做到」这条线（ADR-0003）。
- **下载层仍有 DNS 二次解析的时间窗**：SSRF 校验用的是 `socket.getaddrinfo` 的结果，`httpx` 建立连接时会再解析一次，理论上存在 DNS rebinding 的 TOCTOU 窗口；本机自用可接受，对外暴露前要补「解析一次、按 IP 连接」。
- **`content` 一致性与模型产出边界由 `tests/` 下的用例覆盖**：`tests/test_understand_contract.py` 用同一个源比对两条路的 `content` 逐字节相同（贵缝，要真模型、由人在关键节点跑），`tests/test_structure.py` 静态查 `enhance_*` 是否碰 `content`；新增格式要自己补用例。
