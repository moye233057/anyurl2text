# -*- coding: utf-8 -*-
"""
DeepSeek 模型出口：调用、重试、思考模式、JSON 输出，以及**本仓唯一的第三方计价与成本控制**。

一种模型一个文件（ADR 0007）。本机 OCR / ASR 不计费、不走这里的账本与成本闸。

事实依据（2026-10-10 核对 https://api-docs.deepseek.com/zh-cn/quick_start/pricing）：
- 模型名用 `deepseek-flash`（DeepSeek-V4.1-Flash）；旧名 `deepseek-v4-flash` /
  `deepseek-v4-flash-vision-exp` 仍可调，按 Flash 价计费。
- 思考模式默认打开；思维链按输出侧计费，本仓默认显式关闭。
- 图片按 token 计费；只能放在 user 消息里。
- usage 区分缓存命中 / 未命中 / 输出三档；命中价约为未命中的 1/50。
- 空闲价为高峰一半；高峰 = 北京时间周一至周五 09:00-12:00、14:00-18:00
  （官方另排除中国法定节假日；本仓不做节假日历，仅按工作日窗口近似）。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import configs.settings as settings
from configs.log_config import logger
from utils.convert_contract import Usage
from utils.errors import LlmError
from utils import request_budget
from utils.egress import llm_egress_slot, shared_openai_client
from extensions import registry

_CN_TZ = ZoneInfo("Asia/Shanghai")

# ---------- 单价表（人民币 / 百万 token；换价新增条目，勿改旧生效日记录） ----------
# 核对日 2026-10-10；Flash 空闲：命中 0.02 / 未命中 1 / 输出 4；高峰翻倍。
PRICE_TABLE: Dict[str, Dict[str, Any]] = {
    "deepseek-flash": {
        "effective_from": "2026-09-10",
        "currency": "CNY",
        "per_million": {
            "cache_hit": {"peak": 0.04, "offpeak": 0.02},
            "cache_miss": {"peak": 2.0, "offpeak": 1.0},
            "output": {"peak": 8.0, "offpeak": 4.0},
        },
    },
}
# 旧模型名走 Flash 价（官方兼容路由）
_PRICE_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ---------- 第三方成本控制：默认关闭；开启后用对应阈值卡住单次请求的后续 DeepSeek 调用 ----------
# 本机 OCR / ASR 不读这些开关。
DEEPSEEK_COST_LIMIT_ENABLED = _env_flag("DEEPSEEK_COST_LIMIT_ENABLED", "false")
DEEPSEEK_MAX_COST_CNY = float(os.getenv("DEEPSEEK_MAX_COST_CNY", "1.0"))
DEEPSEEK_TOKEN_LIMIT_ENABLED = _env_flag("DEEPSEEK_TOKEN_LIMIT_ENABLED", "false")
DEEPSEEK_MAX_TOKENS_PER_REQUEST = int(os.getenv("DEEPSEEK_MAX_TOKENS_PER_REQUEST", "200000"))


def _price_row(model: str) -> Optional[Dict[str, Any]]:
    key = _PRICE_ALIASES.get(model, model)
    return PRICE_TABLE.get(key)


def is_peak_hour(now: Optional[datetime] = None) -> bool:
    """
    是否高峰计价时段（北京时间工作日 09-12、14-18）。

    官方还排除中国法定节假日；本仓无节假日历，节假日工作日窗口仍可能按高峰估算。
    """
    moment = now or datetime.now(_CN_TZ)
    if moment.weekday() >= 5:
        return False
    return (9 <= moment.hour < 12) or (14 <= moment.hour < 18)


def compute_cost_cny(
    model: str, cached_tokens: int, miss_tokens: int, output_tokens: int
) -> float:
    """按命中 / 未命中 / 输出三档与峰谷折算人民币；表中无该模型则 0。"""
    table = _price_row(model)
    if not table:
        return 0.0
    band = "peak" if is_peak_hour() else "offpeak"
    per = table["per_million"]
    total = (
        cached_tokens * per["cache_hit"][band]
        + miss_tokens * per["cache_miss"][band]
        + output_tokens * per["output"][band]
    )
    return round(total / 1_000_000, 8)


@dataclass
class UsageLedger:
    """一次请求内的 DeepSeek 用量账本（不含本机 OCR/ASR）。"""

    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_cny: float = 0.0
    output_truncated: bool = False
    limits_hit: List[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, usage: Dict[str, Any]) -> None:
        """登记一次调用的 usage（已从 SDK 对象转成 dict）。"""
        prompt = int(usage.get("prompt_tokens") or 0)
        cached = int(usage.get("prompt_cache_hit_tokens") or 0)
        if not cached:
            details = usage.get("prompt_tokens_details") or {}
            cached = int(details.get("cached_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        completion_details = usage.get("completion_tokens_details") or {}
        reasoning = int(completion_details.get("reasoning_tokens") or 0)

        self.calls += 1
        self.prompt_tokens += prompt
        self.cached_tokens += cached
        self.completion_tokens += completion
        self.reasoning_tokens += reasoning
        self.cost_cny = round(
            self.cost_cny
            + compute_cost_cny(self.model, cached, prompt - cached, completion),
            8,
        )

    def snapshot(self) -> Optional[Usage]:
        """产出契约里的 usage；一次 DeepSeek 都没调过时返回 None。"""
        if not self.calls:
            return None
        table = _price_row(self.model) or {}
        return Usage(
            model=self.model,
            calls=self.calls,
            prompt_tokens=self.prompt_tokens,
            cached_tokens=self.cached_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cost_cny=self.cost_cny,
            price_effective_from=table.get("effective_from"),
        )

    def mark_limit_hits(self) -> None:
        """记账后登记命中的上界名（不抛错，本枪结果仍可交回调用方）。"""
        if DEEPSEEK_COST_LIMIT_ENABLED and self.cost_cny >= DEEPSEEK_MAX_COST_CNY:
            if "deepseek_max_cost_cny" not in self.limits_hit:
                self.limits_hit.append("deepseek_max_cost_cny")
        if DEEPSEEK_TOKEN_LIMIT_ENABLED and self.total_tokens >= DEEPSEEK_MAX_TOKENS_PER_REQUEST:
            if "deepseek_max_tokens_per_request" not in self.limits_hit:
                self.limits_hit.append("deepseek_max_tokens_per_request")

    def assert_within_cost_limits(self) -> None:
        """
        发起下一枪 DeepSeek 之前检查：开启对应开关且已达阈值则抛 LlmError。

        本机 OCR / ASR 不走此闸。默认两开关关闭，不影响日常自用。
        """
        self.mark_limit_hits()
        if DEEPSEEK_COST_LIMIT_ENABLED and self.cost_cny >= DEEPSEEK_MAX_COST_CNY:
            raise LlmError(
                f"第三方 DeepSeek 费用已达上限 {DEEPSEEK_MAX_COST_CNY:g} 元"
                f"（当前约 {self.cost_cny:.6f} 元），已中止后续模型调用",
                "llm_cost_limit",
            )
        if DEEPSEEK_TOKEN_LIMIT_ENABLED and self.total_tokens >= DEEPSEEK_MAX_TOKENS_PER_REQUEST:
            raise LlmError(
                f"第三方 DeepSeek token 已达上限 {DEEPSEEK_MAX_TOKENS_PER_REQUEST}"
                f"（当前 {self.total_tokens}），已中止后续模型调用",
                "llm_token_limit",
            )


class DeepSeekClient:
    """
    模型调用封装：重试、思考模式、JSON、记账与第三方成本闸都在这里。

    OpenAI 传输客户端进程内复用；本对象按请求创建以保住独立账本。
    没配 API key 时构造不报错，真正调用时才给出原因。
    """

    def __init__(self) -> None:
        self.model = settings.DEEPSEEK_MODEL
        self.ledger = UsageLedger(model=self.model)

    def _ensure_client(self):
        """取进程内共用的 OpenAI 客户端；缺 key 时抛出可读原因。"""
        return shared_openai_client()

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

        功能：默认关闭思考模式；json_mode 时要求 JSON；空内容与异常会重试；
              每次把 usage 记进账本；若开启成本/token 上限则在前后检查并可能中止。
        异常：LlmError — 缺 key、成本闸、重试仍失败或空内容。
        """
        self.ledger.assert_within_cost_limits()

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
            timeout = request_budget.call_timeout(settings.LLM_TIMEOUT_SEC, stage="llm")
            started = time.monotonic()
            try:
                with llm_egress_slot():
                    response = client.with_options(timeout=timeout).chat.completions.create(
                        **payload
                    )
            except TimeoutError:
                raise
            except LlmError:
                raise
            except Exception as exc:
                last_error = str(exc)
                logger.error(f"llm 调用失败 attempt={attempt}: {exc}")
                remaining = request_budget.remaining_sec()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("llm 调用耗尽单请求总时间预算") from exc
                _sleep_within_budget(min(2 ** attempt, 8))
                continue

            self._record_usage(response)
            self.ledger.mark_limit_hits()
            text = (response.choices[0].message.content or "").strip()
            elapsed = round(time.monotonic() - started, 2)
            logger.info(
                f"llm 调用完成 attempt={attempt} cost={elapsed}s json={json_mode} "
                f"thinking={thinking} chars={len(text)} "
                f"ledger_cny={self.ledger.cost_cny:.6f} tokens={self.ledger.total_tokens}"
            )
            if text:
                registry.note_api_call(True)
                return text
            last_error = "模型返回空内容"
            _sleep_within_budget(min(2 ** attempt, 8))

        registry.note_api_call(False, last_error)
        raise LlmError(
            f"模型调用失败（已重试 {settings.LLM_MAX_RETRIES} 次）: {last_error}",
            "llm_call_failed",
        )

    def _record_usage(self, response) -> None:
        """把 SDK 响应里的 usage 转成 dict 记进账本，并记下输出是否被 max_tokens 截断。"""
        choices = getattr(response, "choices", None) or []
        if choices and getattr(choices[0], "finish_reason", None) == "length":
            self.ledger.output_truncated = True
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        try:
            data = usage.model_dump()
        except AttributeError:
            data = dict(usage)
        self.ledger.add(data)


def _sleep_within_budget(seconds: float) -> None:
    """重试等待：剩余预算不够就立刻按超时退出，不再空等。"""
    remaining = request_budget.remaining_sec()
    if remaining is not None and remaining <= 0:
        raise TimeoutError("llm 重试前已超出单请求总时间预算")
    wait = seconds if remaining is None else min(seconds, remaining)
    if wait > 0:
        time.sleep(wait)


def parse_json_object(text: str) -> Any:
    """
    从模型输出里解析 JSON 对象或数组。

    功能：容忍代码块围栏与前后的解释性文字；解析失败返回 None。
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
