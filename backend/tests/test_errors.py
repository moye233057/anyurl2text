# -*- coding: utf-8 -*-
r"""
缝 C：稳定错误码。

缝：`step1_download.FetchError` 与 `configs/errors.py` 的白名单——入口阶段失败时，
错误不再是「一句中文」，而是 `{code, message, param}`，`code` 是给程序看的稳定机器名。

为什么选它：文案会改、`code` 不许改。前端按 `code` 分组统计、监控按它告警、验收脚本按它匹配；
码表没有一处定义、或者打错一个字没人发现，这些就全落空。`FetchError` 构造时校验白名单，
所以「打错字」在抛出那一刻就炸，而不是等前端分不了组。

本文件全部**不联网**：用的都是发起连接之前就该拒掉的地址（空、非 http、缺主机、内网/回环），
422 判定发生在 DNS 与连接之前，所以不需要网络也不需要后端服务。

预期值来源：`configs/errors.py` 的码表与注释里写明的触发点、ADR-0001（`error` 恒存在）、
ADR-0003（降级是能力边界而不是故障——所以那些走 `meta`，不进 `error`）。
"""
import ast

import pytest

from configs import errors
from pathlib import Path

from pipelines.steps import step1_download
from test_structure import production_modules, relative

# 四个「不用联网就该被拒」的入口：地址本身非法，判定发生在 DNS 与连接之前
NOT_NETWORK_CASES = (
    ("", "download_url_empty", "url", 422),
    ("ftp://example.com/a.txt", "download_scheme_unsupported", "url", 422),
    ("http://", "download_host_missing", "url", 422),
    ("http://127.0.0.1/a.txt", "download_private_host", "url", 422),
)


def test_the_code_table_is_well_formed():
    """
    行为：码表自洽——每个码都是全小写 + 下划线的模块域命名。

    来源：`configs/errors.py` 的命名规则（前缀是模块域而不是状态码）。
    没有这条，码表会慢慢长出 `DownloadError`、`download-404` 这类混进来的写法，
    而「稳定机器名」的价值正建立在统一的形状上。
    """
    assert errors.HARD_FAILURE_CODES, "码表不能是空的（空了下面几条会假绿）"
    for code in errors.HARD_FAILURE_CODES:
        assert code == code.lower(), f"{code} 不是全小写"
        assert " " not in code and "-" not in code, f"{code} 用了空格或连字符"
        assert code.replace("_", "").isalnum(), f"{code} 含非字母数字字符"


def test_every_code_used_in_the_codebase_is_registered():
    """
    行为：仓里写死的每一个码都在对应的白名单里——**静态扫，不靠用例覆盖到那条路径**。

    来源：`configs/errors.py`「不在表里的码抛不出来」。两个异常在构造时校验，但那只盖到真跑过的
    分支；这条把没跑到的分支（服务不可达、容器损坏、缺依赖…）一起扫一遍，这才是码表不漂移的保证。
    """
    # 异常名 → 它该用的白名单；码在第几个位置参数上
    exception_sets = {
        "FetchError": (errors.HARD_FAILURE_CODES, 2),
        "LlmError": (errors.MODEL_FAILURE_CODES, 1),
        "FfmpegError": (errors.DEGRADE_CODES, 1),
    }
    problems = []
    found = 0
    for path in production_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            # 关键字形式：degrade_code="..." 之类，按关键字名收
            for keyword in node.keywords:
                if keyword.arg == "degrade_code" and isinstance(keyword.value, ast.Constant):
                    found += 1
                    if keyword.value.value not in errors.DEGRADE_CODES:
                        problems.append(f"{relative(path)}:{node.lineno} degrade_code={keyword.value.value!r} 未登记")
            if name not in exception_sets:
                continue
            codes, position = exception_sets[name]
            if len(node.args) <= position or not isinstance(node.args[position], ast.Constant):
                continue
            found += 1
            if node.args[position].value not in codes:
                problems.append(f"{relative(path)}:{node.lineno} {name} 的码 {node.args[position].value!r} 未登记")
    # 没有这条，扫描一旦因为遍历写错而扫不到东西，上面会全绿——那是假绿
    assert found > 20, f"只扫到 {found} 个码，遍历大概率写错了"
    assert not problems, "有码没登记进 configs/errors.py：\n" + "\n".join(problems)


def test_unregistered_code_is_rejected_at_construction():
    """
    行为：抛出白名单之外的码会当场炸掉，而不是静默流到响应里。

    来源：`configs/errors.py`「不在表里的码抛不出来」。
    预期值来源：本用例自己编的码——不依赖对实现的任何理解。
    """
    with pytest.raises(ValueError):
        step1_download.FetchError("随便一条消息", 500, "no_such_code")


