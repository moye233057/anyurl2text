# -*- coding: utf-8 -*-
r"""
缝 S：结构判据（静态）。

这里接替已删除的 `backend/scripts/check_structure.py`。那五条判据守的是**行为测不到的东西**——
类与函数的摆放、依赖朝向、文件体积、`enhance_*` 有没有偷偷改 `content`。
它们是 ADR-0002 与 ADR-0007 的核心不变量，所以从「一个没人跑的脚本」搬成「跟着测试一起跑的用例」。

判据（编号与 ADR-0007 对齐）：

1. **依赖单向**：每层只能 import 它下方的层；**叶子模块**（自己不 import 项目内任何东西）
   任何层都可以用——它们不可能造成循环。
2. **单文件预算**：总行数 ≤ 250 且代码行（剥离 docstring、注释、空行）≤ 150。
3. **扩展名覆盖**：`configs/formats.py` 声明的每个扩展名，在 `step4_extract.dispatch()` 里都有落点。
4. **`__init__.py` 里不放逻辑**（只有模块 docstring 与 import）。
5. **模型增强不碰 content**：`enhance_*` 函数体里不得对 `.content` 赋值或追加（ADR-0002）。

`backend/scripts/` 与 `backend/tests/` 不是生产模块，不参与判据 1、2、4：
前者是一次性生成脚本，后者是测试自身。
"""
import ast
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

BACKEND = Path(__file__).resolve().parent.parent
MAX_FILE_LINES = 250
MAX_CODE_LINES = 150
NON_PRODUCTION = {"scripts", "tests"}

# 能力域见 AGENTS.md「测试用例怎么写」；按域跑：python -m pytest tests -m capability_structure
pytestmark = pytest.mark.capability_structure

# 层 → 允许被它 import 的项目内层（ADR-0007）
ALLOWED: Dict[str, Set[str]] = {
    "configs": set(),
    "utils": {"configs"},
    "models": {"configs", "utils"},
    "pipelines": {"configs", "models", "utils"},
    "routers": {"configs", "pipelines"},
    "app": {"configs", "pipelines", "routers"},
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


def docstring_lines(tree: ast.Module) -> Set[int]:
    """所有 docstring 覆盖的行号。"""
    lines: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            lines.update(range(first.lineno, getattr(first, "end_lineno", first.lineno) + 1))
    return lines


def count_code_lines(path: Path, tree: ast.Module) -> int:
    """代码行 = 总行数 − 空行 − 纯注释行 − docstring 行。"""
    skip = docstring_lines(tree)
    code = 0
    for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or index in skip:
            continue
        code += 1
    return code


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


# ---------- 判据 2：单文件预算 ----------

def test_no_module_exceeds_the_line_budget():
    """
    行为：单文件总行 ≤ 250 且代码行 ≤ 150。

    来源：`architecture.md` 第 4 节。判据不适用于 `backend/scripts/`（一次性生成脚本，
    `make_fixtures.py` 近 500 行）与 `backend/tests/`。
    """
    problems = []
    for path in production_modules():
        tree = parse(path)
        total = len(path.read_text(encoding="utf-8").splitlines())
        code = count_code_lines(path, tree)
        if total > MAX_FILE_LINES or code > MAX_CODE_LINES:
            problems.append(f"{relative(path)} 总行 {total}/{MAX_FILE_LINES} · 代码行 {code}/{MAX_CODE_LINES}")
    assert not problems, "有文件超出预算：\n" + "\n".join(problems)


# ---------- 判据 3：扩展名覆盖 ----------

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


# ---------- 判据 4：__init__ 无逻辑 ----------

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


# ---------- 判据 5：enhance 不碰 content ----------

def test_enhance_functions_never_touch_content():
    """
    行为：`enhance_*` 不赋值、不追加 `content`。

    来源：ADR-0002——模型产出只能进 `summary` / `meta.ocr_text` / `meta.asr_text` / `meta.notes`，
    碰了 `content` 就等于「两条路逐字节可对比」不再成立，而那是本项目存在的理由。
    """
    problems = []
    for path in production_modules():
        for node in ast.walk(parse(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("enhance"):
                continue
            for inner in ast.walk(node):
                touched = False
                if isinstance(inner, (ast.Assign, ast.AugAssign)):
                    targets = inner.targets if isinstance(inner, ast.Assign) else [inner.target]
                    touched = any(isinstance(t, ast.Attribute) and t.attr == "content" for t in targets)
                elif isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    touched = (
                        isinstance(inner.func.value, ast.Attribute)
                        and inner.func.value.attr == "content"
                        and inner.func.attr in ("append", "extend", "insert", "clear")
                    )
                if touched:
                    problems.append(f"{relative(path)}:{inner.lineno} {node.name} 动了 content")
    assert not problems, "模型增强改了 content：\n" + "\n".join(problems)


# ---------- 判据 6：上界的值只在注册表里写一次 ----------

def test_limit_values_live_only_in_the_registry():
    """
    行为：上界的**值**只在 `configs/limits.py` 里写一次——别的生产模块再写一个同值常量，
    或者又冒出一个 `MAX_*` 数字常量，就是第二处定义。

    来源：`configs/limits.py`「两个数值来源在这里汇合……**不再有第三个来源**」
    + 借鉴设计 P5「防漂移门禁 `check_limit_registry()`」。值撞车比名字撞车更隐蔽：
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


# ---------- 判据 7：前端契约镜像不缺字段 ----------

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

    没有这条，`production_modules()` 一旦因为路径写错返回空列表，上面五条会全绿——那是假绿。
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
        "pipelines.rag_extract",
        "pipelines.steps.step4_extract",
        "routers.convert.schema",
        "routers.rag.schema",
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
    for expected in ("routers.convert.schema", "routers.rag.schema", "configs.formats", "configs.settings"):
        assert expected in leaves, f"{expected} 不再是叶子模块，依赖方向需要重新审视"
