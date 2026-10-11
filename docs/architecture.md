# 架构与模块落点（目标态）

> **用途**：项目是什么、明确不做什么、每个模块落在哪个路径、各自什么状态。写代码前读它，与 [adr.md](./adr.md) 一起构成现行约束。
> **关联**：[adr.md](./adr.md) · [HISTORY.md](./HISTORY.md) · [CONTEXT.md](../CONTEXT.md) · [API.md](./API.md) · [PIPELINES.md](./PIPELINES.md) · [RUNBOOK.md](./RUNBOOK.md)
> **基线**：2026-10-02 · **目标态修订**：2026-10-10（统一转换接口 + 仓内可选扩展）

---

## 1. 系统是什么

把任意 URL 或本地文件转成**尽可能保留原始信息**的文本的本地服务。默认走确定性抽取；**OCR / ASR / LLM 均为可选扩展能力**（远程 API 与本机部署同类）。请求上的能力开关与扩展是否可用共同决定是否调用；界面单路操作，**不再做双路正文对比**。

交付规则（模态分流，ADR-0014）：

- **文档类**（含可抽正文的网页等）：关扩展时仍返回纯代码抽取的 `content`。
- **图片 / 音视频**：对应扩展未开或不可用时只返回**基础文件信息**；开启且可用时才有 OCR / ASR / 视觉描述等可读正文，并按规则并进 `content`。
- **摘要**：仅 LLM 扩展开启且可用时产出。

**明确不做**（产品边界，越线要改本文与 ADR）：

- 不做鉴权、多租户、限流、平台计费——本机自用；`ALLOW_PRIVATE_HOSTS=false` 只防 SSRF。
- 不把 URL 交给任何上游代下载：图片自己下载后传字节/base64，音频自己抽音轨后传字节。
- 远程仓**不收录** OCR/ASR（及同类）权重；使用者下载到各服务自己的 `models/`。
- 不把「另起外部 ModelServer 仓」当作默认部署；OCR/ASR **要起就在本仓** `services/` 拉起（ADR-0014）。
- 不静默截断：截断、跳过、降级须在 `meta.truncated` / `meta.notes` / `meta.degrade_reason` 声明。
- 压缩包只列条目，不解压、不递归。
- 老版 Office（doc / ppt / wps）不做深度解析，只降级（ADR-0003）。
- 不为提高成功率放松下载层四条安全基线（ADR-0005）。
- 不附带生产托管脚手架（Dockerfile / systemd / 反代整套）（ADR-0012）。
- 后端核心环境只认本仓 uv `.venv`（ADR-0013）；扩展模块可自带独立环境，由本仓文档与启动器编排。

## 2. 现行约束：四条铁律

写代码前先认这四条。结构门禁仍由 `backend/tests/test_structure.py` 守依赖方向等（条目随迁移调整）。正文与开关语义由 ADR-0014 与对应测试守。

1. **`content` 由「确定性段 + 已启用扩展的转写」组成**：确定性段只来自 `extract_*`；OCR/ASR/视觉转写仅在对应能力实际跑过时并入，并双写 `meta.*`。
2. **确定性产出只有一份实现**：`processors/` 的 `extract_*` 不调扩展、不感知开关；扩展分派在统一编排里显式进行。
3. **失败也走契约**：组装 ConvertResponse 的 `error` 并配语义状态码（入参 422 / 超限 413 / 超时 504 / 上游 502 等）。
4. **降级是正常结果，不是错误**：缺依赖或扩展不可用时返回基础信息或已有确定性段，HTTP 仍可为 200，并在 meta 说明。

## 3. 目标态模块、落点与状态

状态含义：**已实现** = 代码位置与目标态相符；**内联** = 逻辑有但未拆开；**未做** = 目标态有、代码没有；**明确不做** = 只标边界；**迁移中** = 旧双路代码仍在，目标态未落地。

