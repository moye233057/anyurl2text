# -*- coding: utf-8 -*-
"""
探针：只报告本服务的自身状态，不探测上游。

刻意不在这里调用模型或发外部请求——探针被高频访问，一旦它带副作用，
健康检查本身就会变成成本与故障源。
"""
import os

from fastapi import APIRouter

import configs.settings as settings

router = APIRouter(prefix="/probe", tags=["probe"])

@router.get("/health", summary="存活与配置自检")
def health() -> dict:
    """
    报告服务存活与关键配置是否就绪。

    功能：返回模型名、是否配了 API key，以及几个会影响行为的配置值，
          供前端提示「模型未配置」这类问题，而不是等用户点了 understand 才失败。
    应用场景：前端启动自检、本地排查。
    输入：无。
    输出：状态字典。
    """
    return {
        "status": "ok",
        "model": settings.DEEPSEEK_MODEL,
        "model_key_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        "thinking_enabled": settings.THINKING_ENABLED,
        "rendered_page_ocr": {
            "enabled": settings.RENDERED_PAGE_OCR_ENABLED,
            "max_pages": settings.RENDERED_PAGE_MAX,
        },
        "ocr_service": {
            "url": settings.OCR_API_URL,
            "timeout_sec": settings.OCR_TIMEOUT_SEC,
        },
        "asr_service": {
            "url": settings.ASR_API_URL,
            "timeout_sec": settings.ASR_TIMEOUT_SEC,
            "max_audio_seconds": settings.ASR_MAX_AUDIO_SECONDS,
        },
        # 抽音轨是可选依赖（imageio-ffmpeg）：没装时音视频这一类降级，其余格式不受影响
        "ffmpeg_available": bool(settings.FFMPEG_EXE),
        "allow_private_hosts": settings.ALLOW_PRIVATE_HOSTS,
        "max_download_bytes": settings.MAX_DOWNLOAD_BYTES,
        "max_content_chars": settings.MAX_CONTENT_CHARS,
    }
