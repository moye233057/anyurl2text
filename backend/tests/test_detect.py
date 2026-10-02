# -*- coding: utf-8 -*-
r"""
缝 B：类型识别与静态格式清单。

缝：`pipelines/steps/step2_detect` 的 `resolve_extension()` 与它用到的几个判定函数，
数据来自 `configs/formats.py`。纯函数，不联网、不起服务、不碰模型。

为什么选它：
- 它是「这个文件交给哪个处理器」的唯一决策点，判错就等于整条链走错分支。
- 容器格式（OLE / ZIP）要靠往里看一层才能定家族，这条逻辑最容易被改坏。
- 判型顺序在文档里写反过（`docs/PIPELINES.md` 与 `step2_detect.py` 自己的 docstring 都写成
  「文件头 → Content-Type → 扩展名」，实现是「文件头 → 名字扩展名 → Content-Type」），
  本文件把**实际顺序**钉住，避免再被改回去。

预期值来源：`configs/formats.py` 的清单本身 + 造出来的已知字节——不是跑一遍看输出。
"""
import zipfile

import pytest

from configs.formats import (
    AUDIO_EXTENSIONS,
    ARCHIVE_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    INSTALL_PACKAGE_EXTENSIONS,
    OFFICE_LEGACY_EXTENSIONS,
    VIDEO_EXTENSIONS,
    WEBPAGE_EXTENSIONS,
)
from pipelines.steps.step2_detect import (
    detect_magic,
    detect_zip_flavor,
    extension_from_mime,
    extension_from_name,
    modality_from_extension,
    resolve_extension,
)
from routers.convert.schema import Detector, Modality

OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16
PLAIN_TEXT = b"this file has no recognisable magic at all"


def write(work_dir, name: str, data: bytes):
    """把已知字节落盘，返回路径。"""
    path = work_dir / name
    path.write_bytes(data)
    return path


def make_zip(work_dir, name: str, entries, mimetype: str = ""):
    """造一个只带条目名的 ZIP——detect_zip_flavor 只读中央目录，不读内容。"""
    path = work_dir / name
    with zipfile.ZipFile(path, "w") as archive:
        if mimetype:
            archive.writestr("mimetype", mimetype)
        for entry in entries:
            archive.writestr(entry, "placeholder")
    return path


# ---------- 从名字里取扩展名 ----------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "pdf"),
        ("REPORT.PDF", "pdf"),
        ("https://host/a/b/report.pdf?token=1#frag", "pdf"),
        ("https://host/a/b/report.pdf;charset=utf-8", "pdf"),
        ("archive.tar.gz", "gz"),
        ("https://host/dir/", ""),
        ("noextension", ""),
        ("", ""),
        ("weird.thisoneiswaytoolong", ""),
        ("weird.ta$g", ""),
    ],
)
def test_extension_from_name_reads_the_tail_of_a_name_or_url(name, expected):
    """行为：从文件名或 URL 路径尾部取小写扩展名，剥掉 query / 锚点 / 分号参数与目录。"""
    assert extension_from_name(name) == expected


# ---------- 文件头 ----------

@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"%PDF-1.7\n%...", "pdf"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "jpg"),
        (b"GIF89a" + b"\x00" * 8, "gif"),
        (b"PK\x03\x04" + b"\x00" * 8, "zip"),
        (b"\x1f\x8b\x08" + b"\x00" * 8, "gz"),
        (b"7z\xbc\xaf\x27\x1c" + b"\x00" * 8, "7z"),
        (b"MZ\x90\x00" + b"\x00" * 8, "exe"),
        (OLE_HEAD, "ole"),
    ],
)
def test_detect_magic_reads_known_container_prefixes(head, expected):
    """行为：容器格式按文件头前缀认出扩展名。"""
    assert detect_magic(head) == expected


@pytest.mark.parametrize(
    ("flavor", "expected"),
    [(b"WAVE", "wav"), (b"WEBP", "webp"), (b"AVI ", "avi"), (b"XXXX", None)],
)
def test_detect_magic_distinguishes_riff_flavors(flavor, expected):
    """行为：RIFF 容器靠第 8-12 字节的子类型区分；子类型不认识就不猜。"""
    assert detect_magic(b"RIFF\x00\x00\x00\x00" + flavor) == expected


