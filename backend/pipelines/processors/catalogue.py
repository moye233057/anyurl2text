# -*- coding: utf-8 -*-
"""
印刷目录：抽目录行、建区间。

使用前注意事项：
- 目录只认正文里带「目 录」的印刷目录页，不读 PDF 书签，也不收图片索引 / 字母索引。
- 汉EV闪充版这本的目录页码与 PDF 页码一致，区间直接用目录上的数字。
- 本文件只吐 catalogue_ranges。正文页归进标题由调用方（RAG 入库）做。
"""

import re


def _toc_cells(page_text: str) -> list:
    """把一页目录 markdown 拆成单元格。`<br>` 先当换行，点线标题被表格拆开时再拼回去。"""
    text = page_text.replace("**", "").replace("`", "")
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    cells = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line or raw_line.startswith("|---") or set(raw_line) <= set("|-: "):
            continue
        if raw_line.startswith("|"):
            pieces = [piece.strip() for piece in raw_line.strip("|").split("|")]
        else:
            pieces = [raw_line]
        for piece in pieces:
            if not piece or piece == "---":
                continue
            # 同一格里 <br> 可能是断行，也可能是两条目录
            buf = ""
            for part in re.split(r"<br\s*/?>", piece):
                part = part.strip()
                if not part:
                    continue
                buf = part if not buf else buf + part
                if re.search(r"\d+\s*$", buf):
                    cells.append(buf)
                    buf = ""
            if buf:
                cells.append(buf)
    return cells


def _parse_toc_cell(cell: str):
    """
    单元格 → (标题, 页码, 是否 N-N 节标题)。

    节标题形如 `1-1座椅安全带........14`，和同页的叶子条目重复，后面会丢掉。
    """
    cell = re.sub(r"^#+\s*", "", cell).strip()
    kept_star = False
    matched = re.match(r"^(?P<body>.+?)(?:\.{2,}|…{2,}|·{2,})\s*(?P<page>\d+)\s*$", cell)
    if not matched:
        # 开通App的车辆控制功能*321：星号是配置标记，后面直接跟页码
        matched = re.match(r"^(?P<body>.+?)\*(?P<page>\d+)\s*$", cell)
        kept_star = matched is not None
    if not matched:
        return None
    is_section = re.match(r"^\d+-\d+", matched.group("body").strip()) is not None
    title = re.sub(r"^\d+-\d+\s*", "", matched.group("body"))
    title = re.sub(r"[\s.·…\-–—]+$", "", title)
    if kept_star:
        title += "*"
    title = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", title)
    title = title.strip()
    if len(title) < 2 or not re.search(r"[\u4e00-\u9fffA-Za-z]", title):
        return None
    if title.lower().startswith("br"):
        return None
    return title, int(matched.group("page")), is_section


def parse_catalogue_line(line: str):
    """
    解析一行目录为 (标题, 起始页)。

    功能：页码取行末数字。标题里的数字（如 12V）留在标题上。
    应用场景：建目录区间。
    输入：line — extract_catalogue_lines 写出的「标题 页码」。
    输出：(title, start_page)；解析不出则 None。
    """
    line = (line or "").strip()
    matched = re.match(r"^(?P<title>.+?)\s+(?P<page>\d+)\s*$", line)
    if not matched:
        return None
    title = matched.group("title").strip()
    if len(title) < 2:
        return None
    return title, int(matched.group("page"))


def extract_catalogue_lines(catalogue_pages: list) -> list:
    """
    从印刷目录抽出「标题 + 页码」行，保持目录顺序。

    功能：只读含「目 录」的页；丢掉 N-N 节标题（同页已有叶子条目时）；同标题同页去重。
    应用场景：按页抽出之后。汉EV的目录在 PDF 第 4–6 页，图片索引和章首重复目录不收。
    输入：catalogue_pages — 预览页 [{page, text}]，函数自己筛「目 录」。
    输出：`标题 页码` 字符串列表。没有目录页则 []。
    """
    toc_pages = [page for page in catalogue_pages if re.search(r"目\s*录", page.get("text") or "")]
    parsed = []
    seen = set()
    for page in toc_pages:
        for cell in _toc_cells(page.get("text") or ""):
            item = _parse_toc_cell(cell)
            if not item:
                continue
            title, start_page, is_section = item
            parsed.append({"title": title, "start_page": start_page, "is_section": is_section})
    pages_with_leaf = {item["start_page"] for item in parsed if not item["is_section"]}
    lines = []
    for item in parsed:
        if item["is_section"] and item["start_page"] in pages_with_leaf:
            continue
        key = (item["title"], item["start_page"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{item['title']} {item['start_page']}")
    return lines


def build_catalogue_ranges(catalogue_lines: list) -> list:
    """
    目录行 → [{title, start_page, end_page}]。

    功能：同一起始页的多条叶子并成一条，标题用 ` / ` 连接；end_page 取下一组的 start；末项用 10**9。
    应用场景：抽目录行之后，写入 RAG 抽取响应。
    输入：catalogue_lines — extract_catalogue_lines 的产出。
    输出：按 start_page 排好的区间列表。同页不会再切出空区间。
    """
    items = []
    for line in catalogue_lines:
        parsed = parse_catalogue_line(line)
        if parsed:
            title, start_page = parsed
            items.append({"title": title, "start_page": start_page})
    items = sorted(items, key=lambda item: item["start_page"])
    merged = []
    for item in items:
        if merged and merged[-1]["start_page"] == item["start_page"]:
            titles = merged[-1]["title"].split(" / ")
            if item["title"] not in titles:
                merged[-1]["title"] += " / " + item["title"]
            continue
        merged.append({"title": item["title"], "start_page": item["start_page"]})
    for index, item in enumerate(merged):
        item["end_page"] = merged[index + 1]["start_page"] if index + 1 < len(merged) else 10**9
    return merged
