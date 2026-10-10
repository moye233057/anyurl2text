# -*- coding: utf-8 -*-
"""
图片抽取：只给结构信息，不给文字（ADR 0004）。

纯代码对一张图拿不到任何文字，能确定拿到的是格式、尺寸、色彩模式与拍摄参数。
认字是 understand 的职责——两条管线的边界在这里最清楚，也是「取舍」叙事的支点。

模型增强走本文件的 `enhance_image`（ADR 0007 的「按格式聚合处理器」）：先用视觉模型判型，语义图片
直接用这次调用给出的描述，文字图片交本机 Paddle OCR 读字——分流与说明都在那一个函数里，见该函数。

SVG 走纯 XML 解析给出画布尺寸与元素统计——矢量图不需要光栅化就能报出结构，
不必为了拿几个结构字段把它渲染成位图。
"""
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from configs.log_config import logger
from configs.settings import PROMPTS_DIR
from models import paddle_ocr
from models.deepseek import DeepSeekClient, parse_json_object
from models.images import ImagePixelLimitError, encode_image, open_image
from utils.processor_schema import EnhanceOutcome, ExtractResult, PreparedSource
from utils.processor_schema import ImageMeta
from utils.textfile import read_text_file

# HEIC 支持是可选依赖：装不上只是读不了 iPhone 原图，不该让整个服务起不来
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于部署环境
    HEIF_AVAILABLE = False

# 只挑常用的拍摄参数，不把整包 EXIF 倒出来（含 GPS 等敏感信息）
EXIF_TAGS = {
    271: "Make",
    272: "Model",
    305: "Software",
    274: "Orientation",
    306: "DateTime",
    36867: "DateTimeOriginal",
    33434: "ExposureTime",
    33437: "FNumber",
    34855: "ISOSpeedRatings",
    37386: "FocalLength",
}

def _read_exif(image: Image.Image) -> dict:
    """挑出白名单里的 EXIF 标签；读不到时返回空 dict，不抛异常。"""
    result = {}
    try:
        exif = image.getexif()
    except Exception:
        return result
    for tag, name in EXIF_TAGS.items():
        value = exif.get(tag)
        if value is None:
            continue
        result[name] = str(value)
    return result

def _svg_meta(path: Path) -> Tuple[ImageMeta, Optional[str]]:
    """解析 SVG 画布尺寸与元素数量；解析失败返回空 meta 与原因。"""
    try:
        # SVG 是 XML：超过文本读取上限时会被截断，随即在下面解析失败并降级，
        # 所以这里不另报 max_text_bytes——失败原因已经写在 degrade_reason 里。
        svg_text, _ = read_text_file(path)
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return ImageMeta(format="SVG"), f"SVG 解析失败: {exc}"

    def _number(text: Optional[str]) -> Optional[int]:
        if not text:
            return None
        digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
        try:
            return int(float(digits))
        except ValueError:
            return None

    width = _number(root.get("width"))
    height = _number(root.get("height"))
    if (width is None or height is None) and root.get("viewBox"):
        parts = root.get("viewBox").replace(",", " ").split()
        if len(parts) == 4:
            width = width or _number(parts[2])
            height = height or _number(parts[3])

    element_count = sum(1 for _ in root.iter())
    meta = ImageMeta(width=width, height=height, format="SVG", mode="vector")
    return meta, f"SVG 是矢量图，未做光栅化；共 {element_count} 个元素"

