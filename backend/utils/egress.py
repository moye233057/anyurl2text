# -*- coding: utf-8 -*-
"""
出站客户端复用与（很宽的）LLM 出口闸。

本仓常态是本机自用、请求量不大：真正会打到第三方的主要是 DeepSeek。
OCR / ASR 是本机辅服务。因此：

- OpenAI SDK 客户端、httpx 客户端在**进程内复用**（少建连）；
- 用量记账仍在每次 `DeepSeekClient` 实例上，不进程共用；
- LLM 出口信号量默认很宽，只给「请求量级极大」时当安全网；日常几乎碰不到。
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

import httpx

import configs.settings as settings
from utils.errors import LlmError

_lock = threading.Lock()
_openai_client = None
_httpx_client: Optional[httpx.Client] = None
_llm_slots: Optional[threading.Semaphore] = None


def _llm_semaphore() -> threading.Semaphore:
    global _llm_slots
    if _llm_slots is None:
        with _lock:
            if _llm_slots is None:
                _llm_slots = threading.Semaphore(settings.EGRESS_LLM_CONCURRENCY)
    return _llm_slots


def shared_openai_client():
    """
    进程内唯一的 OpenAI 客户端（连 DeepSeek 兼容端点）。

    缺 key 时抛与 DeepSeekClient 相同语义的错误，由调用方转成 LlmError。
    """
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    with _lock:
        if _openai_client is not None:
            return _openai_client
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise LlmError(
                "未配置 DEEPSEEK_API_KEY，请在项目根目录的 .env 里填写（可参考 .env.example）",
                "llm_api_key_missing",
            )
        from openai import OpenAI

        _openai_client = OpenAI(
            api_key=api_key,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=settings.LLM_TIMEOUT_SEC,
        )
        return _openai_client


def shared_httpx_client() -> httpx.Client:
    """进程内共用的 httpx 客户端（本机 OCR / ASR 等）。"""
    global _httpx_client
    if _httpx_client is not None:
        return _httpx_client
    with _lock:
        if _httpx_client is not None:
            return _httpx_client
        # 不在这里定死 timeout：每次 post 仍按预算传 timeout。
        # trust_env=False 是**本机服务必须的**：这个客户端只连 127.0.0.1 上的本机独立服务，
        # 而 httpx 默认会读 HTTP_PROXY / ALL_PROXY 之类的环境变量、把请求发给代理——
        # 代理连不上本机独立服务时回的是 502 Bad Gateway，把「本机独立服务没起」伪装成「上游网关错误」，
        # 排查时会被带偏（2026-10-11 实测：trust_env=True 报 ReadTimeout / 502，
        # trust_env=False 如实报连接被拒）。
        _httpx_client = httpx.Client(trust_env=False)
        return _httpx_client


@contextmanager
def llm_egress_slot() -> Iterator[None]:
    """
    占一个 LLM 出口并发上限；满则阻塞等待（不 429——入口池已经会拒）。

    默认并发上限很宽，本机自用几乎不会在这里排队。
    """
    sem = _llm_semaphore()
    sem.acquire()
    try:
        yield
    finally:
        sem.release()
