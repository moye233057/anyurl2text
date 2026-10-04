# -*- coding: utf-8 -*-
r"""
缝 A2：进程内的模型增强路（**假客户端，免费**）。

缝：`UnderstandPipeline.run_from_upload()` —— 真实的编排（下载/识别/抽取/增强/摘要/组装全走一遍），
只把模型客户端换成脚本化的替身：不联网、不花钱、回答固定、调用次数可数。

**为什么要有这一份**：本项目最核心的不变量——同一个源，`/extract` 与 `/understand` 的 `content`
逐字节相同——原本只在贵缝（`tests/test_understand_contract.py`，要真模型、由人择时跑）里验。
这份文件把它搬进每次 `pytest tests` 都会跑的免费缝，代价为零；贵缝仍然保留，它验的是「真模型
接得上」，这里验的是「编排本身不会碰交付文本」。

为什么用真账本（`utils/usage.py` 的 `UsageLedger`）：它是纯计算（单价表 × token），不依赖网络。
用真账本才能顺带把「记账链路」验进去，而不是验我自己写的假账本。

不在这里验的两类源：图片（判型后要本机 Paddle OCR 8021）与音视频（要本机 Qwen3-ASR 8022）——
它们不由模型客户端控制，仍留在贵缝里验。

预期值来源：假客户端的脚本（本文件给的串与 token 数）、样例的生成参数、同一个源跑两条路各一次。
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
from utils.usage import UsageLedger

pytestmark = pytest.mark.capability_understand

# 假客户端的三份脚本：摘要、图里的字、每次调用的用量
STUB_SUMMARY = "假模型给的摘要"
STUB_IMAGE_TEXT = "假模型读出的图里的字"
STUB_USAGE = {"prompt_tokens": 120, "prompt_cache_hit_tokens": 20, "completion_tokens": 30}

# 只放不需要外部服务的那几类（图片与音视频见文件头说明）
STUBBED_FIXTURES = (
    "plain.txt",
    "table.csv",
    "sample.tsv",
    "report.docx",
    "manual.pdf",
    "sample.odt",
    "sample.epub",
    "sample.ofd",
    "inventory.xlsx",
    "deck.pptx",
    "legacy.doc",
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
    """装一个假客户端，返回它自己，供用例读调用次数。"""
    client = _StubClient()
    monkeypatch.setattr(understand_module, "DeepSeekClient", lambda: client)
    return client


@pytest.fixture
def failing_client(monkeypatch):
    """装一个「一调就抛 LlmError」的假客户端。"""
    client = _StubClient(fail=True)
    monkeypatch.setattr(understand_module, "DeepSeekClient", lambda: client)
    return client


@pytest.fixture(autouse=True)
def request_dir_in_work_dir(work_dir, monkeypatch):
    """
    把「一次请求的临时目录」指到测试工作目录，并跳过它自己的清理。

    为什么这么绕：沙箱**不允许 python 进程写进它自己刚建的目录**——`tempfile.mkdtemp()` 建的目录里
    写第一个文件就 `PermissionError`（实测：同样是新建目录，pwsh 建的、以及进程启动前就存在的目录，
    python 都写得进去）。工作目录随仓库存在，所以直接拿它当请求目录用。
    清理交给 `conftest.clean_work_dir`（每条用例前后清空），因此这里把 `cleanup` 换成空操作——
    否则真清理会把共用的工作目录整个删掉，后面的用例就没地方落盘了。
    """
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kwargs: str(work_dir))
    monkeypatch.setattr(step3_prepare, "cleanup", lambda prepared: None)


def upload(pipeline, path: Path, name: str, query=None):
    """
    把样例按上传口喂给某条管线。

    先清掉上一次落在同一目录里的 `upload.*`：真实运行里每个请求有自己的临时目录，而这里所有请求
    共用测试工作目录（沙箱不让 python 写进它自己新建的目录，见上面那个 fixture），不清就会在
    「补扩展名」那一步撞上同名文件（WinError 183），第二次抽取于是拿到空产出。
    """
    for leftover in path.parent.glob("upload.*"):
        leftover.unlink(missing_ok=True)
    with path.open("rb") as handle:
        return pipeline.run_from_upload(handle, name, query)


@pytest.mark.parametrize("name", STUBBED_FIXTURES)
def test_both_paths_deliver_byte_identical_text(name, stub_client, fixture_copy):
    """
    行为：同一个源，两条路的 `content` 逐字节相同——**在免费缝里逐格式验**。

    来源：README「两条路产出的正文逐字节一致」+ ADR-0002「模型产出只进 `summary` / `meta.*`」。
    预期值来源：同一个样例各跑一次（抽取本身是确定性的，`fixture_copy` 复制进工作目录）。
    实测：把 `enhance_*` 的结果写进 `content`，这一条立刻红。
    """
    path, _ = fixture_copy(name)
    fast = upload(ExtractPipeline(), path, name)
    deep = upload(UnderstandPipeline(), path, name)
    assert fast.status == 200 and deep.status == 200
    assert deep.response.error is None
    assert deep.response.content == fast.response.content, f"{name} 两条路的交付文本不一致"


def test_model_text_never_lands_in_delivered_text(stub_client, fixture_copy):
    """
    行为：假模型读出的字与摘要分别进 `meta.ocr_text` / `summary`，`content` 里一个字符都不多。

    来源：README「模型读出的文字也不进正文」+ CONTEXT「识别文本」。
    预期值来源：假客户端的脚本串——不依赖实现里的任何取值。
    """
    path, _ = fixture_copy("report.docx")
    fast = upload(ExtractPipeline(), path, "report.docx")
    deep = upload(UnderstandPipeline(), path, "report.docx")
    assert STUB_IMAGE_TEXT in (deep.response.meta.ocr_text or ""), "内嵌图的文字应进识别文本"
    assert deep.response.summary == STUB_SUMMARY
    assert STUB_IMAGE_TEXT not in deep.response.content
    assert STUB_SUMMARY not in deep.response.content
    assert deep.response.content == fast.response.content


def test_usage_counts_exactly_the_stub_calls(stub_client, fixture_copy):
    """
    行为：调了几次、用了多少 token 按真实账本记，`usage` 与假客户端的调用次数一一对上。

    来源：README「成本按真实 token 记账」+ `utils/usage.py` 的账本规则。
    预期值来源：假客户端的脚本用量（120 / 20 / 30）与它自己的调用计数。
    """
    path, _ = fixture_copy("report.docx")
    deep = upload(UnderstandPipeline(), path, "report.docx")
    assert stub_client.calls >= 1
    assert deep.response.usage is not None
    assert deep.response.usage.calls == stub_client.calls
    assert deep.response.usage.prompt_tokens == STUB_USAGE["prompt_tokens"] * stub_client.calls
    assert deep.response.usage.cost_cny > 0


def test_nothing_to_understand_skips_the_model(stub_client, fixture_copy):
    """
    行为：源没有可加工的内容时一次模型都不调——`usage` 为 null，并在 `notes` 里说明为什么。

    来源：README「没调模型就没有用量，给 0 是噪音」+ ADR-0003（降级是能力边界）。
    预期值来源：样例 `legacy.doc`（老版 Office，走降级、`content` 为空）。
    """
    path, _ = fixture_copy("legacy.doc")
    result = upload(UnderstandPipeline(), path, "legacy.doc")
    assert stub_client.calls == 0, "没有可加工的内容就不该调用模型"
    assert result.response.usage is None
    assert any("跳过模型调用" in note for note in result.response.meta.notes), result.response.meta.notes


def test_model_failure_keeps_the_extraction_and_names_the_code(failing_client, fixture_copy):
    """
    行为：模型阶段失败仍返回 HTTP 200、`error` 为 null、确定性正文照常交付；结构化说明进
          `meta.model_error`，中文原因留在 `notes`。

    来源：ADR-0002/0007（模型失败不夺产出）+ `configs/errors.py` 第 3 组（模型失败通道）。
    预期值来源：假客户端按脚本抛 `LlmError`（默认码 `llm_call_failed`，在码表白名单里）。
    """
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
    """
    行为：带 query 时交付文本与不带时逐字节相同——query 只改摘要，绝不动正文。

    来源：ADR-0001（否掉「带 query 时用摘要覆盖正文」那套契约，原文会永久丢失）。
    预期值来源：同一个样例两次调用的对比。
    """
    path, _ = fixture_copy("table.csv")
    plain = upload(UnderstandPipeline(), path, "table.csv")
    asked = upload(UnderstandPipeline(), path, "table.csv", query="这份材料的重点是什么？")
    assert asked.response.content == plain.response.content
    assert asked.response.summary == STUB_SUMMARY
