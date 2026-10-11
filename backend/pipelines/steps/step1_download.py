# -*- coding: utf-8 -*-
"""
步骤 1：把 URL 下载到临时目录里的一个文件（两条路共用的第一步）。

安全基线（ADR 0005，不可协商）：
1. 拒绝回环 / 内网 / 链路本地 / 保留地址；重定向**逐跳**重新校验目标，
   否则外部域名可以 302 到内网。解析一次并把连接钉到该 IP（关 DNS rebinding 时间窗）；
   解析结果无法识别即拒（fail-closed）。https 跳到 http 逐跳禁止。
2. 流式写盘时累计字节数，超限立刻中断——不在下载完成之后才检查。
3. TLS 校验不降级：证书问题直接失败并说明原因，绝不静默 verify=False。

这三条是「服务接受用户可控 URL」形态下的必答题：任何为「提高成功率」放松它们的改动
都应被拒绝——一次 SSLError 之后把 verify 关掉、或干脆不查内网地址，都会把服务变成
内网探测器。

另有一个方向相反的开关 `ALLOW_HTTP_SOURCE`（默认 `true`，即与既有行为一致）：置 `false`
后只收 https，`http://` 在发起连接之前就被 422 拒掉（码 `download_http_not_allowed`）。
"""
import ipaddress
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpcore

import configs.errors as errors
import configs.settings as settings
from configs.log_config import logger, redact_url_for_log
from utils import request_budget

REDIRECT_STATUS = (301, 302, 303, 307, 308)
# 受限地址段的判定字段：内网 / 回环 / 链路本地 / 保留 / 组播 / 未指定，任一命中即拒绝
RESTRICTED_IP_FLAGS = ("is_private", "is_loopback", "is_link_local", "is_reserved", "is_multicast", "is_unspecified")


def _is_restricted(ip) -> bool:
    """这个地址是不是受限段。清单见 `RESTRICTED_IP_FLAGS`（ADR 0005 的第一条）。"""
    return any(getattr(ip, flag) for flag in RESTRICTED_IP_FLAGS)


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


