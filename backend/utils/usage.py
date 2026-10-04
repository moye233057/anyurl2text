# -*- coding: utf-8 -*-
"""
用量计价与记账：跨模型共用一本账。

单价表在 `configs/settings.py` 的 `PRICE_TABLE`（按生效日留历史）；这里只做三件事：
判断峰谷时段、按三档 token 折算金额、把一次请求内的多次调用累计成契约里的 `Usage`。
换模型时改的是单价表与 `models/` 下那个模型文件，这一份不动。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import configs.settings as settings
from routers.convert.schema import Usage

_CN_TZ = ZoneInfo("Asia/Shanghai")

def is_peak_hour(now: Optional[datetime] = None) -> bool:
    """
    判断是否处于高峰计价时段。

    功能：官方高峰 = 北京时间周一至周五 09:00-12:00、14:00-18:00；其余为空闲（半价）。
    应用场景：成本折算；把批量任务排到空闲时段能省一半。
    输入：now — 指定时间，默认取当前。
    输出：True 表示高峰。
    """
    moment = now or datetime.now(_CN_TZ)
    if moment.weekday() >= 5:
        return False
    return (9 <= moment.hour < 12) or (14 <= moment.hour < 18)

def compute_cost_cny(model: str, cached_tokens: int, miss_tokens: int, output_tokens: int) -> float:
    """
    按命中 / 未命中 / 输出三档与峰谷时段折算成本。

    功能：三档单价分开乘——命中价约为未命中的 1/50，用单一输入价算会高估几十倍。
    应用场景：每次模型调用后累计；也是 README 里成本对比表的口径来源。
    输入：model — 模型名；cached_tokens — 缓存命中的输入 token；miss_tokens — 未命中的输入 token；
          output_tokens — 输出 token（含思维链）。
    输出：人民币金额；单价表里没有该模型时返回 0.0。
    """
    table = settings.PRICE_TABLE.get(model)
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
    """一次请求内的用量账本，按模型累计后产出契约里的 Usage。"""

    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_cny: float = 0.0
    # 本次请求里是否有一次调用的输出被 max_tokens 截断（SDK 的 finish_reason == "length"）。
    # 它是「命中 llm_max_tokens」的判据，由 models/deepseek.py 标记、understand 报进 limits_hit。
    output_truncated: bool = False

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
        self.cost_cny = round(self.cost_cny + compute_cost_cny(self.model, cached, prompt - cached, completion), 8)

    def snapshot(self) -> Optional[Usage]:
        """产出契约里的 usage；一次模型都没调过时返回 None。"""
        if not self.calls:
            return None
        table = settings.PRICE_TABLE.get(self.model) or {}
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
