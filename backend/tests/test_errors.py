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
import pytest

from configs import errors
from pathlib import Path

from pipelines.steps import step1_download

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
