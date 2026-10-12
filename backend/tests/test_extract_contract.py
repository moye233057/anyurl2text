# -*- coding: utf-8 -*-
r"""
convert 接口测试（扩展全关）的接口约定测试：只打 HTTP，不 import 项目内任何东西。

职责：证明**真实服务**上确定性抽取走得通、字段约定不漂——上传 → 抽取 → 组装 → 响应。
扩展开启见要花钱的模型测试 `test_understand_contract.py`。同一能力只在一个测试入口里验一次。

预期值不来自「跑一遍代码看它输出什么」，而来自：
- `docs/RUNBOOK.md` 的验收清单
- `README.md` 的「支持哪些输入」表与「几个关键取舍」
- `CONTEXT.md` 的术语定义
- 样例生成参数（`scripts/make_fixtures.py` 各阶段写死的页数 / 内嵌图数）
- 用例自己写入的已知内容（最强的一种：输入是我造的，输出该是什么我知道）

本机跑法（用项目自己的环境，服务先起）：
    python app.py                    # cwd=backend，后端起在 8020
    python -m pytest tests/test_extract_contract.py -v

环境：项目自己的 `multimodal`（后端依赖与 pytest 都在这一个环境里），不要另开环境。
`conda activate` 在非交互 shell 里不可用，直接调环境解释器更稳。
缺包不要自己装：把缺什么、装它的命令报给用户，由用户决定；环境不全就不跑。

沙箱注意：上传流要先落盘（`step3_prepare.prepare_from_upload` 用 `tempfile.mkdtemp`）。
文件写入受限的环境里，服务能建目录却建不了文件，`/convert/file` 会一律 500。
服务要在有文件写权限的地方起。
"""
import os
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("ANYURL2TEXT_BASE_URL", "http://127.0.0.1:8020")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# 本地服务的单次请求上限：本文件打的都是 127.0.0.1 的上传口，正常 1 秒内返回。
# **别**照搬「下载大文件」那类 120 秒——服务卡住时那就是十几分钟只换来一行行 F，
# 而人只看到 `F` 与最后那个数字（踩过这个坑，见 docs/RUNBOOK.md「怎么看测试结果」）。
TIMEOUT = 20.0

# 能力类别见 AGENTS.md「测试用例怎么写」；按类别跑：python -m pytest tests -m capability_http
pytestmark = pytest.mark.capability_http

# README「响应形状恒定：7 个固定字段，error 恒存在（成功为 null）」
RESPONSE_KEYS = {"type", "content", "summary", "meta", "usage", "timings", "error"}

# 抽查代表：挑三条风险最高的路径，每条再钉一个**业务事实**（页数 / 内嵌图数 / 正文特征串）。
# 期望值来源：`scripts/make_fixtures.py` 写死的生成参数——manual.pdf 3 页有文字层、
# report.docx 带一个表格与 1 张内嵌图、sample.epub 3 章（spine 顺序与 manifest / zip 顺序都不同）。
REPRESENTATIVES = (
    ("manual.pdf", 3, 0, "=== 第1页 ==="),
    ("report.docx", 1, 1, "\t"),
    ("sample.epub", None, None, "=== 章节 1"),
)


@pytest.fixture(scope="session", autouse=True)
def service_is_answerable():
    """
    前置：后端在 8020 上**真的应答**。不在就当场说清，不要等十几条用例各超时一次。

    来源：`docs/RUNBOOK.md` 第 5 节「怎么看测试结果」——后端由人在自己终端起，AI 不自起
    （端口唯一；uvicorn 的 reloader 在沙箱里建不了命名管道）。卡住的服务比不存在的服务更难查：
    TCP 连着却不回话时，每条用例都要等满 `TIMEOUT`。
    """
    try:
        response = httpx.get(f"{BASE_URL}/probe/health", timeout=5)
    except Exception as exc:
        pytest.fail(
            f"后端没在 {BASE_URL} 应答（{exc.__class__.__name__}）。本文件每条用例都要它："
            "先起/重启后端，或先跑 --ignore=tests/test_extract_contract.py（RUNBOOK 第 5/6 节）"
        )
    if response.status_code != 200:
        pytest.fail(f"{BASE_URL}/probe/health 返回 {response.status_code}，后端状态不对（RUNBOOK 第 6 节）")


def extract(file_name, raw=None, query=None):
    """打一次 `/convert/file`（扩展全关），返回 (状态码, 响应体)。"""
    if raw is None:
        path = DATA_DIR / file_name
        if not path.exists():
            pytest.skip(f"样例不存在：{path}（先跑 python scripts/make_fixtures.py）")
        raw = path.read_bytes()
    files = {"file": (file_name, raw)}
    data = {"ocr": "false", "asr": "false", "llm": "false"}
    if query:
        data["query"] = query
    response = httpx.post(f"{BASE_URL}/convert/file", files=files, data=data, timeout=TIMEOUT)
    return response.status_code, response.json()


# ---------- 真实服务上的抽取（只抽查代表） ----------

@pytest.mark.parametrize(("name", "pages", "images", "marker"), REPRESENTATIVES)
def test_representative_documents_extract_text_over_http(name, pages, images, marker):
    """
    行为：代表性格式经**真实服务**上传后，抽出正文且不降级——页数 / 内嵌图数 / 正文特征串
          与样例生成参数一致（不是「状态码 200 就算过」）。

    来源：README「支持哪些输入」的文档行 + `scripts/make_fixtures.py` 各阶段的生成参数。
    为什么只三条：完整格式矩阵在 A 组 逐格式验（免费、进程内），这里只证明真实服务走得通。
    """
    status, body = extract(name)
    assert status == 200
    assert body["error"] is None
    assert body["meta"]["degraded"] is False
    assert body["meta"]["ext"] == name.rsplit(".", 1)[1]
    assert marker in body["content"], f"{name} 的正文里应出现 {marker!r}"
    if pages is not None:
        assert body["meta"]["pages"] == pages
    if images is not None:
        assert body["meta"]["images"] == images


