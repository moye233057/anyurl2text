# -*- coding: utf-8 -*-
"""
步骤 1：把 URL 下载到临时目录里的一个文件（两条路共用的第一步）。

安全基线（ADR 0005，三条都不可协商）：
1. 拒绝回环 / 内网 / 链路本地 / 保留地址；重定向**逐跳**重新校验目标，
   否则外部域名可以 302 到内网。
2. 流式写盘时累计字节数，超限立刻中断——不在下载完成之后才检查。
3. TLS 校验不降级：证书问题直接失败并说明原因，绝不静默 verify=False。

这三条是「服务接受用户可控 URL」形态下的必答题：任何为「提高成功率」放松它们的改动
都应被拒绝——一次 SSLError 之后把 verify 关掉、或干脆不查内网地址，都会把服务变成
内网探测器。
"""
import ipaddress
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urljoin, urlsplit

import httpx

import configs.errors as errors
import configs.settings as settings
from configs.log_config import logger

REDIRECT_STATUS = (301, 302, 303, 307, 308)

class FetchError(Exception):
    """
    下载或上传阶段的失败。

    status_code 是给 HTTP 层映射用的语义：入参问题 422、体积超限 413、
    超时 504、其余上游 / 网络问题 502。字符串匹配错误类型是脆的，所以在这里就带上。

    code 是**稳定机器名**（见 `configs/errors.py`），给程序看、不许改。构造时就校验它在白名单里：
    打错一个字当场炸掉，而不是等前端发现分不了组。param 指出哪个入参/字段出错。
    """

    def __init__(self, message: str, status_code: int = 502,
                 code: str = "download_network_error", param: Optional[str] = None) -> None:
        if code not in errors.HARD_FAILURE_CODES:
            raise ValueError(f"未登记的错误码: {code}（见 configs/errors.py）")
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.param = param

@dataclass
class FetchedFile:
    """下载结果。path 落在调用方给的临时目录下，由调用方负责清理。"""

    path: Path
    url: str
    size_bytes: int
    content_type: Optional[str]
    file_name: str

def _assert_public_host(host: str) -> None:
    """
    解析主机名并拒绝内网 / 回环 / 链路本地 / 保留地址。

    功能：对 DNS 返回的每一个地址都做判定，任一命中即拒绝。
    应用场景：每次真正发起连接之前调用；重定向后的新地址也要再过一次。
    输入：host — URL 里的主机名。
    异常：FetchError — 解析失败（502）或命中受限地址段（422）。
    """
    if settings.ALLOW_PRIVATE_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"域名解析失败: {host}", 502, "download_dns_failed", "url") from exc

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise FetchError(f"拒绝访问内网或保留地址: {host} -> {ip}", 422, "download_private_host", "url")

def _assert_safe_url(url: str) -> None:
    """校验协议与主机，供每一跳复用。"""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"只支持 http/https，收到: {parsed.scheme or '(空)'}", 422, "download_scheme_unsupported", "url")
    if not parsed.hostname:
        raise FetchError("URL 缺少主机名", 422, "download_host_missing", "url")
    _assert_public_host(parsed.hostname)

def _budgeted_timeout(deadline: float) -> httpx.Timeout:
    """按剩余预算收紧超时，避免「连接与读取各自用满」把总时间冲爆。"""
    remaining = max(1.0, deadline - time.monotonic())
    connect = min(settings.CONNECT_TIMEOUT_SEC, remaining)
    read = min(settings.READ_TIMEOUT_SEC, remaining)
    return httpx.Timeout(connect=connect, read=read, write=connect, pool=connect)

def _filename_from_disposition(header: Optional[str]) -> str:
    """从 Content-Disposition 里取原始文件名，取不到返回空串。"""
    if not header:
        return ""
    index = header.lower().find("filename=")
    if index < 0:
        return ""
    return unquote(header[index + len("filename=") :].strip().strip('";\' '))

