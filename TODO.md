# TODO · 选做清单（启发用）

> **用途**：没方向时随手翻一眼，挑一件有感觉的做。**不是**现行冲刺清单，AI **不要**默认按本文逐项开工。
> **明细归档**：[docs/review-remainder.md](docs/review-remainder.md)（评审余项全文）  
> **进展**：[docs/plan-completed.md](docs/plan-completed.md)　**怎么跑**：[docs/RUNBOOK.md](docs/RUNBOOK.md)

**余项一句话**：对外许可证与派生来源拍板、生产准入与依赖锁定、反代/自检/readiness，以及开源前的 NOTICE、CI 与前端工程补件。

---

## 想对外 / 开源时

- [ ] 定许可证；pymupdf（AGPL）写 NOTICE、换库，还是只自用
- [ ] 表格代码（anydoc 派生）怎么署名
- [ ] 加 LICENSE / NOTICE / README 许可小节
- [ ] 控制台轮换 DeepSeek Key；可选密钥扫描
- [ ] 推公开仓前：要不要改 git 作者历史
- [x] 后端改 uv（`pyproject.toml` + `uv.lock`，ADR-0013）；前端 lockfile 仍建议官方 npm 源
- [ ] 最小 CI（pytest 忽略 HTTP 契约 + `tsc --noEmit`）
- [ ] 按需 CONTRIBUTING / SECURITY / CHANGELOG

## 想挂到公网时

- [ ] 入口鉴权（Key/Bearer 或反代）+ CORS 收敛
- [ ] 反代头：`proxy_headers` / TrustedHost，别限错 IP
- [ ] 启动自检强依赖 + 短超时熔断
- [ ] readiness：真探 OCR / ASR / 临时目录可写

## 想打磨前端时

- [ ] 生产构建可配 `VITE_API_BASE`；子路径部署对齐
- [ ] 长任务可取消（AbortController）+ 已用秒数
- [ ] 预设注明「要另起本机 OCR/ASR」
- [ ] vitest 覆盖 `presets.ts`；传输错误与 ErrorBoundary
- [ ] Health 多数字段在界面有提示

## 明确不做（别又捡起来）

- 仓内 Dockerfile / systemd / 默认多 worker（下载者自配，ADR-0012）
- 请求级缓存 / 去重
- `extract` 里塞 OCR；改「一格式一文件 / 上界单点 / 共用抽取内核」
