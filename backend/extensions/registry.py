# -*- coding: utf-8 -*-
"""
扩展可用性：健康检查、选择与快照。

- `probe()` —— 这个提供商现在能不能用，连原因一起给。
- `resolve()` —— 请求里选的是谁就定谁（`True` 走默认提供商）；**这一层有意不做健康检查**，理由见函数注释。
- `snapshot()` —— 给 `/probe/health` 的能力清单，前端据此把不可用项置灰。
- `note_api_call()` —— 记下最后一次真实 API 调用结果，供界面区分「已配置」与「已验证可用」。

健康检查口径（2026-10-11 与用户确认）：
- 本机独立服务（本机 8021 / 8022）：`GET <origin>/health`，短超时；服务没起就是不可用，原因写清地址。
- API：**只看密钥是否配置**，绝不在这里发模型请求——`routers/probe/router.py` 定下的规矩是
  「探针不带副作用」，健康检查一旦会花钱，它自己就成了成本与故障源。真实可用性靠
  `note_api_call()` 记住的上一次调用，以及用户在界面上主动点的「测试连接」。

两条性能约束（踩过才知道）：
- **并发探**：一个一个探时总耗时是各家超时之和，而 `/probe/health` 的调用方（`start.py` 的启动就绪检查）
  只等 2 秒——串行探两个没起的本机独立服务就足以让它判「后端没起」。
- **缓存**：健康检查会被启动就绪检查与前端反复调用，`EXTENSION_PROBE_CACHE_SEC` 内直接复用上次结果。
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Dict, NamedTuple, Optional, Union
from urllib.parse import urlsplit, urlunsplit

import configs.extensions as ext
import configs.settings as settings
from utils import egress
from utils.errors import LlmError


class ProbeResult(NamedTuple):
    """
    一次健康检查的结果。

    属性：
        available — 现在能不能用。
        reason — **不能用**的原因（可用时为 None）；界面直接显示这句。
        detail — 可用时的补充说明（如「上次调用失败」），不改变可用性。
    """

    available: bool
    reason: Optional[str] = None
    detail: Optional[str] = None


# 进程内记忆：最后一次真实 API 调用的结果。
# 一次失败可能只是网络抖动，所以它不上报为「不可用」，只作为界面上的提示。
_last_api_call: Optional[Dict[str, object]] = None


def note_api_call(ok: bool, message: str = "") -> None:
    """
    记下最后一次真实 API 调用的结果。

    功能：给健康检查提供「上次到底成没成」这条事实，而不必自己再发一次请求。
    应用场景：`extensions/deepseek.py` 的 chat() 在成功与失败两个分支各调一次。
    输入：ok — 是否成功；message — 失败原因（截断到 200 字）。
    输出：无。
    """
    global _last_api_call
    _last_api_call = {"ok": bool(ok), "message": (message or "")[:200]}


# 进程内缓存：{能力/提供商: ProbeResult} 与它的取样时刻。
# 存在的理由见模块文档的两条性能约束——健康检查的调用方只等 2 秒，健康检查不能每次都真跑。
_cache: Dict[str, ProbeResult] = {}
_cache_at: float = 0.0
_cache_lock = Lock()


def _cache_key(provider: ext.Provider) -> str:
    return f"{provider.capability}/{provider.id}"


def _remember(provider: ext.Provider, result: ProbeResult) -> ProbeResult:
    """把一次真实测试的结果写进缓存：测完就该立刻能在界面上看到。"""
    with _cache_lock:
        _cache[_cache_key(provider)] = result
    return result


def _probe_all(force: bool = False) -> Dict[str, ProbeResult]:
    """
    探一遍名单里的所有提供商并写进缓存，返回 {缓存键: 结果}。

    功能：缓存新鲜且没要求强制重探时直接返回上次结果（不发任何请求）；否则**并发**探一遍。
    应用场景：`snapshot()` 的唯一数据来源。
    输入：force — 忽略缓存强制重探。
    输出：缓存键 → ProbeResult。
    """
    global _cache_at
    providers = [provider for capability in ext.CAPABILITIES for provider in ext.PROVIDERS[capability]]
    with _cache_lock:
        cached = dict(_cache)
        fresh = not force and cached and (time.monotonic() - _cache_at) < settings.EXTENSION_PROBE_CACHE_SEC
    if fresh:
        return cached
    if not providers:
        return {}

    # 并发：串行时总耗时是各家超时之和，而 /probe/health 的调用方只等 2 秒。
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        results = list(pool.map(probe, providers))
    with _cache_lock:
        for provider, result in zip(providers, results):
            _cache[_cache_key(provider)] = result
        _cache_at = time.monotonic()
        return dict(_cache)


def _sidecar_url(capability: str, provider_id: str) -> Optional[str]:
    """本机独立服务的业务端点；健康端点由它推出来。"""
    if capability == ext.CAPABILITY_OCR and provider_id == "paddle":
        return settings.OCR_API_URL
    if capability == ext.CAPABILITY_ASR and provider_id == "qwen3":
        return settings.ASR_API_URL
    return None


def _health_url(service_url: str) -> str:
    """本机服务约定是 `GET /health` + `POST /xxx-b64`，把路径换成 /health 即健康端点。"""
    parsed = urlsplit(service_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def _probe_sidecar(provider: ext.Provider) -> ProbeResult:
    """本机服务健康检查：连不上或非 2xx 都算不可用。"""
    service_url = _sidecar_url(provider.capability, provider.id)
    if not service_url:
        return ProbeResult(False, f"{provider.label} 没有配服务地址")
    health_url = _health_url(service_url)
    try:
        response = egress.shared_httpx_client().get(
            health_url, timeout=settings.EXTENSION_PROBE_TIMEOUT_SEC
        )
    except Exception as exc:
        return ProbeResult(False, f"服务没在跑：{health_url} 连不上（{type(exc).__name__}）")
    if response.status_code >= 400:
        return ProbeResult(False, f"服务异常：{health_url} 返回 HTTP {response.status_code}")
    return ProbeResult(True)


def _probe_api(provider: ext.Provider) -> ProbeResult:
    """API 健康检查：只看密钥是否配置，不发请求（见模块文档的健康检查口径）。"""
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        return ProbeResult(False, "未配置 DEEPSEEK_API_KEY（见项目根 .env）")
    if _last_api_call is None:
        return ProbeResult(True, None, "已配置密钥，本次启动还没调用过")
    if _last_api_call.get("ok"):
        return ProbeResult(True, None, "上次调用成功")
    return ProbeResult(True, None, f"上次调用失败：{_last_api_call.get('message')}")


def probe(provider: ext.Provider) -> ProbeResult:
    """按类别做健康检查：本机独立服务发 HTTP、API 看密钥。"""
    if provider.kind == ext.KIND_SIDECAR:
        return _probe_sidecar(provider)
    return _probe_api(provider)


def _tiny_png_data_url() -> str:
    """
    造一张 64×64 白底 PNG 的 data URL。

    用途：测视觉提供商时带一张图——要验的正是「这家 API 收不收图」，只发文字测不出这一点。
    尺寸无所谓（官方会把小图等比放大），关键是走一次真的带图请求。
    """
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _test_api_for_real(provider: ext.Provider) -> ProbeResult:
    """
    真实测一次 API：发一个最小请求，成功才算可用。

    与 `_probe_api()`（只看密钥是否配置）分工不同——这条**会花钱**，只在用户主动点「测试」
    时跑。`DeepSeekClient.chat()` 内部会把结果记进 `note_api_call()`，所以测完再取快照，
    界面上看到的就是刚测出来的状态。
    """
    # 延迟 import：deepseek.py 在模块级 import 本模块，模块级互相 import 会成环。
    from extensions.deepseek import DeepSeekClient

    content: list = [{"type": "text", "text": "只回复 OK"}]
    if provider.capability == ext.CAPABILITY_OCR:
        content.append({"type": "image_url", "image_url": {"url": _tiny_png_data_url()}})
    try:
        text = DeepSeekClient().chat([{"role": "user", "content": content}], max_tokens=16)
    except LlmError as exc:
        return ProbeResult(False, f"测试失败：{exc}")
    except Exception as exc:  # noqa: BLE001 - 探针要把任何失败都翻成原因给界面
        return ProbeResult(False, f"测试失败：{type(exc).__name__}: {exc}")
    return ProbeResult(True, None, f"测试通过（回复 {text.strip()[:20]!r}）")


def test_provider(capability: str, provider_id: str) -> Optional[ProbeResult]:
    """
    真实测一次这个提供商；名字不在名单里返回 None（调用方转 422）。

    与 `snapshot()` 的分工：快照要便宜、可高频调用，所以本机独立服务只探 `/health`、API 只看密钥；
    这里是真的跑一遍——本机独立服务打 `/health`，API 发一次最小请求（会花极小的 token）。
    因此它只该由用户点「测试」触发，不能挂在健康检查里。

    结果会写进缓存：用户测完就该在界面上看到它变成可选，不必等缓存过期或再探一遍。
    """
    provider = ext.find(capability, provider_id)
    if provider is None:
        return None
    if provider.kind == ext.KIND_SIDECAR:
        return _remember(provider, _probe_sidecar(provider))
    return _remember(provider, _test_api_for_real(provider))


def default_provider(capability: str) -> str:
    """默认提供商：环境变量指定且合法就用它，否则用名单里声明的那个。"""
    env_value = {
        ext.CAPABILITY_LLM: settings.LLM_PROVIDER,
        ext.CAPABILITY_OCR: settings.OCR_PROVIDER,
        ext.CAPABILITY_ASR: settings.ASR_PROVIDER,
    }.get(capability, "").strip()
    if env_value and ext.find(capability, env_value):
        return env_value
    return ext.default_of(capability)


def resolve(capability: str, choice: Union[bool, str, None]) -> Optional[str]:
    """
    请求里的选择 → 实际用哪个提供商；`False` / `None` 返回 None（这次不用这个能力）。

    **有意不在这里做健康检查**：健康检查要发 HTTP，挂在每个请求的路径上会引入额外延迟与不确定性
    （本机独立服务没起时还要等超时）。可用性由前端提交前按 `/probe/health` 置灰来保证；
    后端只按名字分派，真用不了时照常报它自己的错（如 `ocr_unreachable`），
    错误照样按约定写入 `meta.model_error`。
    """
    if choice is False or choice is None:
        return None
    if choice is True:
        return default_provider(capability)
    return str(choice)


def snapshot(force: bool = False) -> Dict[str, object]:
    """
    能力清单：每个能力的默认选择 + 各提供商现在能不能用。

    功能：缓存内直接复用上次结果（`EXTENSION_PROBE_CACHE_SEC`），过期或 `force=True` 时真探一遍；
          真探是**并发**的，总耗时约等于最慢那一家，而不是各家超时之和。
    应用场景：`/probe/health` 下发、前端置灰、`?refresh=true` 强制重探、排查「这项为什么是灰的」。
    输入：force — 忽略缓存强制重探。
    输出：{能力名: {"default": 提供商 id, "providers": [{id,label,kind,available,reason,detail}]}}。
    """
    measured = _probe_all(force=force)
    capabilities: Dict[str, object] = {}
    for capability in ext.CAPABILITIES:
        providers = []
        for provider in ext.PROVIDERS[capability]:
            result = measured.get(_cache_key(provider)) or ProbeResult(
                False, "尚未探测（点「刷新可用性」重试）"
            )
            providers.append(
                {
                    "id": provider.id,
                    "label": provider.label,
                    "kind": provider.kind,
                    "available": result.available,
                    "reason": result.reason,
                    "detail": result.detail,
                }
            )
        capabilities[capability] = {"default": default_provider(capability), "providers": providers}
    return capabilities
