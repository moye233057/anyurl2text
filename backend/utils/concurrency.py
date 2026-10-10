# -*- coding: utf-8 -*-
"""按源类型分池的同步请求并发（裁决 #1：文档类快池，图片/音视频慢池）。"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

import configs.settings as settings
from configs.formats import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS

_fast = threading.Semaphore(settings.CONCURRENCY_FAST)
_media = threading.Semaphore(settings.CONCURRENCY_MEDIA)


def pool_for_extension(ext: Optional[str]) -> str:
    normalized = (ext or "").lower().lstrip(".")
    if normalized in IMAGE_EXTENSIONS or normalized in MEDIA_EXTENSIONS:
        return "media"
    return "fast"


def pool_for_upload_name(file_name: str) -> str:
    ext = ""
    if "." in file_name:
        ext = file_name.rsplit(".", 1)[-1]
    return pool_for_extension(ext)


def pool_for_url(url: str) -> str:
    from urllib.parse import urlsplit

    path = urlsplit((url or "").strip()).path
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return pool_for_extension(ext)


@contextmanager
def acquire_slot(pool: str) -> Iterator[None]:
    sem = _media if pool == "media" else _fast
    if not sem.acquire(blocking=False):
        raise ConcurrencyLimitError()
    try:
        yield
    finally:
        sem.release()


class ConcurrencyLimitError(Exception):
    """并发池已满，路由层映射为 429。"""
