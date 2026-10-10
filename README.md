# anyurl2text

把任意 URL 或本地文件转成**尽可能保留原始信息**的文本。
`/extract` 只靠代码抽取（稳、快、不花钱）
`/understand` 在同一份结果上叠加模型（慢、计费，可读图、扫描件、语音，可按问题作答）

设计原型：

- 架构见 [docs/architecture.md](docs/architecture.md)
- 术语见 [CONTEXT.md](CONTEXT.md)
- 接口契约见 [docs/API.md](docs/API.md)
- 现行为什么见 [docs/adr.md](docs/adr.md)；废决策见 [docs/HISTORY.md](docs/HISTORY.md)
- 各格式链路见 [docs/PIPELINES.md](docs/PIPELINES.md)
- 配置、验收与对外部署见 [docs/RUNBOOK.md](docs/RUNBOOK.md)
- 扩展入口见 [docs/EXTENDING.md](docs/EXTENDING.md)
- 本仓给 AI 的规矩见 [AGENTS.md](AGENTS.md)

## 生产信息

- 接口文档：http://127.0.0.1:8020/docs
- 对比页：http://127.0.0.1:5173
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

用编辑器打开仓库根目录的 `.env`，按需填写 `DEEPSEEK_API_KEY`。  
读图字 / 转写语音时，另起本机 OCR（`8021`）与 ASR（`8022`）。

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

Ctrl+C 一起停止。对外部署见 [RUNBOOK](docs/RUNBOOK.md#对外部署下载者自配)。

## 系统依赖

| 能力 | 依赖 | 缺了会怎样 |
|---|---|---|
| 纯代码抽取 | `uv sync` 安装的解析库 | 对应格式降级或起不来 |
| 模型摘要 / 识图描述 | `.env` 的 `DEEPSEEK_API_KEY` | `/understand` 写明原因；`/extract` 不受影响 |
| 图片读字 | 本机 Paddle OCR（`8021`） | 文字图增强失败进 `meta.model_error` |
| 音视频转写 | 本机 ASR（`8022`）+ `imageio-ffmpeg` | 退回结构信息 / 降级，其余格式不受影响 |

扩展名与分派表见 [PIPELINES §2.3](docs/PIPELINES.md)。

## 项目结构

```text
pyproject.toml / uv.lock     后端依赖
start.py                     一键拉起前后端
.env.example                 配置模板 → 复制为根目录 .env
backend/app.py               FastAPI 入口
backend/configs/             设置、格式表、限额、错误码、日志
backend/routers/             /extract · /understand · /probe
backend/pipelines/           编排 + steps 六步 + processors（一格式一文件）
backend/models/              DeepSeek / OCR / ASR / 读图
backend/utils/               契约、下载辅助、表格公共件、出站复用等
backend/tests/               自检缝与契约测试
backend/scripts/             make_fixtures.py 等
frontend/                    三栏对比页（Vite）
docs/                        架构、ADR、API、RUNBOOK、管线
```

```text
app → routers → pipelines → { models , utils } → configs
```

一次请求：下载/落盘 → 判型 → 确定性抽取 →（仅 understand）模型增强与摘要 → 组装七字段响应。

## 使用说明

### 1. 界面对比

打开 http://127.0.0.1:5173 。左输入，中栏纯代码，右栏模型增强；两栏各有「运行」。预设见 [RUNBOOK §4](docs/RUNBOOK.md#4-界面怎么用)。

```powershell
cd backend
uv run python scripts/make_fixtures.py
```

### 2. 调接口

URL：`POST /extract` 或 `POST /understand`（JSON）；本地文件：`…/file`（multipart）。字段见 [docs/API.md](docs/API.md)。

| | `/extract` | `/understand` |
|---|---|---|
| 调模型 | 否 | 是 |
| 费用 | 0，`usage` 为 `null` | 按 DeepSeek token 记账 |
| 图片 / 扫描件 / 音视频 | 结构信息或空正文 | OCR / 逐页还原 / ASR 等 |

### 3. 自检

```powershell
cd backend
uv run python -m pytest tests --ignore=tests/test_extract_contract.py -q
```

HTTP 契约与贵缝见 [RUNBOOK](docs/RUNBOOK.md)。