def test_default_code_is_registered():
    """行为：默认码也在白名单里（默认值是省事用的，不能是漏网的）。来源：`configs/errors.py`。"""
    assert step1_download.FetchError("x").code in errors.HARD_FAILURE_CODES


@pytest.mark.parametrize(("url", "code", "param", "status"), NOT_NETWORK_CASES)
def test_entry_validation_carries_the_documented_code(url, code, param, status):
    """
    行为：四种非法入口各自带上码表里注明的那一个码、`param` 与状态码。

    来源：`configs/errors.py` 每个码后面注明的触发点 + `FetchError.status_code` 的语义注释
    （入参问题 422、体积超限 413、超时 504、其余上游/网络 502）。
    实测（2026-10-04）：修前 `FetchError` 只带 `status_code`，`error` 是一句中文，
    调用方只能靠字符串匹配——那是脆的。
    """
    with pytest.raises(step1_download.FetchError) as caught:
        step1_download.download(url, Path("."))
    assert caught.value.code == code
    assert caught.value.param == param
    assert caught.value.status_code == status
    assert str(caught.value), "面向使用者的文案不能为空"


# ---------- ALLOW_HTTP_SOURCE：只收 https 的开关（默认保持现状） ----------

def test_http_source_can_be_switched_off(monkeypatch):
    """
    行为：`ALLOW_HTTP_SOURCE=false` 时 `http://` 带 `download_http_not_allowed` 与 422 被拒，
          文案里说清怎么放回来；这个码在硬失败白名单里。

    来源：`configs/errors.py` 里该码注明的触发点（`step1_download._assert_safe_url`）
    + `docs/RUNBOOK.md` 配置项全表的语义。
    做法：置开关为 false 后直接调 `download()`——判定发生在 DNS 与连接之前，全程不联网。
    预期值来源：配置项本身的语义与码表，不是跑一遍看输出。
    """
    monkeypatch.setattr(step1_download.settings, "ALLOW_HTTP_SOURCE", False)
    with pytest.raises(step1_download.FetchError) as caught:
        step1_download.download("http://example.com/x", Path("."))
    assert caught.value.code == "download_http_not_allowed"
    assert caught.value.status_code == 422
    assert caught.value.param == "url"
    assert "ALLOW_HTTP_SOURCE" in str(caught.value), "文案要告诉使用者怎么放开"
    assert caught.value.code in errors.HARD_FAILURE_CODES


def test_https_still_passes_when_http_is_switched_off(monkeypatch):
    """
    行为：开关置 false 只拒 `http://`，`https://` 照旧通过地址判定——不把整条 URL 入口连坐。

    来源：`ALLOW_HTTP_SOURCE` 的语义（只收 https）。
    做法：把主机判定换成探针，只走到「地址判定」这一层，不 DNS、不连接。
    预期值来源：`https://example.com/x` 的主机名。
    """
    monkeypatch.setattr(step1_download.settings, "ALLOW_HTTP_SOURCE", False)
    visited = []
    monkeypatch.setattr(step1_download, "_assert_public_host", lambda host: visited.append(host))
    step1_download._assert_safe_url("https://example.com/x")
    assert visited == ["example.com"]


def test_http_is_allowed_by_default(monkeypatch):
    """
    行为：默认（开关为 true）下 `http://` 不会被这一条拒掉——与改动前的行为完全一致。

    来源：`settings.py` 的默认值 `true`（本项改造的硬要求是「默认保持现状」）。
    做法：同上，主机判定换探针，不联网；顺带把默认值本身钉住。
    预期值来源：`ALLOW_HTTP_SOURCE` 的默认值 true。
    """
    assert step1_download.settings.ALLOW_HTTP_SOURCE is True, "默认必须是 true"
    visited = []
    monkeypatch.setattr(step1_download, "_assert_public_host", lambda host: visited.append(host))
    step1_download._assert_safe_url("http://example.com/x")
    assert visited == ["example.com"]


# ---------- 第 3 组：模型阶段失败进 meta.model_error ----------

class _FakeLedger:
    """假用量账本：管线收尾时只读一次，这里恒为「没有用量」。"""

    @staticmethod
    def snapshot():
        return None

class _FakeClient:
    """假模型客户端：不联网、不读 API key，只提供管线要用的那一个入口。"""

    ledger = _FakeLedger()


