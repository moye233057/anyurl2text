# -*- coding: utf-8 -*-
r"""
缝 E：扩展注册与提供商选择（离线，不需要任何服务）。

守 ADR-0015 的三条不变量：

1. 名单只有一处（`configs/extensions.py`）：能力、提供商、谁需要模型都在里面；
2. 同一能力只能选一个提供商，没登记的名字当场拒绝（HTTP 缝上表现为 422）；
3. 探活快照给界面的是「能不能用 + 为什么」——不可用时必须有原因，界面才解释得出为什么是灰的。

**不测「侧车到底在不在跑」**：那取决于本机进程状态，测它等于测环境。这里只测形状与规则。
"""
import pytest
from pydantic import ValidationError

from configs.extensions import (
    CAPABILITIES,
    CAPABILITY_OCR,
    PROVIDERS,
    default_of,
    normalize_choice,
    provider_ids,
)
from extensions import registry
from utils.convert_contract import ConvertUrlBody

pytestmark = pytest.mark.capability_extensions


def test_every_capability_declares_at_least_one_provider():
    """行为：每个能力都至少有一个提供商，且默认那个确实在名单里。"""
    for capability in CAPABILITIES:
        assert PROVIDERS[capability], capability
        assert default_of(capability) in provider_ids(capability)


def test_ocr_offers_both_a_model_and_a_sidecar():
    """行为：OCR 有模型与侧车两家实现，且侧车不吃模型——没开 llm 也能用它读字。"""
    providers = {provider.id: provider for provider in PROVIDERS[CAPABILITY_OCR]}
    assert set(providers) == {"deepseek", "paddle"}
    assert providers["deepseek"].needs_model is True
    assert providers["paddle"].needs_model is False


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        (None, False),
        ("", False),
        ("deepseek", "deepseek"),
        ("paddle", "paddle"),
    ],
)
def test_choice_normalizes_flags_and_provider_ids(raw, expected):
    """行为：true/false 与提供商 id 都能归一；空值按「不用」处理。"""
    assert normalize_choice(CAPABILITY_OCR, raw) == expected


def test_unknown_provider_is_rejected_with_the_valid_names():
    """行为：没登记的名字当场拒绝，且报错里列出可用名字（名单只有一处，文案也由它生成）。"""
    with pytest.raises(ValueError) as caught:
        normalize_choice(CAPABILITY_OCR, "tesseract")
    assert "deepseek" in str(caught.value) and "paddle" in str(caught.value)


def test_contract_rejects_unknown_ocr_provider():
    """行为：请求体里写了没登记的提供商 → 校验失败（HTTP 缝上由 FastAPI 转成 422）。"""
    with pytest.raises(ValidationError):
        ConvertUrlBody(url="https://example.com/", ocr="tesseract")


def test_contract_keeps_boolean_ocr_working():
    """行为：老客户端传 ocr=true/false 仍然成立（兼容路径不许悄悄破坏）。"""
    assert ConvertUrlBody(url="https://example.com/", ocr=True).ocr is True
    assert ConvertUrlBody(url="https://example.com/").ocr is False
    assert ConvertUrlBody(url="https://example.com/", ocr="paddle").ocr == "paddle"


def test_resolve_maps_false_to_none_and_true_to_default():
    """行为：`false` 表示这次不用这个能力（None）；`true` 走默认提供商；给 id 就用它。"""
    assert registry.resolve(CAPABILITY_OCR, False) is None
    assert registry.resolve(CAPABILITY_OCR, None) is None
    assert registry.resolve(CAPABILITY_OCR, True) == registry.default_provider(CAPABILITY_OCR)
    assert registry.resolve(CAPABILITY_OCR, "paddle") == "paddle"


def test_health_snapshot_always_carries_a_reason_when_unavailable(monkeypatch):
    """
    行为：能力快照里每个提供商都有 available；不可用时必须给出原因，可用时可以带补充说明。

    探活超时压到 0.2 秒：这条只验形状，不该为了等本机侧车超时把套件拖慢。
    """
    monkeypatch.setattr(registry.settings, "EXTENSION_PROBE_TIMEOUT_SEC", 0.2)
    snapshot = registry.snapshot()
    assert set(snapshot) == set(CAPABILITIES)
    for capability, entry in snapshot.items():
        assert entry["default"] in provider_ids(capability)
        assert entry["providers"], capability
        for provider in entry["providers"]:
            assert isinstance(provider["available"], bool)
            if not provider["available"]:
                assert provider["reason"], f"{capability}/{provider['id']} 不可用却没给原因"


def test_sidecar_health_url_is_derived_from_the_service_url():
    """行为：侧车探活打的是同一个 origin 的 /health（侧车契约），不把业务路径当健康端点。"""
    assert registry._health_url("http://127.0.0.1:8021/ocr-b64") == "http://127.0.0.1:8021/health"
    assert registry._health_url("http://127.0.0.1:8022/asr-b64") == "http://127.0.0.1:8022/health"


def test_test_provider_dispatches_by_kind(monkeypatch):
    """
    行为：真实测试按类别分派——侧车打 /health，API 发一次最小请求。

    两条都换成替身：这条验的是分派，不该为了它联网、更不该为了它花 token。
    """
    calls = []

    def _sidecar(provider):
        calls.append(("sidecar", provider.id))
        return registry.ProbeResult(True)

    def _api(provider):
        calls.append(("api", provider.id))
        return registry.ProbeResult(True)

    monkeypatch.setattr(registry, "_probe_sidecar", _sidecar)
    monkeypatch.setattr(registry, "_test_api_for_real", _api)
    assert registry.test_provider(CAPABILITY_OCR, "paddle").available is True
    assert registry.test_provider("llm", "deepseek").available is True
    assert calls == [("sidecar", "paddle"), ("api", "deepseek")]


def test_test_provider_rejects_unknown_names():
    """行为：没登记的名字返回 None（HTTP 缝上由路由转成 422），不为它发任何请求。"""
    assert registry.test_provider(CAPABILITY_OCR, "tesseract") is None
    assert registry.test_provider("nope", "deepseek") is None
