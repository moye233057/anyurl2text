# -*- coding: utf-8 -*-
"""
探针：报告本服务的自身状态，以及各扩展提供商现在能不能用。

**不调用模型、不发会产生费用的请求**：探针被高频访问，一旦它带副作用，健康检查本身就会
变成成本与故障源。侧车（本机 OCR / ASR）用短超时的 `GET /health` 探活——它们在本机、
不花钱；第三方 API 只查密钥是否配置，真实可用性见 `extensions/registry.note_api_call()`。
"""
import os

from fastapi import APIRouter, HTTPException

import configs.settings as settings
from pipelines import capabilities as capability_registry

router = APIRouter(prefix="/probe", tags=["probe"])

@router.get("/health", summary="存活与配置自检")
def health() -> dict:
    """
    报告服务存活、关键配置是否就绪，以及各扩展提供商现在能不能用。

    功能：返回模型名、是否配了 API key、几个影响行为的配置值，外加一份能力清单
          （能力 → 提供商 → available / reason / detail），供前端把不可用项置灰并说明原因，
          而不是等用户点了运行才失败。
    应用场景：前端启动自检、用户手动刷新可用性、本地排查。
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
        # 默认 true：http 与 https 都收；false 时只收 https（http:// 在连接之前被 422 拒掉）
        "allow_http_source": settings.ALLOW_HTTP_SOURCE,
        "max_download_bytes": settings.MAX_DOWNLOAD_BYTES,
        "max_content_chars": settings.MAX_CONTENT_CHARS,
        # 能力清单：每项带 available / reason / detail，前端据此置灰不可用项
        "capabilities": capability_registry.snapshot(),
    }


@router.post("/test/{capability}/{provider}", summary="真实测试一个扩展提供商")
def test_provider(capability: str, provider: str) -> dict:
    """
    主动测一次某个提供商现在到底能不能用。

    功能：侧车走 `GET <origin>/health`（本机、不花钱）；API 真发一次最小请求
          （会花掉极小的 token）。前端「测试」按钮调它，通过后该项即可选。
    应用场景：侧车是后起的、或想确认一个 API key 现在还有效时。
    输入：路径参数 capability 与 provider（名单见 `configs/extensions.py`）。
    输出：{available, reason, detail}。
    异常：HTTPException 422 — 名字不在名单里。
    """
    result = capability_registry.test_provider(capability, provider)
    if result is None:
        raise HTTPException(status_code=422, detail=f"未登记的扩展提供商：{capability}/{provider}")
    return {"available": result.available, "reason": result.reason, "detail": result.detail}
