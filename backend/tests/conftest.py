# -*- coding: utf-8 -*-
r"""
测试共用装置：把 `backend/` 放进 sys.path、提供一个真实可写的工作目录，并在跑完后留一份人看得懂的结果。

职责四件，其它一律交给各用例自己。

为什么不用 pytest 的 `tmp_path`：处理器会把内嵌图、渲染页**平铺写进** `ExtractContext.work_dir`，
用固定目录是为了失败时能直接翻看留下的中间产物——`tmp_path` 会被 pytest 清掉，现场就没了。
用例之间不靠换目录隔离，靠每条用例前后清空这个目录。

跑法（在 `backend/` 下，用项目自己的环境）：
    E:\miniconda\envs\multimodal\python.exe -m pytest tests -q                 # 全套（HTTP 缝要后端在跑）
    E:\miniconda\envs\multimodal\python.exe -m pytest tests --ignore=tests/test_extract_contract.py -q   # 只跑免费的自检缝
    E:\miniconda\envs\multimodal\python.exe -m pytest tests -m model -q       # 贵缝（要真模型 + 8021/8022）

**最后那两个 hook 是给人看的**：`-q` 只给一行行点和最后一行数字，人无法据此判断
「哪条在测什么、慢在哪、为什么红」。所以每跑一次都会：
1. 在开头打印这套测试的分缝与跑法（`pytest_report_header`）；
2. 在结尾把**没通过**的用例连同「这条在测什么」打出来，并把**全量清单**
   （状态 / 用时 / 用例名 / 这条在测什么）写到 `backend/data/debug/last_test_run.txt`（`pytest_terminal_summary`）。
"""
import shutil
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND / "data"
WORK_DIR = Path(__file__).resolve().parent / "_workdir"
KEEP = ".gitkeep"
# 全量结果写这里；`data/debug/` 在 .gitignore 里，属于「看现场用」的落点
REPORT_PATH = BACKEND / "data" / "debug" / "last_test_run.txt"

# 用例要 import 项目内核（configs / pipelines / utils）；pytest 只会把 tests/ 自己加进 sys.path
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# nodeid → 该用例 docstring 的第一句（本仓每条用例都写了「行为：…」）
_BEHAVIOR = {}
# nodeid → 能力域标记名（`capability_*`，见 AGENTS.md「测试用例怎么写」）
_CAPABILITY = {}
# [(状态, nodeid, 用时秒)]，跑完由 terminal summary 汇总
_RESULTS = []


def _empty_work_dir() -> None:
    """清空工作目录内容，留下占位文件本身。"""
    if not WORK_DIR.exists():
        return
    for item in WORK_DIR.glob("*"):
        if item.name == KEEP:
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)