def extract_image(path: Path, ext: str) -> ExtractResult:
    """
    抽取图片的结构信息。

    功能：Pillow 读格式、尺寸、色彩模式、帧数与常用 EXIF；SVG 走独立解析分支。
    应用场景：dispatch 分派 image 组时调用。
    输入：path — 文件路径；ext — 已识别的小写扩展名。
    输出：ExtractResult，content 为空字符串（图片没有可抽取的文本），
          meta.image 由管线从 result.image_meta 填入。
    异常：不抛——解码失败时返回降级说明，让调用方拿到可读原因而不是 500。
    """
    if ext == "svg":
        meta, note = _svg_meta(path)
        result = ExtractResult(content="", engines=["ElementTree"])
        result.image_meta = meta
        if note:
            result.notes.append(note)
        return result

    try:
        with open_image(path) as image:
            image.load()
            frames = getattr(image, "n_frames", 1)
            dpi = image.info.get("dpi")
            meta = ImageMeta(
                width=image.width,
                height=image.height,
                mode=image.mode,
                format=image.format,
                frames=frames if frames > 1 else None,
                dpi=f"{int(dpi[0])}x{int(dpi[1])}" if dpi and len(dpi) == 2 else None,
                exif=_read_exif(image),
            )
            notes = []
            if ext in ("heic", "heif") and not HEIF_AVAILABLE:
                notes.append("未安装 pillow-heif，HEIC 的解码结果可能不完整")
            if ext == "ico":
                sizes = image.info.get("sizes")
                if sizes:
                    preview = ", ".join(f"{w}x{h}" for w, h in sorted(sizes, reverse=True)[:8])
                    notes.append(f"ICO 内含图标尺寸: {preview}")
            if frames > 1:
                notes.append(f"动图共 {frames} 帧，只报了首帧的结构信息")
            result = ExtractResult(content="", engines=["Pillow"], notes=notes)
            result.image_meta = meta
            return result
    except ImagePixelLimitError as exc:
        result = ExtractResult(
            content="",
            engines=["Pillow"],
            degraded=True,
            degrade_reason=str(exc),
            degrade_code="image_decode_failed",
            limits_hit=["max_image_pixels"],
        )
        return result
    except Exception as exc:
        result = ExtractResult(
            content="",
            engines=["Pillow"],
            degraded=True,
            degrade_reason=f"图片解码失败: {exc}",
            degrade_code="image_decode_failed",
        )
        return result


# 判型结果：模型判为语义图片时，描述由这次判型调用一并给出，不再 OCR
SEMANTIC_KIND = "semantic"

def _ocr_outcome(prepared: PreparedSource) -> EnhanceOutcome:
    """
    文字图片分支：交给本机 Paddle OCR 读字。

    功能：出口（`models/paddle_ocr.py`）只负责发图收字，分流与说明放在这里。
    应用场景：判型为文字图片，以及判型输出解析不出来时调用（拿不准就 OCR）。
    输入：prepared — 已落盘并识别过的图片。
    输出：EnhanceOutcome：读到文字填 ocr_text，没读到就只在 notes 里说明。
    异常：LlmError — OCR 失败，由调用方转成 notes。
    """
    text, ocr_hits = paddle_ocr.recognize(prepared.path)
    text = text.strip()
    if not text:
        return EnhanceOutcome(
            notes=["判为文字图片，本机 Paddle OCR 没有读出文字"],
            limits_hit=ocr_hits,
        )
    return EnhanceOutcome(
        ocr_text=text,
        notes=[f"判为文字图片，已交本机 Paddle OCR 读出 {len(text)} 字"],
        limits_hit=ocr_hits,
    )

def enhance_image(client: DeepSeekClient, prepared: PreparedSource) -> EnhanceOutcome:
    """
    独立图片的模型增强：先判型，再分流到「客观描述」或「OCR」。

    功能：一次视觉模型调用同时拿到判型与语义描述——判为语义图片时，这次调用给出的 description
          就是产出（不再 OCR）；判为文字图片时把图交给本机 Paddle OCR 读字。
          判型输出解析不出来时按文字图片处理：OCR 是本机服务、不花 token，漏掉整页文字代价更大。
    应用场景：understand 遇到源本身就是图片时调用（`understand._run_model_enhance()` 分派到这里）。
    输入：client — 模型客户端；prepared — 已落盘并识别过的图片。
    输出：EnhanceOutcome：语义图片给 description，文字图片给 ocr_text，分支说明进 notes。
    异常：LlmError — 判型调用或 OCR 失败。
    """
    data_url, encode_hits = encode_image(prepared.path)
    messages = [
        {"role": "system", "content": (PROMPTS_DIR / "classify_image.txt").read_text(encoding="utf-8").strip()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请按 json 结构判断这张图的类型并给出内容。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    parsed = parse_json_object(client.chat(messages, json_mode=True))
    kind = str(parsed.get("kind", "")).strip().lower() if isinstance(parsed, dict) else ""

    if kind != SEMANTIC_KIND:
        logger.info(f"独立图片判型：按文字图片处理（kind={kind or '判型输出无法解析'}）")
        outcome = _ocr_outcome(prepared)
        outcome.limits_hit.extend(encode_hits)
        return outcome

    description = str(parsed.get("description", "") or "").strip()
    logger.info(f"独立图片判型：语义图片，描述 {len(description)} 字")
    if not description:
        return EnhanceOutcome(notes=["判为语义图片，但模型没有给出描述"], limits_hit=encode_hits)
    return EnhanceOutcome(
        description=description,
        notes=["判为语义图片，只给客观描述、不做 OCR"],
        limits_hit=encode_hits,
    )
