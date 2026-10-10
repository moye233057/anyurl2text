# -*- coding: utf-8 -*-
"""Qwen3-ASR 权重加载与识别执行。

职责：
- 进程内只保留一份模型实例，加载与推理分别串行化，避免显存被反复加载挤爆；
- 提供单条音频的识别入口，统一返回 text / language 两项。
对外函数：init_asr_model、get_runtime_info、transcribe_audio。
"""

import threading

import torch
from qwen_asr import Qwen3ASRModel

from configs.base_config import (
    ASR_DEVICE_MAP,
    ASR_DTYPE,
    ASR_MODEL_PATH,
    CONFIG_ENV,
    MAX_INFERENCE_BATCH_SIZE,
    MAX_NEW_TOKENS,
)
from configs.log_config import logger

asr_model = None
asr_model_lock = threading.Lock()
asr_load_lock = threading.Lock()


def build_asr_model():
    """按官方 transformers 后端方式加载权重，不挂 ForcedAligner，因此不产出时间戳。"""
    return Qwen3ASRModel.from_pretrained(
        ASR_MODEL_PATH,
        dtype=getattr(torch, ASR_DTYPE),
        device_map=ASR_DEVICE_MAP,
        max_inference_batch_size=MAX_INFERENCE_BATCH_SIZE,
        max_new_tokens=MAX_NEW_TOKENS,
    )


def get_asr_model():
    """取进程内唯一的模型实例，首次调用时加载并缓存。"""
    global asr_model
    with asr_load_lock:
        if asr_model is None:
            logger.info(
                f"Loading ASR model: model_path={ASR_MODEL_PATH}, "
                f"dtype={ASR_DTYPE}, device_map={ASR_DEVICE_MAP}"
            )
            try:
                asr_model = build_asr_model()
            except Exception as e:
                logger.error(f"Load ASR model failed: model_path={ASR_MODEL_PATH}, error={e}")
                raise
            logger.info("ASR model loaded")
        return asr_model


def init_asr_model():
    """启动阶段预加载模型，加载失败直接让服务起不来。"""
    get_asr_model()


def get_runtime_info():
    """健康检查返回的运行时信息：模型路径、设备与关键推理参数。"""
    return {
        "env": CONFIG_ENV,
        "model_path": ASR_MODEL_PATH,
        "model_loaded": asr_model is not None,
        "dtype": ASR_DTYPE,
        "device_map": ASR_DEVICE_MAP,
        "max_inference_batch_size": MAX_INFERENCE_BATCH_SIZE,
        "max_new_tokens": MAX_NEW_TOKENS,
    }


def transcribe_audio(audio_path, language=None):
    """
    识别单个音频文件。

    功能：调用 Qwen3-ASR 做整段识别，语种默认交给模型自动判定。
    应用场景：HTTP 路由把落盘后的临时音频文件交给这里推理。
    输入：audio_path — 本地音频文件路径；language — 语种名（如 "Chinese"），None 表示自动识别。
    输出：dict，字段 text（识别文本）、language（模型判定的语种）。
    异常：音频无法解码时由底层抛出，由路由层转成 HTTP 错误。
    """
    model = get_asr_model()
    # transformers 后端的 generate 不是线程安全的，同一份权重上串行推理。
    with asr_model_lock:
        results = model.transcribe(
            audio=audio_path,
            language=language,
            return_time_stamps=False,
        )
    result = results[0]
    return {
        "text": result.text,
        "language": result.language,
    }
