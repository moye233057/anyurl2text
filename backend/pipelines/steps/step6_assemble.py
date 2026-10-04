# -*- coding: utf-8 -*-
"""
步骤 6：响应组装（两条管线共用的唯一一处）。

响应形状只有这一份定义（ADR 0001）：`content` 按字符上限截断并在 meta 里声明，不静默截断；
`error` 恒存在，成功为 None，调用方不必判两种响应形状。错误语义在抛出的地方最清楚，
所以状态码与响应体一起返回，不让路由去猜。
"""
from typing import Any, NamedTuple, Optional

import configs.limits as limits
import configs.settings as settings
from pipelines.steps.step2_detect import modality_from_extension
from pipelines.processors.schema import ExtractResult, PreparedSource
from routers.convert.schema import ConvertResponse, Meta, Modality, Timings, truncate_content

class PipelineResult(NamedTuple):
    """
    管线产出：响应体 + HTTP 状态码。

    状态码与响应体一起返回（而不是让路由去猜错误类型）：
    错误语义在抛出的地方最清楚，字符串匹配是脆的。
    """

    response: ConvertResponse
    status: int

def build_response(
    prepared: PreparedSource,
    result: ExtractResult,
    *,
    download_ms: int = 0,
    parse_ms: int = 0,
    model_ms: int = 0,
    total_ms: int = 0,
    summary: Optional[str] = None,
    ocr_text: Optional[str] = None,
    asr_text: Optional[str] = None,
    usage: Any = None,
    error: Optional[str] = None,
) -> ConvertResponse:
    """
    把抽取结果组装成响应契约。

    功能：统一填 meta 与 timings；按字符上限截断 content 并在 meta 里声明，不静默截断。
    应用场景：两个管线各自收尾时调用——响应形状只有这一处定义。
    输入：prepared — 源信息；result — 抽取产出；各段耗时；
          summary / ocr_text / asr_text / usage / error。
    输出：ConvertResponse。
    """
    content, truncated = truncate_content(result.content, settings.MAX_CONTENT_CHARS)
    modality = modality_from_extension(prepared.ext)
    if error:
        modality = Modality.ERROR

    # 命中集合 = 处理器报上来的名字 + 本层自己判定的 content 截断（派生量，不两处独立计算）
    limits_hit = sorted(set(result.limits_hit) | ({"max_content_chars"} if truncated else set()))
    notes = list(result.notes)
    # 命中文案只有 configs/limits.py 一处模板：处理器与本节都只报名字
    notes.extend(limits.note_for(name) for name in limits_hit)

    meta = Meta(
        source=prepared.source,
        ext=prepared.ext or None,
        mime=prepared.mime,
        size_bytes=prepared.size_bytes,
        detector=prepared.detector,
        degraded=result.degraded,
        degrade_reason=result.degrade_reason,
        truncated=truncated,
        limits_hit=limits_hit,
        pages=result.pages,
        pages_used=result.pages_used,
        images=result.images,
        images_used=result.images_used,
        entry_count=result.entry_count,
        image=result.image_meta,
        audio=result.audio_meta,
        ocr_text=ocr_text,
        asr_text=asr_text,
        engine=list(result.engines),
        notes=notes,
    )
    return ConvertResponse(
        type=modality,
        content=content,
        summary=summary,
        meta=meta,
        usage=usage,
        timings=Timings(
            download_ms=download_ms,
            parse_ms=parse_ms,
            model_ms=model_ms,
            total_ms=total_ms,
        ),
        error=error,
    )

def error_response(source: str, reason: str, total_ms: int, status: int = 502) -> PipelineResult:
    """
    组装失败响应。

    功能：error 字段恒存在，成功时才是 None，调用方不必判两种响应形状；
          同时带上语义化状态码，供路由直接返回。
    应用场景：两个管线在入口阶段失败时调用。
    输入：source — 原始输入；reason — 面向使用者的原因；total_ms — 已耗时；
          status — HTTP 状态码（入参 422、超限 413、超时 504、上游 502）。
    输出：PipelineResult。
    """
    return PipelineResult(
        ConvertResponse(
            type=Modality.ERROR,
            content="",
            summary=None,
            meta=Meta(source=source),
            usage=None,
            timings=Timings(total_ms=total_ms),
            error=reason,
        ),
        status,
    )
