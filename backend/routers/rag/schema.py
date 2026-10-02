# -*- coding: utf-8 -*-
"""
RAG 抽取响应契约。不 import 项目内任何东西，任何层都能用（ADR 0007 叶子模块）。

给检索入库用，不进 ConvertResponse：有印刷目录的 PDF 吐 pages 与 catalogue_ranges，
其余源吐 text。error 恒存在，成功为 None。
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class RagPage(BaseModel):
    """一页正文。page 是 PDF 阅读器 1 起的页码。"""

    page: int = Field(..., description="阅读器页码，从 1 起")
    text: str = Field(..., description="该页 markdown 正文")


class RagCatalogueRange(BaseModel):
    """一条印刷目录区间。"""

    title: str = Field(..., description="目录标题；同页多条叶子用 / 连接")
    start_page: int = Field(..., description="起始页，阅读器 1 起")
    end_page: int = Field(..., description="结束页；末项为 10**9")


class RagExtractResponse(BaseModel):
    """
    RAG 抽取结果。

    有印刷目录：kind=pdf，pages 与 catalogue_ranges 有值，text 为 None。
    无目录：kind 为识别出的扩展名，text 有值，pages 与 catalogue_ranges 为 None。
    失败：kind=error，error 非空。
    """

    kind: str = Field(..., description="pdf / 扩展名 / error")
    pages: Optional[List[RagPage]] = Field(None, description="按页正文；仅印刷目录命中时有值")
    text: Optional[str] = Field(None, description="整份交付文本；无目录时有值")
    catalogue_ranges: Optional[List[RagCatalogueRange]] = Field(
        None, description="印刷目录区间；仅命中时有值"
    )
    error: Optional[str] = Field(None, description="恒存在；成功为 null")
