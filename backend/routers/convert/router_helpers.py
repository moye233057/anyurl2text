# -*- coding: utf-8 -*-
"""convert 路由共用的并发与全链路预算。"""
from __future__ import annotations

from typing import Callable, TypeVar

from pipelines.steps import step6_assemble
from pipelines.steps.step6_assemble import PipelineResult
from utils.concurrency import ConcurrencyLimitError, acquire_slot
from utils import request_budget

T = TypeVar("T")


def run_with_limits(pool: str, source: str, runner: Callable[[], PipelineResult]) -> PipelineResult:
    """占槽 → 开始预算 → 执行处理流程；池满返回 429。"""
    try:
        with acquire_slot(pool):
            request_budget.begin_request_budget()
            return runner()
    except ConcurrencyLimitError:
        return step6_assemble.error_response(
            source,
            "并发已满，请稍后重试",
            0,
            429,
            "request_concurrency_limit",
        )
