# -*- coding: utf-8 -*-
r"""
extract 缝的契约测试：只打 HTTP，不 import 项目内任何东西。

职责：把 `docs/RUNBOOK.md` 验收清单里「纯代码那一路」的部分做成可重复执行的 pytest 用例——
每次改动都能跑，不联网、不花钱、不起 Paddle OCR / ASR。

缝：`POST /extract/file`（本地文件上传，纯代码抽取，恒不调模型）。
不覆盖：`understand` 那条路要调模型与本机 OCR / ASR 服务，属于贵缝，由
        `tests/test_understand_contract.py` 覆盖，由人在关键节点跑；本文件刻意不重复它。

预期值不来自「跑一遍代码看它输出什么」，而来自：
- `docs/RUNBOOK.md` 的验收清单
- `README.md` 的「支持哪些输入」表与「几个关键取舍」
- `CONTEXT.md` 的术语定义
- 用例自己写入的已知内容（最强的一种：输入是我造的，输出该是什么我知道）

本机跑法（用项目自己的环境，服务先起）：
    E:\miniconda\envs\multimodal\python.exe app.py                    # cwd=backend，后端起在 8020
    E:\miniconda\envs\multimodal\python.exe -m pytest tests/test_extract_contract.py -v

环境：项目自己的 `multimodal`（后端依赖与 pytest 都在这一个环境里），不要另开环境。
`conda activate` 在非交互 shell 里不可用，直接调环境解释器更稳。
缺包不要自己装：把缺什么、装它的命令报给用户，由用户决定；环境不全就不跑。

沙箱注意：上传流要先落盘（`step3_prepare.prepare_from_upload` 用 `tempfile.mkdtemp`）。
文件写入受限的环境里，服务能建目录却建不了文件，`/extract/file` 会一律 500，服务端报
`PermissionError: ... upload.bin`。所以服务要在有文件写权限的地方起——人在自己终端里起就没
这个问题；只把 `TEMP`/`TMP` 指到可写目录不够（本机实测）。
"""
import os
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("ANYURL2TEXT_BASE_URL", "http://127.0.0.1:8020")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TIMEOUT = 120.0

# README「响应形状恒定：7 个固定字段，error 恒存在（成功为 null）」
RESPONSE_KEYS = {"type", "content", "summary", "meta", "usage", "timings", "error"}

# README「支持哪些输入」表里列为「完整解析（版面顺序、表格、文本框、内嵌图位置）」的格式
DOCUMENT_FIXTURES = (
    "plain.txt",
    "notes.md",
    "table.csv",
    "sample.json",
    "feed.xml",
    "sample.rtf",
    "report.docx",
    "inventory.xlsx",
    "deck.pptx",
    "sample.odt",
    "sample.odp",
    "sample.epub",
    "manual.pdf",
)

def extract(file_name, raw=None, query=None):
    """打一次 `/extract/file`，返回 (状态码, 响应体)。raw 给了就用它当上传内容。"""
    if raw is None:
        path = DATA_DIR / file_name
        if not path.exists():
            pytest.skip(f"样例不存在：{path}（先跑 python scripts/make_fixtures.py）")
        raw = path.read_bytes()
    files = {"file": (file_name, raw)}
    data = {"query": query} if query else None
    response = httpx.post(f"{BASE_URL}/extract/file", files=files, data=data, timeout=TIMEOUT)
    return response.status_code, response.json()

def test_service_is_reachable():
    """缝的前置条件：服务应答健康检查，否则后面每条失败都只是噪音。"""
    response = httpx.get(f"{BASE_URL}/probe/health", timeout=10)
    assert response.status_code == 200

@pytest.mark.parametrize("name", DOCUMENT_FIXTURES)
def test_declared_document_formats_extract_text(name):
    """
    行为：README 声明「完整解析」的格式，都能抽出非空正文，且不当成降级。

    来源：README「支持哪些输入」表的文档行 + 「降级是能力边界而非故障」。
    """
    status, body = extract(name)
    assert status == 200
    assert body["error"] is None
    assert body["meta"]["ext"] == name.rsplit(".", 1)[1]
    assert body["meta"]["degraded"] is False
    assert body["content"].strip(), f"{name} 应抽出非空正文"

def test_failures_carry_a_structured_error_with_a_stable_code():
    """
    行为：入口阶段失败时 `error` 是 `{code, message, param}`，`code` 是稳定机器名。

    来源：`configs/errors.py` 的码表 + ADR-0001「error 恒存在」。
    实测（2026-10-04）：修前 `error` 只是一句中文，前端只能靠字符串匹配分组，改一个字就断。
    这里用回环地址触发——`ALLOW_PRIVATE_HOSTS` 默认关，422 在发起连接之前就返回，不联网也不花钱。
    """
    response = httpx.post(
        f"{BASE_URL}/extract", json={"url": "http://127.0.0.1/private"}, timeout=TIMEOUT
    )
    body = response.json()
    assert response.status_code == 422
    assert body["type"] == "error"
    assert body["error"]["code"] == "download_private_host"
    assert body["error"]["param"] == "url"
    assert body["error"]["message"], "面向使用者的文案不能为空"


