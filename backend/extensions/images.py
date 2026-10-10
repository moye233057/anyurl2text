# -*- coding: utf-8 -*-
"""
送模型前的图片编码。

只做一件事：把磁盘上的图变成 `data:image/jpeg;base64,...`。统一转 JPEG 是为了
既控制请求体大小，也不浪费模型侧的图片 token；本项目从不把 URL 交给服务端代下载。
"""
import base64
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Iterator, List, Tuple

from PIL import Image, ImageFile

import configs.limits as limits
from utils.errors import LlmError

# 进程内像素硬顶：超过即 DecompressionBombError，不再「超 1 倍只告警」
Image.MAX_IMAGE_PIXELS = limits.MAX_IMAGE_PIXELS


class ImagePixelLimitError(ValueError):
    """图片宽×高超过 `limits.MAX_IMAGE_PIXELS`。"""


@contextmanager
def open_image(path: Path) -> Iterator[Image.Image]:
    """
    打开本地图片，仅在本次解码允许截断 JPEG；读前做像素预算。

    功能：把 `LOAD_TRUNCATED_IMAGES` 收在单次打开里，避免进程级全局开关影响其它库；
          头信息里的宽×高超过 `MAX_IMAGE_PIXELS` 直接拒绝，不整图解码进内存。
    应用场景：抽取结构信息、送模型前编码、空白页判定与切半重试。
    输入：path — 图片路径。
    输出：Pillow Image（with 结束即关闭）。
    异常：ImagePixelLimitError — 像素超预算；其余解码错误原样抛出。
    """
    previous = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        try:
            with Image.open(path) as image:
                width, height = image.size
                if width * height > limits.MAX_IMAGE_PIXELS:
                    raise ImagePixelLimitError(
                        f"图片像素 {width}×{height} 超过上限 {limits.MAX_IMAGE_PIXELS}"
                    )
                yield image
        except Image.DecompressionBombError as exc:
            raise ImagePixelLimitError(
                f"图片像素超过上限 {limits.MAX_IMAGE_PIXELS}"
            ) from exc
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous

def encode_image(path: Path) -> Tuple[str, List[str]]:
    """
    把本地图片编码成 data URL，必要时先缩放。

    功能：长边超过 `limits.MAX_LONG_EDGE`（注册表中的唯一定义处）时等比缩小——模型侧本来就会把图
          缩到约 1300x1300 总像素，再大只是白白多传 base64；统一转 JPEG 再 base64。
          命中缩放上界时返回名字 `max_long_edge`，由调用方记进 `limits_hit`。
    应用场景：独立图片判型、文档内嵌图 OCR、扫描件页面识别三处共用。
    输入：path — 图片路径。
    输出：(data URL, 命中的上界名列表)。
    异常：LlmError — 图片无法解码。
    """
    hits: List[str] = []
    try:
        with open_image(path) as image:
            working = image
            if working.mode not in ("RGB", "L"):
                working = working.convert("RGB")
            if max(working.size) > limits.MAX_LONG_EDGE:
                ratio = limits.MAX_LONG_EDGE / max(working.size)
                working = working.resize(
                    (max(1, int(working.width * ratio)), max(1, int(working.height * ratio))),
                    Image.LANCZOS,
                )
                hits.append("max_long_edge")
            buffer = BytesIO()
            working.save(buffer, format="JPEG", quality=90)
    except Exception as exc:
        raise LlmError(f"图片编码失败: {exc}", "image_encode_failed") from exc
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}", hits
