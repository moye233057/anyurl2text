# -*- coding: utf-8 -*-
"""
本机 Paddle OCR 出口：把图交给 OCR 服务，拿回文字。

一种模型一个文件（ADR 0007）。这里只做出口该做的事——发图、收字、把失败转成 `LlmError`；
什么时候调用它由处理器决定（`pipelines/processors/image.py` 的 `enhance_image`）。
OCR 不计 token、也不产生 DeepSeek 账单，所以这里不记账：`usage` 只反映 DeepSeek 调用。

服务契约（对齐 Individual/paddle-ocr 的 `POST /ocr-b64`）：
请求 `{"file_base64": "..."}`，响应 `{"code": "200", "msg": "success", "data": "文本", "cost": 2.1}`，
其中 `cost` 是服务端耗时秒数；`code` 不是 "200" 一律当失败。

纵向超长图服务端不做切片（整图会被缩到检测模型的输入尺寸，小字直接糊掉），所以在本地按固定
高度切段并发识别，再按切片顺序拼回去；普通图与横向图整图一次识别（原图字节直接发，不二次压缩）。
"""
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Tuple

import httpx
from PIL import Image

import configs.settings as settings
from configs.log_config import logger
from utils.errors import LlmError

# 切片几何是实现侧的固定取法，不是部署配置：长宽比到不了 LONG_IMAGE_ASPECT_RATIO 就整图识别。
LONG_IMAGE_ASPECT_RATIO = 2.5
SLICE_HEIGHT = 1400
SLICE_OVERLAP = 120
SLICE_MAX_WORKERS = 4

def _is_long_vertical(width: int, height: int) -> bool:
    """纵向超长图才切片；普通图与横向图仍整图单次识别。"""
    if width <= 0 or height <= 0 or height <= width:
        return False
    return (height / width) >= LONG_IMAGE_ASPECT_RATIO

def _encode_jpeg(image: Image.Image) -> bytes:
    """切片转 JPEG 字节：长截图多是 PNG，转 JPEG 能把请求体压小一个量级。"""
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()

def _recognize_bytes(image_bytes: bytes, label: str) -> str:
    """
    发一次 OCR 请求，返回识别文本。

    输入：image_bytes — 图片字节；label — 日志用短标签（尺寸或切片位置）。
    输出：服务返回的文本（图里没有文字时为空串）。
    异常：LlmError — 图片为空、服务不可达、响应不是预期的 json 对象、服务返回失败。
    """
    if not image_bytes:
        raise LlmError("Paddle OCR 输入图片为空", "ocr_input_empty")

    payload = {"file_base64": base64.b64encode(image_bytes).decode("ascii")}
    try:
        response = httpx.post(
            settings.OCR_API_URL, json=payload, timeout=settings.OCR_TIMEOUT_SEC
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError as exc:
        raise LlmError(f"Paddle OCR 服务不可达（{settings.OCR_API_URL}）: {exc}", "ocr_unreachable") from exc
    except ValueError as exc:
        raise LlmError(f"Paddle OCR 响应不是 json: {exc}", "ocr_bad_response", "json") from exc

    if not isinstance(result, dict):
        raise LlmError(f"Paddle OCR 响应不是对象: {result!r}", "ocr_bad_response", "object")
    if str(result.get("code")) != "200":
        raise LlmError(
            f"Paddle OCR 返回失败: code={result.get('code')} msg={result.get('msg')}",
            "ocr_service_failed",
        )
    text = str(result.get("data") or "")
    logger.info(f"paddle ocr 完成 {label} server_cost={result.get('cost')}s chars={len(text)}")
    return text

def _iter_slices(image: Image.Image) -> List[Tuple[int, int, int, Image.Image]]:
    """按固定高度切段，段间留重叠避免把一行字切断。"""
    width, height = image.size
    step = max(1, SLICE_HEIGHT - SLICE_OVERLAP)
    slices = []
    y_start = 0
    index = 1
    while y_start < height:
        y_end = min(y_start + SLICE_HEIGHT, height)
        slices.append((index, y_start, y_end, image.crop((0, y_start, width, y_end))))
        if y_end >= height:
            break
        y_start += step
        index += 1
    return slices

def _recognize_long_image(image: Image.Image, label: str) -> str:
    """纵向超长图切片并发识别，再按切片顺序拼回文本。"""
    slices = _iter_slices(image)
    if not slices:
        # 空切片会让 ThreadPoolExecutor(max_workers=0) 抛 ValueError；那是个难懂的错，
        # 而且它不是 LlmError，会绕过 understand 对模型错误的处理。这里明确成带码的失败。
        raise LlmError("Paddle OCR 长图切片为空", "ocr_slice_empty", label)

    def recognize_one(slice_info):
        index, y_start, y_end, piece = slice_info
        slice_label = f"{label} 切片{index}/{len(slices)} y={y_start}-{y_end}"
        try:
            return index, _recognize_bytes(_encode_jpeg(piece), slice_label), None
        except LlmError as exc:
            # 连异常一起带出去：只留 str(exc) 会把内层 code 丢掉
            return index, "", exc

    by_index: Dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(SLICE_MAX_WORKERS, len(slices))) as pool:
        futures = [pool.submit(recognize_one, slice_info) for slice_info in slices]
        for future in as_completed(futures):
            index, text, failure = future.result()
            # 任一切片失败即整张图失败：半张的文字混进 ocr_text 比明确失败更难发现
            if failure is not None:
                for pending in futures:
                    pending.cancel()
                raise LlmError(str(failure), "ocr_slice_failed", f"切片{index}")
            by_index[index] = text
    return "\n".join(by_index[index] for index in sorted(by_index) if by_index[index])

def recognize(path: Path) -> str:
    """
    识别一张图里的文字。

    功能：纵向超长图切片并发识别后拼回；其余图整图一次识别。
    应用场景：`pipelines/processors/image.py` 的 `enhance_image` 判型为文字图片时调用。
    输入：path — 本地图片路径。
    输出：识别出的文字（按行拼接）；图里没有文字时返回空串。
    异常：LlmError — 读图失败、服务不可达、服务返回失败。
    """
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            long_vertical = _is_long_vertical(width, height)
            rgb = image.convert("RGB") if long_vertical else None
    except Exception as exc:
        raise LlmError(f"Paddle OCR 读图失败: {exc}", "ocr_read_image_failed", "image") from exc

    label = f"size={width}x{height}"
    if long_vertical:
        return _recognize_long_image(rgb, label)

    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        raise LlmError(f"Paddle OCR 读取图片字节失败: {exc}", "ocr_read_image_failed", "bytes") from exc
    return _recognize_bytes(image_bytes, label)