| 模块 | 落点 | 状态 |
|---|---|---|
| 服务入口：加载 `.env`、装配路由与中间件、请求体早拒 | `backend/app.py` | 已实现 |
| 运行配置、日志 | `backend/configs/settings.py`、`configs/log_config.py` | 已实现 |
| 静态格式清单 | `backend/configs/formats.py` | 已实现 |
| 统一转换接口 `POST /convert`·`/convert/file`（`ocr`/`asr`/`llm`） | `backend/routers/convert/`；字段见 [API.md](./API.md) | 已实现 |
| HTTP 探测 | `backend/routers/probe/` | 已实现（需扩到扩展 readiness） |
| 统一管线编排 | `backend/pipelines/convert.py` | 已实现 |
| 处理流程六步 | `backend/pipelines/steps/step1_download.py` … `step6_assemble.py` | 已实现 |
| OLE 目录流名 | `backend/pipelines/steps/ole_directory.py` | 已实现 |
| 处理器契约 | `backend/utils/processor_schema.py` | 已实现 |
| HTTP 契约主体 | `backend/utils/convert_contract.py` | 已实现 |
| 扩展出口客户端（读素材 / DeepSeek / OCR·ASR HTTP） | `backend/extensions/`（原 `backend/models`） | 已实现 |
| 能力与提供商的静态名单（名字的唯一声明处） | `backend/configs/extensions.py` | 已实现 |
| 提供商探活、单选与能力快照 | `backend/extensions/registry.py` | 已实现 |
| 能力清单的管线出口（供 `/probe/health`） | `backend/pipelines/capabilities.py` | 已实现 |
| 多格式表格公共件 | `backend/utils/excel/` | 已实现 |
| 一格式族一文件处理器 | `backend/pipelines/processors/*.py` | 已实现 |
| 限额 / 错误码 | `backend/configs/limits.py`、`errors.py` | 已实现 |
| 资源防护：ZIP / 请求体 | `backend/utils/zip_read.py`、`body_limit.py` | 已实现 |
| 出站复用 | `backend/utils/egress.py` | 已实现 |
| **OCR 扩展模块** | `services/ocr/paddle-ocr/`（自 ModelServer/paddle-ocr；独立进程） | 已实现（权重自备） |
| **ASR 扩展模块** | `services/asr/qwen3-asr/`（自 ModelServer/qwen3-asr；独立进程） | 已实现（权重自备） |
| **扩展权重** | `各服务自己的 models/`（gitignore；使用者自备） | 已实现（目录约定 + ignore） |
| **扩展安装与启动说明** | `services/{ocr,asr}/<实现名>/README` + `各服务自己的 models/README` | 内联（RUNBOOK 细则未完） |
| **能力门闩**（选择 × 提供商 × 模态） | `pipelines/convert.py` + `extensions/registry.py` | 已实现（含探活与置灰数据） |
| LLM 扩展出口 | `backend/extensions/deepseek.py`（远程 DeepSeek 或本机 OpenAI 兼容，换 `BASE_URL`） | 已实现（远程；本机端点验收未做） |
| 样例与测试 | `backend/scripts/make_fixtures.py`；`backend/tests/` | 已实现（开关矩阵；HTTP 缝须后端在跑） |
| 前端 | `frontend/`：单路 + 三开关 | 已实现 |
| 一键拉起 | `start.py`：权重非空时拉起 OCR/ASR **子进程**；亦可按各 README 手动起 | 已实现 |
| 术语表 | `CONTEXT.md` | 已实现 |

## 4. 依赖方向

核心后端仍单向（由结构测试守）：

```
app → routers → pipelines → { extensions , utils } → configs
         └───────────────→ utils
```

`services/` 下各实现是**独立进程**，不加入上述 import 图；核心只通过配置的 URL（默认本机端口）用 HTTP/字节协议调用。服务自带依赖与独立环境，由启动器与文档约定，不强制打进核心 `.venv`。

## 5. 一次请求怎么走（落点视角）

| 步骤 | 落点 | 产出 |
|---|---|---|
| 收参、能力开关、状态码 | `routers/convert/` | 调统一管线 |
| 1 下载 / 上传落盘 | `steps/step1_download.py` | 临时文件 |
| 2 类型识别 | `steps/step2_detect.py` | 扩展名 + Detector |
| 3 补后缀与组装 | `steps/step3_prepare.py` | `PreparedSource` |
| 4 确定性抽取 | `steps/step4_extract.py` → `processors/*.extract_*()` | 确定性 `content` + `assets`；图片/音视频多为结构信息 |
| 4′ 扩展增强 | 统一编排 → `enhance_*` / 扩展出口 | 仅当对应开关开且扩展可用 |
| 5 摘要 | 原 step5 逻辑 | 仅 LLM 开且可用 |
| 6 组装 | `step6_assemble` + 交付合并 | 七字段响应 |

## 6. 现状缺口

- OCR/ASR 已收编到 `services/{ocr,asr}/<实现名>`；须自备权重并在独立环境装依赖后才能真正起服务。
- 老版 Office 无解析路径（ADR-0003）。
- 下载层 SSRF 已钉 IP；鉴权 / CORS 见选做清单。
- 选做与明确不做见 [TODO.md](./TODO.md)。
