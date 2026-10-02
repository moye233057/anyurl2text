# -*- coding: utf-8 -*-
"""
结构自查：搬迁后「脉络还成不成立」的静态检查——用脚本比对，不靠肉眼（EXTENDING 的完成标准）。

五条判据（对应 ADR 0007）：
1. 依赖单向：每层只能 import 它下方的层；**叶子模块**（自己不 import 项目内任何东西，例如
   `routers/convert/schema.py`、`configs/formats.py`）任何层都可以用——它们不可能造成循环。
2. 单文件预算：总行数 ≤ 250，代码行（剥离 docstring、注释、空行）≤ 150——抓的是「塞了太多东西」。
3. 扩展名覆盖：`configs/formats.py` 声明的每个扩展名，在 `pipelines/steps/step4_extract.py` 里都有落点。
4. `__init__.py` 里不放逻辑（只有模块 docstring）。
5. 模型增强不碰 content：`enhance_*` 函数体里不得对 `.content` 赋值 / 追加（ADR 0002/0007 的核心不变量）。

用法：在 backend/ 下执行 `python scripts/check_structure.py`；退出码 0 全过，1 有失败项。
"""
import ast
import importlib
import sys
import tokenize
from pathlib import Path
from typing import Dict, List, Set, Tuple

BACKEND = Path(__file__).resolve().parent.parent
MAX_FILE_LINES = 250
MAX_CODE_LINES = 150

# 层 → 允许被它 import 的项目内层（ADR 0007：依赖单向；叶子模块任何层都能用，见 check_dependencies）
ALLOWED: Dict[str, Set[str]] = {
    "configs": set(),
    "utils": {"configs"},
    "models": {"configs", "utils"},
    "pipelines": {"configs", "models", "utils"},
    "routers": {"configs", "pipelines"},
    "app": {"configs", "pipelines", "routers"},
}

def iter_modules() -> List[Path]:
    """backend/ 下除 scripts/、tests/ 与缓存外的全部 .py。

    tests/ 和 scripts/ 一样不是生产分层：判据 1 只对 `ALLOWED` 里那几层有意义，
    把测试目录纳进来会让 `layer_of()` 返回不在 ALLOWED 里的层名而直接崩。
    """
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.relative_to(BACKEND).parts[0] in ("scripts", "tests"):
            continue
        found.append(path)
    return found

def layer_of(path: Path) -> str:
    """模块属于哪一层：顶层文件算 app，其余取第一段目录名。"""
    parts = path.relative_to(BACKEND).parts
    return "app" if len(parts) == 1 else parts[0]

def module_name(path: Path) -> str:
    """模块的点分名字，用于判断「被 import 的是不是叶子模块」。"""
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
                top = alias.name.split(".")[0]
                if top in ALLOWED:
                    found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入：本项目不用，出现就当问题
                found.append(("<相对导入>", node.lineno))
                continue
            top = (node.module or "").split(".")[0]
            if top in ALLOWED:
                found.append((node.module or "", node.lineno))
    return found

def leaf_modules(modules: List[Path]) -> Set[str]:
    """叶子模块：自己不 import 项目内任何东西，所以任何层都可以 import 它而不可能造成循环。"""
    return {
        module_name(path)
        for path in modules
        if not project_imports(ast.parse(path.read_text(encoding="utf-8")))
    }

def docstring_lines(tree: ast.Module) -> Set[int]:
    """所有 docstring 覆盖的行号集合。"""
    lines: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            start = first.lineno
            end = getattr(first, "end_lineno", start)
            lines.update(range(start, end + 1))
    return lines

def count_code_lines(path: Path, tree: ast.Module) -> int:
    """代码行 = 总行数 − 空行 − 纯注释行 − docstring 行。"""
    source_lines = path.read_text(encoding="utf-8").splitlines()
    skip = docstring_lines(tree)
    code = 0
    for index, raw in enumerate(source_lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or index in skip:
            continue
        code += 1
    return code

def check_dependencies(modules: List[Path]) -> List[str]:
    """判据 1。"""
    leaves = leaf_modules(modules)
    problems = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = layer_of(path)
        for imported, lineno in project_imports(tree):
            if imported == "<相对导入>":
                problems.append(f"{path.relative_to(BACKEND)}:{lineno} 用了相对导入，本项目统一绝对导入")
                continue
            top = imported.split(".")[0]
            if top == owner:  # 同层内部互相 import 是正常的（如 formats/plaintext.py → formats/rtf.py）
                continue
            if imported in leaves:  # 叶子模块：任何层都能用
                continue
            if top not in ALLOWED[owner]:
                rel = path.relative_to(BACKEND)
                problems.append(f"{rel}:{lineno} {owner} 不该 import {imported}")
    return problems

def check_line_budget(modules: List[Path]) -> List[str]:
    """判据 2。"""
    problems = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total = len(path.read_text(encoding="utf-8").splitlines())
        code = count_code_lines(path, tree)
        if total > MAX_FILE_LINES or code > MAX_CODE_LINES:
            rel = path.relative_to(BACKEND)
            problems.append(f"{rel} 总行 {total}（上限 {MAX_FILE_LINES}）/ 代码行 {code}（上限 {MAX_CODE_LINES}）")
    return problems

def check_extension_coverage() -> List[str]:
    """判据 3。"""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    formats = importlib.import_module("configs.formats")

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

    tree = ast.parse((BACKEND / "pipelines" / "steps" / "step4_extract.py").read_text(encoding="utf-8"))
    dispatch_fn = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "dispatch"
    )
    literals = {node.value for node in ast.walk(dispatch_fn) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    referenced: Set[str] = set()
    for node in ast.walk(dispatch_fn):
        if isinstance(node, ast.Compare) and any(isinstance(op, ast.In) for op in node.ops):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Name) and hasattr(formats, comparator.id):
                    referenced |= set(getattr(formats, comparator.id))

    covered = literals | referenced
    return [f"扩展名 .{ext} 在 dispatch() 里没有落点" for ext in sorted(declared - covered)]

def check_init_files() -> List[str]:
    """判据 4。"""
    problems = []
    for path in sorted(BACKEND.rglob("__init__.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            problems.append(f"{path.relative_to(BACKEND)}:{node.lineno} __init__.py 里出现了逻辑（{type(node).__name__}）")
    return problems

def check_enhance_keeps_content(modules: List[Path]) -> List[str]:
    """判据 5：enhance_* 里不得改 content。"""
    problems = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("enhance"):
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
                    rel = path.relative_to(BACKEND)
                    problems.append(f"{rel}:{inner.lineno} {node.name} 动了 content（模型产出只能进 ocr_text/summary/notes）")
    return problems

def main() -> int:
    modules = iter_modules()
    checks = [
        ("依赖方向", check_dependencies(modules)),
        ("单文件预算", check_line_budget(modules)),
        ("扩展名覆盖", check_extension_coverage()),
        ("__init__ 无逻辑", check_init_files()),
        ("enhance 不碰 content", check_enhance_keeps_content(modules)),
    ]
    print(f"检查 {len(modules)} 个模块")
    print("叶子模块（不 import 项目内任何东西，任何层都能用）：" + "、".join(sorted(leaf_modules(modules))))
    failed = 0
    for name, problems in checks:
        if problems:
            failed += 1
            print(f"[失败] {name}：{len(problems)} 项")
            for problem in problems:
                print(f"    - {problem}")
        else:
            print(f"[通过] {name}")
    print("\n结论：" + ("全部通过" if not failed else f"{failed} 项检查失败"))
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