@pytest.fixture(scope="session", autouse=True)
def work_dir() -> Path:
    """本次测试会话共用的可写目录；随仓库带一个占位文件，保证目录已经存在。"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    yield WORK_DIR


@pytest.fixture(autouse=True)
def clean_work_dir(work_dir: Path):
    """每条用例前后清空工作目录，避免相互看到对方的中间产物。"""
    _empty_work_dir()
    yield
    _empty_work_dir()


@pytest.fixture
def fixture_copy(work_dir: Path):
    """
    把一个样例复制进工作目录，返回 (路径, 扩展名)。

    样例缺失时跳过而不是失败：`backend/data/` 里除 7 个手写样例外都由
    `python scripts/make_fixtures.py` 生成，缺哪个就跳过哪条。
    """

    def copy(file_name: str):
        source = DATA_DIR / file_name
        if not source.exists():
            pytest.skip(f"样例不存在：{source}（先跑 python scripts/make_fixtures.py）")
        target = work_dir / file_name
        shutil.copy2(source, target)
        return target, file_name.rsplit(".", 1)[-1].lower()

    return copy


def _behavior_of(item) -> str:
    """用例 docstring 的第一句实质说明；取不到时返回空串（不影响用例本身）。"""
    text = (getattr(getattr(item, "function", None), "__doc__", "") or "").strip()
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[3:] if line.startswith("行为：") else line
    return ""


def _capability_of(item) -> str:
    """该用例的能力域标记名（`capability_*`）；漏打的返回 `-`，在按域汇总里会显形。"""
    for marker in item.iter_markers():
        if marker.name.startswith("capability_"):
            return marker.name
    return "-"


def pytest_collection_modifyitems(session, config, items):
    """收集每个用例的「这条在测什么」与能力域，供跑完的清单与按域汇总使用。"""
    for item in items:
        _BEHAVIOR[item.nodeid] = _behavior_of(item)
        _CAPABILITY[item.nodeid] = _capability_of(item)


def pytest_runtest_logreport(report):
    """
    记下每条用例的结果与用时。

    setup / teardown 阶段失败不是「用例断言失败」而是 ERROR（夹具没起来、收尾炸了），
    标签要分清——人看到 FAILED 会去读断言，看到 ERROR 才知道该看夹具。
    """
    if report.when == "call":
        _RESULTS.append((report.outcome, report.nodeid, report.duration))
    elif report.when in ("setup", "teardown") and report.outcome != "passed":
        label = "ERROR" if report.outcome == "failed" else report.outcome.upper()
        _RESULTS.append((label, report.nodeid, report.duration))


def pytest_report_header(config):
    """开头就告诉人：这套测试分几缝、各自要什么前置、结果会写到哪。"""
    return [
        "两缝：自检缝（免费、几秒；处理器 / 判型 / 通用件 / 结构判据 / 错误码）"
        " · HTTP 缝（tests/test_extract_contract.py，**要后端在 8020 上应答**）",
        "贵缝另点名：python -m pytest tests -m model -q（要真模型 + 本机 8021/8022）",
        f"跑完的完整清单（每条在测什么 + 用时）写到：{REPORT_PATH}",
    ]


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """
    结尾按**能力域**汇总，并把「没通过的是哪条、这条在测什么」讲清楚、把全量清单落盘。

    为什么按能力域报：条数会被参数化放大（13 种格式 = 13 条），而人要的是
    「这次动了哪块能力、它有几项覆盖、过没过」（AGENTS.md「测试用例怎么写」第 3 条）。
    """
    if not _RESULTS:
        return

    # 能力域 → [(状态, nodeid, 用时)]；普通 dict 保持插入顺序
    domains = {}
    for status, nodeid, seconds in _RESULTS:
        domains.setdefault(_CAPABILITY.get(nodeid, "-"), []).append((status, nodeid, seconds))

    report_lines = []
    for domain, results in domains.items():
        passed = sum(1 for status, _, _ in results if status == "passed")
        report_lines.append(
            f"== {domain}（{len(results)} 个覆盖项：通过 {passed}、未通过 {len(results) - passed}）"
        )
        for status, nodeid, seconds in results:
            report_lines.append(f"{status.upper():<8}{seconds:7.2f}s  {nodeid}")
            behavior = _BEHAVIOR.get(nodeid)
            if behavior:
                report_lines.append(f"{'':<8}{'':7}  └─ 这条在测：{behavior}")
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        report_hint = f"（按能力域分组、含每条的「这条在测什么」与用时）见 {REPORT_PATH}"
    except OSError as exc:
        report_hint = f"（报告文件没写成：{exc}）"

    terminalreporter.write_sep("-", "按能力域汇总")
    for domain, results in domains.items():
        passed = sum(1 for status, _, _ in results if status == "passed")
        failed = len(results) - passed
        mark = "OK " if not failed else "!! "
        terminalreporter.write_line(
            f"{mark}{domain:<22}{len(results):3} 个覆盖项：通过 {passed}、未通过 {failed}"
        )

    bad = [(status, nodeid, seconds) for status, nodeid, seconds in _RESULTS if status != "passed"]
    if bad:
        terminalreporter.write_sep("-", "没通过的用例")
        for status, nodeid, seconds in bad:
            terminalreporter.write_line(f"{status.upper():<8}{nodeid}  ({seconds:.1f}s)")
            if _BEHAVIOR.get(nodeid):
                terminalreporter.write_line(f"{'':<8}└─ 这条在测：{_BEHAVIOR[nodeid]}")
    passed = sum(1 for status, _, _ in _RESULTS if status == "passed")
    terminalreporter.write_line(
        f"本次 {len(_RESULTS)} 个覆盖项 / {len(domains)} 个能力域：通过 {passed}、未通过 {len(bad)}。"
        f"全量清单{report_hint}"
    )
