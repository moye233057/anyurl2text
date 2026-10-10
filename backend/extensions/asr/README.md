# extensions/asr · Qwen3-ASR 服务

自 `ModelServer/qwen3-asr` 收编。独立进程，默认端口 `8022`。

## 接口

- `POST /asr-b64`
- `POST /asr-url`（核心 anyurl2text **只用** `/asr-b64`）
- `GET /health`

## 环境

官方建议独立环境 + Python 3.12；有 NVIDIA 时先装 CUDA 版 torch，再装 `requirements.txt`。  
**不要**装进仓库根 uv `.venv`。

```powershell
conda create -n asr python=3.12 -y
conda activate asr
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
```

## 权重

放到：

```text
backend/extensions/models/Qwen3-ASR-1.7B/
```

或用本目录 `scripts/download_asr_models.py`（若有）下载到上述路径。  
非 local 部署可设 `QWEN3_ASR_MODEL_DIR`。本地目录不存在时会回退为仓库名由库自行下载。

## 启动

```powershell
cd backend/extensions/asr
python main.py
```

端口：`ASR_PORT`（默认 8022）。仓库根 `start.py` 可按条件一并拉起。
