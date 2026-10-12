# -*- coding: utf-8 -*-
r"""
A2 组：进程内转换处理流程（**模拟的模型客户端，免费**）。

测的是：`ConvertPipeline.run_from_upload()` —— 真实处理流程，模型客户端换成脚本化替身。
能力选择见 docs/API.md §1.1 与 ADR-0015：看图只要 `llm`；`ocr` 是**独立文字图转写**的提供商单选。
"""
import json
import tempfile
from pathlib import Path

import pytest

import configs.settings as settings
import pipelines.convert as convert_module
from pipelines.convert import ConvertPipeline
from pipelines.steps import step3_prepare
from utils.errors import LlmError
from extensions.deepseek import UsageLedger

pytestmark = pytest.mark.capability_understand

STUB_SUMMARY = "假模型给的摘要"
STUB_IMAGE_TEXT = "假模型读出的图里的字"
STUB_USAGE = {"prompt_tokens": 120, "prompt_cache_hit_tokens": 20, "completion_tokens": 30}

# 「纯代码」= 不含内嵌图素材：仅开 llm 时正文只该多出摘要，不该多出图像转写。
# deck.pptx 不在此列——它带一张内嵌图，而看图只看 llm（ADR-0015），所以它归 VISION_MERGE_FIXTURES。
CODE_ONLY_FIXTURES = (
    "plain.txt",
    "table.csv",
    "sample.tsv",
    "sample.odt",
    "sample.epub",
    "sample.ofd",
    "inventory.xlsx",
    "legacy.doc",
)

# 含内嵌图素材的样例：只要 llm 就会把图里的文字转写并进 content。
# manual.pdf 有文字层时通常无内嵌图素材，所以不在此列。
VISION_MERGE_FIXTURES = ("report.docx", "deck.pptx")


class _StubClient:
    """脚本化的假模型客户端：回答固定、按需失败、自己数调用次数。"""

    def __init__(self, fail: bool = False) -> None:
        self.ledger = UsageLedger(model=settings.DEEPSEEK_MODEL)
        self.fail = fail
        self.calls = 0

    def chat(self, messages, json_mode: bool = False, thinking=None, **kwargs) -> str:
        self.calls += 1
        if self.fail:
            raise LlmError("模拟的模型客户端按脚本失败")
        self.ledger.add(dict(STUB_USAGE))
        if json_mode:
            return json.dumps({"images": [{"index": 1, "text": STUB_IMAGE_TEXT}]})
        return STUB_SUMMARY


@pytest.fixture
def stub_client(monkeypatch):
    client = _StubClient()
    monkeypatch.setattr(convert_module, "DeepSeekClient", lambda: client)
    return client


@pytest.fixture
def failing_client(monkeypatch):
    client = _StubClient(fail=True)
    monkeypatch.setattr(convert_module, "DeepSeekClient", lambda: client)
    return client


@pytest.fixture(autouse=True)
def request_dir_in_work_dir(work_dir, monkeypatch):
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kwargs: str(work_dir))
    monkeypatch.setattr(step3_prepare, "cleanup", lambda prepared: None)


def upload(path: Path, name: str, query=None, *, ocr=False, asr=False, llm=False):
    for leftover in path.parent.glob("upload.*"):
        leftover.unlink(missing_ok=True)
    with path.open("rb") as handle:
        return ConvertPipeline().run_from_upload(
            handle, name, query=query, ocr=ocr, asr=asr, llm=llm
        )


@pytest.mark.parametrize("name", CODE_ONLY_FIXTURES)
def test_llm_alone_keeps_code_content(name, stub_client, fixture_copy):
    """文档类：仅开 llm 时 content 与全关相同，可另有摘要（样例保证不含内嵌图素材）。"""
    path, _ = fixture_copy(name)
    off = upload(path, name)
    on = upload(path, name, llm=True)
    assert off.status == 200 and on.status == 200
    assert on.response.error is None
    assert on.response.content == off.response.content, f"{name} 开 llm 后交付文本被改动"


@pytest.mark.parametrize("name", VISION_MERGE_FIXTURES)
def test_vision_flags_merge_ocr_into_content(name, stub_client, fixture_copy):
    """看图：只开 llm（不选 ocr）也转写内嵌图并进 content，并同时写入 meta.ocr_text。"""
    path, _ = fixture_copy(name)
    off = upload(path, name)
    on = upload(path, name, llm=True)
    assert STUB_IMAGE_TEXT in (on.response.meta.ocr_text or "")
    assert STUB_IMAGE_TEXT in on.response.content
    assert off.response.content.strip()
    assert off.response.content.strip() in on.response.content


def test_usage_counts_exactly_the_stub_calls(stub_client, fixture_copy):
    path, _ = fixture_copy("report.docx")
    deep = upload(path, "report.docx", ocr=True, llm=True)
    assert stub_client.calls >= 1
    assert deep.response.usage is not None
    assert deep.response.usage.calls == stub_client.calls
    assert deep.response.usage.prompt_tokens == STUB_USAGE["prompt_tokens"] * stub_client.calls
    assert deep.response.usage.cost_cny > 0


def test_nothing_to_enhance_skips_llm(stub_client, fixture_copy):
    path, _ = fixture_copy("legacy.doc")
    result = upload(path, "legacy.doc", llm=True)
    assert stub_client.calls == 0, "没有可加工的内容就不该调用模型"
    assert result.response.usage is None
    assert result.response.meta.notes, result.response.meta.notes


def test_model_failure_keeps_the_extraction_and_names_the_code(failing_client, fixture_copy):
    path, _ = fixture_copy("report.docx")
    result = upload(path, "report.docx", ocr=True, llm=True)
    assert result.status == 200
    assert result.response.error is None
    assert result.response.content.strip(), "模型失败不该把确定性正文也丢掉"
    assert result.response.meta.model_error is not None
    assert result.response.meta.model_error.code == "llm_call_failed"
    assert any("扩展阶段失败" in note for note in result.response.meta.notes), result.response.meta.notes
    assert result.response.usage is None, "这次调用没成功，不该记成用量"


def test_query_only_changes_the_summary(stub_client, fixture_copy):
    path, _ = fixture_copy("table.csv")
    plain = upload(path, "table.csv", llm=True)
    asked = upload(path, "table.csv", query="这份材料的重点是什么？", llm=True)
    assert asked.response.content == plain.response.content
    assert asked.response.summary == STUB_SUMMARY


def test_llm_off_skips_vision_with_note(stub_client, fixture_copy):
    """没开 llm 时看图支路跳过——选不选 ocr 都改变不了这一条（ADR-0015 把开关条件拆开了）。"""
    path, _ = fixture_copy("report.docx")
    only_ocr = upload(path, "report.docx", ocr=True, llm=False)
    assert stub_client.calls == 0
    assert any("未开启 llm" in n for n in only_ocr.response.meta.notes), only_ocr.response.meta.notes
    assert STUB_IMAGE_TEXT not in (only_ocr.response.meta.ocr_text or "")
