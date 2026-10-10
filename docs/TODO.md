# TODO

> **选做**：没方向时随手翻，**不是**冲刺清单；AI **不要**默认按选做逐项开工。  
> **已拍板 / 已实现**：见 [adr.md](./adr.md)（ADR-0014）、[architecture.md](./architecture.md)、[plan-completed.md](./plan-completed.md)。  
> **明细归档**：[review-remainder.md](./review-remainder.md)　**怎么跑**：[RUNBOOK.md](./RUNBOOK.md)

---

## 计划 · 收尾

统一转换与仓内 `services/` 收编已落地。当前无未勾选的实现项。

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
- [ ] readiness：真探 OCR / ASR / 临时目录可写

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
