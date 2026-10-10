# 评审余项（归档）

> **用途**：2026-10-09 评审里**尚未动手**的明细存档；不再当现行工作清单。启发性勾选见 [TODO.md](./TODO.md)；进展见 [plan-completed.md](./plan-completed.md)。
> **基线**：条目里的路径/行号是审计快照，改过代码后按符号名找。

**一句话**：剩下主要是对外许可证与派生来源拍板、生产准入与依赖锁定、反代/自检/readiness，以及开源前的 NOTICE、CI 与前端工程补件。

---

## 1 待拍板

| # | 要定什么 |
|---|---|
| 4.1-① / A-⑥ | 对外许可证；`pymupdf`（AGPL）写 NOTICE、换库，还是不对外部署 |
| 4.1-② | 表格派生自 anydoc：如何署名 / NOTICE |
| 4.1-⑤ | DeepSeek Key 是否已在控制台轮换 |
| 4.2-⑥ | 推 GitHub 前是否改 git 作者 / 重写历史 |

---

## 2 代码侧（未做）

### 生产阻断

- **A-① 准入**：无鉴权、CORS `*`；要对外须 Key/Bearer 或前置反代，并收紧 CORS。
- **A-⑥ 锁依赖 + 许可**：后端已改 uv（`pyproject.toml` + `uv.lock`，ADR-0013）；仍须核对 AGPL（pymupdf）与对外许可证。

### 加固（原批次 C）

- **C-③** `proxy_headers` / `forwarded_allow_ips` / `TrustedHostMiddleware`（`app.py`）——上反代后客户端 IP 才可信。
- **C-④** 外部强依赖启动自检 + 短超时熔断（失败现只落 `meta.model_error`）。
- **C-⑤** `/probe/health` 拆 readiness：真探 OCR/ASR/临时目录可写（现多为静态回显）。

批次 B（SSRF、PDF/图片预算、日志、出站复用、第三方成本控制等）已结；缓存/去重未做、明确不做。

---

## 3 发 GitHub（未做）

### 许可与隐私

- **LICENSE / NOTICE**：仓内尚无；README 无许可章节。
- **派生来源**：`utils/excel/grid.py` 等与 anydoc 的关系须确权后再署名。
- **第三方提示**：pymupdf（AGPL）、PaddleOCR、imageio-ffmpeg 等。
- **轮换 Key**：`.env` 有活密钥（未进 Git）；控制台轮换；可选 pre-commit 扫描。

### 个人信息

- **git 身份**：作者带 gitee noreply；推公开仓会带出——是否改作者 / 新历史待定。

### 工程化

- 锁依赖（同 A-⑥）；`package-lock.json` 换回官方 npm 源。
- 最小 CI：`pytest`（忽略 HTTP 契约）+ `tsc --noEmit`。
- 按需 CONTRIBUTING / SECURITY / CHANGELOG。
- 前端：lint/测试（`presets.ts` / vitest）；生产 `VITE_API_BASE`；长任务 AbortController；预设注明依赖外置 OCR/ASR；传输错误 state、ErrorBoundary、子路径 `BASE`、Health 字段提示等小项。

---

## 4 已确认无需再查

- `.env` 未入库；tracked 无真实凭据；`scanned.pdf` / `debug/` / `dist/` / `node_modules` 未跟踪。
- 体积健康；样例外无真实 PII；前端无 XSS 面。
- 契约三通道分清；下载钉 IP、禁 https→http；PDF/图片预算与 OCR 切片上限进 `limits_hit`。
- 日志 request id + 轮转；出站客户端复用；DeepSeek 成本闸在 `models/deepseek.py`（OCR/ASR 不进闸）；无请求缓存/去重。
- 压缩包只列目录；临时目录按请求清理；无进程内可变状态。
- **A-⑦**：不对外部署附 Docker/systemd；下载者自配（ADR-0012）。
- **别动的设计**：共用抽取内核、契约叶子模块、一格式一文件、上界单点、`extract` 不放 OCR。

---

## 5 评审当日数字快照（可复核）

| 事实 | 值（2026-10-09 前后） |
|---|---|
| 跟踪体积 | 约 2 MB 量级 |
| 远端 | gitee `anyurl2text` |
| 结构门禁 | `test_structure` 通过（行数门禁已废，ADR-0010） |
| 结论 | 解析内核与契约干净；缺口在准入、许可与派生来源 |
