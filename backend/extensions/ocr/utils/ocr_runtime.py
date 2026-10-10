import copy
import threading

import cv2
import numpy as np
from configs.log_config import logger
from mineru.utils import models_download_utils
from mineru.utils.enum_class import ModelPath

from configs.base_config import (
    CONFIG_ENV,
    DEFAULT_OCR_LANG,
    DEFAULT_OCR_MODE,
    DEFAULT_USE_ANGLE_CLS,
    OCR_CLS_MODEL_PATH,
    OCR_MODEL_DIR,
    OCR_MODEL_ROOT_DIR,
    OCR_MODEL_ROOT_DIR_ENV,
    OCR_MODEL_SUB_DIR,
)

def get_local_models_dir_without_mineru_json():
    return {
        "pipeline": OCR_MODEL_ROOT_DIR,
    }

models_download_utils.get_local_models_dir = get_local_models_dir_without_mineru_json
# MinerU 会用 get_local_models_dir()["pipeline"] + ModelPath.pytorch_paddle + 权重名拼路径。
ModelPath.pytorch_paddle = OCR_MODEL_SUB_DIR

from mineru.model.ocr.pytorch_paddle import PytorchPaddleOCR
from mineru.utils.ocr_utils import (
    get_rotate_crop_image_for_text_rec,
    merge_det_boxes,
    preprocess_image,
    sorted_boxes,
)

ocr_model_pool = {}
ocr_lock_pool = {}
ocr_pool_lock = threading.Lock()

def build_ocr_model(lang, use_angle_cls):
    # 对齐 MinerU pipeline.model_init.ocr_model_init 的 OCR 初始化参数。
    # ch 会启用 dilation 并扩大 unclip_ratio；其它语言按 MinerU 默认分支处理。
    use_dilation = False
    det_db_unclip_ratio = 1.5
    if lang in [None, "", "ch"]:
        use_dilation = True
        det_db_unclip_ratio = 1.8

    model_kwargs = {
        "det_db_box_thresh": 0.5,
        "lang": lang,
        "use_dilation": use_dilation,
        "det_db_unclip_ratio": det_db_unclip_ratio,
        "enable_merge_det_boxes": True,
    }
    if use_angle_cls:
        model_kwargs["use_angle_cls"] = True
        model_kwargs["cls_model_path"] = OCR_CLS_MODEL_PATH

    return PytorchPaddleOCR(**model_kwargs)

def get_ocr_model(lang, use_angle_cls):
    model_key = (lang, use_angle_cls)
    with ocr_pool_lock:
        if model_key not in ocr_model_pool:
            logger.info(
                f"Loading OCR model: lang={lang}, "
                f"use_angle_cls={use_angle_cls}, ocr_model_dir={OCR_MODEL_DIR}"
            )
            try:
                ocr_model_pool[model_key] = build_ocr_model(lang, use_angle_cls)
                ocr_lock_pool[model_key] = threading.Lock()
            except Exception as e:
                logger.error(
                    f"Load OCR model failed: lang={lang}, "
                    f"use_angle_cls={use_angle_cls}, error={e}"
                )
                raise
            logger.info(f"OCR model loaded: lang={lang}, use_angle_cls={use_angle_cls}")
        return ocr_model_pool[model_key], ocr_lock_pool[model_key]

def init_default_ocr_model():
    get_ocr_model(DEFAULT_OCR_LANG, DEFAULT_USE_ANGLE_CLS)

def get_loaded_model_info():
    return [
        {
            "lang": lang,
            "use_angle_cls": use_angle_cls,
        }
        for lang, use_angle_cls in ocr_model_pool.keys()
    ]

def get_runtime_info():
    return {
        "env": CONFIG_ENV,
        "loaded_models": get_loaded_model_info(),
        "default_lang": DEFAULT_OCR_LANG,
        "default_mode": DEFAULT_OCR_MODE,
        "default_use_angle_cls": DEFAULT_USE_ANGLE_CLS,
        "ocr_model_root_dir_env": OCR_MODEL_ROOT_DIR_ENV,
        "ocr_model_root_dir": OCR_MODEL_ROOT_DIR,
        "ocr_model_sub_dir": OCR_MODEL_SUB_DIR,
        "ocr_model_dir": OCR_MODEL_DIR,
    }

def decode_image(file_bytes):
    image_array = np.frombuffer(file_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("uploaded file is not a valid image")
    return image

def to_builtin_box(box):
    if hasattr(box, "tolist"):
        return box.tolist()
    return box

def normalize_full_ocr_items(dt_boxes, rec_res, angle_res=None):
    text_items = []
    for index, (box, rec_result) in enumerate(zip(dt_boxes, rec_res)):
        text, score = rec_result
        item = {
            "text": text,
            "score": float(score),
            "box": to_builtin_box(box),
        }
        if angle_res:
            angle_label, angle_score = angle_res[index]
            item["angle_label"] = angle_label
            item["angle_score"] = float(angle_score)
        text_items.append(item)
    return text_items

def normalize_det_items(ocr_result):
    if not ocr_result or ocr_result[0] is None:
        return []
    return [{"box": to_builtin_box(box)} for box in ocr_result[0]]

def normalize_rec_items(ocr_result):
    if not ocr_result:
        return []
    text_items = []
    for text, score in ocr_result[0]:
        text_items.append(
            {
                "text": text,
                "score": float(score),
            }
        )
    return text_items

def restored_full_ocr(model, image, use_angle_cls):
    """复原 OCR 完整链路：预处理、检测、排序合并、裁剪、方向分类、识别。"""
    image = preprocess_image(image)
    ori_im = image.copy()
    dt_boxes, _elapse = model.text_detector(image)
    if dt_boxes is None or len(dt_boxes) == 0:
        return []

    if model.is_seal:
        dt_boxes = model._seal_sort_boxes(dt_boxes)
        img_crop_list = model._seal_crop_by_polys(ori_im, dt_boxes)
    else:
        dt_boxes = sorted_boxes(dt_boxes)
        if model.enable_merge_det_boxes:
            dt_boxes = merge_det_boxes(dt_boxes)

        img_crop_list = []
        for box in dt_boxes:
            crop_box = copy.deepcopy(np.array(box).astype("float32"))
            img_crop_list.append(get_rotate_crop_image_for_text_rec(ori_im, crop_box))

    angle_res = None
    if use_angle_cls and getattr(model, "use_angle_cls", False):
        img_crop_list, angle_res, _elapse = model.text_classifier(img_crop_list)

    rec_res, _elapse = model.text_recognizer(img_crop_list)
    text_items = normalize_full_ocr_items(dt_boxes, rec_res, angle_res)
    return [item for item in text_items if item["score"] >= model.drop_score]

def run_ocr(file_bytes, lang, mode, use_angle_cls):
    image = decode_image(file_bytes)
    mode = mode.strip().lower()
    model, model_lock = get_ocr_model(lang, use_angle_cls)
    with model_lock:
        if mode == "full":
            return restored_full_ocr(model, image, use_angle_cls)
        if mode == "det":
            ocr_result = model.ocr(image, det=True, rec=False)
            return normalize_det_items(ocr_result)
        if mode == "rec":
            ocr_result = model.ocr(image, det=False, rec=True)
            return normalize_rec_items(ocr_result)
    raise ValueError("mode must be one of: full, det, rec")
