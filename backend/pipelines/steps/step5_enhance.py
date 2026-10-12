# -*- coding: utf-8 -*-
"""
步骤 5：生成摘要（只有 understand 那条路走）。

输入是交付文本与步骤 4 读出的识别文本：带 `query` 时转为针对性回答，不带时给通用摘要。
摘要在没有 query 且已有图像描述时直接复用描述，省掉一次纯文本调用。

摘要只进 `summary`，**不动 `content`**——`content` 只在步骤 4 的 `extract_*` 里产生（ADR 0002/0007）。

本步也是**模型侧上界**的报告点：送模型的输入被 `max_model_input_chars` 截断、或模型输出被
`llm_max_tokens` 截断（用量记账上的 `output_truncated`）时，把名字交回调用方进 `meta.limits_hit`。
"""
from typing import List, Optional, Tuple

import configs.settings as settings
from configs.settings import PROMPTS_DIR
from extensions.deepseek import DeepSeekClient
from utils.processor_schema import PreparedSource

def summarize(
    client: DeepSeekClient,
    prepared: PreparedSource,
    content: str,
    ocr_text: Optional[str],
    asr_text: Optional[str],
    image_description: Optional[str],
    query: Optional[str],
) -> Tuple[Optional[str], List[str]]:
    """
    步骤 5 的入口：产出摘要。

    功能：把交付文本与识别文本合成模型输入，按上限截断并把命中的上界名交回调用方声明。
    应用场景：understand 的第 5 步（步骤 4 之后、组装响应之前）。
    输入：client — 模型客户端；prepared — 源；content — 交付文本；
          ocr_text — 图像识别文本；asr_text — 语音转写文本；
          image_description — 独立图片的描述；query — 用户问题。
    输出：(摘要文本 或 None, 本次模型阶段命中的上界名列表)。
    异常：LlmError — 调用失败，由调用方转成 notes。
    """
    # 输出被截断是用量记账上的事实（`finish_reason == "length"`），与本次是否又调了一次无关
    hits = ["llm_max_tokens"] if client.ledger.output_truncated else []
    if image_description and not query:
        return image_description, hits

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
        return None, hits

    if len(text) > settings.MAX_MODEL_INPUT_CHARS:
        text = text[: settings.MAX_MODEL_INPUT_CHARS]
        hits.append("max_model_input_chars")

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
    return summary, hits
