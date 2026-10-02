# -*- coding: utf-8 -*-
"""模型层的统一异常。消息面向使用者，会原样出现在 meta.notes 的失败说明里。"""

class LlmError(Exception):
    """模型调用失败。消息面向使用者。"""
