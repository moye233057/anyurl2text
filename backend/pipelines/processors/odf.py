# -*- coding: utf-8 -*-
"""
ODF 家族中「文本类」文档的抽取：odt（文本文档）与 odp（演示文稿）。

这里直接读 ZIP 里的 content.xml 并用标准库 ElementTree 取文本，而不是引入 odfpy：
ODF 的正文全在 text:p / text:h 元素里，按文档顺序遍历一遍就能拿到，
用标准库可控性更高，也少一层对第三方 API 形状的依赖。
（ods 不在这里——表格必须按行列位置还原，见 excel.py。）

已知边界：只还原文字内容，不还原样式、分栏与列位置；ODT 表格与 ODP 幻灯片
里的每个单元格/段落会各自成行，读起来是「一段一行」而不是表格。
"""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pipelines.processors.schema import ExtractResult

TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
# ODF 里承载正文的元素：p 是普通段落，h 是标题
TEXT_ELEMENTS = (TEXT_NS + "p", TEXT_NS + "h")

def extract_odf(path: Path, ext: str) -> ExtractResult:
    """
    抽取 odt / odp 的正文文字。

    功能：从 ZIP 容器取出 content.xml，按文档顺序收集 text:p 与 text:h 的文本，
          段落各自成行；同时把标题层级标出来。
    应用场景：dispatch 分派 odt / odp 时调用。
    输入：path — 文件路径；ext — "odt" 或 "odp"。
    输出：ExtractResult，content 为按文档顺序拼接的文本。
    异常：不抛——容器损坏或 XML 非法时返回降级结果并说明原因。
    """
    kind = "ODT 文本文档" if ext == "odt" else "ODP 演示文稿"
    try:
        with zipfile.ZipFile(path) as archive:
            content_xml = archive.read("content.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        return ExtractResult(
            content="",
            engines=["标准库"],
            degraded=True,
            degrade_reason=f"{kind}容器解析失败: {exc}",
        )

    try:
        root = ET.fromstring(content_xml)
    except ET.ParseError as exc:
        return ExtractResult(
            content="",
            engines=["标准库"],
            degraded=True,
            degrade_reason=f"{kind}的 content.xml 解析失败: {exc}",
        )

    lines = []
    heading_count = 0
    for element in root.iter():
        if element.tag not in TEXT_ELEMENTS:
            continue
        # itertext 会把 text:span 等内联元素里的文字一起带出来，正文不会因分片而丢字
        text = "".join(element.itertext()).strip()
        if not text:
            continue
        if element.tag == TEXT_NS + "h":
            level = element.get(TEXT_NS + "outline-level", "1")
            try:
                marks = "#" * max(1, min(6, int(level)))
            except ValueError:
                marks = "#"
            lines.append(f"{marks} {text}")
            heading_count += 1
        else:
            lines.append(text)

    notes = []
    if heading_count:
        notes.append(f"含 {heading_count} 个标题，已按层级标为 Markdown 记号")
    if not lines:
        notes.append("文档里没有可提取的文字段落")
    return ExtractResult(content="\n\n".join(lines), engines=["ElementTree"], notes=notes)
