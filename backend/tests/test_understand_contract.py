# -*- coding: utf-8 -*-
r"""
缝 E2：模型增强那条路的契约（**贵缝，默认不跑**）。

对比规则（ADR-0011）：纯代码即可交付正文的源，两条路 `content` 相同；需要模型转写的源，
understand 的 `content` 含模型正文，且 `meta.ocr_text` / `meta.asr_text` 仍保留副本。
"""
import os
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.model

BASE_URL = os.environ.get("ANYURL2TEXT_BASE_URL", "http://127.0.0.1:8020")
OCR_HEALTH_URL = os.environ.get("ANYURL2TEXT_OCR_HEALTH_URL", "http://127.0.0.1:8021/health")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TIMEOUT = 300.0


@pytest.fixture(scope="module")
def understanding_is_possible():
    try:
        health = httpx.get(f"{BASE_URL}/probe/health", timeout=10).json()
    except Exception as exc:
        pytest.skip(f"服务没起来或不可达（{exc}）：先在本机终端起后端")
    if not health.get("model_key_configured"):
        pytest.skip("后端未配置 DEEPSEEK_API_KEY，understand 无法验证")
    if not health.get("ocr_service", {}).get("url"):
        pytest.skip("未配置本机 OCR 服务")
    try:
        httpx.get(OCR_HEALTH_URL, timeout=5)
    except Exception as exc:
        pytest.skip(f"本机 OCR 服务不可达（{exc}）：图片判型那两条需要它")
    return health


def call(pipeline: str, file_name: str, query=None):
    path = DATA_DIR / file_name
    if not path.exists():
        pytest.skip(f"样例不存在：{path}（先跑 python scripts/make_fixtures.py）")
    with path.open("rb") as handle:
        files = {"file": (file_name, handle)}
        data = {"query": query} if query else None
        response = httpx.post(f"{BASE_URL}/{pipeline}/file", files=files, data=data, timeout=TIMEOUT)
    return response.status_code, response.json()


CODE_IDENTICAL = ("plain.txt", "table.csv", "legacy.doc", "sample.wav", "sample.mp4")


@pytest.mark.parametrize("name", CODE_IDENTICAL)
def test_both_paths_same_content_when_only_code_delivers(name, understanding_is_possible):
    _, fast = call("extract", name)
    _, deep = call("understand", name)
    assert fast["content"] == deep["content"], f"{name} 两条路的交付文本不一致"


@pytest.mark.parametrize("name", ["report.docx", "manual.pdf"])
def test_understand_extends_content_with_model_text(name, understanding_is_possible):
    _, fast = call("extract", name)
    _, deep = call("understand", name)
    assert fast["content"].strip(), f"{name} extract 应有纯代码正文"
    assert fast["content"].strip() in deep["content"]
    if deep["meta"].get("ocr_text"):
        assert deep["meta"]["ocr_text"] in deep["content"] or deep["meta"]["ocr_text"].strip() in deep["content"]


@pytest.mark.parametrize("name", ["report.docx", "table.csv"])
def test_query_does_not_change_delivered_text(name, understanding_is_possible):
    _, plain = call("understand", name)
    _, asked = call("understand", name, query="这份材料的重点是什么？")
    assert asked["content"] == plain["content"], f"{name} 带 query 后交付文本被改动了"
    assert asked["summary"], "带 query 时摘要应给出针对性回答"


def test_understand_produces_summary_usage_and_model_timing(understanding_is_possible):
    _, body = call("understand", "report.docx")
    assert body["error"] is None
    assert body["summary"], "understand 应产出摘要"
    assert body["usage"] is not None, "调过模型就该有 usage"
    assert body["usage"]["calls"] >= 1
    assert body["usage"]["cost_cny"] > 0
    assert body["usage"]["model"]
    assert body["usage"]["price_effective_from"], "usage 应带上单价生效日"
    assert body["timings"]["model_ms"] > 0


def test_docx_merges_ocr_into_content_and_meta(understanding_is_possible):
    _, fast = call("extract", "report.docx")
    _, deep = call("understand", "report.docx")
    assert deep["meta"]["ocr_text"], "含内嵌图的文档应读出图里的文字"
    assert deep["summary"]
    assert deep["meta"]["ocr_text"].strip() in deep["content"]
    assert fast["content"].strip() in deep["content"]


def test_model_is_skipped_when_there_is_nothing_to_understand(understanding_is_possible):
    _, body = call("understand", "legacy.doc")
    assert body["error"] is None
    assert body["usage"] is None
    assert body["timings"]["model_ms"] == 0
    assert body["meta"]["notes"], "跳过模型调用要说明原因，不能静默"


def test_text_image_ocr_lands_in_content_and_meta(understanding_is_possible):
    _, fast = call("extract", "screenshot.png")
    _, body = call("understand", "screenshot.png")
    assert body["error"] is None
    assert body["meta"]["ocr_text"], "文字图片应读出文字"
    assert body["content"].strip(), "understand 正文应有 OCR 结果"
    assert body["meta"]["ocr_text"].strip() in body["content"]
    assert fast["content"] == "", "extract 对图片仍为空"
    assert body["meta"]["notes"], "判型结果要说明，不能静默"


def test_semantic_image_description_in_content(understanding_is_possible):
    _, body = call("understand", "photo.png")
    assert body["error"] is None
    assert not body["meta"]["ocr_text"], "语义图片不该有识别文本"
    assert body["summary"], "语义图片应直接给出客观描述"
    assert body["content"].strip(), "语义描述应进 understand 正文"


@pytest.mark.parametrize(("name", "modality"), [("sample.wav", "audio"), ("sample.mp4", "video")])
def test_media_stays_a_boundary_and_says_what_it_did(name, modality, understanding_is_possible):
    _, body = call("understand", name)
    assert body["error"] is None
    assert body["type"] == modality
    assert body["meta"]["degraded"] is False
    assert body["meta"]["audio"], "音轨参数要照报"
    assert not body["meta"]["asr_text"], "正弦音样例没有说话声，不该有转写文本"
    assert body["meta"]["notes"], "转写结果如何要说明，不能静默"
    assert body["summary"] is None, "没有说话声就没有可摘要的文本"
    assert body["usage"] is None, "本机 ASR 不算模型用量，没摘要就不该记账"


def test_degraded_source_stays_a_boundary_on_both_paths(understanding_is_possible):
    fast_status, fast = call("extract", "legacy.doc")
    deep_status, deep = call("understand", "legacy.doc")
    assert fast_status == deep_status == 200
    assert fast["error"] is None and deep["error"] is None
    assert fast["meta"]["degraded"] is True and deep["meta"]["degraded"] is True
    assert deep["meta"]["degrade_reason"]
    assert deep["type"] == fast["type"] == "file"


def test_response_shape_is_the_same_seven_fields(understanding_is_possible):
    _, body = call("understand", "plain.txt")
    assert set(body) == {"type", "content", "summary", "meta", "usage", "timings", "error"}
