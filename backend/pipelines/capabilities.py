# -*- coding: utf-8 -*-
"""
能力清单的管线侧出口。

`routers/` 按分层规则（ADR-0010 的依赖单向）不能 import `extensions/`，而能力清单与探活都在
`extensions/registry.py`。这里只做一次转发，给 HTTP 层一个合法的进口——**本文件不放逻辑**，
要加东西请加到注册表里。
"""
from extensions.registry import snapshot, test_provider

__all__ = ["snapshot", "test_provider"]
