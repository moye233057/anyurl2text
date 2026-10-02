# -*- coding: utf-8 -*-
"""
RTF 控制字解析器：把 RTF 源码还原成纯文本。

按控制字逐字符解析，不用正则剥控制字——正则会把 `\\uN` 转义一起丢掉，中文 RTF 抽出来
几乎没有内容。字体表 / 颜色表 / 图片等可忽略目的地整组跳过；这个解析器单独成文件，
不和 txt / md / json / xml 那四条直读路径挤在一起。
"""

# RTF 里这些目的地装的是字体表、颜色表、图片、页眉页脚等，不是正文，遇到就整组跳过。
# 不跳过的话，字体表里的字体名会混进正文（这是「正则剥控制字」做法的典型症状）。
RTF_IGNORABLE_DESTINATIONS = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header",
    "footer", "footnote", "annotation", "fldinst", "listtable",
    "listoverridetable", "rsidtbl", "generator", "themedata",
    "colorschememapping", "latentstyles", "datastore", "xmlnstbl",
    "filetbl", "revtbl", "pntext", "nonshppict", "shppict",
})

def _rtf_at_group_start(source: str, backslash_pos: int) -> bool:
    """判断某个控制字是不是「某个组的第一个控制字」——可忽略目的地都是这种形态。"""
    cursor = backslash_pos - 1
    while cursor >= 0 and source[cursor] in " \t\r\n":
        cursor -= 1
    return cursor >= 0 and source[cursor] == "{"

def rtf_to_text(source: str) -> str:
    """
    把 RTF 源码转成纯文本。

    功能：处理 `\\uN`（含负值的 UTF-16 码位）与 `\\'hh` 十六进制转义，剥掉控制字与
          花括号，跳过字体表/颜色表/图片等可忽略目的地，`\\par` 还原成换行。
    应用场景：extract_plaintext 处理 .rtf 时调用。
    输入：source — RTF 源码文本。
    输出：还原出的纯文本。
    """
    pieces = []
    hex_buffer = bytearray()
    depth = 0
    skip_depth = None
    index = 0
    length = len(source)

    def flush_hex() -> None:
        """把累积的 \\'hh 字节按 utf-8 → gbk → cp1252 依次尝试解码。"""
        if not hex_buffer:
            return
        raw = bytes(hex_buffer)
        hex_buffer.clear()
        for encoding in ("utf-8", "gbk", "cp1252"):
            try:
                pieces.append(raw.decode(encoding))
                return
            except UnicodeDecodeError:
                continue
        pieces.append(raw.decode("latin-1"))

    while index < length:
        char = source[index]

        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            if skip_depth is not None and depth <= skip_depth:
                skip_depth = None
            depth = max(0, depth - 1)
            index += 1
            continue

        if char != "\\":
            if skip_depth is None:
                flush_hex()
                pieces.append(char)
            index += 1
            continue

        backslash_pos = index
        index += 1
        if index >= length:
            break
        symbol = source[index]

        if symbol == "'":
            try:
                hex_buffer.append(int(source[index + 1 : index + 3], 16))
            except ValueError:
                pass
            index += 3
            continue

        flush_hex()

        if symbol == "u":
            index += 1
            sign = 1
            if index < length and source[index] == "-":
                sign = -1
                index += 1
            digits = ""
            while index < length and source[index].isdigit():
                digits += source[index]
                index += 1
            # \uN 后面通常跟一个「当前代码页的替代字符」，必须跳过，否则正文会多出乱码
            if index < length and source[index] == "?":
                index += 1
            if digits and skip_depth is None:
                code = sign * int(digits)
                pieces.append(chr(code + 65536 if code < 0 else code))
            continue

        if symbol in "\\{}":
            if skip_depth is None:
                pieces.append(symbol)
            index += 1
            continue

        if symbol == "*":
            skip_depth = depth
            index += 1
            continue

        word = ""
        while index < length and source[index].isalpha():
            word += source[index]
            index += 1
        while index < length and (source[index].isdigit() or source[index] == "-"):
            index += 1
        # 控制字后面那一个空格是分隔符，不属于正文
        if index < length and source[index] == " ":
            index += 1

        if word in RTF_IGNORABLE_DESTINATIONS and _rtf_at_group_start(source, backslash_pos):
            skip_depth = depth
            continue
        if skip_depth is not None:
            continue
        if word in ("par", "line", "sect"):
            pieces.append("\n")
        elif word == "tab":
            pieces.append("\t")

    flush_hex()
    # 折叠连续空行，保留段落结构
    lines = [line.strip() for line in "".join(pieces).splitlines()]
    compact = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    return "\n".join(compact).strip()