@pytest.mark.parametrize(
    ("brand", "expected"),
    [(b"heic", "heic"), (b"heix", "heic"), (b"M4A ", "m4a"), (b"M4B ", "m4a"), (b"isom", "mp4"), (b"mp42", "mp4")],
)
def test_detect_magic_distinguishes_ftyp_brands(brand, expected):
    """行为：ftyp 容器靠 major brand 分 HEIC / 纯音频 m4a / 视频 mp4。"""
    assert detect_magic(b"\x00\x00\x00\x18ftyp" + brand) == expected


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"<?xml version='1.0'?>", "xml"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'>", "svg"),
        (b"<!DOCTYPE html><html>", "html"),
        (b"<html lang='zh'>", "html"),
        (b"\xef\xbb\xbf<?xml version='1.0'?>", "xml"),
    ],
)
def test_detect_magic_recognises_the_text_family(head, expected):
    """行为：XML / SVG / HTML 都以 `<` 起头，按声明与根元素区分（含 BOM 的情况）。"""
    assert detect_magic(head) == expected


@pytest.mark.parametrize("head", [PLAIN_TEXT, b"PK", b"", b"RIFF\x00\x00\x00\x00XXXX"])
def test_detect_magic_returns_none_when_nothing_matches(head):
    """行为：认不出就返回 None，绝不猜。来源：`configs/formats.py`「认不出不猜」。"""
    assert detect_magic(head) is None


# ---------- ZIP 容器里的家族指纹 ----------

@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (["word/document.xml"], "docx"),
        (["xl/workbook.xml"], "xlsx"),
        (["ppt/presentation.xml"], "pptx"),
        (["AndroidManifest.xml"], "apk"),
        (["Payload/Demo.app/Info.plist"], "ipa"),
        (["readme.txt", "data/whatever.bin"], None),
    ],
)
def test_detect_zip_flavor_reads_root_level_markers(entries, expected, work_dir):
    """行为：ZIP 头分不出家族，靠根级部件指纹定 docx / xlsx / pptx / apk / ipa；认不出返回 None。"""
    assert detect_zip_flavor(make_zip(work_dir, "probe.zip", entries)) == expected


def test_detect_zip_flavor_reads_odf_mimetype_entry(work_dir):
    """行为：ODF 把文档种类写在根级 mimetype 条目里，据此分 odt / ods / odp。"""
    for mime, expected in (
        ("application/vnd.oasis.opendocument.text", "odt"),
        ("application/vnd.oasis.opendocument.spreadsheet", "ods"),
        ("application/vnd.oasis.opendocument.presentation", "odp"),
    ):
        path = make_zip(work_dir, f"{expected}.zip", [], mimetype=mime)
        assert detect_zip_flavor(path) == expected


def test_detect_zip_flavor_reports_xlsm_when_the_workbook_carries_macros(work_dir):
    """行为：同是 xlsx 家族，带 vbaProject.bin 的判成 xlsm（ADR-0003 的容器指纹之一）。"""
    path = make_zip(work_dir, "macro.zip", ["xl/workbook.xml", "xl/vbaProject.bin"])
    assert detect_zip_flavor(path) == "xlsm"


def test_broken_zip_falls_back_instead_of_raising(work_dir):
    """行为：坏包 / 加密包读不出指纹时返回 None，由压缩包那条路去说明原因，不在这里报错。"""
    path = write(work_dir, "broken.zip", b"PK\x03\x04" + b"\x00" * 40)
    assert detect_zip_flavor(path) is None


# ---------- Content-Type ----------

