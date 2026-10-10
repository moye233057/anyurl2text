# 架构与模块落点（目标态）

> **用途**：项目是什么、明确不做什么、每个模块落在哪个路径、各自什么状态。写代码前读它，与 [adr.md](./adr.md) 一起构成现行约束。
> **关联**：[adr.md](./adr.md)（仍有效的为什么）· [HISTORY.md](./HISTORY.md)（废掉的选定）· [CONTEXT.md](../CONTEXT.md)（术语表）· [API.md](./API.md)（字段契约）· [PIPELINES.md](./PIPELINES.md)（各类型链路）· [RUNBOOK.md](./RUNBOOK.md)（跑起来与验收）
> **基线**：2026-10-02

---

## 1. 系统是什么

把任意 URL 或本地文件转成**尽可能保留原始信息**的文本的本地服务。同一个源有两条**可对比**的路：`/extract` 只用代码做确定性抽取，快、稳、不花钱；`/understand` 在同一份抽取产出之上叠加模型能力，慢且计费。界面**对比正文**：纯代码可完整交付的类型两路相同；需模型转写的类型 understand 更长（ADR-0011）。

**明确不做**（产品边界，越线要改本文与 ADR）：

- 不做鉴权、多租户、限流、计费——本机自用服务，`ALLOW_PRIVATE_HOSTS=false` 只防 SSRF。
- 不把 URL 交给任何上游代下载：图片自己下载后传 base64，音频自己抽音轨后传字节（ADR-0008）。
- 不在 `/extract` 里放任何 OCR 或语言模型（ADR-0004）；understand 的正文合并规则见 ADR-0011。
- 不静默截断：任何截断、跳过、降级都要在 `meta.truncated` / `meta.notes` / `meta.degrade_reason` 里声明。
- 压缩包只列条目，不解压、不递归。
- 老版 Office（doc / ppt / wps）不做深度解析，只降级（ADR-0003）。
- 不为「提高成功率」放松下载层四条安全基线（ADR-0005）。
- 不附带生产托管脚手架（Dockerfile / systemd / 反代整套）；对外部署由下载者按 [RUNBOOK](./RUNBOOK.md)「对外部署」自配（ADR-0012）。
- 后端环境不交给「任意 conda/pip」：只认本仓 uv `.venv`（ADR-0013）。

## 2. 现行约束：四条铁律

写代码前先认这四条，它们决定了各处的写法，由 `backend/tests/test_structure.py` 的静态判据守着：**5 条判据 + 2 条自检 = 7 项**（依赖方向、扩展名覆盖、`__init__` 无逻辑、上界值只写一处、前端契约镜像；单文件行数门禁已按 ADR-0010 删除）。**铁律 3（状态码语义）与铁律 4（降级仍 200）没有静态判据**；正文对比规则由 ADR-0011 与 `tests/test_understand_contract.py` / `test_understand_stubbed.py` 守。

1. **`content` 语义分接口**：extract 仅纯代码段；understand 可合并模型转写，且 OCR/ASR 双写 `meta.*`（ADR-0011）。
2. **确定性产出只有一份实现**：`processors/` 里每个格式的 `extract_*` 不调模型、不感知调用方是谁；模型增强由 `understand.py` 显式分派（ADR-0002 / 0007）。
3. **失败也走契约**：对比接口不把异常抛给调用方，而是组装成 ConvertResponse 的 `error` 字段并配语义状态码（入参 422 / 超限 413 / 超时 504 / 上游 502）。
4. **降级是正常结果，不是错误**：缺依赖的类型返回「这是什么、多大」，HTTP 仍是 200。

## 3. 目标态模块、落点与状态

状态含义：**已实现** = 代码里有且位置与目标态相符；**内联** = 逻辑已有但尚未拆开；**未做** = 目标态有、代码没有；**明确不做** = 只标边界。

