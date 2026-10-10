# -*- coding: utf-8 -*-
"""
模型增强管线：两个入口（URL / 上传）各自按步骤走完「下载 → 识别 → 准备 → 抽取 → 模型增强 + 摘要 → 组装」。

前四步与 `extract.py` 共用步骤文件（ADR 0002/0007）。模型阶段由本文件**显式**决定调用
哪个处理器的 `enhance_*` 或 `models.reading.enhance_assets`（ADR-0011）。
understand 的 `content` 在纯代码抽取段之上合并 OCR/ASR/语义描述；`meta.*` 仍保留副本。

两个入口把整条流程写在方法体里，读哪个入口都能看到完整顺序。
"""
import time
from typing import BinaryIO, Optional

from configs.formats import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS
from configs.log_config import logger
from models.deepseek import DeepSeekClient
from models.reading import enhance_assets
from pipelines.processors import image, media
from utils.processor_schema import EnhanceOutcome, ExtractResult, PreparedSource
from pipelines.steps import (
    step1_download,
    step3_prepare,
    step4_extract,
    step5_enhance,
    step6_assemble,
)
from pipelines.steps.step6_assemble import PipelineResult
from utils.delivery_content import merge_understand_delivery
from utils.processor_schema import ErrorInfo
from utils.errors import LlmError
from utils import request_budget

# SVG 是矢量图，没法当位图送模型；它也没有可摘要的文本，所以整类跳过模型
MODEL_SKIPPED_EXTENSIONS = frozenset({"svg"})

