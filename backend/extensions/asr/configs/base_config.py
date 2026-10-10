# -*- coding: utf-8 -*-
import os

CONFIG_ENV = os.getenv("DEPLOY_ENV", "local").strip().lower()
ASR_MODEL_ROOT_DIR_ENV = "QWEN3_ASR_MODEL_DIR"
# 本服务根：backend/extensions/asr；权重在同级 models/
SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXTENSIONS_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, ".."))

# 模型与推理参数属于服务固定行为，不通过请求参数或环境变量覆盖；只有语种允许按请求覆盖。
ASR_MODEL_ID = "Qwen/Qwen3-ASR-1.7B"
ASR_MODEL_SUB_DIR = "Qwen3-ASR-1.7B"
ASR_DTYPE = "bfloat16"
ASR_DEVICE_MAP = "cuda:0"
# 本机单卡 6GB 显存，批上限压到 8，避免长音频并发时 OOM。
MAX_INFERENCE_BATCH_SIZE = 8
# 生成长度上限，官方默认 256；转写长录音需要调大，否则尾部会被截断。
MAX_NEW_TOKENS = 256

if CONFIG_ENV == "local":
    CUDA_VISIBLE_DEVICES = os.getenv("CUDA_VISIBLE_DEVICES", "0").strip() or "0"
    # 权重：backend/extensions/models/Qwen3-ASR-1.7B
    ASR_MODEL_ROOT_DIR = os.path.join(EXTENSIONS_ROOT, "models")
else:
    raw_asr_model_root_dir = os.getenv(ASR_MODEL_ROOT_DIR_ENV, "").strip()
    if not raw_asr_model_root_dir:
        raise RuntimeError(f"{ASR_MODEL_ROOT_DIR_ENV} must be set")
    CUDA_VISIBLE_DEVICES = os.getenv("CUDA_VISIBLE_DEVICES", "0").strip()
    ASR_MODEL_ROOT_DIR = os.path.normpath(raw_asr_model_root_dir)

LOCAL_ASR_MODEL_DIR = os.path.normpath(os.path.join(ASR_MODEL_ROOT_DIR, ASR_MODEL_SUB_DIR))
# 本地已有权重就直接用本地目录，否则交回仓库名由 qwen-asr 自行从 ModelScope/HF 下载。
ASR_MODEL_PATH = LOCAL_ASR_MODEL_DIR if os.path.isdir(LOCAL_ASR_MODEL_DIR) else ASR_MODEL_ID
