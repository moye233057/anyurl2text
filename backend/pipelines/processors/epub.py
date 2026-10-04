# -*- coding: utf-8 -*-
"""
EPUB 抽取：按 spine 顺序把各章 XHTML 拼成交付文本。

设计要点：

- **身份写在容器里**：EPUB 就是个 ZIP，真实身份由 `META-INF/container.xml` 指向的 OPF 决定
  （识别那一步的指纹同在 `configs/formats.py` 的 `ZIP_ENTRY_FLAVORS`）。
- **阅读顺序的唯一依据是 OPF 的 spine**：manifest 的声明顺序与 zip 的写入顺序都不代表阅读顺序，
  按它们拼会把章节排错。不在 spine 里的 manifest 项（封面、样式、目录页）一概不产出。
- **OPF 可以在子目录**：manifest 的 href 相对 OPF 所在目录解析，所以 `OEBPS/content.opf`
  里的 `ch1.xhtml` 指的是容器里的 `OEBPS/ch1.xhtml`，不是根目录下的同名文件。
- **CSS 一律不进交付文本**：`<link rel="stylesheet">` 本来就没有文本节点，内联 `<style>` 与
  `script` / `noscript` / `iframe` / `svg` 一起整块丢掉——样式与脚本不是这份文档的内容。
- **内部引用必须做文档级消歧**：EPUB 的 `#frag` 只在**单章内**有意义，跨章拼成一整篇之后
  「这个锚点指向哪儿」就成了歧义。所以链接目标一律提升成带章路径的文档级目标——章内自链
  `#sec` → `ch2.xhtml#sec`，跨章的 `ch2.xhtml#sec` 原样保留，两者此后指向同一个位置。
- 已知边界：只还原文字与链接，不还原样式与版式；spine 章数受 `MAX_ENTRIES` 限制。
"""
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup

import configs.limits as limits
from pipelines.processors import html_tables
from pipelines.processors.schema import ExtractContext, ExtractResult

# EPUB 规范要求容器里必有这个文件，它指向 OPF（真正的包描述）
CONTAINER_PATH = "META-INF/container.xml"
# 实际用到的解析器，如实报进 engines
ENGINES = ["zipfile", "ElementTree", "BeautifulSoup"]
# 这些元素的内容不是正文：脚本、样式、备用内容、内嵌框架与矢量图
DROP_TAGS = ("script", "style", "noscript", "iframe", "svg")

def _local(tag) -> str:
    """标签的本地名：OPF 有时不带命名空间，按本地名找比按命名空间找更稳。"""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

def _child(element, name: str):
    """按本地名取第一个直接子元素；element 为 None 时返回 None。"""
    for child in element if element is not None else ():
        if _local(child.tag) == name:
            return child
    return None

def _children(element, name: str) -> list:
    """按本地名取全部直接子元素；element 为 None 时返回空列表。"""
    return [child for child in (element if element is not None else ()) if _local(child.tag) == name]

def _degraded(reason: str) -> ExtractResult:
    """降级是正常结果（ADR-0003）：写清卡在哪一步，不抛给调用方。"""
    return ExtractResult(
        content="", engines=ENGINES, degraded=True,
        degrade_reason=reason, degrade_code="epub_parse_failed",
    )

def _opf_path(archive: zipfile.ZipFile) -> str:
    """读 container.xml，返回 rootfile 的 full-path（OPF 在容器里的路径）。"""
    root = ET.fromstring(archive.read(CONTAINER_PATH))
    rootfile = _child(_child(root, "rootfiles"), "rootfile")
    full_path = (rootfile.get("full-path") or "").strip() if rootfile is not None else ""
    if not full_path:
        raise ValueError("container.xml 里没有 rootfile 的 full-path")
    return full_path

