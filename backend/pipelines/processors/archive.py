# -*- coding: utf-8 -*-
"""
压缩包与安装包的抽取（全链路零模型，且不需要任何系统工具）。

设计要点：
- 条目清单进 content（每行一条），不塞进一句中文描述里：「这是一个压缩包，大小为
  12.34MB，里面包含了 a.txt、b.png 这些文件」这种句子机器没法解析，条目一多还会
  被截断成一句废话。
- 不解压内容，只读目录——这是有意的边界，压缩炸弹与递归解压的风险都在这里挡掉。
- RAR 走纯 Python 的 rarfile 库，不手写二进制解析；装不上时降级为
  「识别格式与加密标记」，不因为一个可选依赖让整类文件失败。
"""
import tarfile
import zipfile
from pathlib import Path

import configs.limits as limits
from pipelines.processors.schema import ExtractResult


def entries_to_content(entries: list, archive_kind: str) -> tuple:
    """把条目名列表转成 content 文本，超出上限时截断。返回 (content, 条目总数, 命中的上界名)。"""
    shown = entries[: limits.MAX_ENTRIES]
    header = f"# {archive_kind} 条目清单（共 {len(entries)} 条）"
    body = "\n".join(shown)
    hits = []
    if len(entries) > limits.MAX_ENTRIES:
        body += f"\n... 其余 {len(entries) - limits.MAX_ENTRIES} 条未列出"
        hits.append("max_entries")
    return f"{header}\n{body}", len(entries), hits

def extract_zip(path: Path) -> ExtractResult:
    """列出 ZIP 条目并检测密码保护。"""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        encrypted = any(info.flag_bits & 0x1 for info in infos)
        entries = [info.filename for info in infos]
    content, count, hits = entries_to_content(entries, "ZIP")
    notes = ["压缩包受密码保护，只能列出条目名" if encrypted else ""]
    return ExtractResult(
        content=content,
        engines=["zipfile"],
        notes=[note for note in notes if note],
        entry_count=count,
        limits_hit=hits,
    )

def extract_tar(path: Path) -> ExtractResult:
    """列出 tar（含 gz/bz2/xz 压缩）条目。"""
    entries = []
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            entries.append(member.name)
    content, count, hits = entries_to_content(entries, "TAR")
    return ExtractResult(content=content, engines=["tarfile"], entry_count=count, limits_hit=hits)

def extract_7z(path: Path) -> ExtractResult:
    """列出 7z 条目并检测密码保护。"""
    import py7zr

    with py7zr.SevenZipFile(str(path), mode="r") as archive:
        entries = archive.getnames()
        encrypted = archive.needs_password()
    content, count, hits = entries_to_content(entries, "7Z")
    notes = ["压缩包受密码保护，只能列出条目名" if encrypted else ""]
    return ExtractResult(
        content=content,
        engines=["py7zr"],
        notes=[note for note in notes if note],
        entry_count=count,
        limits_hit=hits,
    )

def extract_rar(path: Path) -> ExtractResult:
    """
    列出 RAR 条目（含加密标记）。

    rarfile 是纯 Python 的头解析实现，列目录不需要外部 unrar；装不上时降级为
    只报格式与加密标记，不因为一个可选依赖让整类文件失败。
    """
    try:
        import rarfile
    except ImportError:
        return ExtractResult(
            content="",
            engines=["基础文件信息"],
            notes=["未安装 rarfile，RAR 只能识别格式，无法列出条目"],
            degraded=True,
            degrade_reason="缺 rarfile 依赖，RAR 的目录列举不可用",
            degrade_code="rar_dependency_missing",
        )

    entries = []
    encrypted = False
    # 传 str 而不是 Path：rarfile 内部对文件名的处理不保证接受 os.PathLike
    with rarfile.RarFile(str(path)) as archive:
        for info in archive.infolist():
            entries.append(info.filename)
            if info.needs_password():
                encrypted = True
    content, count, hits = entries_to_content(entries, "RAR")
    notes = ["压缩包受密码保护，只能列出条目名" if encrypted else ""]
    return ExtractResult(
        content=content,
        engines=["rarfile"],
        notes=[note for note in notes if note],
        entry_count=count,
        limits_hit=hits,
    )

def extract_single_stream(path: Path, ext: str) -> ExtractResult:
    """
    处理单纯压缩流（.gz / .bz2 / .xz，不是 tar 包）。

    功能：这类文件只有一个成员，条目清单没有意义；改为只验证能否打开并声明这是单流压缩，
          不解压内容。
    应用场景：tarfile 尝试失败后由 dispatch 转来。
    输入：path — 文件路径；ext — 扩展名。
    输出：ExtractResult，content 为空，文件信息由 meta 承载，notes 说明这是什么。
    """
    import bz2
    import gzip
    import lzma

    notes = [f"单文件压缩流（.{ext}，不是 tar 包），只识别格式，未解压内容"]
    opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}.get(ext)
    if opener is not None:
        try:
            with opener(path, "rb") as handle:
                handle.read(1)
        except Exception as exc:
            notes.append(f"压缩流读取失败: {exc}")
    return ExtractResult(content="", engines=["标准库"], notes=notes, entry_count=0)

def extract_archive(path: Path, ext: str) -> ExtractResult:
    """
    按扩展名分派到具体归档解析器。

    功能：zip / tar 系 / 7z / rar / 单流压缩各走一路；tarfile 用 r:* 自动识别压缩。
    应用场景：dispatch 分派 archive 组时调用。
    输入：path — 文件路径；ext — 已识别的小写扩展名。
    输出：ExtractResult，content 为条目清单。
    异常：不抛——解析失败时返回降级结果并说明原因。
    """
    try:
        if ext == "zip":
            return extract_zip(path)
        if ext in ("7z",):
            return extract_7z(path)
        if ext == "rar":
            return extract_rar(path)
        if ext in ("tar", "tgz", "gz", "bz2", "tbz2", "xz", "txz"):
            try:
                return extract_tar(path)
            except tarfile.TarError:
                return extract_single_stream(path, ext)
    except Exception as exc:
        return ExtractResult(
            content="",
            engines=["基础文件信息"],
            degraded=True,
            degrade_reason=f"压缩包解析失败: {exc}",
            degrade_code="archive_parse_failed",
        )
    return ExtractResult(
        content="",
        engines=["基础文件信息"],
        degraded=True,
        degrade_reason=f"未支持的压缩格式: .{ext}",
        degrade_code="archive_unsupported",
    )
