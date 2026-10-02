# -*- coding: utf-8 -*-
"""
接口冒烟自查：服务起来后逐条打接口，用真实样例核对识别、抽取、降级与「两条路 content 一致」。

职责：把 `docs/RUNBOOK.md` 验收清单里可自动化的部分跑一遍，省得每次改动都靠手点。
用法：先起服务（`python app.py` 或 `python start.py`），再在 backend/ 下执行
      `python scripts/smoke_check.py`；退出码 0 全过，1 有失败项。
使用前注意：
- 需要 `backend/data/` 里有样例，缺了先跑 `python scripts/make_fixtures.py`。
- `MODEL_CHECK` 打开时会真的调用一次模型（几厘钱），用来验证模型增强那条路；
  不想花钱就把它改成 False。
- 独立图片那两条用例还要本机 Paddle OCR 服务已启动（`.env` 的 `OCR_API_URL`，见 Individual/paddle-ocr）；
  音视频那条用例还要本机 Qwen3-ASR 服务已启动（`ASR_API_URL`，见 Individual/qwen3-asr）。
"""
import sys
from pathlib import Path
from typing import Dict, List, Optional

import httpx

BASE_URL = "http://127.0.0.1:8020"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MODEL_CHECK = True
TIMEOUT_SEC = 300.0

# 每种样例的期望：抽查识别结果与关键 meta 字段，不只判「请求是否成功」
CASES = (
    # 文件, 期望 type, 期望 ext, 期望 engine, 正文非空?, 备注要求, 期望降级, 期望条目数
    ("plain.txt", "document", "txt", "原文直读", True, None, False, None),
    ("notes.md", "document", "md", "原文直读", True, None, False, None),
    ("table.csv", "document", "csv", "csv", True, None, False, None),
    ("sample.json", "document", "json", "json", True, None, False, None),
    ("feed.xml", "document", "xml", "ElementTree", True, None, False, None),
    ("sample.rtf", "document", "rtf", "RTF 控制字解析", True, None, False, None),
    ("page.html", "webpage", "html", "BeautifulSoup", True, None, False, None),
    ("report.docx", "document", "docx", "python-docx", True, None, False, None),
    ("inventory.xlsx", "document", "xlsx", "openpyxl", True, None, False, None),
    ("deck.pptx", "document", "pptx", "python-pptx", True, None, False, None),
    ("sample.odt", "document", "odt", "ElementTree", True, None, False, None),
    ("sample.odp", "document", "odp", "ElementTree", True, None, False, None),
    ("manual.pdf", "document", "pdf", "PyMuPDF", True, None, False, None),
    ("scanned.pdf", "document", "pdf", "PyMuPDF", False, "没有文字层", False, None),
    ("screenshot.png", "image", "png", "Pillow", False, None, False, None),
    ("sample.wav", "audio", "wav", "soundfile", False, None, False, None),
    ("sample.mp4", "video", "mp4", "ffmpeg", False, None, False, None),
    ("bundle.zip", "archive", "zip", "zipfile", True, None, False, 12),
    ("demo.apk", "install_package", "apk", "zipfile", True, None, False, 6),
    ("legacy.doc", "file", "doc", "基础文件信息", False, None, True, None),
)

# 可选样例：缺了不算失败（由 make_fixtures 的可选阶段生成，例如 mp4 需要 imageio-ffmpeg）
OPTIONAL_FIXTURES = frozenset({"sample.mp4"})

def call(pipeline: str, file_name: str, query: Optional[str] = None) -> Dict:
    """打一次上传接口，返回响应 JSON。"""
    path = DATA_DIR / file_name
    if not path.exists():
        raise SystemExit(f"样例不存在：{path}（先跑 python scripts/make_fixtures.py）")
    with path.open("rb") as handle:
        files = {"file": (file_name, handle)}
        data = {"query": query} if query else None
        response = httpx.post(f"{BASE_URL}/{pipeline}/file", files=files, data=data, timeout=TIMEOUT_SEC)
    response.raise_for_status()
    return response.json()

def skip_case(case) -> Optional[str]:
    """样例不存在时给出跳过原因；必需样例返回 None，由 call() 报错退出。"""
    if (DATA_DIR / case[0]).exists() or case[0] not in OPTIONAL_FIXTURES:
        return None
    return f"{case[0]}（可选样例，未生成）"

def check_case(case) -> List[str]:
    """核对一个样例；返回问题列表（空表示通过）。"""
    file_name, want_type, want_ext, want_engine, want_content, want_note, want_degraded, want_entries = case
    payload = call("extract", file_name)
    meta = payload["meta"]
    problems = []
    if payload["type"] != want_type:
        problems.append(f"type={payload['type']} 期望 {want_type}")
    if meta["ext"] != want_ext:
        problems.append(f"ext={meta['ext']} 期望 {want_ext}")
    if want_engine not in meta["engine"]:
        problems.append(f"engine={meta['engine']} 期望含 {want_engine}")
    if want_content and not payload["content"].strip():
        problems.append("content 为空，期望非空")
    if not want_content and payload["content"].strip():
        problems.append(f"content 非空（{len(payload['content'])} 字符），期望为空")
    if want_note and not any(want_note in note for note in meta["notes"]):
        problems.append(f"notes 里没出现「{want_note}」：{meta['notes']}")
    if bool(meta["degraded"]) != want_degraded:
        problems.append(f"degraded={meta['degraded']} 期望 {want_degraded}")
    if want_entries is not None and meta["entry_count"] != want_entries:
        problems.append(f"entry_count={meta['entry_count']} 期望 {want_entries}")
    if want_type == "image" and not meta.get("image"):
        problems.append("meta.image 为空，图片应给出结构信息")
    if want_type in ("audio", "video") and not meta.get("audio"):
        problems.append("meta.audio 为空，音视频应给出音轨参数")
    if payload["usage"] is not None:
        problems.append("纯代码解析的 usage 应为 null")
    return problems