def test_model_failure_lands_in_meta_model_error(fixture_copy, monkeypatch):
    """
    行为：understand 的模型那一段失败时，`meta.notes` 里那句中文保留、结构化说明进
          `meta.model_error`，而 `error` 仍是 null、HTTP 仍是 200。

    来源：`configs/errors.py` 第 3 组的通道约定（模型失败不夺走确定性产出）+ ADR-0007。
    做法：把「取字节」与「调模型」两处换成假件——不联网、不需要 API key、不起服务，
    只验这条通道的接线；真调模型的贵缝在 `-m model` 那一组里。
    """
    from pipelines import understand
    from pipelines.processors.schema import PreparedSource
    from routers.convert.schema import Detector
    from utils.errors import LlmError

    path, ext = fixture_copy("plain.txt")
    prepared = PreparedSource(
        path=path,
        source=path.name,
        file_name=path.name,
        mime=None,
        size_bytes=path.stat().st_size,
        ext=ext,
        detector=Detector.EXTENSION,
        page_url="",
    )
    monkeypatch.setattr(understand.step3_prepare, "prepare_from_url", lambda url: (prepared, 1))
    monkeypatch.setattr(understand.step3_prepare, "cleanup", lambda prepared: None)
    monkeypatch.setattr(understand, "DeepSeekClient", _FakeClient)

    def fail(*args, **kwargs):
        raise LlmError("模型服务不可达", "ocr_unreachable", "ocr")

    monkeypatch.setattr(understand.step4_extract, "enhance", fail)

    outcome = understand.UnderstandPipeline().run_from_url("https://example.invalid/plain.txt")

    assert outcome.status == 200, "模型失败不该把整条请求变成失败"
    assert outcome.response.error is None, "模型失败走 meta.model_error，不走 error"
    info = outcome.response.meta.model_error
    assert info is not None, "模型失败必须留下机器可读的说明"
    assert info.code == "ocr_unreachable"
    assert info.param == "ocr"
    assert info.code in errors.MODEL_FAILURE_CODES
    assert any(
        "模型增强失败" in note for note in outcome.response.meta.notes
    ), "那句中文是既有验收判据，不能丢"


# ---------- 未预期异常：失败响应的形状也只有一份 ----------

def _prepared_from(fixture_copy, name: str = "plain.txt"):
    """把一个样例包成 PreparedSource：不下载、不识别，直接进抽取阶段。"""
    from pipelines.processors.schema import PreparedSource
    from routers.convert.schema import Detector

    path, ext = fixture_copy(name)
    return PreparedSource(
        path=path,
        source=path.name,
        file_name=path.name,
        mime=None,
        size_bytes=path.stat().st_size,
        ext=ext,
        detector=Detector.EXTENSION,
        page_url="",
    )


def test_unexpected_failure_after_preparation_becomes_internal(fixture_copy, monkeypatch):
    """
    行为：抽取阶段冒出没预料到的异常时返回 500 + `error.code == "internal"`，而不是让异常冒到框架
          （冒上去的默认 500 body 不是 ConvertResponse 形状，调用方得判两种形状）。

    来源：`configs/errors.py` 的 `internal` + ADR-0001「error 恒存在」。异常文本**不回显**：
    它是内部细节，回显等于把路径与依赖版本塞进契约。
    """
    from pipelines import extract

    prepared = _prepared_from(fixture_copy)
    monkeypatch.setattr(extract.step3_prepare, "prepare_from_url", lambda url: (prepared, 1))
    monkeypatch.setattr(extract.step3_prepare, "cleanup", lambda prepared: None)

    def boom(*args, **kwargs):
        raise RuntimeError("内部路径 /srv/secret/anyurl2text 打不开")

    monkeypatch.setattr(extract.step4_extract, "run_extraction", boom)

    outcome = extract.ExtractPipeline().run_from_url("https://example.invalid/plain.txt")

    assert outcome.status == 500
    assert outcome.response.error is not None, "失败响应必须带 error"
    assert outcome.response.error.code == "internal"
    assert "secret" not in outcome.response.error.message, "异常文本不回显给调用方"


def test_unexpected_failure_before_preparation_becomes_internal(monkeypatch):
    """
    行为：准备工作（下载 / 识别 / 建临时目录）这一段出未预期异常时同样兜成 `internal` 的 500。

    来源：ADR-0001。这一段原本只接 `FetchError`，其它异常会整条冒到框架。
    """
    from pipelines import extract

    def boom(url):
        raise OSError("临时目录建不出来")

    monkeypatch.setattr(extract.step3_prepare, "prepare_from_url", boom)

    outcome = extract.ExtractPipeline().run_from_url("https://example.invalid/plain.txt")

    assert outcome.status == 500
    assert outcome.response.error.code == "internal"