| 模块 | 落点 | 状态 |
|---|---|---|
| 服务入口：加载 `.env`、装配路由与中间件、请求体早拒 | `backend/app.py` | 已实现 |
| 运行配置、单价表、日志（JSON + request id + 落盘轮转） | `backend/configs/settings.py`、`configs/log_config.py`；请求进出在 `app.py` | 已实现 |
| 静态格式清单：扩展名集合 / Content-Type → 扩展名反查表 / 魔数表 / ZIP 指纹 / 降级分类 | `backend/configs/formats.py` | 已实现 |
| 对比接口契约（ConvertResponse） | `backend/routers/convert/schema.py` | 已实现 |
| HTTP 层：收参与状态码 | `backend/routers/convert/router.py`、`routers/probe/router.py` | 已实现 |
| 两条对比管线的编排 | `backend/pipelines/extract.py`、`pipelines/understand.py` | 已实现 |
| 处理流程六步 | `backend/pipelines/steps/step1_download.py` … `step6_assemble.py` | 已实现 |
| OLE 复合文档的目录流名读取（判 doc / xls / ppt 的第二层依据） | `backend/pipelines/steps/ole_directory.py` | 已实现 |
| 处理器输入产出契约 + 共用碎片类型 | `backend/utils/processor_schema.py` | 已实现 |
| HTTP 契约主体（ConvertResponse 等） | `backend/utils/convert_contract.py`（`routers/convert/schema.py` 再导出） | 已实现 |
| 跨格式读素材（模型侧） | `backend/models/reading.py` | 已实现 |
| 多格式表格公共件 | `backend/utils/excel/`（grid / cell_text / index_runs / table_kind / html_tables / odf_table_rows） | 已实现 |
| 一格式一个文件的处理器 | `backend/pipelines/processors/{pdf,docx,pptx,excel,odf,ofd,plaintext,rtf,html,image,media,archive,install_package,epub}.py` | 已实现 |
| 表格族（格位 / 合并 / 可见性；OOXML·ODF 容器读法在族文件内） | `excel.py`（xlsx/xls/ods/csv + 包内合并区/可见性）、`rtf.py`（控制字 + `build_lines` 列位）、公共口径在 `utils/excel/` | 已实现 |
| DOCX 超链接 | 收在 `docx.py`（不再单独叶子文件） | 已实现 |
| EPUB 抽取：容器 → OPF → 按 spine 拼章节 + 内部引用提升成文档级目标 | `backend/pipelines/processors/epub.py` | 已实现 |
| OFD 抽取：容器 → `OFD.xml` → 文档根 → 按 `Pages/Page` 声明的页序逐页取正文 | `backend/pipelines/processors/ofd.py` | 已实现 |
| 限额的单一定义处：值 + 命中行为 + 强制点 + 命中文案模板 | `backend/configs/limits.py` | 已实现 |
| 稳定错误码表（机器名白名单：硬失败 / 降级 / 模型三组） | `backend/configs/errors.py` | 已实现（三组全覆盖） |
| 模型出口（一模型一文件）+ 图片编码 + 提示词模板 | `backend/models/deepseek.py`、`paddle_ocr.py`、`qwen3_asr.py`、`images.py`、`prompts/` | 已实现 |
| 通用件：文本解码与文件头、`LlmError`、抽音轨、出站复用 | `backend/utils/textfile.py`、`errors.py`、`ffmpeg.py`、`egress.py` | 已实现 |
| DeepSeek 出口 + 第三方计价与成本闸 | `backend/models/deepseek.py` | 已实现 |
| 资源防护公共件：ZIP 解压预算（声明预检 + 流式封顶 + 跨成员累计） | `backend/utils/zip_read.py`（EPUB / OFD / ODT·ODP 共用；原理见 [PIPELINES.md](./PIPELINES.md) §4.1） | 已实现 |
| 资源防护公共件：请求体早拒（声明预检 + 边收边数） | `backend/utils/body_limit.py`（`app.reject_oversized_body` 调用；反代上限见 RUNBOOK） | 已实现 |
| 样例生成与测试缝 | `backend/scripts/make_fixtures.py`；`backend/tests/`（含 pytest 配置 `backend/pytest.ini`） | 已实现 |
| 单页对比界面（三栏、两栏各有运行按钮） | `frontend/src/`（表见 `App.tsx` / `ResultPanel.tsx`）；预设样例数据源 `frontend/public/presets.config.js` | 已实现 |
| 一键拉起前后端两个进程（须本仓 uv `.venv`） | `start.py`（项目根）；依赖清单 `pyproject.toml` | 已实现 |
| 术语表 | `CONTEXT.md` | 已实现 |

## 4. 依赖方向

