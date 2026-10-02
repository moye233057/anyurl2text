# -*- coding: utf-8 -*-
"""
RAG 抽取管线：给检索入库用的文件内容。

职责：上传落盘后，PDF 抽出印刷目录则吐页与区间；否则复用 extract 内核的交付文本。
      不走 ConvertResponse，不调模型，不按 MAX_CONTENT_CHARS 截断。
"""
from typing import BinaryIO, NamedTuple, Optional

from configs.log_config import logger
from pipelines.processors.catalogue import build_catalogue_ranges, extract_catalogue_lines
from pipelines.processors.pdf_pages import extract_pdf_pages
from pipelines.steps import step1_download, step3_prepare, step4_extract
from routers.rag.schema import RagExtractResponse

PREVIEW_FIRST_PAGES = 15


class RagPipelineResult(NamedTuple):
    """RAG 管线产出：响应体 + HTTP 状态码。"""

    response: RagExtractResponse
    status: int


def _fail(kind: str, error: str, status: int) -> RagPipelineResult:
    """
    组装失败响应。

    功能：kind 与 error 写入契约，pages/text/ranges 保持空。
    应用场景：上传超限、抽页失败、正文为空。
    输入：kind — error 或已识别扩展名；error — 原因；status — HTTP 状态码。
    输出：RagPipelineResult。
    """
    return RagPipelineResult(RagExtractResponse(kind=kind, error=error), status)


class RagExtractPipeline:
    """RAG 入库抽取：不依赖模型。"""

    def run_from_upload(self, stream: BinaryIO, file_name: str) -> RagPipelineResult:
        """
        上传入口：落盘 → 识别 → 印刷目录或 extract 内核 → 组装。

        功能：PDF 先 pymupdf4llm 抽页，前 N 页能建目录区间则返回 pages；否则走 step4 交付文本。
        应用场景：`POST /rag/extract/file`。
        输入：stream — 可读二进制流；file_name — 原始文件名。
        输出：RagPipelineResult。
        """
        prepared = None
        try:
            prepared, _upload_ms = step3_prepare.prepare_from_upload(stream, file_name)
        except step1_download.FetchError as exc:
            return _fail("error", str(exc), exc.status_code)
        try:
            if prepared.ext == "pdf":
                catalogue = self._try_catalogue(prepared.path)
                if catalogue is not None:
                    return catalogue
                logger.info("rag extract：PDF 无印刷目录，改走 extract 内核")
            return self._from_extract(prepared)
        except Exception as exc:
            logger.exception(f"rag extract 失败 file={file_name}")
            return _fail("error", str(exc), 500)
        finally:
            if prepared is not None:
                step3_prepare.cleanup(prepared)

    def _try_catalogue(self, pdf_path) -> Optional[RagPipelineResult]:
        """
        尝试从 PDF 抽出印刷目录。

        功能：按页抽出后读前 N 页；建出区间则成功，否则 None。
        应用场景：识别为 pdf 之后。
        输入：pdf_path — 已落盘 PDF。
        输出：命中则 RagPipelineResult；否则 None。
        """
        logger.info(f"rag extract：pymupdf4llm {pdf_path.name}")
        page_rows = extract_pdf_pages(pdf_path)
        logger.info(f"rag extract：pages={len(page_rows)}")
        preview = page_rows[:PREVIEW_FIRST_PAGES]
        lines = extract_catalogue_lines(preview)
        ranges = build_catalogue_ranges(lines)
        if not lines or not ranges:
            logger.info("rag extract：抽不出目录行")
            return None
        logger.info(f"rag extract：catalogue lines={len(lines)} ranges={len(ranges)}")
        return RagPipelineResult(
            RagExtractResponse(kind="pdf", pages=page_rows, catalogue_ranges=ranges, error=None),
            200,
        )

    def _from_extract(self, prepared) -> RagPipelineResult:
        """
        复用 extract 内核的交付文本。

        功能：step4_extract.run_extraction；不截断。正文为空则失败。
        应用场景：非 PDF，或 PDF 没有印刷目录。
        输入：prepared — 已识别的源。
        输出：RagPipelineResult。
        """
        result, _parse_ms = step4_extract.run_extraction(prepared, prepare_assets=False)
        kind = prepared.ext or "file"
        text = result.content or ""
        if not str(text).strip():
            reason = result.degrade_reason or "抽取正文为空"
            return _fail(kind, reason, 200)
        logger.info(f"rag extract：ok ext={kind} chars={len(text)}")
        return RagPipelineResult(
            RagExtractResponse(kind=kind, text=text, error=None),
            200,
        )
