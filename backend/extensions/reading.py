# -*- coding: utf-8 -*-
"""
各格式共用的读素材实现：把步骤 4 收下来的素材交给模型读成文字。

- `enhance_assets()`：kind="page" 逐页识别（含空白页预判与切半重试），kind="image" 批量识读；
- 独立图片不走这里——它在 `image.py` 的 `enhance_image()`。

分派入口在 `pipelines/steps/step4_extract.py` 的 `enhance()`；本文件只放实现，不放分派。
"""
from pathlib import Path
from typing import Any, Dict, List, Tuple

import configs.settings as settings
from configs.log_config import logger
from configs.settings import PROMPTS_DIR
from extensions.deepseek import DeepSeekClient, parse_json_object
from extensions.images import encode_image, open_image
from utils.processor_schema import EmbeddedAsset, EnhanceOutcome, ExtractResult

# ---------- 读素材的共用件 ----------

# 扫描件整页识别不出来时的哨兵值（见 read_scanned_page.txt）
NO_TEXT_SENTINEL = "NO_TEXT"

# 空白页判定的降采样尺寸与亮度容差：64x64 足够看出有没有内容，开销可忽略
BLANK_PAGE_THUMB = 64
BLANK_PAGE_LUMA_TOLERANCE = 8

def is_blank_page(image_path: Path) -> bool:
    """
    判断一张页面图是否接近纯色（空白页，或整页被大面积色块覆盖）。

    功能：缩到 64x64 灰度图后看亮度极差，不超过容差即认定没有可识别内容。
    应用场景：扫描件逐页识别前的确定性预检——空白页直接跳过，省掉一次模型调用；
              而且不做这个判定的话，空白页返回的短文本还会再触发两次切半重试。
    输入：image_path — 页面图路径。
    输出：True 表示判定为空白；读取失败返回 False，让流程照常走模型。
    """
    try:
        with open_image(image_path) as image_file:
            thumbnail = image_file.convert("L").resize((BLANK_PAGE_THUMB, BLANK_PAGE_THUMB))
            lowest, highest = thumbnail.getextrema()
    except Exception:
        return False
    return (highest - lowest) <= BLANK_PAGE_LUMA_TOLERANCE

