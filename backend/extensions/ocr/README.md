# extensions/ocr · Paddle OCR 服务

自 `ModelServer/paddle-ocr` 收编。独立进程，默认端口 `8021`。

## 接口

- `POST /ocr-b64`
- `POST /ocr-url`（核心 anyurl2text **只用** `/ocr-b64`）
- `GET /health`

## 环境

使用**独立** conda/venv，不要装进仓库根 uv `.venv`。

```powershell
# 示例：按本目录 requirements.txt 在独立环境中安装
pip install -r requirements.txt
```

## 权重

放到：

```text
backend/extensions/models/OCR/paddleocr_torch/
```

也可用环境变量 `PP_OCRV5_PIPELINE_MODEL_DIR` 指向含 `paddleocr_torch` 的根（非 local 部署必填）。  
下载脚本见本目录 `scripts/`（若有）。

## 启动

```powershell
cd backend/extensions/ocr
# 在 OCR 专用环境中：
python main.py
```

端口：`OCR_PORT`（默认 8021）。仓库根 `start.py` 在权重目录非空且本入口存在时会尝试一并拉起。
