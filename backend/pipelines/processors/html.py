# -*- coding: utf-8 -*-
"""
网页抽取：HTML → 正文文本。

设计要点：
- 正文用 `get_text(separator="\n")` 取：不带分隔符时块级元素会粘连成一行（"标题正文"），
  段落不可辨。
- `<table>` 不走拉平那条路：`html_tables` 把它渲成制表符块再回填，列对应关系与合并区因此保住。
- 标题、description 与链接都带出来——它们是网页真实信息的一部分，不带出来的话
  「尽量保留真实信息」在网页这一类上无从谈起。
"""
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import configs.limits as limits
from pipelines.processors import html_tables
from pipelines.processors.schema import ExtractResult
from utils.textfile import read_text_file

def _clean_text(raw: str) -> str:
    """去掉连续空行与行内多余空白，保留段落结构。"""
    lines = [line.strip() for line in raw.splitlines()]
    kept = []
    for line in lines:
        if line:
            kept.append(line)
        elif kept and kept[-1] != "":
            kept.append("")
    return "\n".join(kept).strip()

def extract_html(path: Path, page_url: str = "") -> ExtractResult:
    """
    抽取 HTML 正文。

    功能：剥掉 script/style/noscript 与常见排版包装标签，按段落取文本；表格走格位网格渲成
          制表符块后回填到原位置；额外带出标题、meta description 与去重后的链接清单。
    应用场景：dispatch 分派 webpage 组时调用；也用于「URL 无扩展名但内容是 HTML」的情况。
    输入：path — 落盘的 HTML 文件；page_url — 最终 URL，用于把相对链接补成绝对链接。
    输出：ExtractResult，content 为「标题 + 摘要 + 正文 + 链接」的可读文本。
    """
    html, text_hits = read_text_file(path)
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # 先把表格换成哨兵行，正文清洗之后再把制表符块换回来（清洗会 strip 每一行）
    table_blocks, table_hits = html_tables.render_tables(soup)

    title = soup.title.get_text(strip=True) if soup.title else ""

    description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        description = meta["content"].strip()

    links = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(page_url, href) if page_url else href
        if absolute in seen:
            continue
        seen.add(absolute)
        text = anchor.get_text(strip=True)
        links.append(f"{text} -> {absolute}" if text else absolute)

    # 先把全部去重链接收集完再截断：提前 break 的话，就再也说不清「总共多少条」了
    total_links = len(links)
    links = links[: limits.MAX_LINKS]

    body = _clean_text(soup.get_text(separator="\n"))
    body = html_tables.restore_blocks(body, table_blocks)

    blocks = []
    if title:
        blocks.append(f"# {title}")
    if description:
        blocks.append(f"> {description}")
    blocks.append(body)
    if links:
        blocks.append("## 链接\n" + "\n".join(links))

    return ExtractResult(
        content="\n\n".join(block for block in blocks if block),
        engines=["BeautifulSoup"],
        limits_hit=text_hits + (["max_links"] if total_links > limits.MAX_LINKS else []) + table_hits,
    )
