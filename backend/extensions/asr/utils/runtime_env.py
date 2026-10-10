# -*- coding: utf-8 -*-
import os

from configs.base_config import CUDA_VISIBLE_DEVICES


def setup_env():
    """在导入 torch / qwen_asr 之前固定本进程可见的显卡。"""
    if CUDA_VISIBLE_DEVICES:
        os.environ["CUDA_VISIBLE_DEVICES"] = CUDA_VISIBLE_DEVICES
