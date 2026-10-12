# -*- coding: utf-8 -*-
r"""
D 组：通用件——文本解码与计价记账。

测的是：`utils/textfile.py` 的两个函数、`extensions/deepseek.py` 的时段判定与三档折算。纯函数，
不联网、不起服务、不碰真实模型。

为什么选它：解码顺序与计价口径是两处「错了也不报错、但结果悄悄不对」的地方——
解码退化成 replace 会让交付文本出现替换字符，计价把命中价按未命中价算会高估几十倍。
两件事都被文档写成了明确口径（`docs/PIPELINES.md` 的解码顺序、`README.md` 的成本口径），
所以预期值可以直接来自文档与官方单价，不需要先跑一遍代码。

时段那部分只替换「当前时间」这一个纯时钟依赖，单价与折算逻辑都走真实实现。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import configs.settings as settings
import extensions.deepseek as deepseek_billing
from extensions.deepseek import UsageLedger, compute_cost_cny, is_peak_hour
from utils.textfile import HEADER_PREVIEW_BYTES, read_header_hex, read_text_file
from utils.body_limit import (
    BodyTooLarge,
    declared_over_limit,
    method_may_have_body,
    wrap_receive,
)
from utils.zip_read import ZipBudgetExceeded, ZipReadBudget, read_member

# 能力类别见 AGENTS.md「测试用例怎么写」；按类别跑：python -m pytest tests -m capability_utils
pytestmark = pytest.mark.capability_utils

CN = ZoneInfo("Asia/Shanghai")
MODEL = "deepseek-flash"


def write(work_dir, name: str, data: bytes):
    path = work_dir / name
    path.write_bytes(data)
    return path


@pytest.fixture
def offpeak(monkeypatch):
    """把计价时段钉在空闲（官方空闲档为高峰的半价）。"""
    monkeypatch.setattr(deepseek_billing, "is_peak_hour", lambda now=None: False)


@pytest.fixture
def peak(monkeypatch):
    """把计价时段钉在高峰。"""
    monkeypatch.setattr(deepseek_billing, "is_peak_hour", lambda now=None: True)


# ---------- 文本解码 ----------

def test_utf8_text_round_trips_exactly(work_dir):
    """行为：合法 utf-8 原样解出，不经过探测、不产生替换字符，也不谎报命中上界。"""
    text = "第一行：中文\n第二行：emoji 🙂\n"
    decoded, hits = read_text_file(write(work_dir, "u8.txt", text.encode("utf-8")))
    assert decoded == text
    assert hits == [], "没超上限就不能报 max_text_bytes"


def test_windows_bom_is_stripped(work_dir):
    """行为：带 BOM 的 utf-8 解出后不含 BOM——BOM 进了交付文本就是脏字符。"""
    path = write(work_dir, "bom.txt", b"\xef\xbb\xbf" + "正文".encode("utf-8"))
    assert read_text_file(path)[0] == "正文"


def test_text_over_the_byte_limit_is_read_truncated_and_reported(work_dir, monkeypatch):
    """
    行为：超过 `max_text_bytes` 时按上限截读，并把命中名交回调用方——不静默少读。

    来源：`configs/limits.py` 的 `max_text_bytes`（TRUNCATE）+ ADR-0003「边界要机器可读」。
    做法：把上限压到 8 字节，用 20 字节的文本触发——不写大文件、不动全局配置。
    """
    from configs import limits

    monkeypatch.setattr(limits, "MAX_TEXT_BYTES", 8)
    path = write(work_dir, "long.txt", b"0123456789abcdefghij")
    decoded, hits = read_text_file(path)
    assert decoded == "01234567"
    assert hits == ["max_text_bytes"]


def test_undecodable_bytes_do_not_raise(work_dir):
    """
    行为：不是合法 utf-8 也不抛异常，退化成带替换字符的文本。

    来源：`utils/textfile.read_text_file` 的约定「保证不抛异常」——
    一个坏字节不该让整份文件读不出来。
    """
    path = write(work_dir, "broken.txt", b"\xff\xfe\x00\x01\x02 not utf-8 at all \xff")
    text, _ = read_text_file(path)
    assert isinstance(text, str)
    assert text, "读不出来也要给出内容，而不是空串"


def test_header_hex_previews_known_bytes(work_dir):
    """行为：文件头转成大写、单空格分隔的十六进制串。"""
    path = write(work_dir, "head.zip", b"PK\x03\x04" + b"\x00" * 32)
    assert read_header_hex(path) == "50 4B 03 04" + " 00" * (HEADER_PREVIEW_BYTES - 4)


def test_header_hex_of_missing_file_is_empty(work_dir):
    """行为：读不到文件时返回空串，不抛异常。"""
    assert read_header_hex(work_dir / "does-not-exist.bin") == ""


# ---------- ZIP 解压预算 ----------

def _zip_with(work_dir, name: str, members: dict):
    """把 {路径: 字节} 写成一个 ZIP，返回路径。"""
    import zipfile

    path = work_dir / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
    return path


def test_zip_member_within_budget_reads_exactly(work_dir):
    """行为：未超预算时原样读出，不谎报命中。"""
    import zipfile

    path = _zip_with(work_dir, "ok.zip", {"a.txt": b"hello"})
    budget = ZipReadBudget(limit=64)
    with zipfile.ZipFile(path) as archive:
        assert read_member(archive, "a.txt", budget) == b"hello"
    assert budget.used == 5
    assert budget.hit is False


def test_zip_declared_size_over_remaining_is_refused(work_dir):
    """
    行为：成员声明的未压缩体积已超过剩余额度时拒读，并标记命中。

    来源：A-④「读前用 ZipInfo.file_size 预检」——经典 zip bomb 声明巨大、压后很小，
    必须在解压之前挡住，不能等 `read()` 把声明体积当目标去分配。
    """
    import zipfile

    path = _zip_with(work_dir, "declared.zip", {"big.txt": b"0123456789"})
    budget = ZipReadBudget(limit=4)
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ZipBudgetExceeded):
            read_member(archive, "big.txt", budget)
    assert budget.hit is True
    assert budget.used == 0, "拒读时不得先把额度花掉"


def test_zip_budget_accumulates_across_members(work_dir):
    """
    行为：跨成员累计解压字节；第二份会触达上界时拒读，第一份已读字节仍计入。

    来源：`max_zip_uncompressed_bytes` 是一次抽取请求的总量，不是单文件上限。
    """
    import zipfile

    path = _zip_with(work_dir, "two.zip", {"a.txt": b"abcd", "b.txt": b"efgh"})
    budget = ZipReadBudget(limit=6)
    with zipfile.ZipFile(path) as archive:
        assert read_member(archive, "a.txt", budget) == b"abcd"
        with pytest.raises(ZipBudgetExceeded):
            read_member(archive, "b.txt", budget)
    assert budget.used == 4
    assert budget.hit is True


# ---------- 请求体早拒 ----------

def test_declared_content_length_over_limit_is_flagged():
    """行为：声明的 Content-Length 已超额度时返回该声明值，供中间件直接 413。"""
    assert declared_over_limit({"content-length": "100"}, limit=50) == 100
    assert declared_over_limit({"content-length": "50"}, limit=50) is None
    assert declared_over_limit({}, limit=50) is None


def test_get_requests_skip_body_counting():
    """行为：GET / HEAD 等不会带请求体，不必包 receive。"""
    assert method_may_have_body("GET") is False
    assert method_may_have_body("POST") is True


def test_wrap_receive_raises_when_accumulated_body_exceeds_limit():
    """
    行为：没有可信声明时，边收边数；累计超过额度立刻抛 BodyTooLarge，不再交出后续块。

    来源：A-②；与下载层「边下边数」同形。用假 receive 模拟分块到达，不启真实服务。
    """
    import asyncio

    chunks = [b"aa", b"bb", b"cccc"]  # 2 + 2 + 4；额度 5 时第三块触发
    index = {"i": 0}

    async def fake_receive():
        i = index["i"]
        body = chunks[i]
        index["i"] += 1
        return {"type": "http.request", "body": body, "more_body": index["i"] < len(chunks)}

    limited, size = wrap_receive(fake_receive, limit=5)

    async def drive():
        assert (await limited())["body"] == b"aa"
        assert (await limited())["body"] == b"bb"
        assert size() == 4
        with pytest.raises(BodyTooLarge) as caught:
            await limited()
        assert caught.value.received == 8
        assert caught.value.limit == 5

    asyncio.run(drive())


# ---------- 高峰/低谷时段 ----------

@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 9, 28, 9, 0, tzinfo=CN), True),
        (datetime(2026, 9, 28, 11, 59, tzinfo=CN), True),
        (datetime(2026, 9, 28, 8, 59, tzinfo=CN), False),
        (datetime(2026, 9, 28, 12, 0, tzinfo=CN), False),
        (datetime(2026, 9, 28, 14, 0, tzinfo=CN), True),
        (datetime(2026, 9, 28, 17, 59, tzinfo=CN), True),
        (datetime(2026, 9, 28, 18, 0, tzinfo=CN), False),
        (datetime(2026, 9, 26, 10, 0, tzinfo=CN), False),
        (datetime(2026, 9, 27, 14, 0, tzinfo=CN), False),
    ],
)
def test_peak_window_is_weekday_morning_and_afternoon(moment, expected):
    """
    行为：高峰 = 北京时间周一至周五 09:00-12:00、14:00-18:00，其余为空闲。

    来源：`extensions/deepseek.py` 单价表注释与官方计价说明。
    2026-09-28 是周一，09-26 / 09-27 是周六周日。
    """
    assert is_peak_hour(moment) is expected


# ---------- 三档折算 ----------

def test_each_token_bucket_is_priced_separately(offpeak):
    """
    行为：命中 / 未命中 / 输出三档分开计价，空闲档为高峰的半价。

    预期值来源：官方单价表（元 / 百万 token，deepseek-flash 空闲档
    命中 0.02、未命中 1.0、输出 4.0）——手算字面量，不由实现重算。
    """
    million = 1_000_000
    assert compute_cost_cny(MODEL, million, 0, 0) == pytest.approx(0.02)
    assert compute_cost_cny(MODEL, 0, million, 0) == pytest.approx(1.0)
    assert compute_cost_cny(MODEL, 0, 0, million) == pytest.approx(4.0)


def test_peak_prices_are_double_the_offpeak_ones(peak):
    """行为：高峰档单价翻倍。来源：`extensions/deepseek.PRICE_TABLE` 里 peak / offpeak 两档。"""
    million = 1_000_000
    assert compute_cost_cny(MODEL, million, 0, 0) == pytest.approx(0.04)
    assert compute_cost_cny(MODEL, 0, million, 0) == pytest.approx(2.0)
    assert compute_cost_cny(MODEL, 0, 0, million) == pytest.approx(8.0)


def test_cached_input_is_two_orders_of_magnitude_cheaper(offpeak):
    """
    行为：命中价约为未命中的 1/50。

    来源：`extensions/deepseek.compute_cost_cny`：命中价约为未命中的 1/50。
    """
    cached = compute_cost_cny(MODEL, 1_000_000, 0, 0)
    missed = compute_cost_cny(MODEL, 0, 1_000_000, 0)
    assert missed / cached == pytest.approx(50.0)


def test_model_absent_from_the_price_table_costs_nothing(offpeak):
    """行为：单价表里没有的模型按 0 计，不抛异常——换模型只要补一行单价。"""
    assert compute_cost_cny("no-such-model", 1000, 1000, 1000) == 0.0


# ---------- 用量累计记账 ----------

def test_ledger_is_empty_until_a_model_is_called():
    """行为：一次模型都没调过时 usage 为 null。来源：README「没调模型就没有用量，给 0 是噪音」。"""
    assert UsageLedger(model=MODEL).snapshot() is None


def test_ledger_accumulates_tokens_and_cost_across_calls(offpeak):
    """
    行为：一次请求内的多次调用累计成一份账；未命中 token = prompt − 命中。

    预期值来源：手算。空闲档单价 命中 0.02 / 未命中 1.0 / 输出 4.0：
      第一次 命中 400、未命中 600、输出 100 → 0.000008 + 0.0006 + 0.0004 = 0.001008
      第二次 命中 0、未命中 500、输出 50    → 0.0005 + 0.0002 = 0.0007
    """
    ledger = UsageLedger(model=MODEL)
    ledger.add({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 400, "completion_tokens": 100})
    ledger.add({"prompt_tokens": 500, "completion_tokens": 50})

    snapshot = ledger.snapshot()
    assert snapshot.model == MODEL
    assert snapshot.calls == 2
    assert snapshot.prompt_tokens == 1500
    assert snapshot.cached_tokens == 400
    assert snapshot.completion_tokens == 150
    assert snapshot.cost_cny == pytest.approx(0.001708)


def test_ledger_reads_cached_tokens_from_details_when_the_flat_field_is_absent(offpeak):
    """行为：上游把命中数放在 `prompt_tokens_details.cached_tokens` 时也要认得。"""
    ledger = UsageLedger(model=MODEL)
    ledger.add({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 30}})
    assert ledger.snapshot().cached_tokens == 30


def test_ledger_records_reasoning_tokens(offpeak):
    """行为：思维链 token 单独记一档。来源：README「思维链按输出侧计费」。"""
    ledger = UsageLedger(model=MODEL)
    ledger.add({"prompt_tokens": 10, "completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 15}})
    snapshot = ledger.snapshot()
    assert snapshot.reasoning_tokens == 15
    assert snapshot.completion_tokens == 20


def test_ledger_tolerates_partial_usage_payloads(offpeak):
    """行为：上游缺字段时按 0 记，不抛 KeyError（信任上游字段是缺省而不是必填）。"""
    ledger = UsageLedger(model=MODEL)
    ledger.add({})
    snapshot = ledger.snapshot()
    assert (snapshot.calls, snapshot.prompt_tokens, snapshot.completion_tokens) == (1, 0, 0)


def test_snapshot_carries_the_price_effective_date():
    """行为：usage 带上所用单价表的生效日，便于按当时单价复核历史调用。"""
    ledger = UsageLedger(model=MODEL)
    ledger.add({})
    assert ledger.snapshot().price_effective_from == deepseek_billing.PRICE_TABLE[MODEL]["effective_from"]


# ---------- 单次请求费用上限（默认关；开后用阈值卡住后续 DeepSeek 调用） ----------

def test_cost_gate_off_by_default_allows_any_spend(offpeak, monkeypatch):
    """行为：费用开关默认关时，哪怕累计费用很高也不拦下一枪。"""
    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_COST_LIMIT_ENABLED", False)
    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_TOKEN_LIMIT_ENABLED", False)
    ledger = UsageLedger(model=MODEL)
    ledger.add({"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    ledger.assert_within_cost_limits()  # 不抛


def test_cost_gate_blocks_next_call_when_enabled_and_over_cap(offpeak, monkeypatch):
    """行为：开启费用上限后，累计 cost_cny 达阈值则下一枪抛 llm_cost_limit。"""
    from utils.errors import LlmError

    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_COST_LIMIT_ENABLED", True)
    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_MAX_COST_CNY", 0.001)
    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_TOKEN_LIMIT_ENABLED", False)
    ledger = UsageLedger(model=MODEL)
    # 空闲：未命中 1000 + 输出 0 → 0.001 元，刚好踩线
    ledger.add({"prompt_tokens": 1000, "completion_tokens": 0})
    assert ledger.cost_cny == pytest.approx(0.001)
    with pytest.raises(LlmError) as exc:
        ledger.assert_within_cost_limits()
    assert exc.value.code == "llm_cost_limit"
    assert "deepseek_max_cost_cny" in ledger.limits_hit


def test_token_gate_blocks_next_call_when_enabled_and_over_cap(offpeak, monkeypatch):
    """行为：开启 token 上限后，prompt+completion 达阈值则下一枪抛 llm_token_limit。"""
    from utils.errors import LlmError

    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_COST_LIMIT_ENABLED", False)
    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_TOKEN_LIMIT_ENABLED", True)
    monkeypatch.setattr(deepseek_billing, "DEEPSEEK_MAX_TOKENS_PER_REQUEST", 100)
    ledger = UsageLedger(model=MODEL)
    ledger.add({"prompt_tokens": 80, "completion_tokens": 20})
    with pytest.raises(LlmError) as exc:
        ledger.assert_within_cost_limits()
    assert exc.value.code == "llm_token_limit"
    assert "deepseek_max_tokens_per_request" in ledger.limits_hit
