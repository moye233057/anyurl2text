# -*- coding: utf-8 -*-
r"""
缝 E2：模型增强那条路的契约（**贵缝，默认不跑**）。

缝：`POST /understand/file`。要起服务、要真调模型（花钱），图片判型那两条还要本机
Paddle OCR（8021）在跑。所以整份文件挂 `model` 标记，默认被 `pytest.ini` 的
`addopts = -m "not model"` 挡在外面，由人在关键节点显式跑：

    E:\miniconda\envs\multimodal\python.exe -m pytest tests -m model -q

它接替已删除的 `scripts/smoke_check.py` 里那半边——smoke_check 同时管两条路，
而 `/extract` 那半边已经由 `test_extract_contract.py` 用免费的 HTTP 缝覆盖了。

**本项目最核心的不变量在这里验**：同一个源，两条路的 `content` 逐字节相同。
README 说这是本项目存在的理由；ADR-0002 说模型产出只能进
`summary` / `meta.ocr_text` / `meta.asr_text` / `meta.notes`。

预期值来源：README 的「两条路产出的正文逐字节一致」与「模型读出的文字也不进正文」、
`CONTEXT.md` 对判型与识别文本/转写文本的定义、样例生成参数。
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
    """贵缝的前置条件：服务在、模型 Key 配了、本机 OCR 服务在。缺一条就跳过并说明。"""
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
    """打一次上传接口，返回 (状态码, 响应体)。"""
    path = DATA_DIR / file_name
    if not path.exists():
        pytest.skip(f"样例不存在：{path}（先跑 python scripts/make_fixtures.py）")
    with path.open("rb") as handle:
        files = {"file": (file_name, handle)}
        data = {"query": query} if query else None
        response = httpx.post(f"{BASE_URL}/{pipeline}/file", files=files, data=data, timeout=TIMEOUT)
    return response.status_code, response.json()


# ---------- 核心不变量：两条路逐字节一致 ----------

@pytest.mark.parametrize("name", ["plain.txt", "table.csv", "report.docx", "manual.pdf", "legacy.doc"])
def test_both_paths_deliver_byte_identical_text(name, understanding_is_possible):
    """
    行为：同一个源，`/extract/file` 与 `/understand/file` 的 `content` 逐字节相同。

    来源：README「两条路产出的正文 **逐字节一致**，所以可以直接对比」——本项目存在的理由。
    覆盖三种态：纯文本、表格、带内嵌图的文档、PDF，以及降级源。
    """
    _, fast = call("extract", name)
    _, deep = call("understand", name)
    assert fast["content"] == deep["content"], f"{name} 两条路的交付文本不一致"


@pytest.mark.parametrize("name", ["report.docx", "table.csv"])
def test_query_does_not_change_delivered_text(name, understanding_is_possible):
    """
    行为：带 query 时交付文本仍与不带时一致——query 只改摘要，绝不动正文。

    来源：ADR-0001（否掉「带 query 时用摘要覆盖正文」那套契约，原文会永久丢失）。
    """
    _, plain = call("understand", name)
    _, asked = call("understand", name, query="这份材料的重点是什么？")
    assert asked["content"] == plain["content"], f"{name} 带 query 后交付文本被改动了"
    assert asked["summary"], "带 query 时摘要应给出针对性回答"


# ---------- understand 独有产出 ----------

def test_understand_produces_summary_usage_and_model_timing(understanding_is_possible):
    """
    行为：模型增强路真的产出了摘要、按真实 token 记了账、并给出模型段耗时。

    来源：README「成本按真实 token 记账」+「纯代码那一路 usage 为 null」的反面。
    """
    _, body = call("understand", "report.docx")
    assert body["error"] is None
    assert body["summary"], "understand 应产出摘要"
    assert body["usage"] is not None, "调过模型就该有 usage"
    assert body["usage"]["calls"] >= 1
    assert body["usage"]["cost_cny"] > 0
    assert body["usage"]["model"]
    assert body["usage"]["price_effective_from"], "usage 应带上单价生效日"
    assert body["timings"]["model_ms"] > 0


def test_model_output_never_lands_in_delivered_text(understanding_is_possible):
    """
    行为：模型读出的文字与摘要都进各自的字段，`content` 一个字符都不多。

    来源：README「模型读出的文字也不进正文」+ CONTEXT 对识别文本的定义。
    report.docx 会同时产出 summary 与非空 ocr_text，是对这条最强的样例。
    """
    _, fast = call("extract", "report.docx")
    _, deep = call("understand", "report.docx")
    assert deep["meta"]["ocr_text"], "含内嵌图的文档应读出图里的文字"
    assert deep["summary"]
    assert deep["content"] == fast["content"]


def test_model_is_skipped_when_there_is_nothing_to_understand(understanding_is_possible):
    """
    行为：源没有可加工的内容时不调模型——usage 为 null、模型段耗时为 0，并在现场说明。

    来源：README「没调模型就没有用量，给 0 是噪音」。legacy.doc 走降级、content 为空。
    """
    _, body = call("understand", "legacy.doc")
    assert body["error"] is None
    assert body["usage"] is None
    assert body["timings"]["model_ms"] == 0
    assert body["meta"]["notes"], "跳过模型调用要说明原因，不能静默"


# ---------- 图片判型分流 ----------

def test_text_image_is_read_by_the_local_ocr_service(understanding_is_possible):
    """
    行为：判为文字图片的独立图片，文字进 `meta.ocr_text`，`content` 仍为空。

    来源：CONTEXT「判型」+ ADR-0004（extract 不内置 OCR，认字是 understand 的职责）。
    样例 screenshot.png 是含文字的截图。
    """
    _, body = call("understand", "screenshot.png")
    assert body["error"] is None
    assert body["meta"]["ocr_text"], "文字图片应读出文字"
    assert body["content"] == "", "图片的交付文本仍为空，读出的字不进 content"
    assert body["meta"]["notes"], "判型结果要说明，不能静默"


def test_semantic_image_gets_a_description_instead_of_ocr(understanding_is_possible):
    """
    行为：判为语义图片的独立图片不做 OCR，改给客观描述，`content` 仍为空。

    来源：CONTEXT「判型」——语义图片直接用同一次调用给出的客观描述。
    样例 photo.png 是纯色几何图形，图里没有文字。
    """
    _, body = call("understand", "photo.png")
    assert body["error"] is None
    assert not body["meta"]["ocr_text"], "语义图片不该有识别文本"
    assert body["summary"], "语义图片应直接给出客观描述"
    assert body["content"] == ""


# ---------- 降级与失败仍走契约 ----------

def test_degraded_source_stays_a_boundary_on_both_paths(understanding_is_possible):
    """
    行为：降级源在模型增强路上同样是「正常结果」——HTTP 200、error 为 null、degraded 为真，
    且两条路的降级结论一致。

    来源：README「降级是能力边界而非故障」+ ADR-0003。
    """
    fast_status, fast = call("extract", "legacy.doc")
    deep_status, deep = call("understand", "legacy.doc")
    assert fast_status == deep_status == 200
    assert fast["error"] is None and deep["error"] is None
    assert fast["meta"]["degraded"] is True and deep["meta"]["degraded"] is True
    assert deep["meta"]["degrade_reason"]
    assert deep["type"] == fast["type"] == "file"


def test_response_shape_is_the_same_seven_fields(understanding_is_possible):
    """行为：understand 与 extract 共用同一份响应契约，7 个字段一个不多一个不少。来源：ADR-0001。"""
    _, body = call("understand", "plain.txt")
    assert set(body) == {"type", "content", "summary", "meta", "usage", "timings", "error"}