def test_meta_reports_no_limit_hits_for_a_small_input():
    """
    行为：普通输入不谎报命中——`meta.limits_hit` 恒存在且为空列表。

    来源：ADR-0001「响应形状只有一份定义」+ `configs/limits.py`：只有真的少读了用户的
    字节 / 行 / 页 / 张才记名字，形状对每个成功响应都在。
    """
    status, body = extract("plain.txt")
    assert status == 200
    assert body["meta"]["limits_hit"] == []


def test_meta_carries_every_channel_for_a_small_input():
    """
    行为：三条通道在正常响应里形状齐全——`limits_hit` 与 `unprocessed_pages` 是空列表，
          `degrade_code` 与 `model_error` 为 null。

    来源：ADR-0001「响应形状只有一份」（字段恒存在，调用方不必判两种形状）
    + ADR-0003（降级/边界各走自己的通道，不挤进 `error`）。
    """
    status, body = extract("plain.txt")
    assert status == 200
    meta = body["meta"]
    assert meta["limits_hit"] == []
    assert meta["unprocessed_pages"] == []
    assert meta["degrade_code"] is None
    assert meta["model_error"] is None


def test_plain_text_is_delivered_verbatim():
    """
    行为：交付文本就是源文本本身，不被加工、不被加前缀。

    来源：CONTEXT「交付文本：从源抽取出的文本本身…也不被追加实现细节前缀」。
    预期值来源：本用例自己写入的内容——不依赖对实现的任何理解。
    """
    text = "第一行：中文内容\n第二行：ascii only\n第三行：{braces} 与 $dollar\n"
    status, body = extract("known.txt", raw=text.encode("utf-8"))
    assert status == 200
    assert body["content"] == text

def test_csv_cells_reach_delivered_text():
    """
    行为：表格的单元格字面量进入交付文本。

    来源：README「尽可能保留原始信息」；单元格由本用例给定，属于独立预期值。
    """
    raw = "name,qty\n苹果,3\nbanana,12\n".encode("utf-8")
    status, body = extract("known.csv", raw=raw)
    assert status == 200
    for cell in ("name", "qty", "苹果", "banana", "12"):
        assert cell in body["content"], f"单元格 {cell} 没进入交付文本"

def test_response_shape_is_constant():
    """行为：响应恒为那 7 个字段，成功时 error 为 null。来源：README「响应形状恒定」。"""
    _, body = extract("plain.txt")
    assert set(body) == RESPONSE_KEYS
    assert body["error"] is None

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
    """行为：summary 只由 understand 产出，extract 恒为 null。来源：schema.py 契约 docstring。"""
    _, body = extract("plain.txt")
    assert body["summary"] is None

def test_webpage_keeps_its_own_modality():
    """行为：html 的模态是 webpage，不是 document。来源：Modality 枚举 + README 支持表。"""
    _, body = extract("page.html")
    assert body["type"] == "webpage"
    assert body["meta"]["ext"] == "html"

def test_image_extract_yields_structure_only():
    """
    行为：extract 对图片的全部产出是结构信息，没有正文。

    来源：README「图片：只给尺寸、格式、EXIF 等结构信息」+ CONTEXT 对 extract 的定义。
    """
    _, body = extract("screenshot.png")
    assert body["type"] == "image"
    assert body["content"] == ""
    image = body["meta"]["image"]
    assert image is not None, "图片应给出结构信息"
    assert image["width"] > 0 and image["height"] > 0
    assert image["format"]

def test_archive_lists_entries_without_extracting():
    """行为：压缩包只列条目清单，不解压。来源：README 支持表 + RUNBOOK 0b 期望条目数 12。"""
    _, body = extract("bundle.zip")
    assert body["type"] == "archive"
    assert body["meta"]["entry_count"] == 12
    assert body["content"].strip(), "条目清单应作为交付文本给出"

def test_scanned_pdf_declares_missing_text_layer():
    """行为：没有文字层的 PDF 正文为空，并在 notes 里明说。来源：RUNBOOK 验收第 5 条。"""
    _, body = extract("scanned.pdf")
    assert body["content"] == ""
    assert any("没有文字层" in note for note in body["meta"]["notes"]), body["meta"]["notes"]

def test_degraded_is_a_boundary_not_a_failure():
    """
    行为：老版 Office 降级成「基础文件信息」，HTTP 仍是 200，不算错误。

    来源：README「降级是能力边界而非故障：缺依赖的类型返回『这是什么、多大』，HTTP 仍是 200」。
    """
    status, body = extract("legacy.doc")
    assert status == 200
    assert body["error"] is None
    assert body["type"] == "file"
    assert body["meta"]["degraded"] is True
    assert body["meta"]["degrade_reason"], "降级必须说明缺的是什么"
    assert body["meta"]["degrade_code"] == "legacy_office_requires_converter"
    assert body["meta"]["size_bytes"] > 0

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

def test_missing_file_is_rejected_as_invalid_input():
    """行为：入参不合法返回 422。来源：路由 docstring「状态码按语义返回（入参 422…）」。"""
    response = httpx.post(f"{BASE_URL}/extract/file", timeout=TIMEOUT)
    assert response.status_code == 422
