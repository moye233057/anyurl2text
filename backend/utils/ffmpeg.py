# -*- coding: utf-8 -*-
"""
ffmpeg 出口：把任意音频/视频抽成可以送语音识别的音轨。

用 `imageio-ffmpeg` 自带的静态 ffmpeg 二进制，不要求系统装 ffmpeg——这是 ADR 0003「纯 Python 库能否
做到」那条线在音视频上的落法：pip 装得上就完整实现，装不上就降级。

本模块只做三件事：报出 ffmpeg 在不在、探源时长、抽一条 16kHz 单声道 wav（可按秒数封顶）。
什么时候抽、抽出来干什么由处理器决定（`pipelines/processors/media.py`）。
异常用本模块自己的 `FfmpegError`：它既不是模型错误（`LlmError`），也不该让整条请求失败。
"""
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import configs.errors as errors
import configs.settings as settings
from configs.log_config import logger
from utils import request_budget

# 二进制路径在 configs 里解析（启动时一次），这里只做转发与判定
FFMPEG_EXE = settings.FFMPEG_EXE
FFMPEG_AVAILABLE = bool(FFMPEG_EXE)

# 输出统一成 16kHz 单声道 wav：语音识别的输入规格，也让请求体只有原视频的几十分之一
TRACK_SAMPLE_RATE = 16000
TRACK_SUFFIX = ".wav"

# ffmpeg -i 把元信息打在 stderr；Duration 形如 00:10:03.50
_DURATION_RE = re.compile(
    r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)",
    re.IGNORECASE,
)


class FfmpegError(Exception):
    """
    抽音轨失败。消息面向使用者，会原样出现在 meta.notes 或 degrade_reason 里。

    code 是**稳定机器名**（见 `configs/errors.py` 的 `DEGRADE_CODES`），进 `meta.degrade_code`；
    它的定位是「能力边界」而不是故障，所以整条请求仍是 200。构造时校验白名单。
    """

    def __init__(self, message: str, code: str = "ffmpeg_track_failed",
                 param: Optional[str] = None) -> None:
        if code not in errors.DEGRADE_CODES:
            raise ValueError(f"未登记的降级码: {code}（见 configs/errors.py）")
        super().__init__(message)
        self.code = code
        self.param = param


def probe_duration_seconds(source: Path) -> Optional[float]:
    """
    用 ffmpeg 读容器声明的时长，不落盘、不转码。

    功能：跑 `ffmpeg -i`，从 stderr 解析 Duration；读不出或不可用则返回 None。
    应用场景：`media.extract_media` 在抽音轨前判断是否已超 ASR 时长上限，避免先写满临时 wav。
    输入：source — 已落盘的源文件。
    输出：秒数；探不出时为 None（调用方再走带 `-t` 的抽取兜底）。
    """
    if not FFMPEG_AVAILABLE:
        return None
    try:
        probe_timeout = request_budget.call_timeout(
            min(30.0, settings.FFMPEG_TIMEOUT_SEC), stage="ffmpeg_probe"
        )
        completed = subprocess.run(
            [FFMPEG_EXE, "-hide_banner", "-i", str(source)],
            capture_output=True,
            timeout=probe_timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (TimeoutError, subprocess.TimeoutExpired, OSError) as exc:
        logger.info(f"ffmpeg 探时长未完成 {source.name}: {exc}")
        return None

    text = completed.stderr.decode("utf-8", "ignore")
    match = _DURATION_RE.search(text)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def extract_audio_track(
    source: Path,
    work_dir: Path,
    max_seconds: Optional[float] = None,
) -> Path:
    """
    从音频或视频里抽出一条 16kHz 单声道 wav。

    功能：调 ffmpeg 抽音轨；没有音轨、容器损坏或超时都转成 FfmpegError。
          `max_seconds` 有值时加上 `-t`，转码在上限处停下，避免长源先写满临时目录。
    应用场景：`pipelines/processors/media.py` 处理视频、以及 libsndfile 读不了的音频时调用。
    输入：source — 已落盘的源文件；work_dir — 本次请求的临时目录（抽出的音轨随请求清理）；
          max_seconds — 可选，输出音轨最长秒数（通常传 ASR_MAX_AUDIO_SECONDS）。
    输出：音轨文件路径，位于 work_dir 下。
    异常：FfmpegError — ffmpeg 不可用、源里没有音轨、转码失败或超时。
    """
    if not FFMPEG_AVAILABLE:
        raise FfmpegError("未安装 imageio-ffmpeg，视频与容器音频的音轨抽不出来", "ffmpeg_dependency_missing")

    target = work_dir / f"{source.stem}_track{TRACK_SUFFIX}"
    command = [
        FFMPEG_EXE,
        "-y",
        "-i", str(source),
        "-vn",  # 丢掉视频流，只留声音
        "-ac", "1",
        "-ar", str(TRACK_SAMPLE_RATE),
        "-f", "wav",
    ]
    if max_seconds is not None and max_seconds > 0:
        command.extend(["-t", str(max_seconds)])
    command.append(str(target))
    try:
        ffmpeg_timeout = request_budget.call_timeout(
            settings.FFMPEG_TIMEOUT_SEC, stage="ffmpeg"
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=ffmpeg_timeout,
            # 静默启动窗：Windows 上不隐藏会把控制台窗口弹出来
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except TimeoutError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(f"抽音轨超过 {settings.FFMPEG_TIMEOUT_SEC} 秒未完成", "ffmpeg_timeout") from exc
    except OSError as exc:
        raise FfmpegError(f"ffmpeg 无法启动: {exc}", "ffmpeg_start_failed") from exc

    if completed.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        # ffmpeg 的失败原因只写在 stderr 末尾，摘最后一行足够定位（缺音轨 / 容器损坏）
        detail = completed.stderr.decode("utf-8", "ignore").strip().splitlines()
        reason = detail[-1] if detail else f"退出码 {completed.returncode}"
        raise FfmpegError(f"抽音轨失败: {reason}", "ffmpeg_track_failed")

    logger.info(f"ffmpeg 抽音轨完成 {source.name} → {target.name} {target.stat().st_size} 字节")
    return target
