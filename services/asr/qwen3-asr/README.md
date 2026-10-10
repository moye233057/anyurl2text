# qwen3-asr（默认端口 8022）

最小侧车：`main.py` + `engine.py`。核心只调 `POST /asr-b64`。

```powershell
# 独立环境（建议 Python 3.12 + CUDA torch）
pip install -r requirements.txt
python download_models.py
python main.py
```

权重：`models/Qwen3-ASR-1.7B/`（不入库）。端口：`ASR_PORT`。
