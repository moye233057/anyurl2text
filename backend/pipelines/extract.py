# -*- coding: utf-8 -*-
"""
纯代码解析管线：两个入口（URL / 上传）各自按步骤走完「下载 → 识别 → 准备 → 抽取 → 组装」。

每一步在方法体里点明对应的步骤文件与它的入口方法：
`step3_prepare.prepare_from_url()`（内部依次走 step1 下载、step2 识别）→ `step4_extract.run_extraction()`
→ `step6_assemble.build_response()`。understand 走的是同一批步骤（ADR 0002/0007），本文件不碰模型。
"""
import time
from typing import BinaryIO, Optional

from pipelines.processors.schema import ExtractResult
from pipelines.steps import step1_download, step3_prepare, step4_extract, step6_assemble
from pipelines.steps.step6_assemble import PipelineResult

class ExtractPipeline:
    """确定性抽取管线：不依赖模型，稳定、快、可离线。"""

    def run_from_url(self, url: str, query: Optional[str] = None) -> PipelineResult:
        """
        URL 入口：下载 → 识别 → 准备 → 抽取 → 组装。

        功能：依次调步骤 3（内部含步骤 1 下载与步骤 2 识别）、步骤 4、步骤 6；
              入口阶段失败时按语义状态码组装错误响应。
        应用场景：`POST /extract`。
        输入：url — 用户提供的地址；query — 本管线不消费（会如实说明被忽略）。
        输出：PipelineResult（响应体 + HTTP 状态码）。
        """
        started = time.monotonic()
        try:
            # ── 步骤 1-3：下载 URL → 识别类型 → 补后缀并组装 PreparedSource
            prepared, download_ms = step3_prepare.prepare_from_url(url)
        except step1_download.FetchError as exc:
            return step6_assemble.error_response(
                url, str(exc), int((time.monotonic() - started) * 1000), exc.status_code
            )
        try:
            # ── 步骤 4：按扩展名跑处理器，产出 content（交付文本）与素材清单
            result, parse_ms = step4_extract.run_extraction(prepared, prepare_assets=False)
            _note_ignored_query(result, query)
            # ── 步骤 6：组装响应
            return step6_assemble.PipelineResult(
                step6_assemble.build_response(
                    prepared,
                    result,
                    download_ms=download_ms,
                    parse_ms=parse_ms,
                    total_ms=int((time.monotonic() - started) * 1000),
                ),
                200,
            )
        finally:
            step3_prepare.cleanup(prepared)

    def run_from_upload(self, stream: BinaryIO, file_name: str, query: Optional[str] = None) -> PipelineResult:
        """
        上传入口：落盘 → 识别 → 准备 → 抽取 → 组装。

        功能：与 URL 入口同一条流程，只有第一步的取字节方式不同（上传流落盘而不是下载）。
        应用场景：`POST /extract/file`。
        输入：stream — 可读二进制流；file_name — 原始文件名；query — 本管线不消费。
        输出：PipelineResult（响应体 + HTTP 状态码）。
        """
        started = time.monotonic()
        try:
            # ── 步骤 1-3：上传流落盘 → 识别类型 → 补后缀并组装 PreparedSource
            prepared, upload_ms = step3_prepare.prepare_from_upload(stream, file_name)
        except step1_download.FetchError as exc:
            return step6_assemble.error_response(
                file_name, str(exc), int((time.monotonic() - started) * 1000), exc.status_code
            )
        try:
            # ── 步骤 4：按扩展名跑处理器
            result, parse_ms = step4_extract.run_extraction(prepared, prepare_assets=False)
            _note_ignored_query(result, query)
            # ── 步骤 6：组装响应
            return step6_assemble.PipelineResult(
                step6_assemble.build_response(
                    prepared,
                    result,
                    download_ms=upload_ms,
                    parse_ms=parse_ms,
                    total_ms=int((time.monotonic() - started) * 1000),
                ),
                200,
            )
        finally:
            step3_prepare.cleanup(prepared)

def _note_ignored_query(result: ExtractResult, query: Optional[str]) -> None:
    """extract 不做语言加工，带了 query 就如实说明它被忽略了，而不是静默丢弃。"""
    if query:
        result.notes.append("extract 是确定性抽取，不消费 query；需要按问题加工请用 understand")
