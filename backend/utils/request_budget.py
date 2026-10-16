# -*- coding: utf-8 -*-
"""单请求全链路与下载分段的单调时钟截止点。"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Optional

import configs.settings as settings

_request_deadline: ContextVar[Optional[float]] = ContextVar("request_deadline", default=None)


def begin_request_budget() -> float:
    """在路由入口调用，返回截止点（monotonic）。"""
    deadline = time.monotonic() + settings.REQUEST_BUDGET_SEC
    _request_deadline.set(deadline)
    return deadline


def request_deadline() -> Optional[float]:
    return _request_deadline.get()


def remaining_sec() -> Optional[float]:
    deadline = request_deadline()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def assert_request_budget(stage: str) -> None:
    """剩余时间耗尽时抛 TimeoutError，由处理流程转成 FetchError / 504。"""
    deadline = request_deadline()
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError(f"{stage} 超出单请求总时间预算")


def download_deadline() -> float:
    """下载层截止：min(全链路剩余, DOWNLOAD_BUDGET_SEC)。"""
    now = time.monotonic()
    cap = now + settings.DOWNLOAD_BUDGET_SEC
    overall = request_deadline()
    if overall is None:
        return cap
    return min(cap, overall)


def call_timeout(cap_sec: float, *, stage: str = "call") -> float:
    """
    单次外部调用超时：min(配置上限, 全链路剩余)。

    无请求预算上下文时退回 cap（进程内单测、无路由入口时）。
    剩余 ≤ 0 抛 TimeoutError，由处理流程转成 504 / model_error。
    """
    remaining = remaining_sec()
    if remaining is None:
        return max(0.1, float(cap_sec))
    if remaining <= 0:
        raise TimeoutError(f"{stage} 超出单请求总时间预算")
    return max(0.1, min(float(cap_sec), remaining))
