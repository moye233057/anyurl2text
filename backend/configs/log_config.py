# -*- coding: utf-8 -*-
"""
日志配置。

职责：给整个服务提供唯一 logger，输出单行 JSON，便于本地排查与后续采集。
线上问题复盘依赖它——中间产物默认会被清理，日志是唯一的现场。
"""
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

_CN_TZ = ZoneInfo("Asia/Shanghai")

class JsonLineFormatter(logging.Formatter):
    """把日志格式化成单行 JSON；异常堆栈放进 exc 字段，不打断 JSON 结构。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(_CN_TZ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

def setup_logger(name: str = "anyurl2text") -> logging.Logger:
    """创建带 JSON 行格式的控制台 logger，重复调用幂等。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger

logger = setup_logger()
