# -*- coding: utf-8 -*-
"""下载 PP-OCR 权重到本目录 models/OCR/paddleocr_torch/。"""
import os

from modelscope import snapshot_download

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
PIPELINE_REPO_ID = "OpenDataLab/PDF-Extract-Kit-1.0"
ALLOW = ["models/OCR/paddleocr_torch", "models/OCR/paddleocr_torch/*"]


def main() -> None:
    print(f"download OCR models under: {SERVICE_ROOT}")
    snapshot_download(PIPELINE_REPO_ID, local_dir=SERVICE_ROOT, allow_patterns=ALLOW)
    print("done → models/OCR/paddleocr_torch/")


if __name__ == "__main__":
    main()