def _spine_members(archive: zipfile.ZipFile, opf_path: str) -> list:
    """
    按 spine 顺序返回可读的章节路径。

    功能：manifest 只用来建 id → href 表（**不按它的顺序**出章节），spine 的 itemref 按序查出
           href，再与 OPF 所在目录拼成容器里的真实路径；只留下容器里确实存在的那些。
    应用场景：extract_epub 定位到 OPF 之后调用。
    输入：archive — 已打开的 EPUB；opf_path — OPF 在容器里的路径。
    输出：章节路径列表，顺序即阅读顺序。
    """
    root = ET.fromstring(archive.read(opf_path))
    base = posixpath.dirname(opf_path)
    manifest = {}
    for item in _children(_child(root, "manifest"), "item"):
        item_id, href = item.get("id"), (item.get("href") or "").strip()
        if item_id and href:
            manifest[item_id] = href
    names = set(archive.namelist())
    members = []
    for ref in _children(_child(root, "spine"), "itemref"):
        href = manifest.get(ref.get("idref"))
        if not href:
            continue
        member = posixpath.normpath(posixpath.join(base, href)) if base else href
        if member in names:
            members.append(member)
    return members

def _clean(raw: str) -> str:
    """去掉行首尾空白与连续空行，保留段落结构（与 html.py 同一套整理规则）。"""
    kept = []
    for line in raw.splitlines():
        line = line.strip()
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()

def _chapter_title(soup) -> str:
    """章标题：先 `<title>`，再第一个 h1–h6；都没有返回空串（分章标记只写章号）。"""
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    for level in range(1, 7):
        heading = soup.find(f"h{level}")
        if heading and heading.get_text(strip=True):
            return heading.get_text(strip=True)
    return ""

def _chapter_body(soup) -> tuple:
    """
    取一章的正文文本。

    功能：丢掉 `DROP_TAGS` 与 `<head>`——`<title>` 已经进了分章标记，留在正文里会重复出现；
           然后按块级元素取文本并整理空行；表格走格位网格渲成制表符块后回填到原位置。
    应用场景：extract_epub 逐章调用；必须在取过标题之后再调用。
    输入：soup — 该章 XHTML 的 BeautifulSoup 对象。
    输出：(正文文本, 命中的上界名列表, 该章各表的表头行数列表)。
    """
    for tag in soup(DROP_TAGS + ("head",)):
        tag.decompose()
    table_blocks, table_hits, table_headers, table_kinds = html_tables.render_tables(soup)
    text = _clean((soup.body or soup).get_text(separator="\n"))
    return html_tables.restore_blocks(text, table_blocks), table_hits, table_headers, table_kinds

def _chapter_links(soup, chapter_path: str) -> list:
    """
    收一章里的链接，返回 (文字, 目标) 列表。

    功能：外部 http(s) 链接原样输出；**内部引用提升成文档级目标**——章内锚点 `#sec` 补上
           章路径（→ `ch2.xhtml#sec`），相对路径按该章所在目录解析（`../ch2.xhtml#sec2`
           → `ch2.xhtml#sec2`）；javascript / mailto / tel 跳过。
    应用场景：extract_epub 逐章调用。
    输入：soup — 该章；chapter_path — 该章在容器里的路径，同时充当它的文档级 id。
    输出：(文字, 目标) 列表，顺序即章内出现顺序。
    """
    found = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        if href.startswith(("http://", "https://")):
            target = href
        elif href.startswith("#"):
            target = chapter_path + href
        else:
            base = posixpath.dirname(chapter_path)
            target = posixpath.normpath(posixpath.join(base, href)) if base else href
        found.append((anchor.get_text(strip=True), target))
    return found