def _stream_to_file(
    response: httpx.Response,
    url: str,
    dest_dir: Path,
    deadline: float,
) -> FetchedFile:
    """
    把响应体流式写入临时文件，边写边校验大小与时间预算。

    功能：累计字节数超过 MAX_DOWNLOAD_BYTES 或耗尽预算时立刻中断，并删掉半成品。
    应用场景：download() 拿到 2xx 响应后调用。
    输入：response — 已打开的流式响应；url — 当前跳的 URL；dest_dir — 落盘目录；
          deadline — 单调时钟截止点。
    输出：FetchedFile。
    异常：FetchError — 超限（413）、超时（504）或写盘失败（500）。
    """
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.MAX_DOWNLOAD_BYTES:
        raise FetchError(
            f"文件过大: {int(declared)} 字节，超过上限 {settings.MAX_DOWNLOAD_BYTES} 字节",
            413,
            "download_declared_too_large",
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    handle, raw_path = tempfile.mkstemp(dir=str(dest_dir), prefix="dl_")
    target = Path(raw_path)
    written = 0
    try:
        with open(handle, "wb") as sink:
            for chunk in response.iter_bytes(settings.DOWNLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                if time.monotonic() > deadline:
                    raise FetchError("下载超时：超出请求时间预算", 504, "download_budget_exhausted")
                written += len(chunk)
                if written > settings.MAX_DOWNLOAD_BYTES:
                    raise FetchError(
                        f"文件过大: 已写出 {written} 字节，超过上限 {settings.MAX_DOWNLOAD_BYTES} 字节",
                        413,
                        "download_stream_too_large",
                    )
                sink.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    file_name = _filename_from_disposition(response.headers.get("content-disposition"))
    if not file_name:
        file_name = Path(urlsplit(url).path).name
    return FetchedFile(
        path=target,
        url=url,
        size_bytes=written,
        content_type=response.headers.get("content-type"),
        file_name=file_name,
    )

def download(url: str, dest_dir: Path) -> FetchedFile:
    """
    下载 URL 到临时目录，返回落盘结果。

    功能：手工跟随重定向（每一跳重做地址与协议校验），流式写盘并限大小；
          TLS 校验始终开启，不用任何理由降级。
    应用场景：extract / understand 两个接口的 URL 入口，共用的第一步。
    输入：url — 用户提供的地址；dest_dir — 临时目录。
    输出：FetchedFile（含最终 URL、字节数、Content-Type 与原始文件名）。
    异常：FetchError — 地址非法、命中受限地址、HTTP 错误、超时、超限。
    """
    deadline = time.monotonic() + settings.REQUEST_BUDGET_SEC
    current = (url or "").strip()
    if not current:
        raise FetchError("URL 不能为空", 422, "download_url_empty", "url")
    current = current.replace(" ", "%20")

    headers = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    try:
        with httpx.Client(follow_redirects=False, verify=True, headers=headers) as client:
            for _ in range(settings.MAX_REDIRECTS + 1):
                _assert_safe_url(current)
                with client.stream("GET", current, timeout=_budgeted_timeout(deadline)) as response:
                    if response.status_code in REDIRECT_STATUS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError(
                                f"HTTP {response.status_code} 重定向但缺少 Location",
                                502,
                                "download_redirect_missing_location",
                            )
                        current = urljoin(current, location)
                        logger.info(f"fetch 重定向 -> {current}")
                        continue
                    if response.status_code >= 400:
                        raise FetchError(f"下载失败，HTTP {response.status_code}", 502, "download_http_error")
                    fetched = _stream_to_file(response, current, dest_dir, deadline)
                    logger.info(
                        f"fetch 完成 size={fetched.size_bytes} content_type={fetched.content_type}"
                    )
                    return fetched
    except httpx.TimeoutException as exc:
        raise FetchError("下载超时", 504, "download_timeout") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"网络错误: {exc}", 502, "download_network_error") from exc

    raise FetchError(f"重定向次数超过上限 {settings.MAX_REDIRECTS}", 502, "download_redirect_limit")
