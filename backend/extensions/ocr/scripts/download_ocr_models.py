import os

from modelscope import snapshot_download

# 权重落点：backend/extensions/models/OCR/paddleocr_torch
SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXTENSIONS_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, ".."))
DOWNLOAD_MODEL_DIR = EXTENSIONS_ROOT

PIPELINE_REPO_ID = "OpenDataLab/PDF-Extract-Kit-1.0"

# 只下载 paddleocr_torch，不下载 Layout、MFR、Table、VLM 等非 OCR 模型。
OCR_MODEL_PATHS = [
    "models/OCR/paddleocr_torch",
    "models/OCR/paddleocr_torch/*",
]


def download_ocr_models():
    os.makedirs(DOWNLOAD_MODEL_DIR, exist_ok=True)
    print(f"download OCR models to: {DOWNLOAD_MODEL_DIR}")
    print(
        f"expected weights dir: {os.path.join(DOWNLOAD_MODEL_DIR, 'models', 'OCR', 'paddleocr_torch')}"
    )
    snapshot_download(
        PIPELINE_REPO_ID,
        local_dir=DOWNLOAD_MODEL_DIR,
        allow_patterns=OCR_MODEL_PATHS,
    )


def main():
    download_ocr_models()
    print("OCR models download finished")


if __name__ == "__main__":
    main()