@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("application/pdf", "pdf"),
        ("application/pdf; charset=utf-8", "pdf"),
        ("APPLICATION/PDF", "pdf"),
        ("text/plain", "txt"),
        ("application/octet-stream", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_extension_from_mime_strips_parameters_and_ignores_unknown(mime, expected):
    """行为：只取分号前的主类型查表；查不到返回空串，不当成失败。"""
    assert extension_from_mime(mime) == expected


# ---------- 判型顺序（本文件的核心） ----------

def test_magic_wins_over_name_and_content_type(work_dir):
    """行为：文件头最硬——名字与 Content-Type 都撒谎时按文件头走。"""
    path = write(work_dir, "looks-like.docx", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    ext, detector = resolve_extension(path, "looks-like.docx", "text/plain", b"\x89PNG\r\n\x1a\n")
    assert (ext, detector) == ("png", Detector.MAGIC)


def test_name_wins_over_content_type_when_magic_is_silent(work_dir):
    """
    行为：文件头认不出时，**名字扩展名优先于 Content-Type**。

    这条钉的是实际顺序（`resolve_extension` 里先查名字、再查 Content-Type）。
    文档一度写成 Content-Type 优先，是错的——CDN 常给 application/octet-stream，
    而 URL 路径后缀通常更可信；改动这里等于改动判型语义，必须同步文档。
    """
    path = write(work_dir, "data.csv", PLAIN_TEXT)
    ext, detector = resolve_extension(path, "data.csv", "text/html", PLAIN_TEXT)
    assert (ext, detector) == ("csv", Detector.EXTENSION)


def test_content_type_is_the_last_resort(work_dir):
    """行为：名字里没有扩展名时才用 Content-Type 兜底。"""
    path = write(work_dir, "download", PLAIN_TEXT)
    ext, detector = resolve_extension(path, "download", "application/pdf", PLAIN_TEXT)
    assert (ext, detector) == ("pdf", Detector.CONTENT_TYPE)


def test_unknown_when_nothing_matches(work_dir):
    """行为：三条依据都不成立时返回空扩展名与 unknown，交给降级路径。"""
    path = write(work_dir, "download", PLAIN_TEXT)
    assert resolve_extension(path, "download", None, PLAIN_TEXT) == ("", Detector.UNKNOWN)
    assert resolve_extension(path, "download", "application/octet-stream", PLAIN_TEXT) == ("", Detector.UNKNOWN)


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("a.doc", None, ("doc", Detector.EXTENSION)),
        ("a.xls", None, ("xls", Detector.EXTENSION)),
        ("a.ppt", None, ("ppt", Detector.EXTENSION)),
        ("a.wps", None, ("wps", Detector.EXTENSION)),
        ("blob", "application/msword", ("doc", Detector.CONTENT_TYPE)),
        ("blob", "application/vnd.ms-excel", ("xls", Detector.CONTENT_TYPE)),
        ("blob", "application/vnd.ms-powerpoint", ("ppt", Detector.CONTENT_TYPE)),
        ("blob", None, ("ole", Detector.MAGIC)),
    ],
)
def test_ole_head_defers_the_family_split_to_name_or_content_type(name, mime, expected, work_dir):
    """
    行为：OLE 头分不出 doc / xls / ppt / wps，交给名字扩展名、再交给 Content-Type；
    两者都没有才维持「这是个 OLE」。来源：ADR-0003「容器头常常分不出家族成员」。
    """
    path = write(work_dir, "ole.bin", OLE_HEAD)
    assert resolve_extension(path, name, mime, OLE_HEAD) == expected


@pytest.mark.parametrize(
    ("entries", "expected"),
    [(["word/document.xml"], "docx"), (["readme.txt"], "zip")],
)
def test_zip_head_looks_one_level_inside_the_container(entries, expected, work_dir):
    """行为：ZIP 头一律往里看一层；认不出家族才维持「这是个 ZIP」，此时名字说了不算。"""
    make_zip(work_dir, "container.zip", entries)
    path = work_dir / "container.zip"
    assert resolve_extension(path, "container.docx", None, b"PK\x03\x04") == (expected, Detector.MAGIC)


# ---------- 扩展名 → 模态 ----------

@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        ("pdf", Modality.DOCUMENT),
        ("docx", Modality.DOCUMENT),
        ("csv", Modality.DOCUMENT),
        ("html", Modality.WEBPAGE),
        ("png", Modality.IMAGE),
        ("svg", Modality.IMAGE),
        ("mp3", Modality.AUDIO),
        ("mp4", Modality.VIDEO),
        ("zip", Modality.ARCHIVE),
        ("apk", Modality.INSTALL_PACKAGE),
        ("exe", Modality.INSTALL_PACKAGE),
        ("xyz", Modality.FILE),
        ("", Modality.FILE),
    ],
)
def test_modality_from_extension(ext, expected):
    """行为：扩展名归到对外模态之一；认不出的落 file（基础文件信息）。"""
    assert modality_from_extension(ext) is expected


def test_every_declared_extension_maps_to_a_real_modality():
    """行为：清单里声明过的每个扩展名都要能归到一个具体模态，不能落 file。"""
    declared = (
        DOCUMENT_EXTENSIONS
        | WEBPAGE_EXTENSIONS
        | IMAGE_EXTENSIONS
        | AUDIO_EXTENSIONS
        | VIDEO_EXTENSIONS
        | ARCHIVE_EXTENSIONS
        | INSTALL_PACKAGE_EXTENSIONS
    )
    fallen = sorted(ext for ext in declared if modality_from_extension(ext) is Modality.FILE)
    assert not fallen, f"这些扩展名没归到模态：{fallen}"


def test_legacy_office_is_a_degraded_family_not_a_document_family():
    """
    行为：doc / ppt / wps 不在文档家族里，模态落 file。

    来源：ADR-0003——它们是「缺依赖所以降级」的那一类，不是「完整解析」的那一类；
    混进 DOCUMENT_EXTENSIONS 会让 README 的承诺与实现不一致。
    """
    for ext in sorted(OFFICE_LEGACY_EXTENSIONS):
        assert ext not in DOCUMENT_EXTENSIONS, f"{ext} 不该算文档家族"
        assert modality_from_extension(ext) is Modality.FILE