def read_page_text(client: DeepSeekClient, image_path: Path) -> Tuple[str, List[str]]:
    """识别一张页面图；返回 (正文, 编码阶段命中的上界名)。空串表示这一页没有可识别文字。"""
    data_url, hits = encode_image(image_path)
    messages = [
        {"role": "system", "content": (PROMPTS_DIR / "read_scanned_page.txt").read_text(encoding="utf-8").strip()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "转录这一页的文字。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    raw = client.chat(messages)
    if raw.strip().upper().startswith(NO_TEXT_SENTINEL):
        return "", hits
    return raw, hits

def split_vertical(image_path: Path) -> List[Path]:
    """
    把页面图按上下两半切开。

    功能：各留少量重叠避免切断行；输出到同目录，随请求一起清理。
    应用场景：整页识别效果不佳时的重试（ADR 0006）。
    输入：image_path — 整页图路径。
    输出：两张半页图路径；切分失败返回空列表。
    """
    try:
        with open_image(image_path) as image_file:
            width, height = image_file.size
            if height < 40:
                return []
            overlap = max(1, int(height * 0.02))
            top = image_file.crop((0, 0, width, height // 2 + overlap))
            bottom = image_file.crop((0, height // 2 - overlap, width, height))
            top_path = image_path.with_name(image_path.stem + "_top.png")
            bottom_path = image_path.with_name(image_path.stem + "_bottom.png")
            top.convert("RGB").save(top_path, format="PNG")
            bottom.convert("RGB").save(bottom_path, format="PNG")
        return [top_path, bottom_path]
    except Exception as exc:
        logger.error(f"页面切分失败: {exc}")
        return []

def _read_scanned_pages(
    client: DeepSeekClient, page_assets: List[EmbeddedAsset]
) -> Tuple[str, List[str], List[str]]:
    """
    逐页识别扫描件；整页认不出来时按上下两半重试。

    功能：先做**确定性空白页判定**——接近纯色的页面直接跳过，不花模型调用，
          也不触发切半重试（扫描件里的空白页很常见，白跑两次调用纯属浪费）。
          其余页面先整页识别；结果为空、哨兵值或短于阈值时切成上下两半各识别一次
          再拼回去（ADR 0006）。常态不花多余 token，只有真正模糊的页才加钱。
    应用场景：渲染出整页图（kind="page"）的源，understand 处理时调用。
    输入：client — 模型客户端；page_assets — kind="page" 的素材。
    输出：(按页拼接的原文（含页标记）, 过程说明, 编码阶段命中的上界名)；
          过程说明由调用方放进 meta.notes。
    异常：LlmError — 模型调用失败，由调用方处理。
    """
    blocks = []
    notes = []
    hits: List[str] = []
    retried = 0
    blank = 0
    for asset in sorted(page_assets, key=lambda item: item.order):
        if is_blank_page(asset.path):
            blank += 1
            continue
        text, page_hits = read_page_text(client, asset.path)
        hits.extend(page_hits)
        if len(text) < settings.RENDERED_PAGE_RETRY_MIN_CHARS:
            halves = split_vertical(asset.path)
            if halves:
                half_parts = []
                for half in halves:
                    part, half_hits = read_page_text(client, half)
                    hits.extend(half_hits)
                    if part:
                        half_parts.append(part)
                merged = "\n".join(half_parts)
                # 只有切半确实读出了更多内容才算「重试成功」，否则说明这页本来就没字
                if len(merged) > len(text):
                    text = merged
                    retried += 1
        if text:
            blocks.append(f"=== {asset.label} ===\n{text}")
    if blank:
        notes.append(f"其中 {blank} 页判定为空白页，未调用模型")
    if retried:
        notes.append(f"其中 {retried} 页整页识别效果不佳，已按上下两半重新识别后采用")
    return "\n\n".join(blocks), notes, hits

def read_embedded_images(client: DeepSeekClient, image_assets: List[EmbeddedAsset]) -> Tuple[str, List[str]]:
    """
    批量识别文档内嵌图。

    功能：一次请求带上多张图，模型按 index 返回每张图的文字；index 对不上的结果丢弃。
    应用场景：PDF / DOCX / PPTX 的内嵌图，三条路共用这一份实现。
    输入：client — 模型客户端；image_assets — kind="image" 的素材。
    输出：(按素材顺序拼接的原文, 编码阶段命中的上界名)；都没文字时正文为空串。
    异常：LlmError — 调用失败。
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": "按 json 结构读出这些图片里的文字。"}]
    hits: List[str] = []
    for asset in image_assets:
        data_url, image_hits = encode_image(asset.path)
        hits.extend(image_hits)
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    messages = [
        {"role": "system", "content": (PROMPTS_DIR / "read_embedded_images.txt").read_text(encoding="utf-8").strip()},
        {"role": "user", "content": content},
    ]
    raw = client.chat(messages, json_mode=True)
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return raw, hits

    by_index: Dict[int, str] = {}
    for item in parsed.get("images", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            by_index[int(item.get("index"))] = str(item.get("text", "") or "").strip()
        except (TypeError, ValueError):
            continue

    blocks = []
    for position, asset in enumerate(image_assets, start=1):
        text = by_index.get(position, "")
        if text:
            blocks.append(f"[{asset.label}]\n{text}")
    return "\n\n".join(blocks), hits

def enhance_assets(client: DeepSeekClient, result: ExtractResult) -> EnhanceOutcome:
    """
    模型增强的通用路径：读 `result.assets` 里的素材。

    功能：kind="page" 的素材逐页识别（含空白页预判与切半重试），kind="image" 的批量识读，
          两段文本按素材出现顺序拼接。
    应用场景：没有格式专属增强器的格式都走这里（PDF 的扫描页与内嵌图、DOCX / PPTX 的内嵌图）；
              格式专属逻辑写在那个格式自己的文件里（今天只有 image.py 的独立图片）。
    输入：client — 模型客户端；result — 确定性产出（本函数只读它的 assets，不改 content）。
    输出：EnhanceOutcome，description 恒为 None——「图像的客观描述」是独立图片才有的东西。
    异常：LlmError — 模型调用失败，由调用方转成 notes。
    """
    page_assets = [asset for asset in result.assets if asset.kind == "page"]
    image_assets = [asset for asset in result.assets if asset.kind == "image"]

    parts: List[str] = []
    notes: List[str] = []
    hits: List[str] = []
    if page_assets:
        page_text, page_notes, page_hits = _read_scanned_pages(client, page_assets)
        parts.append(page_text)
        notes.extend(page_notes)
        hits.extend(page_hits)
    if image_assets:
        embedded_text, image_hits = read_embedded_images(client, image_assets)
        parts.append(embedded_text)
        hits.extend(image_hits)

    joined = "\n\n".join(part for part in parts if part)
    return EnhanceOutcome(ocr_text=joined or None, notes=notes, limits_hit=hits)