def test_failures_carry_a_structured_error_with_a_stable_code():
    """
    行为：入口阶段失败时 `error` 是 `{code, message, param}`，`code` 是稳定机器名。

    来源：`configs/errors.py` 的码表 + ADR-0001「error 恒存在」。
    实测（2026-10-04）：修前 `error` 只是一句中文，前端只能靠字符串匹配分组，改一个字就断。
    这里用回环地址触发——`ALLOW_PRIVATE_HOSTS` 默认关，422 在发起连接之前就返回，不联网也不花钱。
    """
    response = httpx.post(
        f"{BASE_URL}/convert", json={"url": "http://127.0.0.1/private"}, timeout=TIMEOUT
    )
    body = response.json()
    assert response.status_code == 422
    assert body["type"] == "error"
    assert body["error"]["code"] == "download_private_host"
    assert body["error"]["param"] == "url"
    assert body["error"]["message"], "面向使用者的文案不能为空"


# ---------- 响应字段约定：形状与四条通道 ----------

def test_response_shape_is_constant():
    """行为：响应恒为那 7 个字段，成功时 error 为 null。来源：README「响应形状恒定」。"""
    _, body = extract("plain.txt")
    assert set(body) == RESPONSE_KEYS
    assert body["error"] is None


def test_meta_carries_every_channel_for_a_small_input():
    """
    行为：四条通道在正常响应里形状齐全且**不谎报**——`limits_hit` 与 `unprocessed_pages`
          是空列表，`degrade_code` 与 `model_error` 为 null。

    来源：ADR-0001「响应形状只有一份」（字段恒存在，调用方不必判两种形状）
    + ADR-0003（降级 / 边界各走自己的通道，不挤进 `error`）。
    """
    status, body = extract("plain.txt")
    assert status == 200
    meta = body["meta"]
    assert meta["limits_hit"] == []
    assert meta["unprocessed_pages"] == []
    assert meta["degrade_code"] is None
    assert meta["model_error"] is None


def test_meta_source_is_the_uploaded_filename():
    """行为：meta.source 回填上传的文件名。来源：schema.py 的 Meta.source 定义。"""
    _, body = extract("plain.txt")
    assert body["meta"]["source"] == "plain.txt"
    assert body["meta"]["size_bytes"] > 0


def test_extract_never_reports_model_usage():
    """行为：纯代码那一路 usage 恒为 null。来源：README「没调模型就没有用量，给 0 是噪音」。"""
    _, body = extract("plain.txt")
    assert body["usage"] is None


def test_extract_never_produces_summary():
    """行为：summary 只由 understand 产出，extract 恒为 null。来源：schema.py 字段约定 docstring。"""
    _, body = extract("plain.txt")
    assert body["summary"] is None


# ---------- 模态与 query 这类业务事实 ----------

def test_webpage_keeps_its_own_modality():
    """行为：html 的模态是 webpage，不是 document。来源：Modality 枚举 + README 支持表。"""
    _, body = extract("page.html")
    assert body["type"] == "webpage"
    assert body["meta"]["ext"] == "html"
    assert body["content"].strip(), "网页应抽出标题与正文"


def test_query_does_not_change_delivered_text_and_is_acknowledged():
    """
    行为：extract 不消费 query，交付文本不受它影响，但会说明被忽略。

    来源：README 支持表「带 query：不消费（会明确说明被忽略）」。
    只断言「有没有就 query 给出说明」，不钉具体动词——第一版钉了「忽略」两字，
    结果代码说的是「不消费」，测试红了而行为其实是对的：钉措辞就是抄实现。
    """
    _, without = extract("plain.txt")
    _, asked = extract("plain.txt", query="这段讲了什么？")
    assert asked["content"] == without["content"]
    assert any("query" in note.lower() for note in asked["meta"]["notes"]), asked["meta"]["notes"]


# ---------- 降级与入参 ----------

def test_degraded_is_a_boundary_not_a_failure():
    """
    行为：老版 Office 降级成「基础文件信息」，HTTP 仍是 200，不算错误；降级带上机器可读的码。

    来源：README「降级是能力边界而非故障：缺依赖的类型返回『这是什么、多大』，HTTP 仍是 200」
    + `configs/errors.py` 的 `legacy_office_requires_converter`。
    """
    status, body = extract("legacy.doc")
    assert status == 200
    assert body["error"] is None
    assert body["type"] == "file"
    assert body["meta"]["degraded"] is True
    assert body["meta"]["degrade_reason"], "降级必须说明缺的是什么"
    assert body["meta"]["degrade_code"] == "legacy_office_requires_converter"
    assert body["meta"]["size_bytes"] > 0


def test_missing_file_is_rejected_as_invalid_input():
    """
    行为：没带文件时被拒，且错误里点明缺的是哪个字段。

    来源：路由 docstring「状态码按语义返回（入参 422…）」。这一层由框架的入参校验给出，
    所以只断言「422 + 指名了 file 字段」，不钉框架的措辞。
    """
    response = httpx.post(f"{BASE_URL}/convert/file", timeout=TIMEOUT)
    assert response.status_code == 422
    assert "file" in response.text, "错误里应点明缺的是 file 字段"
