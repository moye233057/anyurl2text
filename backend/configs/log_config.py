# -*- coding: utf-8 -*-
"""
日志配置。

职责：给整个服务提供唯一 logger——单行 JSON 打到 stdout，并落盘轮转；
每条带 request_id（无请求上下文时为 "-"），便于把同一次请求的多行串起来。
线上问题复盘依赖它——中间产物默认会被清理，日志是唯一的现场。
"""
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

_CN_TZ = ZoneInfo("Asia/Shanghai")
_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

# 落盘目录与轮转（写死：不是业务上界，不进 limits 表）
_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
_LOG_FILE = _LOG_DIR / "anyurl2text.log"
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUP_COUNT = 5


def get_request_id() -> Optional[str]:
    """当前请求的 request id；中间件外为 None。"""
    return _request_id.get()


def set_request_id(request_id: str):
    """进入请求时调用，返回 token 供 reset。"""
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    """请求结束时复位。"""
    _request_id.reset(token)


def redact_url_for_log(url: str) -> str:
    """
    日志里的 URL 只留 scheme + host + path，去掉 query 与 fragment。

    功能：避免把签名、token、问题原文等查询串写进日志。
    应用场景：转换路由与下载重定向等打 URL 的地方。
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class JsonLineFormatter(logging.Formatter):
    """把日志格式化成单行 JSON；异常堆栈放进 exc 字段，不打断 JSON 结构。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(_CN_TZ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "request_id": getattr(record, "request_id", None) or "-",
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class RequestIdFilter(logging.Filter):
    """把当前请求的 request_id 写进 LogRecord，供 Formatter 输出。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


def setup_logger(name: str = "anyurl2text") -> logging.Logger:
    """创建带 JSON 行格式的控制台 + 落盘轮转 logger，重复调用幂等。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    formatter = JsonLineFormatter()
    request_filter = RequestIdFilter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(request_filter)
    logger.addHandler(stream)

    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        rotating.addFilter(request_filter)
        logger.addHandler(rotating)
    except OSError:
        # 落盘失败不挡启动：仍有 stdout
        pass

    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


logger = setup_logger()
