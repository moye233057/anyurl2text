# -*- coding: utf-8 -*-
"""
转换接口：extract（确定性抽取）与 understand（模型增强）。

两者共用同一份请求体与响应契约（ADR 0002 / ADR-0011），差别在是否调用模型以及 understand
的 `content` 是否合并模型转写。路由写成同步 `def`：下载、解析、模型调用都是阻塞式，
交给 FastAPI 的线程池；并发按扩展名分快/慢池（ADR-0011）。

状态码按语义返回（入参 422、超限 413、并发 429、超时 504、上游 502），
同时 body 里始终带 error 字段，兼顾「看状态码」与「看 body」两种消费习惯。
"""
from typing import Optional

from fastapi import APIRouter, File, Form, Response, UploadFile

from configs.log_config import logger, redact_url_for_log
from pipelines.extract import ExtractPipeline
from pipelines.understand import UnderstandPipeline
from routers.convert.router_helpers import run_with_limits
from routers.convert.schema import ConvertResponse, ConvertUrlBody
from utils.concurrency import pool_for_upload_name, pool_for_url

router = APIRouter(tags=["convert"])

extract_pipeline = ExtractPipeline()
understand_pipeline = UnderstandPipeline()

@router.post(
    "/extract",
    response_model=ConvertResponse,
    summary="确定性抽取（不调用模型）",
    description="下载 URL、识别类型、按格式解析出原文。稳定、快、不产生模型费用。",
)
def extract_from_url(body: ConvertUrlBody, response: Response) -> ConvertResponse:
    """URL 入口的确定性抽取。"""
    logger.info(
        f"extract 收到请求 url={redact_url_for_log(body.url)} has_query={bool(body.query)}"
    )
    result = run_with_limits(
        pool_for_url(body.url),
        body.url,
        lambda: extract_pipeline.run_from_url(body.url, body.query),
    )
    response.status_code = result.status
    return result.response

@router.post(
    "/extract/file",
    response_model=ConvertResponse,
    summary="确定性抽取（本地文件上传）",
    description="与 POST /extract 等价，输入改成上传的文件，便于本机演示。",
)
def extract_from_file(
    response: Response,
    file: UploadFile = File(..., description="待处理的文件"),
    query: Optional[str] = Form(None, description="extract 不消费，保留字段是为了两个接口入参一致"),
) -> ConvertResponse:
    """上传入口的确定性抽取。"""
    file_name = file.filename or "upload.bin"
    logger.info(f"extract 收到上传 file={file_name} has_query={bool(query)}")
    result = run_with_limits(
        pool_for_upload_name(file_name),
        file_name,
        lambda: extract_pipeline.run_from_upload(file.file, file_name, query),
    )
    response.status_code = result.status
    return result.response

@router.post(
    "/understand",
    response_model=ConvertResponse,
    summary="模型增强（认字 / 描述 / 摘要）",
    description=(
        "在确定性抽取之上叠加模型能力：图片认字与内容描述、扫描件逐页还原、"
        "文档内嵌图认字；不带 query 给通用摘要，带 query 针对问题回答。"
        "understand 的 content 可含模型转写的正文，extract 仅纯代码段；摘要仅本接口产出。"
    ),
)
def understand_from_url(body: ConvertUrlBody, response: Response) -> ConvertResponse:
    """URL 入口的模型增强。"""
    logger.info(
        f"understand 收到请求 url={redact_url_for_log(body.url)} has_query={bool(body.query)}"
    )
    result = run_with_limits(
        pool_for_url(body.url),
        body.url,
        lambda: understand_pipeline.run_from_url(body.url, body.query),
    )
    response.status_code = result.status
    return result.response

@router.post(
    "/understand/file",
    response_model=ConvertResponse,
    summary="模型增强（本地文件上传）",
    description="与 POST /understand 等价，输入改成上传的文件。",
)
def understand_from_file(
    response: Response,
    file: UploadFile = File(..., description="待处理的文件"),
    query: Optional[str] = Form(None, description="可选问题，用于针对性回答"),
) -> ConvertResponse:
    """上传入口的模型增强。"""
    file_name = file.filename or "upload.bin"
    logger.info(f"understand 收到上传 file={file_name} has_query={bool(query)}")
    result = run_with_limits(
        pool_for_upload_name(file_name),
        file_name,
        lambda: understand_pipeline.run_from_upload(file.file, file_name, query),
    )
    response.status_code = result.status
    return result.response
