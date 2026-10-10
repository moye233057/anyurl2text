# -*- coding: utf-8 -*-
"""ASR 音频输入处理：把 base64 / URL 两种入参统一落成临时文件。

使用前注意：
- 临时文件由调用方负责清理，正常返回和异常路径都要调用 cleanup_temp_file；
- 临时文件后缀统一为 .audio，qwen_asr 内部用 librosa 按内容嗅探音频格式，不依赖后缀。
对外函数：save_base64_audio_to_temp_file、download_audio_to_temp_file、cleanup_temp_file。
"""

import base64
import os
import tempfile
from urllib.parse import urlparse

import requests

DOWNLOAD_CONNECT_TIMEOUT = 10
DOWNLOAD_READ_TIMEOUT = 60
DOWNLOAD_CHUNK_SIZE = 65536
MAX_AUDIO_BYTES = 100 * 1024 * 1024
DOWNLOAD_HEADERS = {
    "User-Agent": "qwen3-asr/1.0",
}


def _write_temp_file(file_bytes):
    if not file_bytes:
        raise ValueError("audio data is empty")
    if len(file_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("audio data is too large")

    fd, temp_path = tempfile.mkstemp(prefix="qwen3_asr_", suffix=".audio")
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    return temp_path


def save_base64_audio_to_temp_file(audio_base64):
    """
    把 base64 音频内容写成临时文件。

    功能：兼容纯 base64 与 data URL 两种写法，解码后写入临时文件。
    应用场景：/asr-b64 路由的输入落盘。
    输入：audio_base64 — base64 字符串，允许带 data:audio/xxx;base64, 前缀。
    输出：临时文件路径。
    异常：内容为空、超限或不是合法 base64 时抛 ValueError。
    """
    if not audio_base64 or not isinstance(audio_base64, str):
        raise ValueError("audio_base64 is required")

    base64_text = audio_base64.strip()
    if base64_text.startswith("data:") and "," in base64_text:
        base64_text = base64_text.split(",", 1)[1]
    base64_text = "".join(base64_text.split())

    try:
        file_bytes = base64.b64decode(base64_text, validate=True)
    except Exception as e:
        raise ValueError("audio_base64 is not valid base64 data") from e

    return _write_temp_file(file_bytes)


def download_audio_to_temp_file(url):
    """
    下载远端音频到临时文件。

    功能：只接受 http(s) 链接，边下边限流，拒绝非音频响应体。
    应用场景：/asr-url 路由的输入落盘，避免把无超时的下载交给推理库。
    输入：url — 音频地址。
    输出：临时文件路径。
    异常：链接非法、下载失败、体积超限或内容不是音频时抛 ValueError。
    """
    if not url or not isinstance(url, str):
        raise ValueError("url is required")

    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an http or https audio url")

    temp_path = None
    downloaded_size = 0
    try:
        response = requests.get(
            url,
            stream=True,
            timeout=(DOWNLOAD_CONNECT_TIMEOUT, DOWNLOAD_READ_TIMEOUT),
            headers=DOWNLOAD_HEADERS,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        normalized_content_type = content_type.lower()
        is_audio_content = normalized_content_type.startswith("audio/")
        is_binary_content = normalized_content_type.startswith("application/octet-stream")
        if content_type and not is_audio_content and not is_binary_content:
            raise ValueError(f"url content type is not audio: {content_type}")

        fd, temp_path = tempfile.mkstemp(prefix="qwen3_asr_url_", suffix=".audio")
        with os.fdopen(fd, "wb") as f:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                downloaded_size += len(chunk)
                if downloaded_size > MAX_AUDIO_BYTES:
                    raise ValueError("downloaded audio is too large")
                f.write(chunk)

        if downloaded_size == 0:
            raise ValueError("downloaded audio is empty")
        return temp_path
    except requests.RequestException as e:
        cleanup_temp_file(temp_path)
        raise ValueError(f"download audio failed: {e}") from e
    except Exception:
        cleanup_temp_file(temp_path)
        raise


def cleanup_temp_file(temp_path):
    """删除临时文件，路径为空或删除失败都不影响主流程。"""
    if not temp_path:
        return
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception:
        pass
