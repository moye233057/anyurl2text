# -*- coding: utf-8 -*-
"""
本机 Qwen3-ASR 出口：把音轨交给语音识别服务，拿回文字。

一种模型一个文件（ADR 0007）。这里只做出口该做的事——发音频、收文字、把失败转成 `LlmError`；
什么时候调用它由处理器决定（`pipelines/processors/media.py` 的 `enhance_media`）。
语音识别不计 token、也不产生 DeepSeek 账单，所以这里不记账：`usage` 只反映 DeepSeek 调用。

服务契约（对齐 Individual/qwen3-asr 的 `POST /asr-b64`）：
请求 `{"audio_base64": "..."}`，响应 `{"code": "200", "data": "文本", "language": "Chinese", "cost": 1.2}`，
其中 `cost` 是服务端耗时秒数，`language` 是模型判定的语种；`code` 不是 "200" 一律当失败。

音频一律由本服务自己抽出来再传 base64，不用服务的 `/asr-url`：本项目不把 URL 交给上游代下载
（鉴权、防盗链、内网、慢跳转都会让上游拿不到文件，EXTENDING 第 5 节）。
"""
import base64
from pathlib import Path
from typing import NamedTuple

import httpx

import configs.settings as settings
from configs.log_config import logger
from utils.errors import LlmError

class Transcription(NamedTuple):
    """一次转写的产出：识别文本 + 模型判定的语种（语种只用于说明，不参与后续加工）。"""

    text: str
    language: str

def transcribe(path: Path) -> Transcription:
    """
    转写一个本地音轨文件。

    功能：读音轨字节 → base64 → 发 `/asr-b64` → 收文本与语种。
    应用场景：`pipelines/processors/media.py` 的 `enhance_media` 在拿到音轨后调用。
    输入：path — 16kHz 单声道 wav，或 libsndfile 能直接读的音频文件。
    输出：Transcription；音轨里没有说话声时 text 为空串。
    异常：LlmError — 音轨为空、服务不可达、响应不是预期的 json 对象、服务返回失败。
    """
    try:
        audio_bytes = path.read_bytes()
    except OSError as exc:
        raise LlmError(f"Qwen3-ASR 读取音轨字节失败: {exc}") from exc
    if not audio_bytes:
        raise LlmError("Qwen3-ASR 输入音轨为空")

    payload = {"audio_base64": base64.b64encode(audio_bytes).decode("ascii")}
    try:
        response = httpx.post(
            settings.ASR_API_URL, json=payload, timeout=settings.ASR_TIMEOUT_SEC
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPError as exc:
        raise LlmError(f"Qwen3-ASR 服务不可达（{settings.ASR_API_URL}）: {exc}") from exc
    except ValueError as exc:
        raise LlmError(f"Qwen3-ASR 响应不是 json: {exc}") from exc

    if not isinstance(result, dict):
        raise LlmError(f"Qwen3-ASR 响应不是对象: {result!r}")
    if str(result.get("code")) != "200":
        raise LlmError(f"Qwen3-ASR 返回失败: code={result.get('code')} msg={result.get('msg')}")

    text = str(result.get("data") or "")
    language = str(result.get("language") or "")
    logger.info(
        f"qwen3-asr 完成 输入={len(audio_bytes)} 字节 chars={len(text)} "
        f"language={language or '(未判定)'} server_cost={result.get('cost')}s"
    )
    return Transcription(text=text, language=language)
