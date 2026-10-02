# -*- coding: utf-8 -*-
"""
音频与视频处理：纯代码给结构信息（时长 / 采样率 / 声道），understand 再交本机 ASR 转写。

音轨的来源分两种：音频文件本身就是音轨（libsndfile 直接读），视频与 m4a/aac/wma 这些容器要先
抽音轨。抽出来的 wav 既是结构信息的来源，也在 `prepare_assets` 为真时登记进 `result.assets`，
`enhance_media` 直接复用它——所以一次请求只转码一次（ADR 0008）。

与图片的对称：图片的确定性产出是尺寸与 EXIF（ADR 0004），音视频的确定性产出是时长与音轨参数；
认字交本机 Paddle OCR，听声交本机 Qwen3-ASR，两者的产出都进 meta，都不进 content。
"""
from pathlib import Path

import configs.settings as settings
from configs.formats import FFMPEG_ONLY_AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from configs.log_config import logger
from models import qwen3_asr
from pipelines.processors.schema import (
    EmbeddedAsset,
    EnhanceOutcome,
    ExtractContext,
    ExtractResult,
)
from routers.convert.schema import AudioMeta
from utils import ffmpeg
from utils.ffmpeg import FfmpegError

# soundfile 是可选依赖：装不上只是音视频这一类读不出结构信息，不该让整个服务起不来
# （与 rarfile / pillow-heif 同一套处置）。
try:
    import soundfile as sf
except ImportError:  # pragma: no cover - 取决于部署环境
    sf = None

# 音轨素材在 assets 里的 kind：视频与容器音频都要先抽音轨，读素材的通用路径不认这一类
AUDIO_TRACK_KIND = "audio_track"

def _needs_transcode(ext: str) -> bool:
    """这类源要不要先抽音轨：视频与 libsndfile 不认的容器音频都需要。"""
    return ext in VIDEO_EXTENSIONS or ext in FFMPEG_ONLY_AUDIO_EXTENSIONS

def _track_asset(track: Path, ctx: ExtractContext) -> list:
    """
    准备 enhance 要用的音轨素材。

    功能：`prepare_assets` 为真时登记音轨，为假时返回空——它是能力开关，
           不是调用方身份；两种情况下 content 都为空，差别只在要不要留着音轨。
    应用场景：`extract_media` 拿到音轨之后。
    输入：track — 音轨路径；ctx — 处理器上下文。
    输出：素材列表（0 或 1 条）。
    """
    if not ctx.prepare_assets:
        return []
    return [EmbeddedAsset(kind=AUDIO_TRACK_KIND, label="音轨", path=track, order=0)]

def extract_media(path: Path, ext: str, ctx: ExtractContext) -> ExtractResult:
    """
    抽取音频/视频的结构信息。

    功能：取到音轨（音频直接读，视频与容器音频用 ffmpeg 抽）→ soundfile 读时长、采样率、声道。
          视频容器本身没有可确定性抽取的文本，语音是模型产出，所以 content 恒为空。
    应用场景：dispatch 分派音视频那一组时调用。
    输入：path — 文件路径；ext — 已识别的小写扩展名；ctx — 处理器上下文。
    输出：ExtractResult，content 为空，audio_meta 填时长与音轨参数；
          ffmpeg 不可用或音轨抽不出来/读不动时返回降级产出，不抛异常（ADR 0003）。
    """
    engines = ["ffmpeg"] if _needs_transcode(ext) else ["soundfile"]
    if sf is None:
        return ExtractResult(
            content="",
            engines=["基础文件信息"],
            degraded=True,
            degrade_reason="未安装 soundfile，音频与视频的结构信息读不出来",
        )

    track = path
    if _needs_transcode(ext):
        try:
            track = ffmpeg.extract_audio_track(path, ctx.work_dir)
        except FfmpegError as exc:
            return ExtractResult(
                content="", engines=engines, degraded=True, degrade_reason=str(exc)
            )

    try:
        info = sf.info(track)
    except Exception as exc:
        return ExtractResult(
            content="",
            engines=engines,
            degraded=True,
            degrade_reason=f"音轨读不出来: {exc}",
        )

    result = ExtractResult(content="", engines=engines, assets=_track_asset(track, ctx))
    result.audio_meta = AudioMeta(
        duration_seconds=round(float(info.duration), 3),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        format=info.format,
    )
    if _needs_transcode(ext):
        result.notes.append("视频与容器音频要先抽音轨，这里报的是音轨的参数")
    return result

def enhance_media(result: ExtractResult) -> EnhanceOutcome:
    """
    音视频的模型增强：把音轨交给本机 Qwen3-ASR 转写。

    功能：取 `result.assets` 里的音轨送 `/asr-b64`，产出识别文本与语种说明；
          音轨缺失（降级）或超过时长上限时不发请求，只在 notes 里说明——不静默丢内容。
    应用场景：understand 遇到音频/视频时调用（`step4_extract.enhance()` 分派到这里）。
    输入：result — 确定性产出（本函数只读它的素材与时长，不改 content）。
    输出：EnhanceOutcome，asr_text 为转写结果；没有说话声时只写说明。
    异常：LlmError — 服务不可达或返回失败，由调用方转成 notes。
    """
    tracks = [asset for asset in result.assets if asset.kind == AUDIO_TRACK_KIND]
    if not tracks:
        return EnhanceOutcome(notes=["没有可送语音识别的音轨，未做转写"])

    duration = result.audio_meta.duration_seconds if result.audio_meta else None
    if duration and duration > settings.ASR_MAX_AUDIO_SECONDS:
        return EnhanceOutcome(
            notes=[
                f"音轨 {duration:.0f} 秒，超过 ASR_MAX_AUDIO_SECONDS="
                f"{settings.ASR_MAX_AUDIO_SECONDS:.0f} 秒上限，未做转写"
            ]
        )

    transcription = qwen3_asr.transcribe(tracks[0].path)
    language_note = f"，语种判定为 {transcription.language}" if transcription.language else ""
    if not transcription.text:
        return EnhanceOutcome(notes=[f"音轨里没有说话声{language_note}"])
    logger.info(f"媒体转写完成 {len(transcription.text)} 字{language_note}")
    return EnhanceOutcome(
        asr_text=transcription.text,
        notes=[f"已交本机 Qwen3-ASR 转写出 {len(transcription.text)} 字{language_note}"],
    )
