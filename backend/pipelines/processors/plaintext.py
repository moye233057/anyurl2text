# -*- coding: utf-8 -*-
"""
纯文本类格式的处理器：txt / md / json / xml / rtf。

设计要点（前三条都是「尽可能保留真实信息」这一产品目标的要求）：
- Markdown 原样返回，不转成 HTML 再剥标签——那样标题层级、列表、表格、链接全丢；
  Markdown 本来就是文本，再加工只会掉信息。
- JSON 缩进美化后返回，信息量不变但可读；原样透传的话长单行没法读。
- XML 按结构缩进输出，**保留属性**；只递归拼 text 与 tail 会把属性全丢掉。
- RTF 用控制字解析器，不用正则剥控制字——正则会连 `\\uN` 转义一起丢掉，
  结果是中文 RTF 抽出来几乎没有内容。（解析器在 `rtf.py`，解码在 `textfile.py`。）
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from pipelines.processors.rtf import extract_rtf, rtf_to_text
from pipelines.processors.schema import ExtractResult
from utils.textfile import read_text_file

def _pretty_json(text: str) -> str:
    """JSON 缩进美化；解析失败时原样返回，不因格式问题丢内容。"""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(parsed, ensure_ascii=False, indent=2)

def _pretty_xml(text: str) -> str:
    """XML 缩进输出并保留属性；解析失败时原样返回。"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return text
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")

def extract_plaintext(path: Path, ext: str) -> ExtractResult:
    """
    抽取纯文本类格式。

    功能：txt 直读；md 原样；json 缩进美化；xml 结构化缩进；rtf 走控制字解析（表格还原成网格列）。
          五种都保留原始信息，不做会掉内容的加工。
    应用场景：dispatch 分派 plaintext 组时调用。
    输入：path — 文件路径；ext — 已识别的小写扩展名。
    输出：ExtractResult，content 为文本本身。
    说明：rtf 在**读盘之前**分出去——`extract_rtf` 自己读一次盘（表格要按控制字重建网格，
          顺带把表头行数与 `max_table_rows` 带回结果），这里再读一次纯属浪费。
    """
    if ext == "rtf":
        return extract_rtf(path)
    text, hits = read_text_file(path)
    if ext == "json":
        return ExtractResult(content=_pretty_json(text), engines=["json"], limits_hit=hits)
    if ext == "xml":
        return ExtractResult(content=_pretty_xml(text), engines=["ElementTree"], limits_hit=hits)
    return ExtractResult(content=text, engines=["原文直读"], limits_hit=hits)
