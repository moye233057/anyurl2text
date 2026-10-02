# -*- coding: utf-8 -*-
"""
步骤 2：类型识别——决定一个文件交给哪个处理器（两条路共用）。

识别顺序固定为 文件头 → Content-Type → 扩展名：
文件头最可靠但只覆盖容器格式，认不出时退回 Content-Type，最后才用最容易撒谎的扩展名
（URL 常无后缀、CDN 常给 application/octet-stream）。

容器头只能定到「容器」，还要往里看一层：OLE 头分不出 doc / xls / ppt，
ZIP 头分不出普通压缩包与 docx / xlsx / pptx / odt / ods / odp / apk / ipa 这一族，
两者都在 resolve_extension 里结合容器内部结构落到具体格式。

格式清单、降级分类与 ZIP 指纹表在 `configs/formats.py`（纯数据，不含函数）；
降级在这里判定，不让下游各写一份。
"""
import re
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from configs.formats import (
    ARCHIVE_EXTENSIONS,
    AUDIO_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    EXTENSION_BY_MIME,
    HEIC_BRANDS,
    IMAGE_EXTENSIONS,
    INSTALL_PACKAGE_EXTENSIONS,
    IPA_PLIST_SUFFIX,
    M4A_BRANDS,
    MAGIC_PREFIXES,
    ODF_EXTENSION_BY_MIMETYPE,
    RIFF_FLAVORS,
    VIDEO_EXTENSIONS,
    WEBPAGE_EXTENSIONS,
    ZIP_ENTRY_FLAVORS,
    ZIP_MACRO_ENTRY,
)
from routers.convert.schema import Detector, Modality

# 复合归档后缀要整体判断，否则 .tar.gz 会被当成 .gz
_COMPOUND_SUFFIXES = ("tar.gz", "tar.bz2", "tar.xz")

def read_head(path: Path, size: int = 32) -> bytes:
    """读文件头若干字节，供魔数识别与容器 ZIP 指纹判定。"""
    with path.open("rb") as handle:
        return handle.read(size)


def extension_from_name(name: str) -> str:
    """
    从文件名或 URL 路径里取扩展名。

    功能：去掉 query、分号参数与目录层级，返回小写扩展名（不含点）。
          复合后缀（tar.gz 等）归一到尾部一段，便于和归档表对齐。
    应用场景：识别第一步；上传文件与 URL 共用。
    输入：name — 文件名或 URL 路径。
    输出：扩展名；取不到时返回空串。
    """
    if not name:
        return ""
    path = name.split("?", 1)[0].split("#", 1)[0].split(";", 1)[0]
    path = path.rstrip("/")
    last = path.rsplit("/", 1)[-1].lower()
    for suffix in _COMPOUND_SUFFIXES:
        if last.endswith("." + suffix):
            return suffix.rsplit(".", 1)[-1]
    if "." not in last:
        return ""
    ext = last.rsplit(".", 1)[-1]
    if not ext or len(ext) > 10 or not re.fullmatch(r"[a-z0-9_+-]+", ext):
        return ""
    return ext

def detect_magic(head: bytes) -> Optional[str]:
    """
    按文件头判断扩展名。

    功能：先认 RIFF 与 ftyp 这两个「要靠子类型区分」的容器，再按 `configs/formats.MAGIC_PREFIXES`
          的前缀表认其余容器格式，最后处理文本类（XML / SVG / HTML 都以 `<` 起头）。
          认不出返回 None，不做猜测。
    应用场景：扩展名缺失或扩展名明显不可信时调用。
    输入：head — 文件前若干字节（建议 >= 16）。
    输出：扩展名；认不出返回 None。
    """
    if len(head) < 4:
        return None
    # RIFF 容器：靠第 8-12 字节的子类型区分；子类型不认识就走后面的判断
    if head[:4] == b"RIFF":
        flavor = RIFF_FLAVORS.get(head[8:12])
        if flavor:
            return flavor
    # HEIC/HEIF、纯音频 MP4（m4a）与视频 MP4 都是 ftyp 容器，靠 major brand 区分
    if head[4:8] == b"ftyp":
        if head[8:12] in HEIC_BRANDS:
            return "heic"
        return "m4a" if head[8:12] in M4A_BRANDS else "mp4"
    for prefix, ext in MAGIC_PREFIXES:
        if head.startswith(prefix):
            return ext
    # 文本类兜底：XML / SVG / HTML 都可能以 `<` 起头，按声明与根元素区分。
    # 窗口只有几十字节，所以只认开头那一段；更细的分辨交给扩展名与 Content-Type。
    probe = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if probe.startswith(b"<?xml"):
        return "xml"
    if probe.startswith(b"<svg"):
        return "svg"
    if probe[:1] in (b"<", b"<!") or probe[:5] == b"<!doc":
        return "html"
    return None

