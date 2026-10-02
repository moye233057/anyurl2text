# -*- coding: utf-8 -*-
"""
步骤 5：生成摘要（只有 understand 那条路走）。

输入是交付文本与步骤 4 读出的识别文本：带 `query` 时转为针对性回答，不带时给通用摘要。
摘要在没有 query 且已有图像描述时直接复用描述，省掉一次纯文本调用。

摘要只进 `summary`，**不动 `content`**——`content` 只在步骤 4 的 `extract_*` 里产生（ADR 0002/0007）。
"""
from typing import Optional, Tuple

import configs.settings as settings
from configs.settings import PROMPTS_DIR
from models.deepseek import DeepSeekClient
from pipelines.processors.schema import PreparedSource

def summarize(
    client: DeepSeekClient,
    prepared: PreparedSource,
    content: str,
    ocr_text: Optional[str],
    asr_text: Optional[str],
    image_description: Optional[str],
    query: Optional[str],
) -> Tuple[Optional[str], bool]:
    """
    步骤 5 的入口：产出摘要。

    功能：把交付文本与识别文本合成模型输入，按上限截断并回报给调用方声明。
    应用场景：understand 的第 5 步（步骤 4 之后、组装响应之前）。
    输入：client — 模型客户端；prepared — 源；content — 交付文本；
          ocr_text — 图像识别文本；asr_text — 语音转写文本；
          image_description — 独立图片的描述；query — 用户问题。
    输出：(摘要文本 或 None, 送模型输入是否被截断)。
    异常：LlmError — 调用失败，由调用方转成 notes。
    """
    if image_description and not query:
        return image_description, False

    # 交付文本与识别文本拼成模型输入。
    # 带 query 问一张没有文字的图时，图像描述是唯一可依据的内容，必须带上，
    # 否则会出现「明明读懂了图，却因为没有文本而答不出来」。
    parts = []
    if content.strip():
        parts.append(content)
    if ocr_text:
        parts.append(f"=== 模型识别出的图像文字 ===\n{ocr_text}")
    if asr_text:
        parts.append(f"=== 语音识别出的文字 ===\n{asr_text}")
    if image_description and query:
        parts.append(f"=== 模型的图像描述 ===\n{image_description}")
    text = "\n\n".join(parts)
    if not text.strip():
        return None, False

    truncated = False
    if len(text) > settings.MAX_MODEL_INPUT_CHARS:
        text = text[: settings.MAX_MODEL_INPUT_CHARS]
        truncated = True

    if query:
        system_prompt = (PROMPTS_DIR / "answer.txt").read_text(encoding="utf-8").strip()
        user_prompt = f"用户问题：\n{query}\n\n给定文本：\n{text}"
    else:
        system_prompt = (PROMPTS_DIR / "summarize.txt").read_text(encoding="utf-8").strip()
        user_prompt = f"给定文本（来源：{prepared.file_name or prepared.source}）：\n{text}"

    # 默认关思考模式（官方默认是开的且按输出侧计费）；需要更高质量时用 .env 打开
    summary = client.chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        thinking=settings.THINKING_ENABLED,
    )
    return summary, truncated
