# -*- coding: utf-8 -*-
"""
DeepSeek 模型出口：调用、重试、思考模式开关、JSON 输出与用量记账。

一种模型一个文件（ADR 0007）：Paddle OCR 就是按这条加进来的（`models/paddle_ocr.py`），
再接别的模型同样在 `models/` 下加一个文件，提示词模板放 `models/prompts/`，计价与记账共用 `utils/usage.py`。

事实依据（2026-09-11 核对官方文档）：
- `deepseek-flash` **一个模型同时支持文本与图片**；旧的 `deepseek-v4-flash` 与
  `deepseek-v4-flash-vision-exp` 已下线，仅作兼容路由，不要使用。
- **思考模式默认是打开的**（effort 默认 high），思维链按输出侧计费、输出价约为
  输入未命中价的 4 倍，所以这里默认显式关闭，只在对质量敏感的摘要阶段按配置打开。
- 图片按 token 换算计费、**每张上限 1024 token**；图片只能放在 user 消息里，
  放进 system 会返回 400。
- Chat Completions 只有 `json_object`，没有 `json_schema`；用 JSON 输出时 prompt 里
  必须出现 "json" 字样，且必须显式设 `max_tokens`（默认非思考只有 8K，会被静默截断）。
- usage 区分 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，命中价约为未命中价的
  1/50，所以成本必须分开算，不能拿 prompt_tokens 乘一个单价。
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

import configs.settings as settings
from configs.log_config import logger
from utils.errors import LlmError
from utils.usage import UsageLedger

class DeepSeekClient:
    """
    模型调用封装：重试、思考模式开关、JSON 输出与用量记账都在这里。

    客户端按需创建：没配 API key 时构造对象不报错，只在真正调用时给出明确原因，
    这样接口一（纯代码抽取）不会被模型配置问题连坐。
    """

    def __init__(self) -> None:
        self.model = settings.DEEPSEEK_MODEL
        self.ledger = UsageLedger(model=self.model)
        self._client = None

    def _ensure_client(self):
        """惰性创建 OpenAI 客户端；缺 key 时抛出可读原因。"""
        if self._client is not None:
            return self._client
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise LlmError("未配置 DEEPSEEK_API_KEY，请在项目根目录的 .env 里填写（可参考 .env.example）")
        from openai import OpenAI

        # 超时必须显式给：视觉模型逐页识别单页就可能跑几十秒，用 SDK 默认值会过早断开
        self._client = OpenAI(
            api_key=api_key,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=settings.LLM_TIMEOUT_SEC,
        )
        return self._client

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        json_mode: bool = False,
        thinking: bool = False,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        发起一次对话调用并返回文本内容。

        功能：默认关闭思考模式；json_mode 时要求模型返回 JSON；空内容与异常都会重试
              （官方承认偶发空 content）；每次都把 usage 记进账本。
        应用场景：认字、描述、摘要、按 query 回答四条链路共用。
        输入：messages — OpenAI 格式消息（图片务必放在 user 消息里）；
              json_mode — 是否要求 JSON 输出，prompt 里必须含 "json" 字样；
              thinking — 是否开启思考模式；
              max_tokens — 输出上限，默认取配置值（必须显式给，否则长文本会被截断）。
        输出：模型返回的文本（已 strip）。
        异常：LlmError — 重试仍失败或返回空内容。
        """
        client = self._ensure_client()
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
            "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
        }
        if thinking:
            payload["reasoning_effort"] = settings.THINKING_EFFORT
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error = ""
        for attempt in range(1, settings.LLM_MAX_RETRIES + 1):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(**payload)
            except Exception as exc:
                last_error = str(exc)
                logger.error(f"llm 调用失败 attempt={attempt}: {exc}")
                time.sleep(min(2 ** attempt, 8))
                continue

            self._record_usage(response)
            text = (response.choices[0].message.content or "").strip()
            elapsed = round(time.monotonic() - started, 2)
            logger.info(
                f"llm 调用完成 attempt={attempt} cost={elapsed}s json={json_mode} "
                f"thinking={thinking} chars={len(text)}"
            )
            if text:
                return text
            last_error = "模型返回空内容"
            time.sleep(min(2 ** attempt, 8))

        raise LlmError(f"模型调用失败（已重试 {settings.LLM_MAX_RETRIES} 次）: {last_error}")

    def _record_usage(self, response) -> None:
        """把 SDK 响应里的 usage 转成 dict 记进账本。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        try:
            data = usage.model_dump()
        except AttributeError:
            data = dict(usage)
        self.ledger.add(data)

def parse_json_object(text: str) -> Any:
    """
    从模型输出里解析 JSON 对象或数组。

    功能：容忍代码块围栏与前后的解释性文字；解析失败返回 None。
    应用场景：json_mode 调用的结果解析；官方承认偶尔仍会带围栏。
    输入：text — 模型原始输出。
    输出：解析出的对象；失败返回 None。
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
