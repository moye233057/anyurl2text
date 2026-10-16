# -*- coding: utf-8 -*-
r"""
B 组：类型识别与静态格式清单。

测的是：`pipelines/steps/step2_detect` 的 `resolve_extension()` 与它用到的几个判定函数，
数据来自 `configs/formats.py`。纯函数，不联网、不起服务、不碰模型。

为什么选它：
- 它是「这个文件交给哪个处理器」的唯一决策点，判错就等于整条链走错分支。
- 容器格式（OLE / ZIP）要靠往里看一层才能定家族，这条逻辑最容易被改坏。
- 判型顺序在文档里写反过（`docs/pipelines.md` 与 `step2_detect.py` 自己的 docstring 都写成
  「文件头 → Content-Type → 扩展名」，实现是「文件头 → 名字扩展名 → Content-Type」），
  本文件把**实际顺序**钉住，避免再被改回去。

预期值来源：`configs/formats.py` 的清单本身 + 造出来的已知字节——不是跑一遍看输出。
"""
import struct
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
from pipelines.steps.ole_directory import stream_names
from pipelines.steps.step2_detect import (
    detect_magic,
    detect_zip_flavor,
    extension_from_mime,
    extension_from_name,
    modality_from_extension,
    read_head,
    resolve_extension,
)
from routers.convert.schema import Detector, Modality

# 能力类别见 AGENTS.md「测试用例怎么写」；按类别跑：python -m pytest tests -m capability_detect
pytestmark = pytest.mark.capability_detect

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
        (["META-INF/container.xml"], "epub"),
        (["OFD.xml"], "ofd"),
        (["readme.txt", "data/whatever.bin"], None),
    ],
)
def test_detect_zip_flavor_reads_root_level_markers(entries, expected, work_dir):
    """行为：ZIP 头分不出家族，靠容器内固定部件指纹定 docx / xlsx / pptx / apk / ipa / epub / ofd；认不出返回 None。"""
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


def test_epub_fingerprint_does_not_swallow_the_archive_family(work_dir):
    """
    行为：EPUB 指纹只认带 `META-INF/container.xml` 的 ZIP；普通 ZIP 仍判成 zip，
          走压缩包那一路——哪怕它的名字是 `.epub`。

    来源：ADR-0003「容器格式必须往里看一层」的反面约束——往里看一层不等于把归档族吃掉；
    `docs/pipelines.md` §2.2 的 ZIP 行「以上都不命中或容器读不出 → 维持 zip」。
    预期值来源：指纹本身（EPUB 规范强制每个 EPUB 都带 container.xml，普通 ZIP 不会有这个路径）。
    """
    make_zip(work_dir, "plain.zip", ["readme.txt", "data/table.csv"])
    path = work_dir / "plain.zip"
    assert detect_zip_flavor(path) is None
    assert resolve_extension(path, "plain.zip", None, b"PK\x03\x04") == ("zip", Detector.MAGIC)
    # 名字与 Content-Type 都说是 epub，但容器里没有那个部件——维持 zip，名字说了不算
    assert resolve_extension(path, "not-really.epub", "application/epub+zip", b"PK\x03\x04") == (
        "zip",
        Detector.MAGIC,
    )


# ---------- Content-Type ----------

@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("application/pdf", "pdf"),
        ("application/pdf; charset=utf-8", "pdf"),
        ("APPLICATION/PDF", "pdf"),
        ("text/plain", "txt"),
        ("text/tab-separated-values", "tsv"),
        ("application/epub+zip", "epub"),
        ("application/ofd", "ofd"),
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


# ---------- OLE 目录里的流名（容器头之外的第二层依据） ----------

OLE_SECTOR = 512
OLE_ENTRY_KINDS = {"storage": 1, "stream": 2, "root": 5}


def ole_entry(name: str, kind: str = "stream") -> bytes:
    """造一个 128 字节目录项：识别只读名字与条目类型，其余字段按规范填占位值。"""
    raw = name.encode("utf-16-le") + b"\x00\x00"
    entry = bytearray(128)
    entry[0 : len(raw)] = raw
    struct.pack_into("<H", entry, 64, len(raw))                       # 名字字节数（含结束符）
    entry[66] = OLE_ENTRY_KINDS[kind]                                 # 条目类型
    entry[67] = 1                                                     # 红黑树颜色位
    struct.pack_into("<III", entry, 68, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)   # 无左右兄弟、无子
    struct.pack_into("<I", entry, 116, 0xFFFFFFFE)                    # 起始扇区：本用例不读流内容
    return bytes(entry)


