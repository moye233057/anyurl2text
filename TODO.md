# TODO

> **选做**：没方向时随手翻，**不是**冲刺清单；AI **不要**默认按选做逐项开工。  
> **计划**：目标态已拍板、待实现的工作；动手前先读 [docs/architecture.md](docs/architecture.md) 与 [docs/adr.md](docs/adr.md)。  
> **明细归档**：[docs/review-remainder.md](docs/review-remainder.md)　**进展**：[docs/plan-completed.md](docs/plan-completed.md)　**怎么跑**：[docs/RUNBOOK.md](docs/RUNBOOK.md)

---

## 计划 · 统一转换接口与仓内扩展

### 已拍板（勿再争）

- 关扩展时：**文档类**仍确定性抽正文；**图片 / 音视频**只给基础文件信息（Q1=C）。
- 取消双路对比产品；前端单路 + 能力开关（Q2=A）。
- OCR / ASR / LLM 均为可选扩展；远程 API 与本机部署同类（Q3）。
- 远程仓不收录权重；权重落 `backend/extensions/models/`。
- OCR/ASR **要起就在本仓**起，不默认另起外部 ModelServer（Q4）。
- 目录：`backend/extensions/ocr`、`backend/extensions/asr`；出口包 `backend/extensions/`（原 `backend/models`）；权重 `backend/extensions/models/`。
- 启动：默认由本仓管理子进程一并拉起；并文档说明可手动起 `optional` 进程（Q8=C）。
- LLM：DeepSeek 远程 + 本机 OpenAI 兼容端点（同一客户端换 `BASE_URL`）（Q9=B）。
- 接口：`POST /convert`、`/convert/file`；字段 `ocr` / `asr` / `llm` 默认 `false`（Q6=A）。
- 看图支路一律 `ocr && llm`；ASR 仅 `asr`；摘要仅 `llm`（Q10=B，矩阵见 [API.md](docs/API.md) §1.1）。
- 迁移完成即删除 `/extract*` `/understand*`，无别名（Q11=A）。

### 实现清单

- [x] 契约：`ConvertUrlBody` 增加 `ocr` / `asr` / `llm`；语义按 API §1.1 / ADR-0014
- [x] 路由：`POST /convert` 与 `/convert/file`；已删旧 `/extract*` `/understand*`
- [x] 编排：`pipelines/convert.py` + 能力门闩（开关矩阵 × 模态）
- [x] 交付：按实际跑过的扩展并进 `content`
- [x] 收编 OCR：`backend/extensions/ocr/`（自 ModelServer/paddle-ocr；权重外置）
- [x] 收编 ASR：`backend/extensions/asr/`（自 ModelServer/qwen3-asr；权重外置）
- [x] 启动器：`start.py` 条件满足时尝试拉起扩展（缺 main.py/权重则跳过并提示）
- [ ] LLM：RUNBOOK 写本机 OpenAI 兼容端点一例
- [x] 文档骨架：`backend/extensions/{ocr,asr}/README` + `backend/extensions/models/README`
- [x] 包重命名：`backend/models` → `backend/extensions`；权重目录 `backend/extensions/models/`
- [x] 前端：单路 + 三开关
- [x] 测试：进程内/贵缝改开关矩阵；结构测试跟新模块
- [x] `.env.example`：`START_EXTENSIONS` / OCR·ASR 端口与 URL
- [ ] 文案清扫：RUNBOOK 验收表仍有旧路径表述，与扩展收编同轮改完

### 注意点（实现时易踩）

| 点 | 说明 |
|---|---|
| 依赖隔离 | 用本仓子进程 + 各自环境；不要强行塞进核心 `uv` `.venv` |
| 权重体积 | 下载脚本、磁盘与首次加载耗时写进 RUNBOOK |
| 看图双开 | 只开 `ocr` 或只开 `llm` 时看图支路必须跳过并写 notes，禁止半套静默 |
| 扫描件 / 内嵌图 | 与独立图片同属看图，均要 `ocr && llm` |
| 旧贵缝 | 音视频 `content` 在 `asr=true` 时可含转写；删除「两路 content 相同」类断言 |
| 结构测试 | 路由名、前端 `types.ts` 镜像一并改 |
| 安全基线 | 核心只向扩展送字节，不转交用户 URL 代下载 |
| 成本 | 仅 LLM 进 `usage`；本机 OCR/ASR 默认不计 |
| 并发与预算 | 双池与全链路预算保留；扩展开更易触顶，notes 说清 |

---

## 选做 · 想对外 / 开源时

- [ ] 定许可证；pymupdf（AGPL）写 NOTICE、换库，还是只自用
- [ ] 表格代码（anydoc 派生）怎么署名
- [ ] 加 LICENSE / NOTICE / README 许可小节
- [ ] 控制台轮换 DeepSeek Key；可选密钥扫描
- [ ] 推公开仓前：要不要改 git 作者历史
- [x] 后端改 uv（`pyproject.toml` + `uv.lock`，ADR-0013）；前端 lockfile 仍建议官方 npm 源
- [ ] 最小 CI（pytest 忽略 HTTP 契约 + `tsc --noEmit`）
- [ ] 按需 CONTRIBUTING / SECURITY / CHANGELOG

## 选做 · 想挂到公网时

- [ ] 入口鉴权（Key/Bearer 或反代）+ CORS 收敛
- [ ] 反代头：`proxy_headers` / TrustedHost，别限错 IP
- [ ] 启动自检强依赖 + 短超时熔断
- [ ] readiness：真探 OCR / ASR / 临时目录可写（与「计划」重叠时以计划为准）

## 选做 · 想打磨前端时

- [ ] 生产构建可配 `VITE_API_BASE`；子路径部署对齐
- [ ] 长任务可取消（AbortController）+ 已用秒数
- [ ] vitest 覆盖预设与传输错误、ErrorBoundary
- [ ] Health 多数字段在界面有提示

## 明确不做（别又捡起来）

- 仓内 Dockerfile / systemd / 默认多 worker（下载者自配，ADR-0012）
- 请求级缓存 / 去重
- 远程仓收录模型权重
- 把「使用者必须另起外部 ModelServer 仓」写成默认路径
- 恢复双路对比作为产品主身份
