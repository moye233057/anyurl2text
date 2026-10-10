import os

CONFIG_ENV = os.getenv("DEPLOY_ENV", "local").strip().lower()
OCR_MODEL_ROOT_DIR_ENV = "PP_OCRV5_PIPELINE_MODEL_DIR"
OCR_MODEL_SUB_DIR = "paddleocr_torch"
# 本服务根：backend/extensions/ocr；权重在同级 models/
SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXTENSIONS_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, ".."))

# OCR 默认参数属于服务固定行为，不通过请求参数或环境变量覆盖。
DEFAULT_OCR_LANG = "ch"
DEFAULT_OCR_MODE = "full"
DEFAULT_USE_ANGLE_CLS = True

if CONFIG_ENV == "local":
    CUDA_VISIBLE_DEVICES = os.getenv("CUDA_VISIBLE_DEVICES", "0").strip() or "0"
    # 权重：backend/extensions/models/OCR/paddleocr_torch
    OCR_MODEL_ROOT_DIR = os.path.join(EXTENSIONS_ROOT, "models", "OCR")
else:
    CONFIG_ENV = "nanxiang"
    CUDA_VISIBLE_DEVICES = os.getenv("CUDA_VISIBLE_DEVICES", "0").strip() or "0"
    raw_ocr_model_root_dir = os.getenv(OCR_MODEL_ROOT_DIR_ENV, "").strip()
    if not raw_ocr_model_root_dir:
        raise RuntimeError(f"{OCR_MODEL_ROOT_DIR_ENV} must be set")
    OCR_MODEL_ROOT_DIR = os.path.normpath(raw_ocr_model_root_dir)
OCR_MODEL_DIR = os.path.normpath(os.path.join(OCR_MODEL_ROOT_DIR, OCR_MODEL_SUB_DIR))
OCR_CLS_MODEL_PATH = os.path.join(OCR_MODEL_DIR, "ch_ptocr_mobile_v2.0_cls_infer.pth")
