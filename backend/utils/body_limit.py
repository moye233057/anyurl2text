# -*- coding: utf-8 -*-
"""
请求体体积早拒：声明预检 + 边收边数。

multipart 边界与各段头会多占一些字节，额度上留出 `MULTIPART_OVERHEAD_BYTES`；
文件本身的硬上限仍是 `MAX_DOWNLOAD_BYTES`（落盘时再按实字节把关）。

- 有 Content-Length 且已超限 → 一字节都不收（与下载层「声明过大」同形）。
- 没声明、或声明偏小 → 包装 ASGI `receive`，累计收到的 body 字节，超限抛 `BodyTooLarge`。
  这样框架来不及把整包先写进系统临时目录。

调用方（`app.reject_oversized_body`）负责把异常收成 413 / `request_body_too_large`。
"""
from __future__ import annotations

from typing import Awaitable, Callable, Dict, Mapping, Optional, Tuple

import configs.settings as settings

# multipart 边界与各段头部的余量；真正的文件上限仍是 MAX_DOWNLOAD_BYTES
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

# 没有请求体的方法，不必包 receive
_METHODS_WITHOUT_BODY = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

Receive = Callable[[], Awaitable[Dict]]


class BodyTooLarge(Exception):
    """累计收到的请求体已超过额度。"""

    def __init__(self, received: int, limit: int) -> None:
        super().__init__(f"请求体过大: 已接收 {received} 字节，超过上限 {limit} 字节")
        self.received = received
        self.limit = limit


def request_body_limit() -> int:
    """一次请求允许的 body 总字节（文件上限 + multipart 余量）。"""
    return settings.MAX_DOWNLOAD_BYTES + MULTIPART_OVERHEAD_BYTES


def declared_over_limit(headers: Mapping[str, str], limit: Optional[int] = None) -> Optional[int]:
    """
    若 Content-Length 已超过额度，返回声明的字节数；否则返回 None。

    功能：有声明且超限时，调用方应直接 413，不要再去读 body。
    应用场景：中间件入口。
    输入：headers — 请求头（大小写不敏感的取法由调用方保证，或传 Starlette 的 headers）；
          limit — 额度，默认 `request_body_limit()`。
    输出：超限时的声明值；未超限或没有合法声明 → None。
    """
    cap = request_body_limit() if limit is None else limit
    raw = headers.get("content-length")
    if raw is None or not str(raw).isdigit():
        return None
    declared = int(raw)
    return declared if declared > cap else None


def method_may_have_body(method: str) -> bool:
    """这个 HTTP 方法是否可能带请求体（需要边收边数）。"""
    return method.upper() not in _METHODS_WITHOUT_BODY


def wrap_receive(receive: Receive, limit: int) -> Tuple[Receive, Callable[[], int]]:
    """
    包装 ASGI receive：累计 http.request 的 body 字节，超限抛 BodyTooLarge。

    功能：每来一块 body 加一次；超过 limit 立刻抛，不再把后续块交给框架。
    应用场景：中间件在放行 call_next 之前换上。
    输入：receive — 原始 receive；limit — 字节上限。
    输出：(新的 receive, 取当前已收字节数的无参函数)。
    """
    received = 0

    async def limited_receive() -> Dict:
        nonlocal received
        message = await receive()
        if message.get("type") == "http.request":
            chunk = message.get("body") or b""
            received += len(chunk)
            if received > limit:
                raise BodyTooLarge(received, limit)
        return message

    return limited_receive, lambda: received
