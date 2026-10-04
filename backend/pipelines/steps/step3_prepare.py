# -*- coding: utf-8 -*-
"""
步骤 3：把「临时目录里的一个文件」变成 `PreparedSource`——补上识别结果作为扩展名。

它把步骤 1（下载 / 上传落盘）与步骤 2（类型识别）串起来，两条路共用；
请求结束时由 `cleanup` 清掉本次请求的临时目录。产出的 `PreparedSource` 定义在 `processors/schema.py`。
"""
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Tuple

import configs.settings as settings
from configs.log_config import logger
from pipelines.steps.step1_download import FetchError, download
from pipelines.steps.step2_detect import read_head, resolve_extension
from pipelines.processors.schema import PreparedSource

# 上传落盘用的固定文件名（下载落盘名由 tempfile 生成）
UPLOAD_FILE_NAME = "upload.bin"

def _align_extension(path: Path, ext: str) -> Path:
    """
    把落盘临时文件改名为带识别结果的扩展名。

    功能：下载落盘名是 `dl_xxxx`、上传落盘名是 `upload.bin`，都不带真实后缀，
          而部分解析库是**按后缀挑引擎**的——openpyxl 只认 .xlsx/.xlsm，
          pyexcel-io 按后缀选读取器——识别对了也一样读不出来。
    应用场景：两个 prepare 在 resolve_extension 之后调用，让磁盘上的文件名与识别结果一致。
    输入：path — 临时文件；ext — 已识别的小写扩展名（可能为空）。
    输出：改名后的路径；扩展名为空或改名失败时返回原路径（改名失败只记日志，
         不改判识别结果，后续解析照原路径走）。
    """
    if not ext:
        return path
    renamed = path.with_suffix(f".{ext}")
    try:
        path.rename(renamed)
    except OSError as exc:
        logger.error(f"临时文件补扩展名失败: {exc}")
        return path
    return renamed

def cleanup(prepared: PreparedSource) -> None:
    """
    收尾：按配置决定是否留档中间产物，然后整树删除本次请求的临时目录。

    功能：`DEBUG_ARTIFACTS` 打开时先把临时目录整体复制到 `backend/data/debug/` 下再删除原件——
          默认不留档（省磁盘），需要复现线上问题时把它打开，下载原文件与渲染出的页面图都在。
    应用场景：两个管线的 finally 里调用。
    输入：prepared — 源信息，path.parent 即本次请求的临时目录。
    输出：无。
    """
    temp_dir = prepared.path.parent
    if settings.DEBUG_ARTIFACTS and temp_dir.exists():
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = settings.DEBUG_DIR / f"{stamp}_{uuid.uuid4().hex[:6]}_{temp_dir.name}"
            shutil.copytree(temp_dir, target, dirs_exist_ok=True)
            logger.info(f"中间产物已留档: {target}")
        except Exception as exc:
            logger.error(f"中间产物留档失败: {exc}")
    shutil.rmtree(temp_dir, ignore_errors=True)

def prepare_from_url(url: str) -> Tuple[PreparedSource, int]:
    """
    下载 URL 并识别类型。

    功能：建临时目录 → 下载 → 读文件头 → 综合识别扩展名与识别方式。
    应用场景：两个接口的 URL 入口第一步。
    输入：url — 用户提供的地址。
    输出：(PreparedSource, 下载耗时毫秒)。调用方负责在 finally 里清理 path.parent。
    异常：FetchError — 地址校验失败或下载失败。
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="anyurl2text_"))
    started = time.monotonic()
    try:
        fetched = download(url, temp_dir)
    except FetchError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    head = read_head(fetched.path)
    ext, detector = resolve_extension(
        fetched.path, fetched.file_name or fetched.url, fetched.content_type, head
    )
    prepared = PreparedSource(
        path=_align_extension(fetched.path, ext),
        source=url,
        file_name=fetched.file_name,
        mime=fetched.content_type,
        size_bytes=fetched.size_bytes,
        ext=ext,
        detector=detector,
        page_url=fetched.url,
    )
    download_ms = int((time.monotonic() - started) * 1000)
    logger.info(f"prepare 完成 ext={ext or '(未知)'} detector={detector.value} cost={download_ms}ms")
    return prepared, download_ms

def prepare_from_upload(stream: BinaryIO, file_name: str) -> Tuple[PreparedSource, int]:
    """
    把上传流落盘并识别类型。

    功能：建临时目录 → 分块写入并累计大小（与下载共用同一条上限，不让上传成为绕过大小限制的入口）
          → 识别类型 → 把识别结果补成扩展名。
    应用场景：两个接口的文件上传入口第一步。
    输入：stream — 可读二进制流（FastAPI 的 UploadFile.file）；file_name — 原始文件名。
    输出：(PreparedSource, 落盘耗时毫秒)。调用方负责清理 path.parent。
    异常：FetchError — 超过大小上限。
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="anyurl2text_upload_"))
    target = temp_dir / UPLOAD_FILE_NAME
    started = time.monotonic()
    written = 0
    try:
        with target.open("wb") as sink:
            while True:
                chunk = stream.read(settings.DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.MAX_DOWNLOAD_BYTES:
                    raise FetchError(
                        f"上传文件过大: 已写入 {written} 字节，超过上限 {settings.MAX_DOWNLOAD_BYTES} 字节",
                        413,
                        "upload_too_large",
                        "upload",
                    )
                sink.write(chunk)
        ext, detector = resolve_extension(target, file_name, None, read_head(target))
        prepared = PreparedSource(
            path=_align_extension(target, ext),
            source=file_name,
            file_name=file_name,
            mime=None,
            size_bytes=written,
            ext=ext,
            detector=detector,
            page_url="",
        )
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    upload_ms = int((time.monotonic() - started) * 1000)
    logger.info(f"prepare(upload) 完成 ext={ext or '(未知)'} size={written} cost={upload_ms}ms")
    return prepared, upload_ms