def extract_epub(path: Path, ctx: ExtractContext) -> ExtractResult:
    """
    抽取 EPUB 的交付文本：按 spine 顺序拼接各章正文，末尾附去重后的链接段。

    功能：打开 ZIP → 读 container.xml 找 OPF → 按 spine 顺序逐章取正文（每章前加分章标记）→
           收链接并提升成文档级目标 → 截到 `MAX_LINKS`；spine 章数超过 `MAX_ENTRIES` 时只读
           前几章，命中名字进 `limits_hit` 并在 `notes` 里说明，不静默少读用户的章节。
    应用场景：dispatch 分派 epub 时调用。
    输入：path — EPUB 文件；ctx — 处理器上下文（本格式没有模型素材，不使用）。
    输出：ExtractResult，content 为「各章正文 + `## 链接`」。
    异常：不抛——不是 ZIP、容器损坏或受保护、缺 container.xml / OPF、OPF 不是合法 XML、
          spine 全空都返回降级结果；单章读不出来只跳过那一章并在 `notes` 里说明。
    """
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return _degraded(f"EPUB 容器打不开（不是有效的 ZIP）: {exc}")

    with archive:
        try:
            opf_path = _opf_path(archive)
        except KeyError:
            return _degraded(f"EPUB 容器里没有 {CONTAINER_PATH}，无法定位 OPF")
        except ET.ParseError as exc:
            return _degraded(f"EPUB 的 {CONTAINER_PATH} 不是合法 XML: {exc}")
        except Exception as exc:
            # 容器损坏 / 受保护 / 结构异常：一律降级，不让它冒成 500（ADR-0003）
            return _degraded(f"EPUB 容器读不出 {CONTAINER_PATH}: {exc}")
        try:
            chapters = _spine_members(archive, opf_path)
        except ET.ParseError as exc:
            return _degraded(f"EPUB 的 OPF（{opf_path}）不是合法 XML: {exc}")
        except Exception as exc:
            return _degraded(f"EPUB 的 OPF（{opf_path}）读不出来: {exc}")
        if not chapters:
            return _degraded(f"EPUB 的 OPF（{opf_path}）spine 里没有可读的章节")

        hits, notes = [], []
        used = chapters[: limits.MAX_ENTRIES]
        if len(used) < len(chapters):
            hits.append("max_entries")
            notes.append(
                f"spine 里共 {len(chapters)} 章，超过容器条目上限，只读了前 {limits.MAX_ENTRIES} 章"
            )
        blocks, links, unreadable = [], [], 0
        table_headers: list = []
        table_kinds: list = []
        for index, chapter in enumerate(used, start=1):
            try:
                markup = archive.read(chapter)
                soup = BeautifulSoup(markup, "html.parser")
                title = _chapter_title(soup)
                body, chapter_hits, chapter_headers, chapter_kinds = _chapter_body(soup)
                chapter_links = _chapter_links(soup, chapter)
            except Exception as exc:
                # 单章坏掉不该让整份文档失败：跳过它、如实说明，其余章照常交付。
                # 这里必须接宽：zipfile 对损坏数据抛的是 zlib.error、对加密成员抛 RuntimeError，
                # 都不在 BadZipFile 之下——按类型列举一定会漏。
                unreadable += 1
                notes.append(f"第 {index} 章（{chapter}）读不出来，已跳过: {exc}")
                continue
            marker = f"=== 章节 {index}: {title} ===" if title else f"=== 章节 {index} ==="
            blocks.append(f"{marker}\n\n{body}")
            hits.extend(chapter_hits)
            table_headers.extend(chapter_headers)
            table_kinds.extend(chapter_kinds)
            links.extend(chapter_links)
        if unreadable and not blocks:
            return _degraded(f"EPUB 的 {unreadable} 个章节都读不出来（容器损坏或受保护）")

    lines, seen = [], set()
    for text, target in links:
        if target in seen:
            continue
        seen.add(target)
        lines.append(f"{text} -> {target}" if text else target)
    if len(lines) > limits.MAX_LINKS:
        hits.append("max_links")
    if lines:
        blocks.append("## 链接\n" + "\n".join(lines[: limits.MAX_LINKS]))
    notes.append(
        f"已按 spine 顺序拼接 {len(used)} 章；`<link rel=stylesheet>` 与内联 `<style>` 的内容不进交付文本"
    )
    if len(table_headers) > 1:
        notes.append(
            f"全书共 {len(table_headers)} 张表，meta.table_header_rows 只在交付文本只有一张表时给值"
        )
    return ExtractResult(
        content="\n\n".join(block for block in blocks if block),
        engines=ENGINES,
        notes=notes,
        limits_hit=hits,
        table_header_rows=table_headers[0] if len(table_headers) == 1 else None,
        table_kind=table_kinds[0] if len(table_kinds) == 1 else None,
    )
