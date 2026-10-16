# -*- coding: utf-8 -*-
"""
扩展能力与提供商的静态名单（名字的唯一声明处）。

一个**能力**（capability）可以有多个**提供商**（provider）。本机独立服务与第三方 API 在这一层是同一类东西：
对调用方来说它们只是「谁能干这件事」——可用性由健康检查决定（`extensions/registry.py`），
不由「是本地的还是云上的」决定。

为什么名单必须落在 `configs/`：提供商的**名字**同时被三处用到——HTTP 字段约定的入参校验（`utils/`）、
健康检查与选择（`extensions/`）、能力清单接口（`routers/` 经 `pipelines/`）。而分层规则（ADR-0007）
只允许它们各自 import 到 `configs`，所以名单在这里写一次，其余两处都从这里取。
"""
from typing import Dict, NamedTuple, Optional, Tuple, Union

# ---------- 能力 ----------
CAPABILITY_LLM = "llm"
CAPABILITY_OCR = "ocr"
CAPABILITY_ASR = "asr"
# 顺序即界面顺序；也是「请求传 true 时按声明顺序挑」的顺序
CAPABILITIES: Tuple[str, ...] = (CAPABILITY_LLM, CAPABILITY_OCR, CAPABILITY_ASR)

# ---------- 提供商类别 ----------
# 本机独立服务：探 HTTP /health；第三方 API：看密钥是否配置
KIND_SIDECAR = "sidecar"
KIND_API = "api"


class Provider(NamedTuple):
    """
    一个能力实现。

    属性：
        id — 稳定机器名，进请求字段约定（如 ocr="paddle"），不许改。
        capability — 它属于哪个能力。
        label — 界面上显示的名字。
        kind — KIND_SIDECAR / KIND_API，决定健康检查方式。
        is_default — 请求传 `true` 且没有配置默认值时挑它。
        needs_model — 干这件事本身要不要模型。本机独立服务为 False：选了它就不必开 llm
                      （独立文字图可以不经判型直接交给它读字）。
    """

    id: str
    capability: str
    label: str
    kind: str
    is_default: bool = False
    needs_model: bool = False


PROVIDERS: Dict[str, Tuple[Provider, ...]] = {
    CAPABILITY_LLM: (
        Provider("deepseek", CAPABILITY_LLM, "DeepSeek API", KIND_API, is_default=True, needs_model=True),
    ),
    # OCR 是唯一一个「本机独立服务与 API 都能干」的能力：文字转写要么交给模型的视觉能力，
    # 要么交给本机专用 OCR。同一请求只能选一个（`normalize_choice` 保证）。
    CAPABILITY_OCR: (
        Provider("deepseek", CAPABILITY_OCR, "DeepSeek 视觉（API）", KIND_API, is_default=True, needs_model=True),
        Provider("paddle", CAPABILITY_OCR, "Paddle OCR（本机 8021）", KIND_SIDECAR),
    ),
    CAPABILITY_ASR: (
        Provider("qwen3", CAPABILITY_ASR, "Qwen3-ASR（本机 8022）", KIND_SIDECAR, is_default=True),
    ),
}


def provider_ids(capability: str) -> Tuple[str, ...]:
    """这个能力下所有提供商的 id，顺序即声明顺序。"""
    return tuple(provider.id for provider in PROVIDERS.get(capability, ()))


def find(capability: str, provider_id: str) -> Optional[Provider]:
    """按 id 找一个提供商；不存在返回 None。"""
    for provider in PROVIDERS.get(capability, ()):
        if provider.id == provider_id:
            return provider
    return None


def needs_model(capability: str, provider_id: str) -> bool:
    """
    这个提供商干活本身要不要模型。

    功能：给处理流程判断「没开 llm 时这条路还能不能跑」——本机独立服务不需要模型，模型提供商需要。
    应用场景：`pipelines/convert.Caps.want_text_image`、`processors/image.py` 的文字图分支。
    输入：capability — 能力名；provider_id — 提供商 id。
    输出：需要模型为 True；id 不存在也是 False（调用方另有校验）。
    """
    provider = find(capability, provider_id)
    return bool(provider and provider.needs_model)


def default_of(capability: str) -> str:
    """声明里的默认提供商（不看环境变量；环境覆盖在 `extensions/registry.py`）。"""
    for provider in PROVIDERS.get(capability, ()):
        if provider.is_default:
            return provider.id
    providers = PROVIDERS.get(capability, ())
    return providers[0].id if providers else ""


def normalize_choice(capability: str, value) -> Union[bool, str]:
    """
    把请求里的能力选择归一到 `False` / `True` / 提供商 id。

    功能：接受布尔、字符串布尔、以及提供商 id 三种写法，非法值抛 ValueError——
          报错文案也从这里生成，名单只有这一处。
    应用场景：HTTP 字段约定校验（`utils/convert_contract.py` 的字段校验与 multipart 入参）。
    输入：capability — 能力名；value — 请求里给的值。
    输出：False（不用这个能力）/ True（用默认提供商）/ 提供商 id。
    异常：ValueError — 名字不在名单里。
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("", "false", "0", "no", "off"):
        return False
    if text in ("true", "1", "yes", "on"):
        return True
    if text in provider_ids(capability):
        return text
    raise ValueError(
        f"{capability} 只接受 true / false 或 {' / '.join(provider_ids(capability))}，收到 {value!r}"
    )
