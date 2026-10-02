# -*- coding: utf-8 -*-
"""
PDF 抽取。

设计要点：
- 没有文字层时把页面渲染成图交给 understand 处理（ADR 0006）：纯扫描件既无文字层、
  往往也没有可抽的内嵌图，少了这一步就无内容可取；渲染本身是确定性的，不调用模型。
- 版面按 (页码, y, x) 排序；图片位置锚点只取左上角，同页多图可能键相近，
  但不影响文本顺序。
"""
from pathlib import Path

import fitz  # PyMuPDF

import configs.settings as settings
from pipelines.processors.schema import EmbeddedAsset, ExtractContext, ExtractResult

# 判定「这份 PDF 没有文字层」的字符数下限：低于它就走页面渲染
TEXT_LAYER_MIN_CHARS = 30

def _page_text(page: "fitz.Page") -> str:
    """取一页的文本块，按 (y, x) 排序后拼成文本；空页返回空串。"""
    blocks = page.get_text("blocks")
    items = []
    for block in blocks:
        if len(block) < 5:
            continue
        text = str(block[4]).strip()
        if text:
            items.append((round(float(block[1]), 1), round(float(block[0]), 1), text))
    items.sort(key=lambda item: (item[0], item[1]))
    return "\n".join(item[2] for item in items)

def _save_embedded_images(doc: "fitz.Document", pages: int, ctx: ExtractContext) -> list:
    """把前若干张内嵌图落盘成 PNG 素材，返回素材列表。"""
    assets = []
    used = 0
    for page_index in range(pages):
        if used >= settings.MAX_EMBEDDED_IMAGES:
            break
        for info in doc[page_index].get_images(full=True):
            if used >= settings.MAX_EMBEDDED_IMAGES:
                break
            xref = info[0]
            try:
                pixmap = fitz.Pixmap(doc, xref)
                if pixmap.n - pixmap.alpha >= 4:
                    pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
                target = ctx.work_dir / f"pdf_p{page_index + 1}_x{xref}.png"
                pixmap.save(str(target))
            except Exception:
                continue
            used += 1
            assets.append(
                EmbeddedAsset(
                    kind="image",
                    label=f"第{page_index + 1}页内嵌图{xref}",
                    path=target,
                    order=used,
                )
            )
    return assets

def render_pages(doc: "fitz.Document", page_indexes: list, ctx: ExtractContext) -> list:
    """
    把指定页渲染成 PNG 素材。

    功能：按配置 DPI 逐页渲染并落盘；单页渲染失败只跳过该页，不影响其余页。
    应用场景：扫描件补全（ADR 0006）；understand 会把这些页面交给视觉模型。
    输入：doc — 已打开的 PyMuPDF 文档；page_indexes — 从 0 开始的页序号列表；ctx — 抽取上下文。
    输出：EmbeddedAsset 列表（kind="page"）。
    """
    assets = []
    for page_index in page_indexes:
        try:
            pixmap = doc[page_index].get_pixmap(dpi=settings.RENDERED_PAGE_DPI)
            target = ctx.work_dir / f"page_{page_index + 1:03d}.png"
            pixmap.save(str(target))
        except Exception:
            continue
        assets.append(
            EmbeddedAsset(
                kind="page",
                label=f"第{page_index + 1}页",
                path=target,
                order=page_index,
            )
        )
    return assets

def extract_pdf(path: Path, ctx: ExtractContext) -> ExtractResult:
    """
    抽取 PDF 文本与结构信息。

    功能：逐页取文本块并按版面排序；统计内嵌图并在需要时落盘为素材；
          无文字层时在需要时渲染页面为素材（供 understand 识别）。
    应用场景：dispatch 分派 PDF 时调用。
    输入：path — 文件路径；ctx — 抽取上下文。
    输出：ExtractResult；纯扫描件在 extract 下 content 为空且 notes 声明无文字层。
    异常：不抛——打开或解析失败时返回降级结果并说明原因。
    """
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        return ExtractResult(
            degraded=True,
            degrade_reason=f"PDF 打开失败: {exc}",
            engines=["PyMuPDF"],
        )

    try:
        if doc.needs_pass and not doc.authenticate(""):
            return ExtractResult(
                degraded=True,
                degrade_reason="PDF 受密码保护，无法提取内容",
                engines=["PyMuPDF"],
            )

        pages = doc.page_count
        page_texts = []
        images_total = 0
        for page_index in range(pages):
            page = doc[page_index]
            text = _page_text(page)
            if text:
                page_texts.append(f"=== 第{page_index + 1}页 ===\n{text}")
            images_total += len(page.get_images(full=True))

        content = "\n\n".join(page_texts)
        text_chars = sum(len(part) for part in page_texts)

        result = ExtractResult(
            content=content,
            engines=["PyMuPDF"],
            pages=pages,
            pages_used=pages,
            images=images_total,
            images_used=0,
        )

        if not ctx.prepare_assets:
            if text_chars < TEXT_LAYER_MIN_CHARS:
                result.notes.append("没有文字层（疑似扫描件）；understand 会渲染页面交给视觉模型")
            return result

        # 扫描件判定：没有文字层，且渲染开关打开
        needs_page_render = (
            text_chars < TEXT_LAYER_MIN_CHARS and settings.RENDERED_PAGE_OCR_ENABLED and pages > 0
        )

        if needs_page_render:
            # 页面渲染与内嵌图抽取**互斥**：扫描件的每一页本身就是一张整页图，
            # `get_images()` 照样会把它列出来。两条路都走的话，同一页文字会被识别两遍、
            # 付两份钱，meta.ocr_text 里还会出现重复段落。
            limit = min(pages, settings.RENDERED_PAGE_MAX)
            assets = render_pages(doc, list(range(limit)), ctx)
            result.pages_used = limit
            result.notes.append(
                f"没有文字层，已渲染前 {limit} 页交给视觉模型"
                + (f"（共 {pages} 页，超出上限部分未处理）" if pages > limit else "")
                + "；整页渲染已覆盖页面上的图，不再单独抽内嵌图"
            )
        else:
            assets = _save_embedded_images(doc, pages, ctx)
            result.images_used = len(assets)

        result.assets = assets
        return result
    finally:
        doc.close()
