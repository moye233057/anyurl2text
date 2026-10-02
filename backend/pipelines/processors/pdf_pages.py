# -*- coding: utf-8 -*-
"""
PDF 按页抽出 markdown（pymupdf4llm）。

使用前注意事项：
- 页码对外一律 PDF 阅读器 1 起；pymupdf4llm 的 metadata.page 是 0 起。
- 原始 chunk 含 Rect，不能 json.dumps，只保留 page/text。
- RAG 抽取不消费内嵌图，不落盘。
"""

from pathlib import Path


def chunk_to_page_row(item: dict, fallback_index: int) -> dict:
    """
    把 pymupdf4llm 的一页 chunk 收成 {page:int, text:str}。

    功能：读 metadata.page（0 起）或 item.page，转成阅读器页码。
    应用场景：抽页之后立刻规范化，后续目录映射只认这个形状。
    输入：item — pymupdf4llm 一页；fallback_index — 列表下标，缺页码时用。
    输出：{page, text}。
    """
    text = item.get("text", "") or ""
    metadata = item.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("page") is not None:
        page_no = int(metadata["page"]) + 1
    elif isinstance(item.get("page"), int):
        page_no = item["page"]
    else:
        page_no = fallback_index + 1
    return {"page": page_no, "text": text}


def chunks_to_page_rows(chunks: list) -> list:
    """
    一批 pymupdf4llm chunk → 页列表。

    功能：按出现顺序编号。
    应用场景：to_markdown(page_chunks=True) 返回 list 时。
    输入：chunks — pymupdf4llm 页 chunk 列表。
    输出：[{page, text}, ...]。
    """
    return [chunk_to_page_row(item, index) for index, item in enumerate(chunks)]


def extract_pdf_pages(pdf_path: Path) -> list:
    """
    用 pymupdf4llm 按页抽出 markdown。

    功能：page_chunks=True；不写内嵌图。
    应用场景：RAG 抽取带印刷目录的 PDF。
    输入：pdf_path — PDF 路径。
    输出：[{page, text}, ...]。
    异常：PDF 不存在或 pymupdf4llm 失败时向上抛。
    """
    import pymupdf4llm

    md_chunks = pymupdf4llm.to_markdown(
        str(pdf_path),
        page_chunks=True,
        write_images=False,
    )
    if isinstance(md_chunks, list):
        return chunks_to_page_rows(md_chunks)
    raise TypeError(f"pymupdf4llm 期望 list，实际为 {type(md_chunks).__name__}")