单向，由 `tests/test_structure.py` 的 `ALLOWED` 守着（层名取路径第一段目录，见 `layer_of`）：

```
app → routers → pipelines → { models , utils } → configs
         └───────────────→ utils（处理器契约碎片等）
```

`pipelines/steps/` 与 `pipelines/processors/` **同属 `pipelines` 层**：门禁不单独卡二者之间的方向。编排上约定由 steps 调 processors，这是可读性约定，不是静态规则。真要拆成两层，再扩 `ALLOWED` 与本图。`routers` 可 import `utils`（共用碎片类型）。

叶子模块（自己不 import 项目内任何东西，所以任何层都能拿它当数据结构用，不会成环）：关键的是契约与配置（`utils/convert_contract.py`、`utils/processor_schema.py`、`configs/formats|settings|errors.py` 等），以及 `utils/excel/` 下若干叶子（如 `cell_text`、`index_runs`、`odf_table_rows`、`table_kind`）、`pipelines/steps/ole_directory.py` 与各 `__init__.py`。完整清单：`python -m pytest tests/test_structure.py -s -k leaf`（加 `-s` 才看得见打印）。

结构性规矩：`__init__.py` 里不放逻辑（只有模块 docstring）。**单文件行数门禁已废除**（ADR-0010）：`processors/` 以「一格式族一文件」为准，不为过门禁拆文件。

跨格式落点（目标态，迁移中）：

| 类别 | 落点 |
|---|---|
| 格式族实现 | `pipelines/processors/{excel,odf,docx,rtf,…}.py`（一族一文件） |
| 表格公共 | `utils/excel/`（grid / cell_text / index_runs / table_kind / html_tables / odf_table_rows） |
| 处理器契约 | `utils/processor_schema.py` |
| 模型侧读素材 | `models/reading.py` |
| `processors/` | **只**留格式族文件 |

## 5. 一次请求怎么走（落点视角）

| 步骤 | 落点 | 产出 |
|---|---|---|
| 收参与状态码 | `routers/convert/router.py` | 调下面某条管线，把 `PipelineResult.status` 写进 HTTP |
| 1 下载 / 上传落盘 | `steps/step1_download.py` | 临时目录里的一个文件（SSRF 校验、流式限大小、TLS 不降级） |
| 2 类型识别 | `steps/step2_detect.py` | 扩展名 + `Detector`（文件头 → 文件名扩展名 → Content-Type，容器再看一层） |
| 3 补后缀与组装 | `steps/step3_prepare.py` | `PreparedSource`；请求结束 `cleanup()` 整树删临时目录 |
| 4 确定性抽取 | `steps/step4_extract.py` → `processors/*.extract_*()` | `content`（唯一来源）+ `assets` 素材清单 |
| 4′ 模型增强（仅 understand） | `steps/step4_extract.py` → `processors/*.enhance_*()` / `models/reading.py` | `meta.ocr_text` / `meta.asr_text` / 描述 |
| 5 摘要（仅 understand） | `steps/step5_enhance.py` | `summary`（带 `query` 时是针对性回答） |
| 6 组装响应 | `steps/step6_assemble.py` → `routers/convert/schema.py` | `ConvertResponse` 七字段一次填齐 |

## 6. 现状缺口

已完成的闭环里还剩这些，不是待办清单，是已知边界与下一步的入口：

- **老版 Office（doc / ppt / wps）没有解析路径**：要补就得引 LibreOffice 转换器或自己解析 OLE 流，两者都超出「纯 Python 库能否做到」这条线（ADR-0003）。
- **下载层 SSRF 已钉 IP**：连接按解析结果建连（Host/SNI 仍用原主机名）；https→http 降级跳转拒绝。其余入口暴露面（鉴权 / CORS）见 [review-remainder.md](./review-remainder.md) / 根目录选做清单。
- **正文对比规则由 `tests/` 覆盖**：`test_understand_stubbed.py`（免费）与 `test_understand_contract.py`（贵缝）按 ADR-0011 区分「纯代码相同」与「understand 合并模型转写」；新增格式要自己补用例。
- **ADR-0010 布局已落地**：`processors/` 只留格式族文件；表格公共与 `odf_table_rows` 在 `utils/excel/`；契约与 `reading` 在既定落点。RAG 独立契约已下线（见 HISTORY）。