def _assert_public_host(host: str) -> str:
    """
    解析主机名、拒绝受限地址，并返回用于建连的公网 IP（钉死，避免二次 DNS）。

    功能：对 DNS 返回的每一个地址都做判定，任一命中受限段即拒绝；任一地址无法识别
          也拒绝（fail-closed）。通过后优先选 IPv4，否则取第一个合法地址。
    应用场景：每次真正发起连接之前调用；重定向后的新地址也要再过一次。
    输入：host — URL 里的主机名。
    输出：要连接的 IP 字符串（无括号）。
    异常：FetchError — 解析失败（502）或命中受限地址段（422）。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"域名解析失败: {host}", 502, "download_dns_failed", "url") from exc
    if not infos:
        raise FetchError(f"域名解析失败: {host}（无地址）", 502, "download_dns_failed", "url")

    candidates = []
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise FetchError(
                f"域名解析返回无法识别的地址: {host} -> {raw!r}",
                502,
                "download_dns_failed",
                "url",
            ) from exc
        if settings.ALLOW_PRIVATE_HOSTS:
            candidates.append(ip)
            continue
        if _is_restricted(ip):
            raise FetchError(f"拒绝访问内网或保留地址: {host} -> {ip}", 422, "download_private_host", "url")
        candidates.append(ip)

    if not candidates:
        raise FetchError(f"域名解析失败: {host}（无可用地址）", 502, "download_dns_failed", "url")

    for ip in candidates:
        if isinstance(ip, ipaddress.IPv4Address):
            return str(ip)
    return str(candidates[0])


def _assert_safe_url(url: str) -> str:
    """校验协议、http 开关与主机，返回钉死建连用的 IP；供每一跳复用。"""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"只支持 http/https，收到: {parsed.scheme or '(空)'}", 422, "download_scheme_unsupported", "url")
    if not parsed.hostname:
        raise FetchError("URL 缺少主机名", 422, "download_host_missing", "url")
    if parsed.scheme == "http" and not settings.ALLOW_HTTP_SOURCE:
        raise FetchError("只允许 https；如需放开 http 请设 ALLOW_HTTP_SOURCE=true", 422, "download_http_not_allowed", "url")
    return _assert_public_host(parsed.hostname)


def _host_header(parsed) -> str:
    """给钉 IP 后的请求补回原来的 Host（含非默认端口）。"""
    host = parsed.hostname or ""
    if parsed.port is None:
        return host
    return f"{host}:{parsed.port}"


def _pinned_url(url: str, connect_ip: str) -> Tuple[str, str, str]:
    """
    把 URL 的主机换成已校验的 IP，保留 path/query；返回 (钉死 URL, Host 头, SNI 主机名)。

    TLS 仍按原主机名做 SNI / 证书校验（extensions.sni_hostname），连接只走钉死的 IP。
    """
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    try:
        ip_obj = ipaddress.ip_address(connect_ip)
    except ValueError as exc:
        raise FetchError(f"内部错误：无效的钉死地址 {connect_ip!r}", 500, "internal") from exc
    if isinstance(ip_obj, ipaddress.IPv6Address):
        netloc = f"[{connect_ip}]"
    else:
        netloc = connect_ip
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    pinned = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return pinned, _host_header(parsed), host


def _budgeted_timeout(deadline: float) -> dict:
    """
    按剩余预算收紧超时，避免「连接与读取各自用满」把总时间冲爆。

    形态是 httpcore 的 `timeout` 扩展（一个 dict），不是 httpx.Timeout。
    """
    remaining = max(1.0, deadline - time.monotonic())
    connect = min(settings.CONNECT_TIMEOUT_SEC, remaining)
    read = min(settings.READ_TIMEOUT_SEC, remaining)
    return {"connect": connect, "read": read, "write": connect, "pool": connect}


def _header_value(headers: List[Tuple[bytes, bytes]], name: str) -> Optional[str]:
    """
    取 httpcore 响应里的一个头。

    httpcore 的响应头是 `[(bytes, bytes)]` 列表而不是 dict，所以按名字逐个比、大小写不敏感；
    值按 latin-1 解——HTTP 头是字节，`Content-Disposition` 里的非 ASCII 文件名靠它保持原样。
    """
    wanted = name.lower().encode("ascii")
    for key, value in headers:
        if key.lower() == wanted:
            return value.decode("latin-1")
    return None


def _filename_from_disposition(header: Optional[str]) -> str:
    """从 Content-Disposition 里取原始文件名，取不到返回空串。"""
    if not header:
        return ""
    index = header.lower().find("filename=")
    if index < 0:
        return ""
    return unquote(header[index + len("filename=") :].strip().strip('";\' '))


def _stream_to_file(
    response: httpcore.Response,
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
    declared = _header_value(response.headers, "content-length")
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
            # httpcore 每次最多读 64 KiB（http11.READ_NUM_BYTES），与 DOWNLOAD_CHUNK_BYTES 同值，
            # 所以这里不再显式传粒度：边收边数的语义与逐块中断的上界都没变。
            for chunk in response.iter_stream():
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

    file_name = _filename_from_disposition(_header_value(response.headers, "content-disposition"))
    if not file_name:
        file_name = Path(urlsplit(url).path).name
    return FetchedFile(
        path=target,
        url=url,
        size_bytes=written,
        content_type=_header_value(response.headers, "content-type"),
        file_name=file_name,
    )


def download(url: str, dest_dir: Path) -> FetchedFile:
    """
    下载 URL 到临时目录，返回落盘结果。

    功能：手工跟随重定向（每一跳重做地址与协议校验，钉 IP 建连），流式写盘并限大小；
          TLS 校验始终开启；https → http 的降级跳转直接拒绝。
    应用场景：extract / understand 两个接口的 URL 入口，共用的第一步。
    输入：url — 用户提供的地址；dest_dir — 临时目录。
    输出：FetchedFile（含最终 URL、字节数、Content-Type 与原始文件名）。
    异常：FetchError — 地址非法、命中受限地址、HTTP 错误、超时、超限。
    """
    if request_budget.request_deadline() is not None:
        deadline = request_budget.download_deadline()
    else:
        deadline = time.monotonic() + min(settings.DOWNLOAD_BUDGET_SEC, settings.REQUEST_BUDGET_SEC)
    current = (url or "").strip()
    if not current:
        raise FetchError("URL 不能为空", 422, "download_url_empty", "url")
    current = current.replace(" ", "%20")

    headers = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    try:
        # 这一跳用 httpcore 而不是 httpx 发（2026-10-11 实测 httpx 0.28.1 + httpcore 1.0.9 不行）：
        # httpx 会把请求 URL 上的主机——这里是钉死的 IP——当成 TLS 的 server_hostname，
        # `sni_hostname` 扩展被它丢掉，证书于是按 IP 校验，**所有 https 都 CERTIFICATE_VERIFY_FAILED**。
        # httpcore 认这个扩展（`_sync/connection.py` 读它并当 server_hostname 用），
        # 于是 ADR 0005 第 1 条（钉 IP）与第 3 条（TLS 不降级）同时成立。
        # 换回来之前先看这条注释：httpx 那条路不是「配置不对」，是它这一层就做不到。
        with httpcore.ConnectionPool() as pool:
            for _ in range(settings.MAX_REDIRECTS + 1):
                previous_scheme = urlsplit(current).scheme
                connect_ip = _assert_safe_url(current)
                pinned, host_header, sni_host = _pinned_url(current, connect_ip)
                request_headers = {**headers, "Host": host_header}
                extensions = {"timeout": _budgeted_timeout(deadline)}
                if previous_scheme == "https":
                    extensions["sni_hostname"] = sni_host
                response = pool.handle_request(
                    httpcore.Request("GET", pinned, headers=request_headers, extensions=extensions)
                )
                try:
                    if response.status in REDIRECT_STATUS:
                        location = _header_value(response.headers, "location")
                        if not location:
                            raise FetchError(
                                f"HTTP {response.status} 重定向但缺少 Location",
                                502,
                                "download_redirect_missing_location",
                            )
                        next_url = urljoin(current, location)
                        next_scheme = urlsplit(next_url).scheme
                        if previous_scheme == "https" and next_scheme == "http":
                            raise FetchError(
                                "拒绝 https 重定向到 http（协议降级）",
                                422,
                                "download_redirect_insecure",
                                "url",
                            )
                        current = next_url
                        logger.info(f"fetch 重定向 -> {redact_url_for_log(current)}")
                        continue
                    if response.status >= 400:
                        raise FetchError(f"下载失败，HTTP {response.status}", 502, "download_http_error")
                    fetched = _stream_to_file(response, current, dest_dir, deadline)
                    logger.info(
                        f"fetch 完成 size={fetched.size_bytes} content_type={fetched.content_type}"
                    )
                    return fetched
                finally:
                    # 每一跳都要关：成功时响应体已写完，重定向与非 2xx 时也得把连接放回池子。
                    response.close()
    except httpcore.TimeoutException as exc:
        raise FetchError("下载超时", 504, "download_timeout") from exc
    except httpcore.NetworkError as exc:
        raise FetchError(f"网络错误: {exc}", 502, "download_network_error") from exc

    raise FetchError(f"重定向次数超过上限 {settings.MAX_REDIRECTS}", 502, "download_redirect_limit")
