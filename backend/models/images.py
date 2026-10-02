# -*- coding: utf-8 -*-
"""
送模型前的图片编码。

只做一件事：把磁盘上的图变成 `data:image/jpeg;base64,...`。统一转 JPEG 是为了
既控制请求体大小，也不浪费模型侧的图片 token；本项目从不把 URL 交给服务端代下载。
"""
import base64
from io import BytesIO
from pathlib import Path

from utils.errors import LlmError

# 送模型前把图缩到这个长边以内：模型侧本来就会把图缩到约 1300x1300 总像素，
# 再大只是白白多传 base64。
MAX_LONG_EDGE = 1600

def encode_image(path: Path) -> str:
    """
    把本地图片编码成 data URL，必要时先缩放。

    功能：长边超过 MAX_LONG_EDGE 时等比缩小，统一转 JPEG 再 base64；
          这样既控制请求体大小，也不浪费模型侧的图片 token。
    应用场景：独立图片判型、文档内嵌图 OCR、扫描件页面识别三处共用。
    输入：path — 图片路径。
    输出：`data:image/jpeg;base64,...` 形式的字符串。
    异常：LlmError — 图片无法解码。
    """
    from PIL import Image

    try:
        with Image.open(path) as image:
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            if max(image.size) > MAX_LONG_EDGE:
                ratio = MAX_LONG_EDGE / max(image.size)
                image = image.resize(
                    (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                    Image.LANCZOS,
                )
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=90)
    except Exception as exc:
        raise LlmError(f"图片编码失败: {exc}") from exc
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"
