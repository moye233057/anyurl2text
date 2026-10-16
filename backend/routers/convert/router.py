# -*- coding: utf-8 -*-
"""
转换接口：POST /convert 与 /convert/file。

能力选择 ocr / asr / llm（默认都不用）控制扩展；确定性抽取始终跑（ADR-0001）。
`ocr` 是**提供商单选**：false / true / deepseek / paddle，只决定文字转写交给谁（ADR-0004）；
看图支路（判型、语义描述、扫描页、内嵌图）只要 llm。
路由写成同步 def：下载、解析、模型调用都是阻塞式，交给 FastAPI 线程池；
并发按扩展名分快/慢池。
"""
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from configs.extensions import CAPABILITY_OCR, normalize_choice
from configs.log_config import logger, redact_url_for_log
from pipelines.convert import ConvertPipeline
from routers.convert.router_helpers import run_with_limits
from routers.convert.schema import ConvertResponse, ConvertUrlBody
from utils.concurrency import pool_for_upload_name, pool_for_url

router = APIRouter(tags=["convert"])

convert_pipeline = ConvertPipeline()


@router.post(
    "/convert",
    response_model=ConvertResponse,
    summary="统一转换（URL）",
    description=(
        "下载 URL、识别类型、确定性抽取；按 ocr/asr/llm 选择叠加扩展。"
        "ocr 是文字转写的提供商单选（false / true / deepseek / paddle）；"
        "看图（判型、语义描述、扫描页、内嵌图）只要 llm=true；摘要需要 llm；音视频转写需要 asr。"
    ),
)
def convert_from_url(body: ConvertUrlBody, response: Response) -> ConvertResponse:
    """URL 入口的统一转换。"""
    logger.info(
        f"convert 收到请求 url={redact_url_for_log(body.url)} "
        f"ocr={body.ocr} asr={body.asr} llm={body.llm} has_query={bool(body.query)}"
    )
    result = run_with_limits(
        pool_for_url(body.url),
        body.url,
        lambda: convert_pipeline.run_from_url(
            body.url, query=body.query, ocr=body.ocr, asr=body.asr, llm=body.llm
        ),
    )
    response.status_code = result.status
    return result.response


@router.post(
    "/convert/file",
    response_model=ConvertResponse,
    summary="统一转换（本地上传）",
    description="与 POST /convert 等价，输入改成上传的文件。",
)
def convert_from_file(
    response: Response,
    file: UploadFile = File(..., description="待处理的文件"),
    query: Optional[str] = Form(None, description="可选问题；仅 llm=true 时消费"),
    ocr: str = Form("false", description="OCR 提供商：false / true / deepseek / paddle"),
    asr: bool = Form(False, description="启用音视频转写"),
    llm: bool = Form(False, description="启用 LLM"),
) -> ConvertResponse:
    """上传入口的统一转换。"""
    file_name = file.filename or "upload.bin"
    # 表单里的东西都是字符串，这里用与 JSON 入参同一个名单归一（configs/extensions.py）
    try:
        ocr_choice = normalize_choice(CAPABILITY_OCR, ocr)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        f"convert 收到上传 file={file_name} ocr={ocr_choice} asr={asr} llm={llm} has_query={bool(query)}"
    )
    result = run_with_limits(
        pool_for_upload_name(file_name),
        file_name,
        lambda: convert_pipeline.run_from_upload(
            file.file, file_name, query=query, ocr=ocr_choice, asr=asr, llm=llm
        ),
    )
    response.status_code = result.status
    return result.response
