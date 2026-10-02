# -*- coding: utf-8 -*-
"""
RAG 抽取接口：给检索入库用，契约独立于 extract / understand。

路由写成同步 def：抽页与解析都是阻塞式，交给 FastAPI 线程池。
"""
from fastapi import APIRouter, File, Response, UploadFile

from configs.log_config import logger
from pipelines.rag_extract import RagExtractPipeline
from routers.rag.schema import RagExtractResponse

router = APIRouter(tags=["rag"])
pipeline = RagExtractPipeline()


@router.post(
    "/rag/extract/file",
    response_model=RagExtractResponse,
    summary="RAG 抽取（本地文件上传）",
    description=(
        "给检索入库用。PDF 抽出印刷目录则返回 pages 与 catalogue_ranges；"
        "否则返回 extract 内核的交付文本。不调用模型，不走 ConvertResponse。"
    ),
)
def rag_extract_from_file(
    response: Response,
    file: UploadFile = File(..., description="待入库的文件"),
) -> RagExtractResponse:
    """上传入口的 RAG 抽取。"""
    file_name = file.filename or "upload.bin"
    logger.info(f"rag extract 收到上传 file={file_name}")
    result = pipeline.run_from_upload(file.file, file_name)
    response.status_code = result.status
    return result.response
