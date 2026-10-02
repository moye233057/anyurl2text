# -*- coding: utf-8 -*-
r"""
测试共用装置：把 `backend/` 放进 sys.path，并提供一个真实可写的工作目录。

职责只有两件，其它一律交给各用例自己。

为什么不用 pytest 的 `tmp_path`：处理器会把内嵌图、渲染页**平铺写进** `ExtractContext.work_dir`，
用固定目录是为了失败时能直接翻看留下的中间产物——`tmp_path` 会被 pytest 清掉，现场就没了。
用例之间不靠换目录隔离，靠每条用例前后清空这个目录。

跑法（在 `backend/` 下，用项目自己的环境）：
    E:\miniconda\envs\multimodal\python.exe -m pytest tests -q
"""
import shutil
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND / "data"
WORK_DIR = Path(__file__).resolve().parent / "_workdir"
KEEP = ".gitkeep"

# 用例要 import 项目内核（configs / pipelines / utils）；pytest 只会把 tests/ 自己加进 sys.path
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


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
