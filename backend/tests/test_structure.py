# -*- coding: utf-8 -*-
r"""
缝 S：结构判据（静态）。

这里接替已删除的 `backend/scripts/check_structure.py`。这 **5 条判据**（另加 2 条自检，共 7 个覆盖项）守的是**行为测不到的东西**——
类与函数的摆放、依赖朝向、上界值是否只写一处、前端契约是否缺字段。
它们是 ADR-0002 与 ADR-0007 的核心不变量，所以从「一个没人跑的脚本」搬成「跟着测试一起跑的用例」。
原「单文件行数」判据已按 ADR-0010 废除（为贴预算拆文件比巨石更糟）。

判据：

1. **依赖单向**：每层只能 import 它下方的层；**叶子模块**（自己不 import 项目内任何东西）
   任何层都可以用——它们不可能造成循环。
2. **扩展名覆盖**：`configs/formats.py` 声明的每个扩展名，在 `step4_extract.dispatch()` 里都有落点。
3. **`__init__.py` 里不放逻辑**（只有模块 docstring 与 import）。
4. **上界的值只在注册表里写一次**：散落常量不得与 `limits.py` 撞车。
5. **前端契约镜像不缺字段**：`frontend/src/types.ts` 与后端 schema 对齐。

`backend/scripts/` 与 `backend/tests/` 不是生产模块，不参与判据 1、3：
前者是一次性生成脚本，后者是测试自身。
"""
import ast
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

BACKEND = Path(__file__).resolve().parent.parent
# `data` 也在这里：AGENTS.md 让 AI 把一次性探针写进 `data/debug/`，那些 .py 不是生产模块——
# 不排除的话判据 1 会拿它当「data 层」去查依赖白名单，直接 KeyError（2026-10-04 踩到）。
NON_PRODUCTION = {"scripts", "tests", "data"}

# 能力域见 AGENTS.md「测试用例怎么写」；按域跑：python -m pytest tests -m capability_structure
pytestmark = pytest.mark.capability_structure

# 层 → 允许被它 import 的项目内层（ADR-0007）
ALLOWED: Dict[str, Set[str]] = {
    "configs": set(),
    "utils": {"configs"},
    "models": {"configs", "utils"},
    "pipelines": {"configs", "models", "utils"},
    "routers": {"configs", "pipelines", "utils"},
    "app": {"configs", "pipelines", "routers", "utils"},
}


# ---------- 读代码 ----------

def production_modules() -> List[Path]:
    """backend/ 下参与结构判据的全部 .py。"""
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.relative_to(BACKEND).parts[0] in NON_PRODUCTION:
            continue
        found.append(path)
    return found


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def layer_of(path: Path) -> str:
    """模块属于哪一层：顶层文件算 app，其余取第一段目录名。"""
    parts = path.relative_to(BACKEND).parts
    return "app" if len(parts) == 1 else parts[0]


