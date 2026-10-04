# 给 AI 的本仓约定

本仓（anyurl2text）的例外与禁令。通用规矩在个人 skills 里；这里只放**本仓特有、且从代码与文档里查不到**的部分。

事实源指针：跑法 [docs/RUNBOOK.md](docs/RUNBOOK.md) ｜ 叫法 [CONTEXT.md](CONTEXT.md) ｜ 为什么这么定 [docs/adr.md](docs/adr.md) ｜ 当前工作态 [docs/HANDOFF.md](docs/HANDOFF.md)

## 后端由用户启动与维护

AI 不自起服务，也不为「让改动生效」重启它。动 backend 前后各探一次：

```powershell
Invoke-WebRequest http://127.0.0.1:8020/probe/health -TimeoutSec 5 -UseBasicParsing   # 200 = 在跑
```

没在听就**停下来请用户启动**。三条硬约束（端口唯一、reloader 要命名管道而沙箱禁、E1 的 26 条 HTTP 用例要写运行期临时目录）在 RUNBOOK 第 5 节；服务默认开 `RELOAD`，改完 `.py` 自己生效（第 6 节）。

## 验收：AI 可以跑自检缝

```powershell
cd backend
E:\miniconda\envs\multimodal\python.exe -m pytest tests -q    # 需要后端在跑
```

这是本仓对「验证由用户手动执行」那条全局默认的**例外**：本仓既定的验收标准是「用例通过才算过」，交接的「下一步」通常就是这条命令。用项目自己的解释器，不依赖 `conda activate`（见 `self-python-style`）。

期望数字以 [docs/HANDOFF.md](docs/HANDOFF.md) 为准（随用例增减而变）。贵缝 `-m model` 真调模型花钱，**由用户决定何时跑**。

## 临时探针与可写落点

系统 Temp 在沙箱下会被拒写（python 进程 `Permission denied`）。探针写仓内：

- `backend/tests/_workdir/` —— 随仓库带 `.gitkeep`，被 conftest 前后清空，适合一次性探针。
- `backend/data/debug/` —— 已在 `.gitignore` 里，适合要看中间产物。

**反复要用的检查落进仓里**：回归比对、指纹、结构扫描这类第二轮就写成 `backend/tests/` 的用例或 `backend/scripts/` 的一份，用上面这两个落点，别每轮现写再删。

## 改代码时的三条硬约束

1. **单文件预算**（判据 2：总行 ≤ 250、代码行 ≤ 150，见 `backend/tests/test_structure.py`）。改 `pipelines/` 下的文件前先量行数——`image.py`、`understand.py`、`step1_download.py` 都贴着上限。
2. **上界与错误码各只有一个定义处**：`backend/configs/limits.py`、`backend/configs/errors.py`。新增上界、或抛出新的错误码，都要登记进那两个表的白名单，否则静态门禁会红（它就该红）。
3. **`content` 不被模型产出改动**（ADR-0002）：`enhance_*` 只追加到 `summary` / `meta.*`，结构判据 5 会拦。

## Git

`commit` 可以做——小步、中文、说清为什么；**`push` 等用户说**（原话「需要推送的时候我会说」）。
