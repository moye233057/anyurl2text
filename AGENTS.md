# 给 AI 的本仓约定

本仓（anyurl2text）的例外与禁令。这里只放**本仓特有、且从代码与文档里查不到**的部分。

事实源指针：跑法 [docs/RUNBOOK.md](docs/RUNBOOK.md) ｜ 叫法 [CONTEXT.md](CONTEXT.md) ｜ 为什么这么定 [docs/adr.md](docs/adr.md) ｜ 选做启发 [TODO.md](TODO.md)（勿默认逐项开工）｜ 评审余项归档 [docs/review-remainder.md](docs/review-remainder.md) ｜ 进展报告 [docs/plan-completed.md](docs/plan-completed.md)

## 环境：用 uv，不用随便哪个 conda/pip

后端解释器与依赖只认本仓 `.venv`（`uv sync` / `uv run`）。不要 `pip install -r backend/requirements.txt`（该文件已是迁移动态提示）。改依赖只改根目录 `pyproject.toml`，再 `uv lock` / `uv sync`。

## 后端由维护者启动

不要为「让改动生效」擅自重启后端。动 backend 前后各探一次：

```powershell
Invoke-WebRequest http://127.0.0.1:8020/probe/health -TimeoutSec 5 -UseBasicParsing   # 200 = 在跑
```

没在听就停下来说明，请维护者启动。端口唯一、自动重载、HTTP 用例要写仓内临时目录等边界见 RUNBOOK 第 5–6 节；默认开 `RELOAD` 时改完 `.py` 会自己生效。

## 验收：可跑自检缝

```powershell
# 仓库根先 uv sync；再：
cd backend
uv run python -m pytest tests -q    # 需要后端在跑
```

本仓既定验收标准是「用例通过才算过」。贵缝 `-m model` 真调模型花钱，**由维护者决定何时跑**。

## 临时探针与可写落点

系统 Temp 在部分沙箱下会被拒写。探针写仓内：

- `backend/tests/_workdir/` —— 随仓库带 `.gitkeep`，被 conftest 前后清空，适合一次性探针。
- `backend/data/debug/` —— 已在 `.gitignore` 里，适合要看中间产物。

反复要用的检查落进仓里：回归比对、指纹、结构扫描写成 `backend/tests/` 的用例或 `backend/scripts/` 的一份。

## 报成果怎么写

一律按 **能力 → 触发场景 → 可观测指标（前 → 后）→ 靠什么验证** 写，指标要能被复核。

- 能用数字就用数字：支持的格式数 / 抽出多少章·页·列·张·条 / 落盘或调模型次数 / 耗时 / 成本 / 契约字段数。
- **禁用没有数字的形容**：「重构了 X」「优化了 Y 模块」「完善了机制」「提升了代码质量」。
- 给不出指标的改动（纯文档、纯重命名）就直说「这次没有能力或指标变化」。
- 没有性能改动就不要写性能。

## 测试用例怎么写

1. **一条用例 = 一个用户可见事实**：断言「这份 CSV 出来是 3 列」，而不是「函数返回了某类型」。
2. **预期值必须有独立来源**：样例生成参数 / 文档声明 / 手算常量；**禁止**「跑一遍看输出」当预期。
3. **清单驱动的覆盖**（每个扩展名 / 魔数 / 时段各一条）允许参数化，但 docstring 要写明它覆盖的是「清单里的每一项」；**汇报时按「能力点 + 覆盖项数」说**。
4. **同一能力只在最靠近它的缝验一次**：HTTP 缝只抽查代表格式，不重复进程内的格式矩阵。
5. **状态码只是附加断言**，主断言必须是业务事实（正文内容、字段值、降级码、命中的上界名）。
6. 每条用例的 docstring 第一行会被 `tests/conftest.py` 收进留痕文件（`backend/data/debug/last_test_run.txt`）——写成一句人能读懂的话。
7. **能力域标记**：每条用例打一个 `@pytest.mark.capability_*`，并同步登记进 `backend/pytest.ini` 的 `markers`。按域跑：`pytest tests -m capability_epub`；清单见 `docs/RUNBOOK.md` 第 5 节。

## 改代码时的三条硬约束

1. **`processors/` 一格式族一文件**（ADR-0010 / ADR-0007）：不为过行数门禁再拆 `*_merges` / `*_visibility` 式兄弟文件；表格公共进 `utils/excel/`，契约进 `utils/processor_schema.py`，模型侧公共进 `models/`。单文件行数门禁已废除（原判据 2 测试已删）。
2. **上界与错误码各只有一个定义处**：`backend/configs/limits.py`、`backend/configs/errors.py`。
3. **`content` 分接口**（ADR-0011）：extract 仅纯代码段；understand 合并模型转写，OCR/ASR 双写 `meta.*`。

## Git

小步提交、说明为什么；是否推送到远端由维护者决定。
