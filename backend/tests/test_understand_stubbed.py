# -*- coding: utf-8 -*-
r"""
缝 A2：进程内的模型增强路（**假客户端，免费**）。

缝：`UnderstandPipeline.run_from_upload()` —— 真实的编排（下载/识别/抽取/增强/摘要/组装全走一遍），
只把模型客户端换成脚本化的替身：不联网、不花钱、回答固定、调用次数可数。

**对比规则（ADR-0011）**：纯代码能完整交付正文的源，两条路 `content` 仍相同；需要模型转写
非文本内容的源，understand 的 `content` 更长，且 OCR/ASR 仍双写进 `meta.*`。

为什么用真账本（`models/deepseek.py` 的 `UsageLedger`）：它是纯计算（单价表 × token），不依赖网络。

不在这里验的两类源：图片（判型后要本机 Paddle OCR 8021）与音视频（要本机 Qwen3-ASR 8022）——
它们不由模型客户端控制，仍留在贵缝里验。
"""
import json
import tempfile
from pathlib import Path

import pytest

import configs.settings as settings
import pipelines.understand as understand_module
from pipelines.extract import ExtractPipeline
from pipelines.steps import step3_prepare
from pipelines.understand import UnderstandPipeline
from utils.errors import LlmError
from models.deepseek import UsageLedger

pytestmark = pytest.mark.capability_understand

STUB_SUMMARY = "假模型给的摘要"
STUB_IMAGE_TEXT = "假模型读出的图里的字"
STUB_USAGE = {"prompt_tokens": 120, "prompt_cache_hit_tokens": 20, "completion_tokens": 30}

# 假模型只补内嵌图文字，不改变纯代码正文段
CODE_IDENTICAL_FIXTURES = (
    "plain.txt",
    "table.csv",
    "sample.tsv",
    "sample.odt",
    "sample.epub",
    "sample.ofd",
    "inventory.xlsx",
    "deck.pptx",
    "legacy.doc",
)

MODEL_MERGE_FIXTURES = (
    "report.docx",
    "manual.pdf",
)


class _StubClient:
    """脚本化的假模型客户端：回答固定、按需失败、自己数调用次数。"""

    def __init__(self, fail: bool = False) -> None:
        self.ledger = UsageLedger(model=settings.DEEPSEEK_MODEL)
        self.fail = fail
        self.calls = 0

    def chat(self, messages, json_mode: bool = False, thinking=None, **kwargs) -> str:
        self.calls += 1
        if self.fail:
            raise LlmError("假客户端按脚本失败")
        self.ledger.add(dict(STUB_USAGE))
        if json_mode:
            return json.dumps({"images": [{"index": 1, "text": STUB_IMAGE_TEXT}]})
        return STUB_SUMMARY


@pytest.fixture
def stub_client(monkeypatch):
    client = _StubClient()
    monkeypatch.setattr(understand_module, "DeepSeekClient", lambda: client)
    return client


@pytest.fixture
def failing_client(monkeypatch):
    client = _StubClient(fail=True)
    monkeypatch.setattr(understand_module, "DeepSeekClient", lambda: client)
    return client


@pytest.fixture(autouse=True)
def request_dir_in_work_dir(work_dir, monkeypatch):
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kwargs: str(work_dir))
    monkeypatch.setattr(step3_prepare, "cleanup", lambda prepared: None)


def upload(pipeline, path: Path, name: str, query=None):
    for leftover in path.parent.glob("upload.*"):
        leftover.unlink(missing_ok=True)
    with path.open("rb") as handle:
        return pipeline.run_from_upload(handle, name, query)


@pytest.mark.parametrize("name", CODE_IDENTICAL_FIXTURES)
def test_both_paths_same_content_when_only_code_delivers(name, stub_client, fixture_copy):
    """纯代码即可交付正文的源，两条路 content 仍逐字节相同。"""
    path, _ = fixture_copy(name)
    fast = upload(ExtractPipeline(), path, name)
    deep = upload(UnderstandPipeline(), path, name)
    assert fast.status == 200 and deep.status == 200
    assert deep.response.error is None
    assert deep.response.content == fast.response.content, f"{name} 两条路的交付文本不一致"


@pytest.mark.parametrize("name", MODEL_MERGE_FIXTURES)
def test_understand_merges_model_text_into_content(name, stub_client, fixture_copy):
    """文档/扫描类：understand 在纯代码段之上追加模型转写，且 meta.ocr_text 双写。"""
    path, _ = fixture_copy(name)
    fast = upload(ExtractPipeline(), path, name)
    deep = upload(UnderstandPipeline(), path, name)
    assert STUB_IMAGE_TEXT in (deep.response.meta.ocr_text or "")
    assert STUB_IMAGE_TEXT in deep.response.content
    assert fast.response.content.strip()
    assert fast.response.content.strip() in deep.response.content


def test_usage_counts_exactly_the_stub_calls(stub_client, fixture_copy):
    path, _ = fixture_copy("report.docx")
    deep = upload(UnderstandPipeline(), path, "report.docx")
    assert stub_client.calls >= 1
    assert deep.response.usage is not None
    assert deep.response.usage.calls == stub_client.calls
    assert deep.response.usage.prompt_tokens == STUB_USAGE["prompt_tokens"] * stub_client.calls
    assert deep.response.usage.cost_cny > 0


def test_nothing_to_understand_skips_the_model(stub_client, fixture_copy):
    path, _ = fixture_copy("legacy.doc")
    result = upload(UnderstandPipeline(), path, "legacy.doc")
    assert stub_client.calls == 0, "没有可加工的内容就不该调用模型"
    assert result.response.usage is None
    assert any("跳过模型调用" in note for note in result.response.meta.notes), result.response.meta.notes


def test_model_failure_keeps_the_extraction_and_names_the_code(failing_client, fixture_copy):
    path, _ = fixture_copy("report.docx")
    result = upload(UnderstandPipeline(), path, "report.docx")
    assert result.status == 200
    assert result.response.error is None
    assert result.response.content.strip(), "模型失败不该把确定性正文也丢掉"
    assert result.response.meta.model_error is not None
    assert result.response.meta.model_error.code == "llm_call_failed"
    assert any("模型增强失败" in note for note in result.response.meta.notes), result.response.meta.notes
    assert result.response.usage is None, "这次调用没成功，不该记成用量"


def test_query_only_changes_the_summary(stub_client, fixture_copy):
    path, _ = fixture_copy("table.csv")
    plain = upload(UnderstandPipeline(), path, "table.csv")
    asked = upload(UnderstandPipeline(), path, "table.csv", query="这份材料的重点是什么？")
    assert asked.response.content == plain.response.content
    assert asked.response.summary == STUB_SUMMARY