def detect_zip_flavor(path: Path) -> Optional[str]:
    """
    按 ZIP 容器内部的条目指纹判断它到底是什么格式。

    功能：只读中央目录（不解压内容）判家族——OOXML 认 word / xl / ppt 下的固定部件，
          带 vbaProject.bin 的判成 xlsm；ODF 读根级 mimetype 条目；APK 认
          AndroidManifest.xml；IPA 认 Payload/*.app/Info.plist。
    应用场景：resolve_extension 判到 ZIP 魔数后调用，避免整族文档被当成压缩包只列条目。
    输入：path — 已落盘的候选 ZIP 文件。
    输出：真实扩展名；不是已知格式、容器损坏或读不出条目时返回 None，由调用方维持 zip。
          读不出来不在这里报错：加密包与坏包本来就该按压缩包的路径处理，由那边说明原因。
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for entry, ext in ZIP_ENTRY_FLAVORS:
                if entry not in names:
                    continue
                if ext == "xlsx" and ZIP_MACRO_ENTRY in names:
                    return "xlsm"
                return ext
            if "mimetype" in names:
                mime = archive.read("mimetype").decode("ascii", "ignore").strip()
                return ODF_EXTENSION_BY_MIMETYPE.get(mime)
            if any(name.startswith("Payload/") and name.endswith(IPA_PLIST_SUFFIX) for name in names):
                return "ipa"
    except Exception:
        return None
    return None

def modality_from_extension(ext: str) -> Modality:
    """
    扩展名 → 模态。

    功能：把扩展名归到对外模态之一；认不出的落 FILE（基础文件信息）。
    应用场景：识别完成后确定响应 type 与处理器分派。
    输入：ext — 小写扩展名。
    输出：Modality。
    """
    if ext in DOCUMENT_EXTENSIONS:
        return Modality.DOCUMENT
    if ext in WEBPAGE_EXTENSIONS:
        return Modality.WEBPAGE
    if ext in IMAGE_EXTENSIONS:
        return Modality.IMAGE
    if ext in AUDIO_EXTENSIONS:
        return Modality.AUDIO
    if ext in VIDEO_EXTENSIONS:
        return Modality.VIDEO
    if ext in ARCHIVE_EXTENSIONS:
        return Modality.ARCHIVE
    if ext in INSTALL_PACKAGE_EXTENSIONS:
        return Modality.INSTALL_PACKAGE
    return Modality.FILE

def extension_from_mime(mime: Optional[str]) -> str:
    """
    Content-Type → 扩展名提示。

    功能：取分号前的主类型查表；查不到返回空串。
    应用场景：URL 无扩展名时补候选扩展名，作为落盘名与初步模态判断。
    输入：mime — 原始 Content-Type。
    输出：扩展名或空串。
    """
    if not mime:
        return ""
    return EXTENSION_BY_MIME.get(mime.split(";", 1)[0].strip().lower(), "")

def resolve_extension(
    path: Path,
    name: str,
    mime: Optional[str],
    head: bytes,
) -> Tuple[str, Detector]:
    """
    综合文件头、Content-Type 与扩展名得出最终扩展名。

    功能：按「文件头优先于 Content-Type 优先于名字扩展名」定序；
          文件头只认容器格式，认不出时退回另两者；
          两类容器还要再看一层内部结构——OLE 头靠扩展名或 Content-Type 分
          doc / xls / ppt，ZIP 靠容器条目指纹分 docx / xlsx / pptx / odt / apk 一族（ADR 0003）。
    应用场景：fetch 落盘后、分派处理器之前调用一次。
    输入：path — 已落盘的文件；name — 原始文件名或 URL；mime — 响应 Content-Type；
          head — 文件前若干字节。
    输出：(扩展名, 识别方式)；都认不出时返回 ("", Detector.UNKNOWN)。
    """
    magic_ext = detect_magic(head)
    if magic_ext == "ole":
        # OLE 头无法区分 doc/xls/ppt，交给扩展名或 Content-Type 决定
        name_ext = extension_from_name(name)
        if name_ext in ("doc", "xls", "ppt", "wps"):
            return name_ext, Detector.EXTENSION
        mime_ext = extension_from_mime(mime)
        if mime_ext in ("doc", "xls", "ppt"):
            return mime_ext, Detector.CONTENT_TYPE
        return "ole", Detector.MAGIC
    if magic_ext == "zip":
        # ZIP 头同样分不出家族成员，要看容器里装了哪些部件；认不出才维持「这是个 ZIP」
        return detect_zip_flavor(path) or "zip", Detector.MAGIC
    if magic_ext:
        return magic_ext, Detector.MAGIC

    name_ext = extension_from_name(name)
    if name_ext:
        # 认得出扩展名就用它，哪怕它不在覆盖表里——那种情况的结论是「降级」，也一样要报出来
        return name_ext, Detector.EXTENSION

    mime_ext = extension_from_mime(mime)
    if mime_ext:
        return mime_ext, Detector.CONTENT_TYPE
    return "", Detector.UNKNOWN
