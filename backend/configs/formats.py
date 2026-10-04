# -*- coding: utf-8 -*-
"""
格式清单与降级分类：本项目的静态清单，改清单改这里，不在代码里散落字面量。

纯数据，不含函数——识别逻辑在 `pipelines/steps/step2_detect.py`，识别之后按清单分派到
`processors/`。覆盖范围以「纯 Python 库能否解析」为界（ADR 0003）。
"""

# ---------- 扩展名 → 模态 ----------

DOCUMENT_EXTENSIONS = frozenset({
    "pdf", "docx", "pptx", "xlsx", "xlsm", "xls", "csv", "ods", "odt", "odp",
    "epub", "rtf", "txt", "md", "markdown", "json", "xml",
})
WEBPAGE_EXTENSIONS = frozenset({"html", "htm"})
IMAGE_EXTENSIONS = frozenset({
    "png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "heic", "heif", "ico", "svg",
})
ARCHIVE_EXTENSIONS = frozenset({"zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz", "7z", "rar"})
INSTALL_PACKAGE_EXTENSIONS = frozenset({"apk", "ipa", "exe"})

# ---------- 音视频（ADR 0008）----------
# 音频由 soundfile 直接读结构信息；容器音频与视频要先抽音轨，用 imageio-ffmpeg 自带的 ffmpeg。

AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "aac", "flac", "ogg", "wma", "m4a"})
VIDEO_EXTENSIONS = frozenset({"mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "mpeg", "mpg"})
# libsndfile 不认这几个容器，和视频一样必须先抽音轨才能读
FFMPEG_ONLY_AUDIO_EXTENSIONS = frozenset({"m4a", "aac", "wma"})
# 两者归一类：处理链相同（拿到音轨 → 本机 ASR 服务转写），只是音轨的来源不同
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# ---------- 降级（ADR 0003）----------
# 音视频原本也在这一类里，接上 ASR 之后只剩老版 Office：它缺的是 LibreOffice 转换器。

OFFICE_LEGACY_EXTENSIONS = frozenset({"doc", "ppt", "wps"})

# ---------- Content-Type → 扩展名 ----------

# Content-Type → 扩展名提示。只在文件头认不出、名字又取不到扩展名时兜底用，
# 这时已经没有更可靠的依据，所以这里可以宽松。
EXTENSION_BY_MIME = {
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
    "text/html": "html",
    "application/json": "json",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/zip": "zip",
    "application/x-tar": "tar",
    "application/gzip": "gz",
    "application/x-7z-compressed": "7z",
    "application/vnd.rar": "rar",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/epub+zip": "epub",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/svg+xml": "svg",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "application/vnd.android.package-archive": "apk",
}

# ---------- 文件头魔数（只认容器格式，认不出不猜）----------

# 前缀 → 扩展名，顺序即优先级（一条前缀命中就返回）
MAGIC_PREFIXES = (
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"\x00\x00\x01\x00", "ico"),
    (b"\x1a\x45\xdf\xa3", "mkv"),
    (b"ID3", "mp3"),
    (b"\xff\xfb", "mp3"),
    (b"\xff\xf3", "mp3"),
    (b"\xff\xf2", "mp3"),
    (b"fLaC", "flac"),
    (b"OggS", "ogg"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
    (b"\x1f\x8b", "gz"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),
    (b"MZ", "exe"),
)

# RIFF 容器：靠第 8-12 字节的子类型区分
RIFF_FLAVORS = {
    b"WEBP": "webp",
    b"WAVE": "wav",
    b"AVI ": "avi",
}

# ftyp 容器的 major brand：这几个是 HEIC/HEIF，M4A_BRANDS 是纯音频的 MP4 容器，其余（含 mp4）一律按 mp4 处理
HEIC_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1")
M4A_BRANDS = (b"M4A ", b"M4B ", b"M4P ")

# ---------- ZIP 容器内部的指纹（识别 docx / xlsx / pptx / odt / apk 这一族用）----------

# 这些格式的文件头都是 PK\x03\x04，真实身份写在容器里，
# 单看文件头只能判到「这是个 ZIP」。顺序即优先级，条目名要与容器里的完整路径精确匹配
# （不限于根级部件——EPUB 的指纹就在 META-INF/ 下）。
ZIP_ENTRY_FLAVORS = (
    ("word/document.xml", "docx"),
    ("xl/workbook.xml", "xlsx"),
    ("ppt/presentation.xml", "pptx"),
    ("AndroidManifest.xml", "apk"),
    # EPUB 规范强制每个 EPUB 都带 container.xml（里面指向 OPF），普通 ZIP 不会有这个路径
    ("META-INF/container.xml", "epub"),
)
# 带这个部件说明工作簿启用了宏，与 xlsx 同为 ZIP，只能靠容器内容区分
ZIP_MACRO_ENTRY = "xl/vbaProject.bin"
# ODF 把文档种类写在根级 mimetype 条目里
ODF_EXTENSION_BY_MIMETYPE = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
}
# iOS 安装包的标志性路径
IPA_PLIST_SUFFIX = ".app/Info.plist"
