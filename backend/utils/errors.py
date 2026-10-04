# -*- coding: utf-8 -*-
"""模型层的统一异常。消息面向使用者，会原样出现在 meta.notes 的失败说明里。"""
from typing import Optional

import configs.errors as errors

class LlmError(Exception):
    """
    模型调用失败（视觉模型、本机 OCR、本机 ASR 三处共用）。

    code 是**稳定机器名**（见 `configs/errors.py` 的 `MODEL_FAILURE_CODES`），进
    `meta.model_error.code`；构造时校验白名单，打错一个字当场炸掉。param 指出出错的位置
    （如 `json` / `object` / `切片2`），没有明确归属时为 None。
    """

    def __init__(self, message: str, code: str = "llm_call_failed",
                 param: Optional[str] = None) -> None:
        if code not in errors.MODEL_FAILURE_CODES:
            raise ValueError(f"未登记的模型错误码: {code}（见 configs/errors.py）")
        super().__init__(message)
        self.code = code
        self.param = param
