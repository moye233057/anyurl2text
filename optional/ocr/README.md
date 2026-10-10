# optional/ocr · Paddle OCR 扩展

目标：把 `ModelServer/paddle-ocr` 收编到本目录，由 anyurl2text 管理启动。

## 现状

代码收编尚未完成时，可继续用外部已起的 OCR（`.env` 的 `OCR_API_URL`，默认 `http://127.0.0.1:8021/ocr-b64`）。

## 收编后约定

- 入口：本目录 `main.py`（`POST /ocr-b64`、`GET /health`）
- 权重：`backend/extensions/models/`（**不提交 git**）
- 环境：独立 conda/venv（勿装进核心 `uv` `.venv`）
- 端口：默认 `8021`

## 手动启动（收编完成后）

```powershell
python main.py
```

核心只向本扩展送字节（`/ocr-b64`），不转交用户 URL。