def check_content_identical(file_name: str, query: Optional[str] = None) -> List[str]:
    """两条路的 content 必须逐字节相同（RUNBOOK 验收第 4 条，本项目最核心的不变量）。"""
    fast = call("extract", file_name, query)
    deep = call("understand", file_name, query)
    problems = []
    if fast["content"] != deep["content"]:
        problems.append(
            f"两条路 content 不一致：extract {len(fast['content'])} 字符 / understand {len(deep['content'])} 字符"
        )
    return problems

def check_model_enhancement(file_name: str) -> List[str]:
    """模型增强那条路要真的产出识别文本、摘要与用量。"""
    payload = call("understand", file_name)
    meta = payload["meta"]
    usage = payload["usage"]
    problems = []
    if payload["error"]:
        problems.append(f"understand 返回 error：{payload['error']}")
    if not meta["ocr_text"]:
        problems.append("meta.ocr_text 为空，含内嵌图的文档应读出图里的文字")
    if not payload["summary"]:
        problems.append("summary 为空")
    if not usage or usage["calls"] < 1:
        problems.append(f"usage 异常：{usage}")
    elif usage["cost_cny"] <= 0:
        problems.append(f"usage.cost_cny 应大于 0：{usage}")
    if payload["timings"]["model_ms"] <= 0:
        problems.append("timings.model_ms 应大于 0")
    return problems

def check_image_routing(file_name: str, want_ocr: bool) -> List[str]:
    """
    独立图片要走判型分流：文字图片给 meta.ocr_text（本机 Paddle OCR），语义图片给描述。

    前置：`OCR_API_URL` 指向的 Paddle OCR 服务要已启动，否则文字图片那一支会失败。
    """
    payload = call("understand", file_name)
    meta = payload["meta"]
    problems = []
    if payload["error"]:
        problems.append(f"understand 返回 error：{payload['error']}")
    if want_ocr:
        if not meta["ocr_text"]:
            problems.append(f"meta.ocr_text 为空：{meta['notes']}")
    else:
        if meta["ocr_text"]:
            problems.append("语义图片不该有 ocr_text")
        if not payload["summary"]:
            problems.append("summary 为空，语义图片应直接给出客观描述")
    return problems

def check_media_enhancement(file_name: str) -> List[str]:
    """
    音视频那条链要真的走到本机 ASR 服务。

    样例是一段正弦音，本来就没有语音，所以不要求 meta.asr_text 非空；
    要验的是「服务被调到了」：模型段耗时不为 0，且没有出现「模型增强失败」。
    """
    payload = call("understand", file_name)
    meta = payload["meta"]
    problems = []
    if payload["error"]:
        problems.append(f"understand 返回 error：{payload['error']}")
    failed = [note for note in meta["notes"] if "模型增强失败" in note]
    if failed:
        problems.append(f"本机 ASR 服务没走到：{'；'.join(failed)}")
    if payload["timings"]["model_ms"] <= 0:
        problems.append("timings.model_ms 应大于 0")
    return problems

def main() -> int:
    print(f"目标服务：{BASE_URL}")
    try:
        health = httpx.get(f"{BASE_URL}/probe/health", timeout=10).json()
    except Exception as exc:
        print(f"服务没起来或不可达：{exc}")
        return 1
    print(f"健康检查：{health}")

    failures: List[str] = []
    for case in CASES:
        reason = skip_case(case)
        if reason:
            print(f"[跳过] {reason}")
            continue
        problems = check_case(case)
        if problems:
            failures.append(f"{case[0]}: " + "；".join(problems))
            print(f"[失败] {case[0]}")
        else:
            print(f"[通过] {case[0]}")

    if MODEL_CHECK:
        if not health.get("model_key_configured"):
            print("[跳过] 模型增强：后端未配置 DEEPSEEK_API_KEY")
        else:
            for name, problems in (
                ("两条路 content 一致 · report.docx", check_content_identical("report.docx")),
                ("模型增强产出 · report.docx", check_model_enhancement("report.docx")),
                ("两条路 content 一致 · table.csv", check_content_identical("table.csv")),
                ("图片判型 · screenshot.png（含文字，应走 OCR）", check_image_routing("screenshot.png", True)),
                ("图片判型 · photo.png（无文字，应给描述）", check_image_routing("photo.png", False)),
                ("音视频转写 · sample.wav（需本机 Qwen3-ASR 已启动）", check_media_enhancement("sample.wav")),
            ):
                if problems:
                    failures.append(f"{name}: " + "；".join(problems))
                    print(f"[失败] {name}")
                else:
                    print(f"[通过] {name}")

    print("\n结论：" + ("全部通过" if not failures else f"{len(failures)} 项失败"))
    for item in failures:
        print(f"  - {item}")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
