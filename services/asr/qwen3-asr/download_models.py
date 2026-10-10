# -*- coding: utf-8 -*-
"""下载 Qwen3-ASR-1.7B 到本目录 models/Qwen3-ASR-1.7B/。"""
import os

from modelscope import snapshot_download

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCAL_DIR = os.path.join(SERVICE_ROOT, "models", "Qwen3-ASR-1.7B")
MODEL_ID = "Qwen/Qwen3-ASR-1.7B"


def main() -> None:
    os.makedirs(os.path.dirname(LOCAL_DIR), exist_ok=True)
    print(f"download ASR model to: {LOCAL_DIR}")
    snapshot_download(MODEL_ID, local_dir=LOCAL_DIR)
    print("done")


if __name__ == "__main__":
    main()
