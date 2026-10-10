# 扩展权重目录

OCR / ASR 服务进程读这里的权重（**不入库**；`.gitignore` 忽略除本 README 外的内容）。

```text
backend/extensions/models/
  ├── OCR/
  │   └── paddleocr_torch/     # Paddle OCR 权重（见 extensions/ocr）
  └── Qwen3-ASR-1.7B/          # ASR 权重（见 extensions/asr）
```

下载方式见 `backend/extensions/ocr/README.md` 与 `backend/extensions/asr/README.md`。  
勿在本目录加 `__init__.py`。
