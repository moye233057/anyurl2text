# -*- coding: utf-8 -*-
"""
扩展可用性：探活、选择与快照。

- `probe()` —— 这个提供商现在能不能用，连原因一起给。
- `resolve()` —— 请求里选的是谁就定谁（`True` 走默认提供商）；**这一层有意不探活**，理由见函数注释。
- `snapshot()` —— 给 `/probe/health` 的能力清单，前端据此把不可用项置灰。
- `note_api_call()` —— 记下最后一次真实 API 调用结果，供界面区分「已配置」与「已验证可用」。

探活口径（2026-10-11 与用户确认）：
- 侧车（本机 8021 / 8022）：`GET <origin>/health`，短超时；服务没起就是不可用，原因写清地址。
- API：**只看密钥是否配置**，绝不在这里发模型请求——`routers/probe/router.py` 定下的规矩是
  「探针不带副作用」，健康检查一旦会花钱，它自己就成了成本与故障源。真实可用性靠
  `note_api_call()` 记住的上一次调用，以及用户在界面上主动点的「测试连接」。
"""
import os
from typing import Dict, NamedTuple, Optional, Union
from urllib.parse import urlsplit, urlunsplit

import configs.extensions as ext
import configs.settings as settings
from utils import egress


class ProbeResult(NamedTuple):
    """
    一次探活的结果。

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

    功能：给探活提供「上次到底成没成」这条事实，而不必自己再发一次请求。
    应用场景：`extensions/deepseek.py` 的 chat() 在成功与失败两个分支各调一次。
    输入：ok — 是否成功；message — 失败原因（截断到 200 字）。
    输出：无。
    """
    global _last_api_call
    _last_api_call = {"ok": bool(ok), "message": (message or "")[:200]}


def _sidecar_url(capability: str, provider_id: str) -> Optional[str]:
    """侧车的业务端点；健康端点由它推出来。"""
    if capability == ext.CAPABILITY_OCR and provider_id == "paddle":
        return settings.OCR_API_URL
    if capability == ext.CAPABILITY_ASR and provider_id == "qwen3":
        return settings.ASR_API_URL
    return None


def _health_url(service_url: str) -> str:
    """侧车契约是 `GET /health` + `POST /xxx-b64`，把路径换成 /health 即健康端点。"""
    parsed = urlsplit(service_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def _probe_sidecar(provider: ext.Provider) -> ProbeResult:
    """侧车探活：连不上或非 2xx 都算不可用。"""
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
    """API 探活：只看密钥是否配置，不发请求（见模块文档的探活口径）。"""
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        return ProbeResult(False, "未配置 DEEPSEEK_API_KEY（见项目根 .env）")
    if _last_api_call is None:
        return ProbeResult(True, None, "已配置密钥，本次启动还没调用过")
    if _last_api_call.get("ok"):
        return ProbeResult(True, None, "上次调用成功")
    return ProbeResult(True, None, f"上次调用失败：{_last_api_call.get('message')}")


def probe(provider: ext.Provider) -> ProbeResult:
    """按类别探活：侧车发 HTTP、API 看密钥。"""
    if provider.kind == ext.KIND_SIDECAR:
        return _probe_sidecar(provider)
    return _probe_api(provider)


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

    **有意不在这里探活**：探活要发 HTTP，挂在每个请求的路径上会引入额外延迟与不确定性
    （侧车没起时还要等超时）。可用性由前端提交前按 `/probe/health` 置灰来保证；
    后端只按名字分派，真用不了时照常报它自己的错（如 `ocr_unreachable`），
    错误照样走契约进 `meta.model_error`。
    """
    if choice is False or choice is None:
        return None
    if choice is True:
        return default_provider(capability)
    return str(choice)


def snapshot() -> Dict[str, object]:
    """
    能力清单：每个能力的默认选择 + 各提供商现在能不能用。

    功能：一次算全所有能力的可用性与原因，供 `/probe/health` 下发、前端置灰与排障。
    应用场景：前端启动自检、用户手动刷新、排查「这项为什么是灰的」。
    输入：无。
    输出：{能力名: {"default": 提供商 id, "providers": [{id,label,kind,available,reason,detail}]}}。
    """
    capabilities: Dict[str, object] = {}
    for capability in ext.CAPABILITIES:
        providers = []
        for provider in ext.PROVIDERS[capability]:
            result = probe(provider)
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
