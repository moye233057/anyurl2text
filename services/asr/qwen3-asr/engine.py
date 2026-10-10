# -*- coding: utf-8 -*-
"""Qwen3-ASR 权重加载与识别（本服务唯一推理模块）。"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger("qwen3-asr")

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_SUB_DIR = "Qwen3-ASR-1.7B"
MODEL_ID = "Qwen/Qwen3-ASR-1.7B"
LOCAL_MODEL_DIR = os.path.join(SERVICE_ROOT, "models", MODEL_SUB_DIR)
MODEL_PATH = LOCAL_MODEL_DIR if os.path.isdir(LOCAL_MODEL_DIR) else MODEL_ID

DTYPE = "bfloat16"
DEVICE_MAP = "cuda:0"
MAX_INFERENCE_BATCH_SIZE = 8
MAX_NEW_TOKENS = 256

_model = None
_infer_lock = threading.Lock()
_load_lock = threading.Lock()


def setup_env() -> None:
    cuda = os.getenv("CUDA_VISIBLE_DEVICES", "0").strip() or "0"
    os.environ["CUDA_VISIBLE_DEVICES"] = cuda


setup_env()

import torch  # noqa: E402
from qwen_asr import Qwen3ASRModel  # noqa: E402


def init_model() -> None:
    global _model
    with _load_lock:
        if _model is not None:
            return
        logger.info("Loading ASR model path=%s dtype=%s", MODEL_PATH, DTYPE)
        _model = Qwen3ASRModel.from_pretrained(
            MODEL_PATH,
            dtype=getattr(torch, DTYPE),
            device_map=DEVICE_MAP,
            max_inference_batch_size=MAX_INFERENCE_BATCH_SIZE,
            max_new_tokens=MAX_NEW_TOKENS,
        )
        logger.info("ASR model loaded")


def runtime_info() -> dict:
    return {
        "model_path": MODEL_PATH,
        "model_loaded": _model is not None,
        "dtype": DTYPE,
        "device_map": DEVICE_MAP,
    }


def transcribe(audio_path: str, language: str | None = None) -> dict:
    init_model()
    with _infer_lock:
        results = _model.transcribe(
            audio=audio_path,
            language=language,
            return_time_stamps=False,
        )
    result = results[0]
    return {"text": result.text, "language": result.language}
