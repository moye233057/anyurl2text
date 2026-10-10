# -*- coding: utf-8 -*-
"""PP-OCRv5 权重加载与识别（本服务唯一推理模块）。"""
from __future__ import annotations

import copy
import logging
import os
import sys
import threading

import cv2
import numpy as np

logger = logging.getLogger("paddle-ocr")

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
OCR_MODEL_SUB_DIR = "paddleocr_torch"
OCR_MODEL_ROOT_DIR = os.path.join(SERVICE_ROOT, "models", "OCR")
OCR_MODEL_DIR = os.path.join(OCR_MODEL_ROOT_DIR, OCR_MODEL_SUB_DIR)
OCR_CLS_MODEL_PATH = os.path.join(OCR_MODEL_DIR, "ch_ptocr_mobile_v2.0_cls_infer.pth")

DEFAULT_LANG = "ch"
DEFAULT_USE_ANGLE_CLS = True

_CUDA_LIB_READY = "PP_OCRV5_CUDA_LIB_PATH_READY"


def setup_env() -> None:
    """导入 torch / MinerU 前固定显卡，并（Linux）优先本 venv 的 nvjitlink。"""
    cuda = os.getenv("CUDA_VISIBLE_DEVICES", "0").strip() or "0"
    os.environ["CUDA_VISIBLE_DEVICES"] = cuda
    os.environ["MINERU_MODEL_SOURCE"] = "local"
    os.environ.setdefault("TORCH_FORCE_WEIGHTS_ONLY_LOAD", "1")

    if os.environ.get(_CUDA_LIB_READY) == "1":
        return
    py = f"python{sys.version_info.major}.{sys.version_info.minor}"
    nvjitlink = os.path.join(
        sys.prefix, "lib", py, "site-packages", "nvidia", "nvjitlink", "lib"
    )
    if not os.path.isdir(nvjitlink):
        return
    current = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{nvjitlink}:{current}"
    os.environ[_CUDA_LIB_READY] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


setup_env()

from mineru.utils import models_download_utils  # noqa: E402
from mineru.utils.enum_class import ModelPath  # noqa: E402

models_download_utils.get_local_models_dir = lambda: {"pipeline": OCR_MODEL_ROOT_DIR}
ModelPath.pytorch_paddle = OCR_MODEL_SUB_DIR

from mineru.model.ocr.pytorch_paddle import PytorchPaddleOCR  # noqa: E402
from mineru.utils.ocr_utils import (  # noqa: E402
    get_rotate_crop_image_for_text_rec,
    merge_det_boxes,
    preprocess_image,
    sorted_boxes,
)

_pool: dict = {}
_locks: dict = {}
_pool_guard = threading.Lock()


def _build_model(lang: str, use_angle_cls: bool):
    use_dilation = lang in (None, "", "ch")
    kwargs = {
        "det_db_box_thresh": 0.5,
        "lang": lang,
        "use_dilation": use_dilation,
        "det_db_unclip_ratio": 1.8 if use_dilation else 1.5,
        "enable_merge_det_boxes": True,
    }
    if use_angle_cls:
        kwargs["use_angle_cls"] = True
        kwargs["cls_model_path"] = OCR_CLS_MODEL_PATH
    return PytorchPaddleOCR(**kwargs)


def _get_model(lang: str, use_angle_cls: bool):
    key = (lang, use_angle_cls)
    with _pool_guard:
        if key not in _pool:
            logger.info("Loading OCR model lang=%s angle_cls=%s dir=%s", lang, use_angle_cls, OCR_MODEL_DIR)
            _pool[key] = _build_model(lang, use_angle_cls)
            _locks[key] = threading.Lock()
            logger.info("OCR model loaded")
        return _pool[key], _locks[key]


def init_model() -> None:
    _get_model(DEFAULT_LANG, DEFAULT_USE_ANGLE_CLS)


def runtime_info() -> dict:
    return {
        "loaded_models": [
            {"lang": lang, "use_angle_cls": angle} for lang, angle in _pool
        ],
        "ocr_model_dir": OCR_MODEL_DIR,
    }


def _to_box(box):
    return box.tolist() if hasattr(box, "tolist") else box


def _full_ocr(model, image, use_angle_cls: bool) -> list[dict]:
    image = preprocess_image(image)
    ori = image.copy()
    dt_boxes, _ = model.text_detector(image)
    if dt_boxes is None or len(dt_boxes) == 0:
        return []

    if model.is_seal:
        dt_boxes = model._seal_sort_boxes(dt_boxes)
        crops = model._seal_crop_by_polys(ori, dt_boxes)
    else:
        dt_boxes = sorted_boxes(dt_boxes)
        if model.enable_merge_det_boxes:
            dt_boxes = merge_det_boxes(dt_boxes)
        crops = []
        for box in dt_boxes:
            crop_box = copy.deepcopy(np.array(box).astype("float32"))
            crops.append(get_rotate_crop_image_for_text_rec(ori, crop_box))

    angle_res = None
    if use_angle_cls and getattr(model, "use_angle_cls", False):
        crops, angle_res, _ = model.text_classifier(crops)

    rec_res, _ = model.text_recognizer(crops)
    items = []
    for index, (box, (text, score)) in enumerate(zip(dt_boxes, rec_res)):
        item = {"text": text, "score": float(score), "box": _to_box(box)}
        if angle_res:
            label, a_score = angle_res[index]
            item["angle_label"] = label
            item["angle_score"] = float(a_score)
        if item["score"] >= model.drop_score:
            items.append(item)
    return items


def run_ocr(file_bytes: bytes) -> list[dict]:
    arr = np.frombuffer(file_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("uploaded file is not a valid image")
    model, lock = _get_model(DEFAULT_LANG, DEFAULT_USE_ANGLE_CLS)
    with lock:
        return _full_ocr(model, image, DEFAULT_USE_ANGLE_CLS)
