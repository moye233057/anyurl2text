# -*- coding: utf-8 -*-
"""下载 Qwen3-ASR-1.7B 权重到 backend/extensions/models/。

使用前注意：需要先 pip install modelscope；权重约 4GB，走 ModelScope 源。
运行：在 ASR 专用环境中 python scripts/download_asr_models.py
"""

import os

from modelscope import snapshot_download

SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXTENSIONS_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, ".."))
LOCAL_MODEL_DIR = os.path.join(EXTENSIONS_ROOT, "models", "Qwen3-ASR-1.7B")

ASR_MODEL_ID = "Qwen/Qwen3-ASR-1.7B"


def download_asr_model():
    os.makedirs(os.path.dirname(LOCAL_MODEL_DIR), exist_ok=True)
    print(f"download ASR model to: {LOCAL_MODEL_DIR}")
    snapshot_download(ASR_MODEL_ID, local_dir=LOCAL_MODEL_DIR)


def main():
    download_asr_model()
    print("ASR model download finished")


if __name__ == "__main__":
    main()
