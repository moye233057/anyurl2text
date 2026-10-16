# -*- coding: utf-8 -*-
r"""
E2 组：扩展开启时的 HTTP 字段约定（**要花钱的模型测试，默认不跑**）。

开关：看图只要 `llm`；`ocr` 是独立文字图的提供商单选；摘要要 `llm`；音视频转写要 `asr`。
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
def extensions_possible():
    try:
        health = httpx.get(f"{BASE_URL}/probe/health", timeout=10).json()
    except Exception as exc:
        pytest.skip(f"服务没起来或不可达（{exc}）：先在本机终端起后端")
    if not health.get("model_key_configured"):
        pytest.skip("后端未配置 DEEPSEEK_API_KEY，llm/看图无法验证")
    if not health.get("ocr_service", {}).get("url"):
        pytest.skip("未配置本机 OCR 服务")
    try:
        httpx.get(OCR_HEALTH_URL, timeout=5)
    except Exception as exc:
        pytest.skip(f"本机 OCR 服务不可达（{exc}）：图片判型那两条需要它")
    return health


def call(file_name: str, query=None, *, ocr=False, asr=False, llm=False):
    path = DATA_DIR / file_name
    if not path.exists():
        pytest.skip(f"样例不存在：{path}（先跑 python scripts/make_fixtures.py）")
    with path.open("rb") as handle:
        files = {"file": (file_name, handle)}
        data = {
            "ocr": str(ocr).lower(),
            "asr": str(asr).lower(),
            "llm": str(llm).lower(),
        }
        if query:
            data["query"] = query
        response = httpx.post(f"{BASE_URL}/convert/file", files=files, data=data, timeout=TIMEOUT)
    return response.status_code, response.json()


@pytest.mark.parametrize("name", ("plain.txt", "table.csv", "legacy.doc"))
def test_llm_alone_keeps_code_content(name, extensions_possible):
    _, off = call(name)
    _, on = call(name, llm=True)
    assert off["content"] == on["content"], f"{name} 开 llm 后交付文本被改动"


@pytest.mark.parametrize("name", ["report.docx", "manual.pdf"])
def test_vision_extends_content_with_model_text(name, extensions_possible):
    _, off = call(name)
    _, on = call(name, ocr=True, llm=True)
    assert off["content"].strip(), f"{name} 全关时应有纯代码正文"
    assert off["content"].strip() in on["content"]
    if on["meta"].get("ocr_text"):
        assert on["meta"]["ocr_text"].strip() in on["content"]


@pytest.mark.parametrize("name", ["report.docx", "table.csv"])
def test_query_does_not_change_delivered_text(name, extensions_possible):
    _, plain = call(name, llm=True, ocr=(name == "report.docx"))
    _, asked = call(name, query="这份材料的重点是什么？", llm=True, ocr=(name == "report.docx"))
    assert asked["content"] == plain["content"], f"{name} 带 query 后交付文本被改动了"
    assert asked["summary"], "带 query 时摘要应给出针对性回答"


def test_vision_produces_summary_usage_and_model_timing(extensions_possible):
    _, body = call("report.docx", ocr=True, llm=True)
    assert body["error"] is None
    assert body["summary"], "应产出摘要"
    assert body["usage"] is not None, "调过 LLM 就该有 usage"
    assert body["usage"]["calls"] >= 1
    assert body["usage"]["cost_cny"] > 0
    assert body["usage"]["model"]
    assert body["usage"]["price_effective_from"], "usage 应带上单价生效日"
    assert body["timings"]["model_ms"] > 0


def test_docx_merges_ocr_into_content_and_meta(extensions_possible):
    _, off = call("report.docx")
    _, on = call("report.docx", ocr=True, llm=True)
    assert on["meta"]["ocr_text"], "含内嵌图的文档应读出图里的文字"
    assert on["summary"]
    assert on["meta"]["ocr_text"].strip() in on["content"]
    assert off["content"].strip() in on["content"]


def test_nothing_to_enhance_notes_when_llm_on(extensions_possible):
    _, body = call("legacy.doc", llm=True)
    assert body["error"] is None
    assert body["usage"] is None
    assert body["timings"]["model_ms"] == 0
    assert body["meta"]["notes"], "跳过扩展要说明原因，不能静默"


def test_text_image_ocr_lands_in_content_and_meta(extensions_possible):
    _, off = call("screenshot.png")
    _, body = call("screenshot.png", ocr=True, llm=True)
    assert body["error"] is None
    assert body["meta"]["ocr_text"], "文字图片应读出文字"
    assert body["content"].strip(), "正文应有 OCR 结果"
    assert body["meta"]["ocr_text"].strip() in body["content"]
    assert off["content"] == "", "全关时图片 content 仍为空"
    assert body["meta"]["notes"], "判型结果要说明，不能静默"


def test_semantic_image_description_in_content(extensions_possible):
    _, body = call("photo.png", ocr=True, llm=True)
    assert body["error"] is None
    assert not body["meta"]["ocr_text"], "语义图片不该有识别文本"
    assert body["summary"], "语义图片应直接给出客观描述"
    assert body["content"].strip(), "语义描述应进正文"


@pytest.mark.parametrize(("name", "modality"), [("sample.wav", "audio"), ("sample.mp4", "video")])
def test_media_asr_boundary(name, modality, extensions_possible):
    _, body = call(name, asr=True)
    assert body["error"] is None
    assert body["type"] == modality
    assert body["meta"]["degraded"] is False
    assert body["meta"]["audio"], "音轨参数要照报"
    assert not body["meta"]["asr_text"], "正弦音样例没有说话声，不该有转写文本"
    assert body["meta"]["notes"], "转写结果如何要说明，不能静默"
    assert body["summary"] is None, "没有说话声就没有可摘要的文本"
    assert body["usage"] is None, "本机 ASR 不算模型用量，没摘要就不该记账"


def test_degraded_source_stays_a_boundary(extensions_possible):
    off_status, off = call("legacy.doc")
    on_status, on = call("legacy.doc", llm=True)
    assert off_status == on_status == 200
    assert off["error"] is None and on["error"] is None
    assert off["meta"]["degraded"] is True and on["meta"]["degraded"] is True
    assert on["meta"]["degrade_reason"]
    assert on["type"] == off["type"] == "file"


def test_response_shape_is_the_same_seven_fields(extensions_possible):
    _, body = call("plain.txt")
    assert set(body) == {"type", "content", "summary", "meta", "usage", "timings", "error"}