class UnderstandPipeline:
    """
    模型增强管线：在确定性抽取之上叠加认字、描述与摘要。

    只有这里会调用模型；抽取那一段与 ExtractPipeline 完全共用同一份代码。
    """

    def run_from_url(self, url: str, query: Optional[str] = None) -> PipelineResult:
        """
        URL 入口：下载 → 识别 → 准备 → 抽取 → 模型增强 + 摘要 → 组装。

        功能：依次调步骤 3（内部含步骤 1 下载与步骤 2 识别）、步骤 4、模型阶段、步骤 5、步骤 6。
        应用场景：`POST /understand`。
        输入：url — 用户提供的地址；query — 可选问题（带它则摘要变成针对性回答）。
        输出：PipelineResult（响应体 + HTTP 状态码）。
        """
        started = time.monotonic()
        try:
            # ── 步骤 1-3：下载 URL → 识别类型 → 补后缀并组装 PreparedSource
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
            return step6_assemble.internal_error_response(url, exc, started, "understand")
        try:
            request_budget.assert_request_budget("understand.prepare")
            # ── 步骤 4：按扩展名跑处理器（prepare_assets=True：准备内嵌图与扫描页素材）
            result, parse_ms = step4_extract.run_extraction(prepared, prepare_assets=True)
            request_budget.assert_request_budget("understand.extract")

            client = DeepSeekClient()
            model_ms = 0
            ocr_text: Optional[str] = None
            asr_text: Optional[str] = None
            summary: Optional[str] = None
            description: Optional[str] = None
            if _has_model_work(prepared, result):
                model_started = time.monotonic()
                model_failed = False
                model_hits = []
                try:
                    request_budget.assert_request_budget("understand.model")
                    # ── 模型增强：按格式显式分派（见 `_run_model_enhance`）
                    outcome = _run_model_enhance(prepared, result, client)
                    result.notes.extend(outcome.notes)
                    ocr_text = outcome.ocr_text
                    asr_text = outcome.asr_text
                    description = outcome.description
                    # ── 步骤 5：生成摘要
                    summary, model_hits = step5_enhance.summarize(
                        client, prepared, result.content, ocr_text, asr_text, description, query
                    )
                except LlmError as exc:
                    model_failed = True
                    result.notes.append(f"模型增强失败: {exc}")
                    result.model_error = ErrorInfo(code=exc.code, message=str(exc), param=exc.param)
                    logger.error(f"understand 模型阶段失败: {exc}")
                except TimeoutError as exc:
                    model_failed = True
                    result.notes.append(str(exc))
                    result.model_error = ErrorInfo(
                        code="request_budget_exhausted", message=str(exc), param=None
                    )
                model_ms = int((time.monotonic() - model_started) * 1000)
                if summary is None and not model_failed:
                    result.notes.append("模型阶段没有产出摘要：这份内容里既没有可加工的文本，也没有读出可用的图像信息")
                result.limits_hit.extend(model_hits)
            else:
                result.notes.append("没有可供模型处理的内容，understand 跳过模型调用")

            delivery = merge_understand_delivery(
                result.content,
                ocr_text=ocr_text,
                asr_text=asr_text,
                description=description,
            )
            # ── 步骤 6：组装响应
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
                    usage=client.ledger.snapshot(),
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
            return step6_assemble.internal_error_response(prepared.source, exc, started, "understand")
        finally:
            step3_prepare.cleanup(prepared)

    def run_from_upload(self, stream: BinaryIO, file_name: str, query: Optional[str] = None) -> PipelineResult:
        """
        上传入口：落盘 → 识别 → 准备 → 抽取 → 模型增强 + 摘要 → 组装。

        功能：与 URL 入口同一条流程，只有第一步的取字节方式不同（上传流落盘而不是下载）。
        应用场景：`POST /understand/file`。
        输入：stream — 可读二进制流；file_name — 原始文件名；query — 可选问题。
        输出：PipelineResult（响应体 + HTTP 状态码）。
        """
        started = time.monotonic()
        try:
            # ── 步骤 1-3：上传流落盘 → 识别类型 → 补后缀并组装 PreparedSource
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
            return step6_assemble.internal_error_response(file_name, exc, started, "understand")
        try:
            request_budget.assert_request_budget("understand.prepare")
            # ── 步骤 4：按扩展名跑处理器（prepare_assets=True）
            result, parse_ms = step4_extract.run_extraction(prepared, prepare_assets=True)
            request_budget.assert_request_budget("understand.extract")

            client = DeepSeekClient()
            model_ms = 0
            ocr_text: Optional[str] = None
            asr_text: Optional[str] = None
            summary: Optional[str] = None
            description: Optional[str] = None
            if _has_model_work(prepared, result):
                model_started = time.monotonic()
                model_failed = False
                model_hits = []
                try:
                    request_budget.assert_request_budget("understand.model")
                    outcome = _run_model_enhance(prepared, result, client)
                    result.notes.extend(outcome.notes)
                    ocr_text = outcome.ocr_text
                    asr_text = outcome.asr_text
                    description = outcome.description
                    summary, model_hits = step5_enhance.summarize(
                        client, prepared, result.content, ocr_text, asr_text, description, query
                    )
                except LlmError as exc:
                    model_failed = True
                    result.notes.append(f"模型增强失败: {exc}")
                    result.model_error = ErrorInfo(code=exc.code, message=str(exc), param=exc.param)
                    logger.error(f"understand 模型阶段失败: {exc}")
                except TimeoutError as exc:
                    model_failed = True
                    result.notes.append(str(exc))
                    result.model_error = ErrorInfo(
                        code="request_budget_exhausted", message=str(exc), param=None
                    )
                model_ms = int((time.monotonic() - model_started) * 1000)
                if summary is None and not model_failed:
                    result.notes.append("模型阶段没有产出摘要：这份内容里既没有可加工的文本，也没有读出可用的图像信息")
                result.limits_hit.extend(model_hits)
            else:
                result.notes.append("没有可供模型处理的内容，understand 跳过模型调用")

            delivery = merge_understand_delivery(
                result.content,
                ocr_text=ocr_text,
                asr_text=asr_text,
                description=description,
            )
            # ── 步骤 6：组装响应
            return step6_assemble.PipelineResult(
                step6_assemble.build_response(
                    prepared,
                    result,
                    download_ms=upload_ms,
                    parse_ms=parse_ms,
                    model_ms=model_ms,
                    total_ms=int((time.monotonic() - started) * 1000),
                    summary=summary,
                    ocr_text=ocr_text,
                    asr_text=asr_text,
                    usage=client.ledger.snapshot(),
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
            return step6_assemble.internal_error_response(prepared.source, exc, started, "understand")
        finally:
            step3_prepare.cleanup(prepared)


def _run_model_enhance(
    prepared: PreparedSource, result: ExtractResult, client: DeepSeekClient
) -> EnhanceOutcome:
    """understand 专用：按格式决定调用哪个 enhance 实现。"""
    if prepared.ext in IMAGE_EXTENSIONS and not result.assets:
        return image.enhance_image(client, prepared)
    if prepared.ext in MEDIA_EXTENSIONS:
        return media.enhance_media(result)
    return enhance_assets(client, result)


def _has_model_work(prepared: PreparedSource, result: ExtractResult) -> bool:
    """有没有可供模型加工的内容：交付文本、素材清单、或源本身就是图片/音视频。"""
    return (
        prepared.ext not in MODEL_SKIPPED_EXTENSIONS
        and (
            bool(result.content.strip())
            or bool(result.assets)
            or prepared.ext in IMAGE_EXTENSIONS
            or prepared.ext in MEDIA_EXTENSIONS
        )
    )
