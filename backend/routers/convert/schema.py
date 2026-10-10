# -*- coding: utf-8 -*-
"""
请求与响应契约的路由侧入口（ADR 0001 / ADR-0010）。

定义本体在 `utils/convert_contract.py` 与 `utils/processor_schema.py`，本文件再导出，
以便读接口仍从「这个路由的 schema」进，且 `pipelines/` 不必 import `routers`。
"""
from utils.convert_contract import (
    ConvertResponse,
    ConvertUrlBody,
    Meta,
    Modality,
    Timings,
    Usage,
    truncate_content,
)
from utils.processor_schema import AudioMeta, Detector, ErrorInfo, ImageMeta

__all__ = [
    "AudioMeta",
    "ConvertResponse",
    "ConvertUrlBody",
    "Detector",
    "ErrorInfo",
    "ImageMeta",
    "Meta",
    "Modality",
    "Timings",
    "Usage",
    "truncate_content",
]
