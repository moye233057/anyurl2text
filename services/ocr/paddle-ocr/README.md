# paddle-ocr（默认端口 8021）

最小侧车：`main.py` + `engine.py`。核心只调 `POST /ocr-b64`。

```powershell
# 独立环境
pip install --no-deps mineru==3.2.3
pip install -r requirements.txt
python download_models.py
python main.py
```

权重：`models/OCR/paddleocr_torch/`（不入库）。端口：`OCR_PORT`。
