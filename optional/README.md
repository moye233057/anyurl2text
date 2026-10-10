# 可选扩展模块

本目录收纳 **OCR / ASR** 服务进程（ADR-0014）。核心经 HTTP 调用它们（默认 `8021` / `8022`）。  
扩展出口客户端在 `backend/extensions/`；权重在 `backend/extensions/models/`（不进 git）。

| 目录 | 端口（默认） | 权重落点 |
|---|---|---|
| `optional/ocr/` | 8021 | `backend/extensions/models/` |
| `optional/asr/` | 8022 | `backend/extensions/models/` |

## 启动方式

1. **推荐**：`uv run python start.py` —— 若对应模块已安装且权重目录有文件，会尝试一并拉起子进程。
2. **手动**：见各子目录 `README.md`（独立环境，勿塞进核心 `.venv`）。
