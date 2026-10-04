# -*- coding: utf-8 -*-
"""
OLE 复合文档的目录流名读取（只读，纯标准库）。

为什么单独成文件：`step2_detect.py` 贴着单文件预算，而这段要按 OLE 规范跟 FAT 链走目录扇区，
放进去会顶穿当时的代码行上限（判据 2，2026-10-04 已从 150 抬到 300）。本模块不 import 项目内
任何东西（叶子模块），任何层都能用。

读法（OLE 头就是文件前 512 字节）：偏移 30 是扇区大小以 2 为底的对数，偏移 44 是 FAT 扇区个数，
偏移 48 是目录流首个扇区号，偏移 76 起是 109 个 FAT 扇区号；目录项 128 字节、名字是 UTF-16LE。
目录流常跨多个扇区，所以**必须**按 FAT 链一节节走——只看第一节会漏掉后面的条目。

任何解析异常都吞掉并返回空结果：识别这一层绝不允许抛异常（ADR-0003，降级是正常结果）。
"""
import struct
from pathlib import Path
from typing import List, Optional

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREE_SECTOR = 0xFFFFFFFF
END_OF_CHAIN = 0xFFFFFFFE
DIRECTORY_ENTRY_SIZE = 128
# 目录流最多走多少节：FAT 链若成环，没有这个上限就会转到天荒地老
DIRECTORY_SECTOR_LIMIT = 64

# 目录流名 → 老版 Office 扩展名（ADR-0003：容器头分不出家族成员，要看容器里装了什么）
STREAM_EXTENSIONS = (
    ("WordDocument", "doc"),
    ("Workbook", "xls"),
    ("Book", "xls"),
    ("PowerPoint Document", "ppt"),
)

def _fat_table(handle, sector_size: int, header: bytes) -> List[int]:
    """读 OLE 头里 DIFAT 数组指向的 FAT 扇区，拼成一张表：扇区号 → 下一个扇区号。"""
    count = struct.unpack_from("<I", header, 44)[0]
    difat = struct.unpack_from("<109I", header, 76)
    table: List[int] = []
    for sector in difat[: min(count, len(difat))]:
        if sector == FREE_SECTOR:
            break
        handle.seek((sector + 1) * sector_size)
        chunk = handle.read(sector_size)
        if len(chunk) < sector_size:
            return []
        table.extend(struct.unpack(f"<{sector_size // 4}I", chunk))
    return table

def _directory_bytes(handle, sector_size: int, header: bytes) -> bytes:
    """按 FAT 链把目录流的全部扇区读出来拼在一起。"""
    fat = _fat_table(handle, sector_size, header)
    sector = struct.unpack_from("<I", header, 48)[0]
    chunks: List[bytes] = []
    while sector not in (END_OF_CHAIN, FREE_SECTOR) and len(chunks) < DIRECTORY_SECTOR_LIMIT:
        handle.seek((sector + 1) * sector_size)
        chunk = handle.read(sector_size)
        if len(chunk) < sector_size or sector >= len(fat):
            break
        chunks.append(chunk)
        sector = fat[sector]
    return b"".join(chunks)

def _entry_name(entry: bytes) -> str:
    """一个 128 字节目录项的名字；条目类型或名字长度不合法时返回空串。"""
    if entry[66] not in (1, 2, 5):  # 1 存储 / 2 流 / 5 根条目，其余按空条目跳过
        return ""
    name_bytes = struct.unpack_from("<H", entry, 64)[0]
    if not 2 <= name_bytes <= 64:
        return ""
    return entry[: name_bytes - 2].decode("utf-16-le", "ignore")

def stream_names(path: Path) -> List[str]:
    """
    读出 OLE 复合文档目录里的全部条目名（存储与流都算）。

    功能：按 FAT 链遍历目录扇区，逐个 128 字节目录项取 UTF-16LE 名字。
    应用场景：容器头只说明「这是个 OLE」，家族成员写在目录里，靠它分 doc / xls / ppt。
    输入：path — 已落盘的 OLE 候选文件。
    输出：名字列表；不是 OLE、结构损坏或读不出时返回 []，绝不抛异常。
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(512)
            if header[:8] != OLE_SIGNATURE:
                return []
            sector_size = 1 << struct.unpack_from("<H", header, 30)[0]
            if sector_size < DIRECTORY_ENTRY_SIZE:
                return []
            blob = _directory_bytes(handle, sector_size, header)
    except Exception:
        return []
    names = []
    for start in range(0, len(blob) - DIRECTORY_ENTRY_SIZE + 1, DIRECTORY_ENTRY_SIZE):
        name = _entry_name(blob[start : start + DIRECTORY_ENTRY_SIZE])
        if name:
            names.append(name)
    return names

def legacy_office_extension(path: Path) -> Optional[str]:
    """
    按 OLE 目录流名判老版 Office 家族成员。

    功能：`WordDocument` → doc，`Workbook` / `Book` → xls，`PowerPoint Document` → ppt。
    应用场景：`step2_detect.resolve_extension` 判到 OLE 头之后、退回扩展名与 Content-Type 之前调用。
    输入：path — 已落盘的 OLE 候选文件。
    输出：扩展名；读不出目录、或没有一个流名认识时返回 None，由调用方维持原退回逻辑。
    """
    names = set(stream_names(path))
    for stream, ext in STREAM_EXTENSIONS:
        if stream in names:
            return ext
    return None
