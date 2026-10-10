# -*- coding: utf-8 -*-
"""
文件字节级的两个小工具：文本解码与文件头预览。

两件事都不属于某一种格式，却被多用在：
- `read_text_file` —— plaintext / html / excel / image(SVG) 四种格式共用；
- `read_header_hex` —— 降级产出（`step4_extract.py`）与安装包指纹（`processors/install_package.py`）共用。

放在这里是为了不让格式文件之间互相 import：`excel.py` 去 import `plaintext.py` 会让人以为
「Excel 依赖纯文本处理」，而它们只是都需要一个解码器。

`read_text_file` 把**命中上界的事实**与文本一起返回（而不是自己记进某个 result）：它是工具函数，
调用方才知道往哪报；`configs/limits.py` 的 `max_text_bytes` 因此有了报告链路。
"""
from pathlib import Path
from typing import List, Tuple

import chardet

import configs.limits as limits

# 文件头前若干字节的十六进制预览长度
HEADER_PREVIEW_BYTES = 16

def read_text_file(path: Path) -> Tuple[str, List[str]]:
    """
    读取文本文件，自动处理编码。

    功能：先按 utf-8-sig 尝试（吃掉 Windows BOM），失败再用 chardet 探测一次，
          最后退回 utf-8 + errors=replace，保证不抛异常。
    应用场景：txt / csv / md / json / xml / html 的落地读盘，多处共用。
    输入：path — 文件路径。
    输出：(解码后的文本, 命中的上界名列表)。超过 `limits.MAX_TEXT_BYTES` 时按上限截读，
          并把 `max_text_bytes` 放进第二个返回值——调用方负责报进 `limits_hit`。
    """
    with path.open("rb") as handle:
        raw = handle.read(limits.MAX_TEXT_BYTES + 1)
    hits = ["max_text_bytes"] if len(raw) > limits.MAX_TEXT_BYTES else []
    raw = raw[: limits.MAX_TEXT_BYTES]
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace"), hits
    try:
        return raw.decode("utf-8"), hits
    except UnicodeDecodeError:
        pass
    detected = chardet.detect(raw[:65536]).get("encoding")
    if detected:
        try:
            return raw.decode(detected, errors="replace"), hits
        except (LookupError, UnicodeDecodeError):
            pass
    return raw.decode("utf-8", errors="replace"), hits

def read_header_hex(path: Path) -> str:
    """
    读取文件头并转成十六进制预览串。

    功能：读固定长度字节，转成 "50 4B 03 04" 形式；读不到时返回空串。
    应用场景：安装包指纹分析与降级产出的现场证据，两处共用。
    输入：path — 文件路径。
    输出：十六进制串（大写、单空格分隔）。
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(HEADER_PREVIEW_BYTES)
    except OSError:
        return ""
    return " ".join(f"{byte:02X}" for byte in head)
