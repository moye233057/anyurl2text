# anyurl2text

把任意 URL 或本地文件转成**尽可能保留原始信息**的文本。

- 默认：确定性抽取（文档类关扩展仍有正文）
- 可选扩展：OCR（读图字）、ASR（音视频转写）、LLM（摘要 / 视觉相关能力）
- 图片与音视频：对应扩展未启用时只返回基础文件信息

接口：`POST /convert`（及 `/convert/file`），请求带 `ocr` / `asr` / `llm`。模块落点见 [docs/architecture.md](docs/architecture.md)。

设计原型：

- 架构见 [docs/architecture.md](docs/architecture.md)
- 术语见 [CONTEXT.md](CONTEXT.md)
- 接口契约见 [docs/API.md](docs/API.md)
- 现行为什么见 [docs/adr.md](docs/adr.md)；废决策见 [docs/HISTORY.md](docs/HISTORY.md)
- 各格式链路见 [docs/PIPELINES.md](docs/PIPELINES.md)
- 配置、验收与对外部署见 [docs/RUNBOOK.md](docs/RUNBOOK.md)
- 扩展入口见 [docs/EXTENDING.md](docs/EXTENDING.md)
- 计划与选做见 [docs/TODO.md](docs/TODO.md)
- 本仓给 AI 的规矩见 [AGENTS.md](AGENTS.md)

## 生产信息

- 接口文档：http://127.0.0.1:8020/docs
- 前端：http://127.0.0.1:5173
- 健康检查：http://127.0.0.1:8020/probe/health
- GitHub：无（当前远端在 Gitee）

## 获取代码

```powershell
git clone <本仓库地址>
cd anyurl2text
```

目录名以你 clone 下来的为准。以下命令均在**仓库根目录**执行，除非写了 `cd`。

## 环境要求

| 工具 | 版本 |
|---|---|
| uv | 终端能执行 `uv --version` |
| Python | ≥ 3.12（由 uv 按 `.python-version` 安装） |
| Node.js | ≥ 18（自带 npm） |

### 安装 uv

Windows（PowerShell）：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS / Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

装完后**新开一个终端**，确认：

```powershell
uv --version
```

### 安装 Node.js

安装 Node.js **18 或更高**。确认：

```powershell
node -v
npm -v
```

## 配置

Windows：

```powershell
copy .env.example .env
```

macOS / Linux：

```bash
cp .env.example .env
```

用编辑器打开仓库根目录的 `.env`，按需填写 LLM 相关项（如 `DEEPSEEK_API_KEY`）。  
OCR / ASR 为本仓第三方服务（`services/{ocr,asr}/<实现>/` + 各实现自己的 `models/` 自备权重）；`start.py` 默认可一并拉起（`8021` / `8022`）。详见 [services/README.md](services/README.md) 与 RUNBOOK。

## 后端

安装依赖（仓库根目录）：

```powershell
uv sync
```

启动：

```powershell
cd backend
uv run uvicorn app:app --host 127.0.0.1 --port 8020
```

`--port` 须与 `.env` 里 `ANYURL2TEXT_PORT` 一致（默认 `8020`）。

确认：

```powershell
Invoke-WebRequest http://127.0.0.1:8020/probe/health -UseBasicParsing
```

应返回 HTTP 200。接口文档：http://127.0.0.1:8020/docs

## 前端

另开一个终端，在仓库根目录：

```powershell
cd frontend
npm install
npm run dev
```

确认浏览器打开 http://127.0.0.1:5173 。  
前端开发服把 `/api` 代理到后端；后端须已在跑。

## 一键启动

两端依赖都装好后（已执行过 `uv sync` 与 `frontend` 下的 `npm install`），可在仓库根同时拉起前后端：

```powershell
uv run python start.py
```

Ctrl+C 一起停止。对外部署见 [RUNBOOK](docs/RUNBOOK.md#对外部署下载者自配)。目标态下同一启动器还应能按条件拉起 OCR/ASR 扩展（见计划）。

## 系统依赖

| 能力 | 依赖 | 缺了会怎样 |
|---|---|---|
| 确定性抽取 | `uv sync` 安装的解析库 | 对应格式降级或起不来 |
| LLM 扩展 | 配置的 API Key 或本地兼容端点 | 无摘要 / 无视觉相关增强；确定性段不受影响 |
| OCR 扩展 | `services/ocr/paddle-ocr` + 自备权重 | 图片读字不可用；文档确定性正文仍在 |
| ASR 扩展 | `services/asr/qwen3-asr` + 自备权重 + `imageio-ffmpeg` | 音视频无转写，仅结构信息 |

扩展名与分派表见 [PIPELINES §2.3](docs/PIPELINES.md)。

## 项目结构

```text
pyproject.toml / uv.lock     核心后端依赖
start.py                     一键拉起（可含 services 子进程）
.env.example                 配置模板 → 复制为根目录 .env
services/                    第三方模型服务（ocr/asr 专栏 → 各实现）
  ocr/paddle-ocr/            OCR 实现（自带 models/ 权重目录）
  asr/qwen3-asr/             ASR 实现（自带 models/ 权重目录）
backend/app.py               FastAPI 入口
backend/configs/             设置、格式表、限额、错误码、日志
backend/routers/             /convert · /probe
backend/pipelines/           convert 编排 + steps + processors
backend/extensions/          出口客户端（只调第三方 / 远程 API）
backend/utils/               契约、表格公共件、出站复用等
backend/tests/               自检缝
backend/scripts/             make_fixtures.py 等
frontend/                    单路 + OCR/ASR/LLM 开关
docs/                        架构、ADR、API、RUNBOOK、管线
```

```text
app → routers → pipelines → { extensions , utils } → configs
```

`services/{ocr,asr}/<实现名>` 不进入上图 import 链；核心经本机 URL 调用。

## 使用说明

### 1. 界面

打开 http://127.0.0.1:5173 。左侧输入与三开关，右侧结果。

```powershell
cd backend
uv run python scripts/make_fixtures.py
```

### 2. 调接口

`POST /convert` 或 `/convert/file`，字段见 [docs/API.md](docs/API.md)。

### 3. 自检

```powershell
cd backend
uv run python -m pytest tests --ignore=tests/test_extract_contract.py -q
```

HTTP 契约与贵缝见 [RUNBOOK](docs/RUNBOOK.md)。随计划迁移后，双路对比类用例将改为开关矩阵。