def module_name(path: Path) -> str:
    """模块的点分名字。"""
    parts = list(path.relative_to(BACKEND).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def project_imports(tree: ast.Module) -> List[Tuple[str, int]]:
    """项目内部的 import：返回 [(点分模块名, 行号)]。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in ALLOWED:
                    found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入：本项目统一绝对导入
                found.append(("<相对导入>", node.lineno))
                continue
            if (node.module or "").split(".")[0] in ALLOWED:
                found.append((node.module or "", node.lineno))
    return found


def leaf_modules(modules: List[Path]) -> Set[str]:
    """叶子模块：自己不 import 项目内任何东西，因此任何层都能用而不可能成环。"""
    return {module_name(path) for path in modules if not project_imports(parse(path))}


def relative(path: Path) -> str:
    return str(path.relative_to(BACKEND)).replace("\\", "/")


# ---------- 判据 1：依赖单向 ----------

def test_dependencies_point_one_way():
    """
    行为：每层只 import 它下方的层，叶子模块例外（任何层都能用）。

    来源：ADR-0007「契约跟着它的主人走且都做成叶子模块」+ `architecture.md` 第 4 节的依赖图。
    """
    modules = production_modules()
    leaves = leaf_modules(modules)
    problems = []
    for path in modules:
        owner = layer_of(path)
        for imported, lineno in project_imports(parse(path)):
            if imported == "<相对导入>":
                problems.append(f"{relative(path)}:{lineno} 用了相对导入，本项目统一绝对导入")
                continue
            top = imported.split(".")[0]
            if top == owner or imported in leaves or top in ALLOWED[owner]:
                continue
            problems.append(f"{relative(path)}:{lineno} {owner} 不该 import {imported}")
    assert not problems, "依赖方向被破坏：\n" + "\n".join(problems)


# ---------- 判据 2：扩展名覆盖 ----------

def test_every_declared_extension_has_a_dispatch_branch():
    """
    行为：`configs/formats.py` 声明的每个扩展名，在 `dispatch()` 里都有落点。

    来源：ADR-0003「容器格式必须往里看一层，否则完整解析是空头承诺」+ `docs/EXTENDING.md`
    第 3 步让加格式时改这两处。漏了这条，新格式会被判成容器本身，处理器永远进不去。
    """
    import configs.formats as formats

    declared: Set[str] = set()
    for name in (
        "DOCUMENT_EXTENSIONS",
        "WEBPAGE_EXTENSIONS",
        "IMAGE_EXTENSIONS",
        "AUDIO_EXTENSIONS",
        "VIDEO_EXTENSIONS",
        "ARCHIVE_EXTENSIONS",
        "INSTALL_PACKAGE_EXTENSIONS",
        "OFFICE_LEGACY_EXTENSIONS",
    ):
        declared |= set(getattr(formats, name))

    dispatch_fn = next(
        node
        for node in parse(BACKEND / "pipelines" / "steps" / "step4_extract.py").body
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch"
    )
    covered = {node.value for node in ast.walk(dispatch_fn) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    for node in ast.walk(dispatch_fn):
        if isinstance(node, ast.Compare) and any(isinstance(op, ast.In) for op in node.ops):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Name) and hasattr(formats, comparator.id):
                    covered |= set(getattr(formats, comparator.id))

    missing = sorted(declared - covered)
    assert not missing, "这些扩展名在 dispatch() 里没有落点：" + "、".join("." + ext for ext in missing)


# ---------- 判据 3：__init__ 无逻辑 ----------

def test_init_files_hold_no_logic():
    """行为：`__init__.py` 只有模块 docstring 与 import，不放任何逻辑。来源：ADR-0007。"""
    problems = []
    for path in sorted(BACKEND.rglob("__init__.py")):
        if "__pycache__" in path.parts or path.relative_to(BACKEND).parts[0] in NON_PRODUCTION:
            continue
        for node in parse(path).body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            problems.append(f"{relative(path)}:{node.lineno} 出现了逻辑（{type(node).__name__}）")
    assert not problems, "__init__.py 里放了逻辑：\n" + "\n".join(problems)


# ---------- 判据 4：上界的值只在注册表里写一次 ----------

def test_limit_values_live_only_in_the_registry():
    """
    行为：上界的**值**只在 `configs/limits.py` 里写一次——别的生产模块再写一个同值常量，
    或者又冒出一个 `MAX_*` 数字常量，就是第二处定义。

    来源：`configs/limits.py`「两个数值来源在这里汇合……**不再有第三个来源**」
    + 防漂移门禁 `check_limit_registry()`。值撞车比名字撞车更隐蔽：
    改了一处、另一处留着旧值，两份代码都「看起来对」，而用户看到的行为已经不一致。
    `configs/`（唯一来源本身）不参与，`scripts/` 与 `tests/` 不是生产模块。
    """
    import configs.limits as limits

    registered = {limit.value for limit in limits.LIMITS.values()}
    assert registered, "注册表是空的，下面的比对会假绿"
    problems = []
    for path in production_modules():
        relative_path = relative(path)
        if relative_path.startswith("configs/"):
            continue
        for node in parse(path).body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
                continue
            value = node.value.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if not names:
                continue
            if value in registered or any(name.startswith("MAX_") for name in names):
                problems.append(
                    f"{relative_path}:{node.lineno} {names[0]} = {value} 是第二处上界定义"
                    "（读 configs/limits.py 里的那一条）"
                )
    assert not problems, "上界的值只能在 configs/limits.py 里写：\n" + "\n".join(problems)


# ---------- 判据 5：前端契约镜像不缺字段 ----------

FRONTEND_TYPES = BACKEND.parent / "frontend" / "src" / "types.ts"


def ts_interface_fields(source: str, name: str) -> Set[str]:
    """从 TS 源里取 `export interface <name> { … }` 的字段名（只处理平铺字段）。"""
    # 非贪婪匹配到第一个行首的 `}`：契约里的这几个 interface 都是平铺字段，没有嵌套大括号
    match = re.search(rf"export interface {name} \{{(.*?)^\}}", source, re.S | re.M)
    if not match:
        return set()
    return set(re.findall(r"^\s*(\w+)\??:", match.group(1), re.M))


def test_frontend_meta_mirror_has_every_contract_field():
    """
    行为：`frontend/src/types.ts` 的 `Meta` 与后端 `routers/convert/schema.py` 的 `Meta`
    字段**一一对应**——少一个就是契约漂移，多一个就是前端凭空造字段。

    来源：`docs/architecture.md`「改 `meta` 是跨前后端的：`frontend/src/types.ts` 是契约镜像」。
    为什么需要这条门禁：这一层没有编译器兜底，漏字段不会报错，只会在界面上悄悄少显示一项——
    2026-10-04 这一轮就发现后端已落、镜像没跟的两个（`degrade_code` / `limits_hit`）。
    """
    from routers.convert.schema import Meta

    if not FRONTEND_TYPES.exists():
        pytest.skip(f"前端类型文件不存在：{FRONTEND_TYPES}")

    mirrored = ts_interface_fields(FRONTEND_TYPES.read_text(encoding="utf-8"), "Meta")
    # 没有这条自检，正则一旦失配就会两边都空、判据假绿
    assert len(mirrored) > 15, f"只从 types.ts 解析出 {len(mirrored)} 个 Meta 字段，正则大概率写错了"
    missing = sorted(set(Meta.model_fields) - mirrored)
    extra = sorted(mirrored - set(Meta.model_fields))
    assert not missing, "前端 Meta 缺这些后端字段：" + "、".join(missing)
    assert not extra, "前端 Meta 有后端没有的字段：" + "、".join(extra)


# ---------- 判据本身的自检 ----------

def test_the_rules_are_looking_at_real_modules():
    """
    行为：结构判据确实扫到了预期的模块量。

    没有这条，`production_modules()` 一旦因为路径写错返回空列表，上面各判据会全绿——那是假绿。
    """
    modules = production_modules()
    names = {module_name(path) for path in modules}
    assert len(modules) > 30, f"只扫到 {len(modules)} 个模块，路径大概率写错了"
    for expected in (
        "app",
        "configs.formats",
        "configs.settings",
        "pipelines.extract",
        "pipelines.understand",
        "pipelines.steps.step4_extract",
        "routers.convert.schema",
        "utils.textfile",
        "utils.usage",
    ):
        assert expected in names, f"结构判据没扫到 {expected}"


def test_leaf_modules_include_the_contracts():
    """
    行为：契约文件是叶子模块——它们不 import 实现层，所以任何层都能拿它当数据结构用。

    同时把完整清单打出来（pytest 会捕获输出，要加 `-s` 才看得见）：
    加新模块后想知道它算不算叶子、因而能不能被任何层 import，看这一行就够。
    """
    leaves = leaf_modules(production_modules())
    listing = "、".join(sorted(leaves))
    print(f"\n叶子模块（不 import 项目内任何东西，任何层都能用，共 {len(leaves)} 个）：{listing}")
    for expected in (
        "utils.processor_schema",
        "configs.formats",
        "configs.settings",
        "configs.errors",
    ):
        assert expected in leaves, f"{expected} 不再是叶子模块，依赖方向需要重新审视"
