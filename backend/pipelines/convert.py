# -*- coding: utf-8 -*-
"""
统一转换流程：下载 → 识别 → 准备 → 确定性抽取 →（按能力开关）扩展增强 / 摘要 → 组装。

能力开关见 ADR-0014 / ADR-0015 / docs/API.md §1.1：
- 看图（判型、语义描述、扫描页、内嵌图）只要 `llm=true`；
- `ocr` 选的是**文字转写交给谁**（提供商单选，见 configs/extensions.py），不再门禁看图；
- ASR 要 `asr`；摘要要 `llm`。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import BinaryIO, Optional, Union

import configs.extensions as ext
from configs.extensions import CAPABILITY_OCR
from configs.formats import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS
from configs.log_config import logger
from extensions import registry
from extensions.deepseek import DeepSeekClient
from extensions.reading import enhance_assets
from pipelines.processors import image, media
from pipelines.steps import (
    step1_download,
    step3_prepare,
    step4_extract,
    step5_enhance,
    step6_assemble,
)
from pipelines.steps.step6_assemble import PipelineResult
from utils import request_budget
from utils.delivery_content import merge_understand_delivery
from utils.errors import LlmError
from utils.processor_schema import EnhanceOutcome, ErrorInfo, ExtractResult, PreparedSource

# SVG 是矢量图，没法当位图送模型；也没有可摘要文本
MODEL_SKIPPED_EXTENSIONS = frozenset({"svg"})


@dataclass(frozen=True)
class Caps:
    """一次请求的能力开关与提供商选择。"""

    ocr: Union[bool, str] = False
    asr: bool = False
    llm: bool = False

    @property
    def ocr_provider(self) -> Optional[str]:
        """文字转写用哪个提供商；None 表示这次不做文字转写（`ocr=false`）。"""
        return registry.resolve(CAPABILITY_OCR, self.ocr)

    @property
    def want_vision(self) -> bool:
        """看图支路（判型 / 语义描述 / 扫描页 / 内嵌图）。只看 llm——`ocr` 选的是文字转写给谁。"""
        return self.llm

    @property
    def want_text_image(self) -> bool:
        """
        独立文字图这条路要不要跑。

        - 开了 llm：判型与「模型转写」都要它，跑；
        - 没开 llm 但选的是不吃模型的提供商（本机 Paddle）：不判型、直接把图交给它读字，仍然跑；
        - 都没有：不跑。
        """
        if self.llm:
            return True
        provider = ext.find(CAPABILITY_OCR, self.ocr_provider) if self.ocr_provider else None
        return bool(provider and not provider.needs_model)

    @property
    def want_asr(self) -> bool:
        return self.asr

    @property
    def want_summary(self) -> bool:
        return self.llm


class ConvertPipeline:
    """统一转换：确定性抽取 + 按开关叠加扩展。"""

    def run_from_url(
        self,
        url: str,
        *,
        query: Optional[str] = None,
        ocr: Union[bool, str] = False,
        asr: bool = False,
        llm: bool = False,
    ) -> PipelineResult:
        caps = Caps(ocr=ocr, asr=asr, llm=llm)
        started = time.monotonic()
        try:
            prepared, download_ms = step3_prepare.prepare_from_url(url)
        except step1_download.FetchError as exc:
            return step6_assemble.error_response(
                url, str(exc), int((time.monotonic() - started) * 1000), exc.status_code, exc.code, exc.param
            )
        except TimeoutError as exc:
            return step6_assemble.error_response(
                url, str(exc), int((time.monotonic() - started) * 1000), 504, "request_budget_exhausted"
            )
        except Exception as exc:
            return step6_assemble.internal_error_response(url, exc, started, "convert")
        return self._after_prepare(prepared, download_ms, started, query, caps)

    def run_from_upload(
        self,
        stream: BinaryIO,
        file_name: str,
        *,
        query: Optional[str] = None,
        ocr: Union[bool, str] = False,
        asr: bool = False,
        llm: bool = False,
    ) -> PipelineResult:
        caps = Caps(ocr=ocr, asr=asr, llm=llm)
        started = time.monotonic()
        try:
            prepared, upload_ms = step3_prepare.prepare_from_upload(stream, file_name)
        except step1_download.FetchError as exc:
            return step6_assemble.error_response(
                file_name, str(exc), int((time.monotonic() - started) * 1000), exc.status_code, exc.code, exc.param
            )
        except TimeoutError as exc:
            return step6_assemble.error_response(
                file_name, str(exc), int((time.monotonic() - started) * 1000), 504, "request_budget_exhausted"
            )
        except Exception as exc:
            return step6_assemble.internal_error_response(file_name, exc, started, "convert")
        return self._after_prepare(prepared, upload_ms, started, query, caps)

    def _after_prepare(
        self,
        prepared: PreparedSource,
        download_ms: int,
        started: float,
        query: Optional[str],
        caps: Caps,
    ) -> PipelineResult:
        try:
            request_budget.assert_request_budget("convert.prepare")
            prepare_assets = caps.want_vision or caps.want_asr
            result, parse_ms = step4_extract.run_extraction(prepared, prepare_assets=prepare_assets)
            request_budget.assert_request_budget("convert.extract")

            _note_vision_switches(result, caps)
            if query and not caps.want_summary:
                result.notes.append("已忽略 query：未开启 llm")

            client: Optional[DeepSeekClient] = None
            model_ms = 0
            ocr_text: Optional[str] = None
            asr_text: Optional[str] = None
            summary: Optional[str] = None
            description: Optional[str] = None

            need_enhance = _needs_enhance(prepared, result, caps)
            # 摘要可能依赖增强后的 ocr/asr/描述，故在增强前按「源是否可能产出可摘要材料」判断
            need_summary = caps.want_summary and _can_summarize(prepared, result)

            if need_enhance or need_summary:
                if caps.want_vision or need_summary:
                    client = DeepSeekClient()
                model_started = time.monotonic()
                model_failed = False
                model_hits: list = []
                try:
                    request_budget.assert_request_budget("convert.model")
                    if need_enhance:
                        outcome = _run_enhance(prepared, result, client, caps)
                        result.notes.extend(outcome.notes)
                        result.limits_hit.extend(outcome.limits_hit)
                        ocr_text = outcome.ocr_text
                        asr_text = outcome.asr_text
                        description = outcome.description
                    if need_summary:
                        if client is None:
                            client = DeepSeekClient()
                        summary, model_hits = step5_enhance.summarize(
                            client,
                            prepared,
                            result.content,
                            ocr_text,
                            asr_text,
                            description,
                            query,
                        )
                except LlmError as exc:
                    model_failed = True
                    result.notes.append(f"扩展阶段失败: {exc}")
                    result.model_error = ErrorInfo(code=exc.code, message=str(exc), param=exc.param)
                    logger.error(f"convert 扩展阶段失败: {exc}")
                except TimeoutError as exc:
                    model_failed = True
                    result.notes.append(str(exc))
                    result.model_error = ErrorInfo(
                        code="request_budget_exhausted", message=str(exc), param=None
                    )
                model_ms = int((time.monotonic() - model_started) * 1000)
                if need_summary and summary is None and not model_failed:
                    result.notes.append(
                        "未产出摘要：没有可加工的文本，也没有读出可用的图像信息"
                    )
                result.limits_hit.extend(model_hits)
                if client is not None:
                    ledger_hits = getattr(client.ledger, "limits_hit", None) or []
                    result.limits_hit.extend(ledger_hits)
            elif caps.ocr or caps.asr or caps.llm:
                result.notes.append("已开扩展开关，但当前源没有可跑的扩展工作")

            delivery = merge_understand_delivery(
                result.content,
                ocr_text=ocr_text,
                asr_text=asr_text,
                description=description,
            )
            usage = client.ledger.snapshot() if client is not None else None
            return step6_assemble.PipelineResult(
                step6_assemble.build_response(
                    prepared,
                    result,
                    download_ms=download_ms,
                    parse_ms=parse_ms,
                    model_ms=model_ms,
                    total_ms=int((time.monotonic() - started) * 1000),
                    summary=summary,
                    ocr_text=ocr_text,
                    asr_text=asr_text,
                    usage=usage,
                    delivery_content=delivery,
                ),
                200,
            )
        except TimeoutError as exc:
            return step6_assemble.error_response(
                prepared.source,
                str(exc),
                int((time.monotonic() - started) * 1000),
                504,
                "request_budget_exhausted",
            )
        except Exception as exc:
            return step6_assemble.internal_error_response(prepared.source, exc, started, "convert")
        finally:
            step3_prepare.cleanup(prepared)


def _note_vision_switches(result: ExtractResult, caps: Caps) -> None:
    """把「看图支路为什么没全跑」讲清楚——它现在由 llm 与 ocr 选择分别决定。"""
    if not caps.llm:
        if caps.want_text_image:
            result.notes.append("未开启 llm：不判型、不给语义描述；独立图片只交给所选本机 OCR 读字")
        else:
            result.notes.append("未开启 llm：跳过判型 / 语义描述 / 扫描页 / 内嵌图")
    elif caps.ocr_provider is None:
        result.notes.append("没有选 OCR 提供商：判型与语义描述照跑，独立文字图不做转写")


def _needs_enhance(prepared: PreparedSource, result: ExtractResult, caps: Caps) -> bool:
    if prepared.ext in MODEL_SKIPPED_EXTENSIONS:
        return False
    if caps.want_asr and prepared.ext in MEDIA_EXTENSIONS:
        return True
    # 源本身是图片：跑不跑由「看懂这张图要什么」决定——模型或本机 OCR 任一即可
    if prepared.ext in IMAGE_EXTENSIONS and not result.assets:
        return caps.want_text_image
    # 靠素材（扫描页 / 内嵌图）增强的格式：那一路只有模型能做
    if caps.want_vision and bool(result.assets):
        return True
    return False


def _can_summarize(prepared: PreparedSource, result: ExtractResult) -> bool:
    if prepared.ext in MODEL_SKIPPED_EXTENSIONS:
        return False
    return bool(result.content.strip()) or prepared.ext in IMAGE_EXTENSIONS or prepared.ext in MEDIA_EXTENSIONS


def _run_enhance(
    prepared: PreparedSource,
    result: ExtractResult,
    client: Optional[DeepSeekClient],
    caps: Caps,
) -> EnhanceOutcome:
    """按开关、提供商与格式显式分派扩展。"""
    notes: list = []
    if prepared.ext in IMAGE_EXTENSIONS and not result.assets:
        if not caps.want_text_image:
            return EnhanceOutcome(
                notes=["未开启看图：跳过图片增强（需要 llm，或选一个本机 OCR 提供商）"]
            )
        return image.enhance_image(client, prepared, caps.ocr_provider)
    if prepared.ext in MEDIA_EXTENSIONS:
        if not caps.want_asr:
            return EnhanceOutcome(notes=["未开启 asr，跳过音视频转写"])
        return media.enhance_media(result)
    if caps.want_vision:
        if client is None:
            return EnhanceOutcome(notes=["看图需要 LLM 客户端，内部状态异常"])
        return enhance_assets(client, result)
    return EnhanceOutcome(notes=notes)
