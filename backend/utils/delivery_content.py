# -*- coding: utf-8 -*-
"""
understand 路的交付正文组装。

extract 只返回纯代码抽取的 `result.content`；understand 在此基础上把模型/OCR/ASR
转写出的文本并进 `content`，同时仍写入 `meta.ocr_text` / `meta.asr_text`（ADR-0011）。
"""
from typing import Optional


def merge_understand_delivery(
    base: str,
    *,
    ocr_text: Optional[str] = None,
    asr_text: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    拼 understand 的交付正文。

    功能：保留确定性抽取段，再按段追加模型转写（段与段之间空一行）。
    应用场景：`ConvertPipeline` 在步骤 6 组装前调用一次。
    输入：base — 步骤 4 的 `result.content`；ocr/asr/description — 模型阶段产出。
    输出：写入响应 `content` 的字符串（可能仍为空）。
    """
    parts: list[str] = []
    stripped_base = (base or "").strip()
    if stripped_base:
        parts.append(stripped_base)

    for chunk in (ocr_text, asr_text, description):
        text = (chunk or "").strip()
        if not text:
            continue
        if parts and text in parts[-1]:
            continue
        if text in stripped_base:
            continue
        parts.append(text)

    return "\n\n".join(parts)
