# optional/asr · Qwen3-ASR 扩展

目标：把 `ModelServer/qwen3-asr` 收编到本目录，由 anyurl2text 管理启动。

## 现状

代码收编尚未完成时，可继续用外部已起的 ASR（`.env` 的 `ASR_API_URL`，默认 `http://127.0.0.1:8022/asr-b64`）。

## 收编后约定

- 入口：本目录 `main.py`（`POST /asr-b64`、`GET /health`）
- 权重：`backend/extensions/models/`（如 `Qwen3-ASR-1.7B`，**不提交 git**）
- 环境：独立 conda/venv（官方建议 Python 3.12 + CUDA torch）；勿装进核心 `.venv`
- 端口：默认 `8022`

## 手动启动（收编完成后）

```powershell
python main.py
```

核心只向本扩展送字节（`/asr-b64`），不转交用户 URL。
