# -*- coding: utf-8 -*-
"""
ZIP 成员的限量读取：声明体积预检 + 流式封顶，挡住解压炸弹。

`ZipFile.read()` 会把整个成员解压进内存，且不累计跨成员的总量。
EPUB / OFD / ODF 都是「ZIP 里读若干 XML/XHTML」，共用这一处预算：

- 读前看 `ZipInfo.file_size`：声明已超过剩余额度就拒读（经典 zip bomb 声明巨大、压后很小）；
- 再用 `ZipFile.open()` 最多读「剩余 + 1」字节：声明撒谎、实际膨胀时也不会越过额度。

命中时抛 `ZipBudgetExceeded`；调用方决定是整份降级，还是停读后续成员并把
`max_zip_uncompressed_bytes` 记进 `limits_hit`（与 `textfile.read_text_file` 同一分工）。
"""
from __future__ import annotations

import zipfile
from typing import Optional

import configs.limits as limits


class ZipBudgetExceeded(Exception):
    """本次读会或已经超过解压字节预算。"""

    def __init__(self, name: str, declared: int, remaining: int) -> None:
        super().__init__(
            f"ZIP 成员 {name!r} 解压后会超过剩余额度"
            f"（声明 {declared} 字节，剩余 {remaining} 字节）"
        )
        self.name = name
        self.declared = declared
        self.remaining = remaining


class ZipReadBudget:
    """一次抽取请求内、跨成员累计的解压字节额度。"""

    def __init__(self, limit: Optional[int] = None) -> None:
        self.limit = limits.MAX_ZIP_UNCOMPRESSED_BYTES if limit is None else limit
        self.used = 0
        self.hit = False

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume(self, nbytes: int) -> None:
        self.used += nbytes


def read_member(archive: zipfile.ZipFile, name: str, budget: ZipReadBudget) -> bytes:
    """
    按预算读取一个 ZIP 成员。

    功能：声明体积超剩余额度 → 拒读；实际解压超剩余额度 → 拒读并标记命中。
    应用场景：epub / ofd / odf 打开容器后读 XML 或章节时调用。
    输入：archive — 已打开的 ZipFile；name — 成员路径；budget — 本次请求的累计额度。
    输出：成员的未压缩字节。
    异常：KeyError — 成员不存在（zipfile 原样抛）；ZipBudgetExceeded — 触达解压上界。
    """
    info = archive.getinfo(name)
    remaining = budget.remaining
    if remaining <= 0 or info.file_size > remaining:
        budget.hit = True
        raise ZipBudgetExceeded(name, info.file_size, remaining)
    with archive.open(name) as stream:
        data = stream.read(remaining + 1)
    if len(data) > remaining:
        budget.hit = True
        raise ZipBudgetExceeded(name, info.file_size, remaining)
    budget.consume(len(data))
    return data
