# -*- coding: utf-8 -*-
"""
DOCX 超链接的收集（只读，叶子模块）。

为什么单独成文件：`docx.py` 贴着单文件预算（判据 2：总行 ≤ 250、代码行 ≤ 150），而「按关系
取外部地址」这段自带一层 XML 与 rels 的来回，放进 `docx.py` 会顶穿那条线。本模块只 import
python-docx 与标准库，不 import 项目内任何东西（叶子模块），任何层都能用。
"""
from docx.oxml.ns import qn

def hyperlink_lines(document) -> list:
    """
    收出文档里的超链接（每行 `文字 -> URL`，没有文字时只给 URL），按目标地址去重。

    功能：走 `w:hyperlink`（表格与文本框里的超链接同在 body 里，一起收）；地址取自 `r:id`
          指向的**外部**关系，内部跳转（只有 `w:anchor`、没有地址）跳过——与网页那一路跳过
          `#` 锚点同一条规矩。先去重再截断，调用方才知道总共多少条。
    应用场景：`docx.extract_docx` 组装交付文本时调用。
    输入：document — python-docx 文档对象。
    输出：链接行列表（未截断，顺序即文档顺序）。
    """
    lines, seen = [], set()
    for element in document.element.body.iter(qn("w:hyperlink")):
        rel_id = element.get(qn("r:id"))
        rel = document.part.rels.get(rel_id) if rel_id else None
        url = rel.target_ref if rel is not None and rel.is_external else ""
        if not url or url in seen:
            continue
        seen.add(url)
        text = "".join(node.text or "" for node in element.iter(qn("w:t"))).strip()
        lines.append(f"{text} -> {url}" if text else url)
    return lines