def make_ole(work_dir, name: str, streams: list, split: bool = False):
    """
    造一个最小 OLE 复合文档：头（512 字节）+ FAT 扇区 + 目录扇区。

    布局固定为「扇区 0 = FAT，扇区 1 起是目录流」；`split=True` 时把目录拆成两节并让 FAT 链
    从第 1 节指向第 2 节——每扇区只装 4 个 128 字节目录项，所以第 5 个条目必然落在第二节。
    """
    directory = [ole_entry("Root Entry", "root")] + [ole_entry(text) for text in streams]
    if split:
        sectors = [b"".join(directory[:4]), b"".join(directory[4:])]
        while sectors[-1] == b"":
            sectors.pop()
    else:
        sectors = [b"".join(directory)]
    # 每个扇区都要补齐到整扇区：OLE 里扇区是定长的，截短的目录扇区不是合法容器
    sectors = [chunk.ljust(OLE_SECTOR, b"\x00") for chunk in sectors]

    fat = [0xFFFFFFFD]                                     # 第 0 节是 FAT 自己
    for index in range(len(sectors)):
        fat.append(index + 2 if index + 1 < len(sectors) else 0xFFFFFFFE)
    fat_sector = b"".join(struct.pack("<I", value) for value in fat).ljust(OLE_SECTOR, b"\xff")

    header = bytearray(OLE_SECTOR)
    header[0:8] = OLE_HEAD[:8]
    struct.pack_into("<H", header, 28, 0xFFFE)             # 字节序标记
    struct.pack_into("<H", header, 30, 9)                  # 扇区大小 = 2^9 = 512
    struct.pack_into("<H", header, 32, 6)                  # 迷你扇区大小 = 2^6
    struct.pack_into("<I", header, 44, 1)                  # FAT 占 1 节
    struct.pack_into("<I", header, 48, 1)                  # 目录流首个扇区号
    struct.pack_into("<I", header, 56, 4096)               # 迷你流阈值
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)         # 没有迷你 FAT
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)         # 没有 DIFAT 链
    struct.pack_into("<I", header, 76, 0)                  # DIFAT[0] 指向扇区 0
    for offset in range(80, OLE_SECTOR, 4):
        struct.pack_into("<I", header, offset, 0xFFFFFFFF)  # 其余 DIFAT 项空闲

    path = work_dir / name
    path.write_bytes(bytes(header) + fat_sector + b"".join(sectors))
    return path


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        ("WordDocument", "doc"),
        ("Workbook", "xls"),
        ("Book", "xls"),
        ("PowerPoint Document", "ppt"),
    ],
)
def test_ole_directory_stream_names_split_the_family(stream, expected, work_dir):
    """
    行为：OLE 头本身分不出家族，读目录里的流名——WordDocument → doc、Workbook / Book → xls、
          PowerPoint Document → ppt；此时名字扩展名说了不算。

    来源：ADR-0003「容器格式必须往里看一层，否则完整解析是空头承诺」。
    预期值来源：ADR-0003 与 `ole_directory.py` 写明的流名映射，不是跑一遍看输出。
    """
    path = make_ole(work_dir, "payload.bin", [stream])
    assert resolve_extension(path, "payload.bin", None, OLE_HEAD) == (expected, Detector.MAGIC)


def test_ole_directory_walks_the_whole_fat_chain(work_dir):
    """
    行为：目录流跨扇区时后面的扇区也要走到——流名落在第二节目录扇区里照样认得出。

    来源：OLE 规范（头里只有目录流**首节**，后续节靠 FAT 链）+ `ole_directory._directory_bytes`。
    预期值来源：本用例自己拼的容器布局（5 个条目、每扇区 4 项，所以 Workbook 必在第 2 节）；
    只看第一节的实现会读不到它、退回扩展名而返回 ole。
    """
    path = make_ole(work_dir, "chained.bin", ["Data1", "Data2", "Data3", "Workbook"], split=True)
    assert resolve_extension(path, "chained.bin", None, OLE_HEAD) == ("xls", Detector.MAGIC)


@pytest.mark.parametrize(
    ("streams", "name", "mime", "expected"),
    [
        (["Data"], "a.xls", None, ("xls", Detector.EXTENSION)),
        (["Data"], "blob", "application/msword", ("doc", Detector.CONTENT_TYPE)),
        (["Data"], "blob", None, ("ole", Detector.MAGIC)),
        ([], "a.doc", None, ("doc", Detector.EXTENSION)),
    ],
)
def test_unrecognised_ole_directory_keeps_the_old_fallback(streams, name, mime, expected, work_dir):
    """
    行为：目录里没有一个认识的流名、或目录根本读不出时，维持旧退回逻辑
          （名字扩展名 → Content-Type → 维持 ole）；改名扩展名仍不可信。

    来源：ADR-0003 的既有行为——流名只是**多一层依据**，不许把现在能判对的弄坏。
    预期值来源：旧行为的映射表（流名认不出时的三条退回路径）。
    """
    path = make_ole(work_dir, "ole.bin", streams) if streams else write(work_dir, "ole.bin", OLE_HEAD)
    assert resolve_extension(path, name, mime, OLE_HEAD) == expected


def test_ole_reader_returns_nothing_instead_of_raising(work_dir):
    """
    行为：不是 OLE、被截断、空文件时 `stream_names` 返回空列表，绝不抛异常。

    来源：`ole_directory.py` 的约定——识别这一层不许把解析异常带出去（ADR-0003）。
    预期值来源：三种已知的坏输入，与实现无关。
    """
    assert stream_names(write(work_dir, "garbage.bin", PLAIN_TEXT)) == []
    assert stream_names(write(work_dir, "cut.bin", OLE_HEAD)) == []
    assert stream_names(write(work_dir, "empty.bin", b"")) == []


def test_the_repo_legacy_doc_sample_is_still_judged_doc(fixture_copy):
    """
    行为：仓库样例 `legacy.doc`（OLE 头 + 填充，没有可用目录）仍判成 doc——读不出目录就退回扩展名。

    来源：「不许把现在能判对的弄坏」+ 样例生成参数（`make_fixtures.py` 阶段 9 造的是 OLE 头样本，
    不是真的 Word 文档，所以它走的正是退回分支）。
    预期值来源：文件名 `.doc`。
    """
    path, _ = fixture_copy("legacy.doc")
    assert resolve_extension(path, path.name, None, read_head(path)) == ("doc", Detector.EXTENSION)


@pytest.mark.parametrize(
    ("entries", "expected"),
    [(["word/document.xml"], "docx"), (["META-INF/container.xml"], "epub"), (["OFD.xml"], "ofd"), (["readme.txt"], "zip")],
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
        ("tsv", Modality.DOCUMENT),
        ("epub", Modality.DOCUMENT),
        ("ofd", Modality.DOCUMENT),
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
