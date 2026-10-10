# TODO · 评审后的待办与待裁决

> **用途**：2026-10-09 那轮「上生产 / 发 GitHub / 设计问题 / 文档与代码冲突」评审结论的**可执行副本**。
> 勾选即状态：`[ ]` 未做、`[x]` 已做。标 **待裁决** 的条目在拿到用户裁决前**不动代码**；标 **可直接做** 的随时能开工。
> **关联**：[architecture.md](docs/architecture.md)（目标态） · [adr.md](docs/adr.md)（为什么） · [RUNBOOK.md](docs/RUNBOOK.md)（怎么跑与验收） · [CONTEXT.md](CONTEXT.md)（叫法） · [AGENTS.md](AGENTS.md)（本仓 AI 禁令）
> **基线**：2026-10-09 ｜ HEAD `bb1ea10` ｜ 文中 `路径:行` 是**审计当时的快照**，改过代码就按符号名找，别信行号。
> **怎么来的**：五路只读审计（隐私与发布、生产就绪、文档-代码核对、前端、架构批判）+ 主会话逐条复核。下面每条都带出处，没有出处的判断不收。
> **不做什么**：这里不记「为什么这么定」——那属于 `docs/adr.md`；本轮裁决生效后再落进对应的文档。

## 0 可复核的结论数字

| 事实 | 值 | 怎么核 |
|---|---|---|
| 跟踪文件 / 体积 | 158 个 / 1.93 MB（`backend/data` 39 个共 1.01 MB） | `git ls-files` + 求和 |
| 提交 / 远端 | 59 个提交，`github.com/moye233057/anyurl2text` | `git log` / `git remote -v` |
| 结构门禁 | `pytest tests/test_structure.py -q` → **8 passed**（行数门禁已废，ADR-0010） | 实测 |
| 能力域覆盖项 | 15 个域与 `RUNBOOK.md:160-174` 逐个相符：detect 105 / table 74 / document 30 / utils 25 / degrade 18 / understand 16 / limits 16 / errors 14 / http 13 / structure 9 / epub 8 / media 7 / links 3 / container 2 / rag 2 | `--collect-only` 逐域计数 |
| 最大生产模块 | `rtf.py` 总行 463 / 代码行 291（预算 500 / 300） | 与判据同算法实测 |
| 上界 / 错误码 | `limits.py` 20 条；硬失败 17、降级 25、模型 15 | 计数 + 注释比对 |

**结论一句话**：解析内核与契约是干净的（两条路逐字节一致有人守、格式矩阵一一对应、上界单点登记），问题集中在**对外暴露的准入与生命周期、许可证与派生来源、以及文档腐烂**。

---

## 1 待裁决（6 条）

| # | 状态 | 结论（2026-10-10） |
|---|---|---|
| 1 | **已落代码+ADR-0011** | 全链路 `REQUEST_BUDGET_SEC=120`；下载 `DOWNLOAD_BUDGET_SEC=20`；快/慢双池 + 429 |
| 2 | **已落** | understand 显式 `_run_model_enhance()`；EXTENDING 改为四步 |
| 3 | 已失效 | RAG 口已下线 |
| 4 | 已裁决 | 依赖图合并 |
| 5 | **已裁决 E** | 废除「enhance 不碰 content」结构判据；正文语义见 ADR-0011 |
| 6 | **已删工作区** | 交接长文删除；**commit 仍等你点头** |

---

## 2 文档修订（可直接做）

代码是对的、文档腐烂的那些。做完在 `backend/` 下跑一次 `pytest tests -q` 确认没碰坏任何东西。

- [x] **2.1 判据条数**（2026-10-10；ADR-0010 后再改）：现为 **6 条判据 + 2 条自检 = 8 项**（行数门禁已删）。
- [x] **2.2 能力域描述**（2026-10-10）：`capability_structure` 已跟 8 项对齐。
- [x] **2.3 缝数量文案**（2026-10-10）：`conftest` 开头改为「三缝」。
- [x] **2.4 自检缝前置**（2026-10-10）：`EXTENDING` / `RUNBOOK` 写明 `pytest tests -q` 含 HTTP、须后端或 `--ignore`。
- [x] **2.5 ADR-0003 指纹清单**（2026-10-10）：ZIP 指纹补 epub / ofd。
- [x] **2.6 ADR-0006 表述**（2026-10-10）：开关与页数上限拆成两项。
- [x] **2.7 clamp 类是否上报**（2026-10-10）：改为逐条名单（含 `max_visibility_ranges` 上报）。
- [x] **2.8 `unprocessed_pages` 语义**（2026-10-10）：补 `= pages_used+1 … pages`。
- [x] **2.9 阈值口径**（2026-10-10）：上界值 vs 判定阈值分开写。
- [x] **2.10 预算压力的对象**（2026-10-10）：指出 `rtf.py` 贴预算，改正 `limits.py` 自称。
- [x] **2.11 降级码分组**（2026-10-10）：改为 5 + 20。
- [x] **2.12 模块清单口径**（2026-10-10）：表格族合并成一份清单。
- [x] **2.13 `max_long_edge` 例外**（2026-10-10）：标注当前不上报，指向 B-⑧。
- [x] **2.14 新增本文档的指针**：README / AGENTS 已挂 `TODO.md`。

---

## 3 代码修复

### 3.1 批次 A · 生产阻断（P0，不解决不能对外）

- [ ] **A-① 准入**：入口无任何鉴权（`routers/convert/router.py:32-88`）、CORS 全开（`app.py:97` `allow_origins=["*"]`），而 `docs/architecture.md:15` 明写「不做鉴权、多租户、限流、计费」。上生产第一步是改这条决策：加 Key/Bearer（FastAPI `Depends`）或前置带认证的反代，CORS 收敛到具体来源。现状下任意网页可跨域驱动本服务花你的 Key、拿你的响应。
- [ ] **A-② 请求体早拒补全**：中间件只按 `Content-Length` 判（`app.py:76-78`），ADR-0005 第 4 条承诺的是「读请求体之前拒掉」。不带 `Content-Length` 的分块上传会先被 Starlette 缓冲落盘（>1 MB 滚落到系统临时目录，无总量上限）→ 反代 `client_max_body_size` + 中间件累计读取字节提前断开。
- [x] **A-③ 请求生命周期**（2026-10-10）：全链路/下载分预算、检查点、快/慢双池 429；LLM/OCR/ASR/ffmpeg 单次调用按剩余预算收紧超时。**明确不做（首版）**：客户端断开取消在途模型调用。
- [ ] **A-④ 解压总量上限**：容器成员 `archive.read()` 全量进内存（`processors/epub.py:207`、`ofd.py:126`、`odf.py:221`），无解压总量上限 → 读前用 `ZipInfo.file_size` 预检 + `ZipFile.open()` 限量读。
- [x] **A-⑤ RAG 500 固定文案**：RAG 口已下线（2026-10-10），本项关闭。
- [ ] **A-⑥ 依赖可复现 + 许可证**：`backend/requirements.txt:1` 自述「未锁版本」，无 `==`；`pymupdf` 是 **AGPL-3.0**，对外提供服务触发网络分发条款（`pymupdf4llm` 已随 RAG 口移除）。→ 出带 `==` 的锁文件（至少 pymupdf / Pillow / openai / starlette / fastapi），并把 AGPL 的影响写进 README/NOTICE 或换库。
- [ ] **A-⑦ 进程模型**：`RELOAD` 默认 true（`configs/settings.py:27`）、`uvicorn.run` 单进程无 workers（`app.py:120-128`）、无 Dockerfile/systemd/反代说明、前端对外只跑 Vite dev server（`start.py:86`）。→ 生产 `RELOAD=false` + 多 worker + 由容器/systemd 托管，前端交 `vite build` 产物。

### 3.2 批次 B · 生产加固（P1）

- [ ] **B-① 成本闸门**：无单请求 token/金额上限、无缓存、无去重，`cost_cny` 只事后上报（`utils/usage.py:86`）。→ 按请求累计 token 达阈即中止模型阶段；对相同源做短期缓存。
- [ ] **B-② 只读前 16 字节**：`utils/textfile.py:64` `path.read_bytes()[:HEADER_PREVIEW_BYTES]` 整文件进内存（调用点 `steps/step4_extract.py:85`、`processors/install_package.py:35`）→ 改 `path.open("rb").read(16)`。
- [ ] **B-③ 抽音轨先截断**：`utils/ffmpeg.py:59-68` 全量转码，`processors/media.py:128` 在整条 wav 落盘后才判 `ASR_MAX_AUDIO_SECONDS` → 先 `-t` 截断或先探时长。
- [ ] **B-④ 临时目录不泄漏**：`steps/step3_prepare.py:78-84` 只对 `FetchError` 清理；写盘 `OSError`、`httpx.InvalidURL` 会穿透（`step1_download.py:221-224` 只转两类）→ mkdtemp 后整段 `try/except BaseException` 清理。
- [ ] **B-⑤ 端口单一事实源**：`start.py:29-31` 硬编码 8020 / 5173 且不读 `.env`（真值在 `configs/settings.py:24`，`RUNBOOK.md:27` 承诺可配）。改 `ANYURL2TEXT_PORT` 后一键启动等满 90 s 再把两个好进程一起 `taskkill`。→ `start.py` 读同一个 `.env`；`vite.config.ts` 加 `strictPort: true`。
- [ ] **B-⑥ SSRF 收口**：DNS 二次解析的 TOCTOU 窗口（`steps/step1_download.py:83` vs `:199`，`architecture.md:96` 自认）；`step1_download.py:88-92` 解析失败是 `continue`（fail-open）；https → http 的重定向降级目前允许。→ 解析一次并把 IP 固定进连接、解析失败即拒、逐跳禁止降级。
- [ ] **B-⑦ PDF 上界**：`processors/pdf.py:137` 全页累积、`:89` 单页 pixmap 无像素上限 → 给总页数、单页像素各设一条并登记进 `limits_hit`。
- [ ] **B-⑧ 补齐报告链路**：`max_long_edge` 登记在表（`configs/limits.py:136-137`）却全链路不上报（`models/images.py:32-37` 只缩放）→ 回传命中名，或把它降成不报告的条目。
- [ ] **B-⑨ 回退不整本加载**：`processors/excel.py:132-134` openpyxl `read_only` 失败后回退整本加载，可被畸形 xlsx 触发（行数上限形同虚设）→ 直接降级。
- [ ] **B-⑩ 图片预算**：Pillow 只到「超 1 倍仅告警」（`processors/image.py:120-121`）；长图切片数无上限（`models/paddle_ocr.py:90-97`）→ 读图前做像素预算、切片数设限。
- [ ] **B-⑪ 日志与可观测**：日志只到 stdout、无 request id、无耗时、无轮转（`configs/log_config.py:37`），且完整记录含 query 的 URL（`routers/convert/router.py:34`）→ 加 request id/耗时、落盘轮转、URL 只留 scheme+host+path。
- [ ] **B-⑫ 客户端复用**：每请求新建 OpenAI 客户端与每次 `httpx.post`（`pipelines/understand.py:62`、`models/deepseek.py:56-60`、`models/paddle_ocr.py:62-64`）→ 进程级复用 + 出口信号量。

### 3.3 批次 C · 加固（P2）

- [ ] **C-①** 归档条目先全量建列表再截断（`processors/archive.py:35`、`install_package.py:44`）→ `infolist()` 后立即切片。
- [ ] **C-②** `ImageFile.LOAD_TRUNCATED_IMAGES = True` 是进程级全局开关（`processors/image.py:30`）→ 收到单次解码处。
- [ ] **C-③** `uvicorn.run` 未配 `proxy_headers` / `forwarded_allow_ips`，全仓无 `TrustedHostMiddleware`（`app.py:120-128`）→ 上反代后 `request.client` 会失真，将来按 IP 限流会限错人。
- [ ] **C-④** 外部强依赖无启动自检与熔断（`configs/settings.py:83,89`，失败只落 `meta.model_error`）→ 启动自检 + 短超时熔断。
- [ ] **C-⑤** `/probe/health` 全为静态回显（`routers/probe/router.py:46` 的 `ffmpeg_available` 是 import 期结果）→ 拆出 readiness 真探 OCR/ASR/临时目录可写。

### 3.4 待裁决后才动（引用第 1 节）

- [x] 1 → ADR-0011 + `request_budget` / `concurrency`（2026-10-10）
- [x] 2 → EXTENDING 四步 + `understand._run_model_enhance`（2026-10-10）
- [x] 3 → RAG 口已下线，无需再改契约（2026-10-10）
- [x] 4 → `architecture.md` 第 4 节依赖图合并成一层（2026-10-10）
- [x] 5 → 废除原 enhance/content 判据（ADR-0011 / 结构判据 7 项）
- [x] 6 → 提交 handoff 删除（随本轮 push）

---

## 4 发 GitHub

### 4.1 发布前必须（许可与隐私）

- [ ] **4.1-① LICENSE**：全仓无 LICENSE / COPYING / NOTICE，README 114 行无许可章节 → 默认「保留所有权利」，公开后没人能合法使用。
- [ ] **4.1-② 派生来源确权**：表格实现派生自 `anydoc`，代码里精确引到上游文件行号（现 `utils/excel/grid.py` 等）。私有设计文档名已从测试/交接清掉；仍须确认上游仓库与许可证，再决定署名 / NOTICE。
- [ ] **4.1-③ 第三方许可提示**：`pymupdf`(AGPL-3.0)、PaddleOCR(Apache-2.0)、`imageio-ffmpeg`(打包静态 ffmpeg，LGPL/GPL) → 加 `NOTICE` 或 README「第三方组件」小节。
- [x] **4.1-④ 预设换源**（2026-10-10）：去掉私有 OSS / 租户 / 企微 CDN；本仓样例落在 `frontend/public/samples/`，点击走本地上传；保留一条公共 `https://example.com/`。
- [ ] **4.1-⑤ 轮换 Key**：`.env` 有真实 DeepSeek Key —— **未进 Git**，但仍是活密钥 → **请你在 DeepSeek 控制台轮换**；本对话读过该文件，按已暴露面处理。可再加 pre-commit 密钥扫描。
- [x] **4.1-⑥ SSRF 边界如实声明**（2026-10-10）：README 已写明 DNS rebinding 时间窗；补按 IP 连接仍见 B-⑥。

### 4.2 个人信息与私有工作态

- [x] **4.2-① 本机解释器绝对路径**（2026-10-10）：文档与测试说明一律改为 `python -m pytest` / `python app.py`。
- [x] **4.2-② 私有兄弟仓名**（2026-10-10）：改为中性描述 + 指向本仓契约文件。
- [x] **4.2-③ 私人 skill / 私有设计文档名**（2026-10-10）：`self-*` 与 `借鉴设计/…` 引用已删或改写。
- [x] **4.2-④ 私有工作态文档出库**（2026-10-10）：`HANDOFF.md` 已改指 `TODO.md`；交接正文工作区已删。**仍待你说一声再 commit**，才能从 HEAD 去掉。
- [x] **4.2-⑤ `AGENTS.md` / `HANDOFF.md` / `start.py` 去个人化**（2026-10-10）：保留本仓硬规矩，去掉个人协作原话与 conda 环境名。
- [ ] **4.2-⑥ git 身份**：唯一作者带 gitee noreply 邮箱；推到 GitHub 会带出账号 → **待你裁决**是否改作者 / 新历史。

### 4.3 工程化补件

- [ ] **4.3-① 锁依赖**：见 A-⑥。
- [ ] **4.3-② lockfile 换源**：`frontend/package-lock.json` 115 条 `resolved` 全指 `registry.npmmirror.com` → 用官方源重新生成。
- [ ] **4.3-③ 最小 CI**：无 `.github/` → 加一条 Actions 跑 `pytest tests --ignore=tests/test_extract_contract.py` + `npx tsc --noEmit`。
- [ ] **4.3-④ 协作件**：无 CONTRIBUTING / SECURITY / CHANGELOG → 按需补。
- [x] **4.3-⑤ 样例断链**（2026-10-10）：`notes.md` 链到 `../../docs/adr.md`。
- [x] **4.3-⑥ 注释腐烂**（2026-10-10）：`.gitattributes` 不再写死样例个数。
- [x] **4.3-⑦ 配置模板同步**（2026-10-10）：本地 `.env` 已对齐 `.env.example` 缺项（仍 gitignore）。
- [ ] **4.3-⑧ 前端工程面**：无 lint、无测试、`tsconfig.json:18` 的 `include` 漏掉 `vite.config.ts`；`presets.ts` 六条校验分支零测试；`vitest` 覆盖 `presets.ts` 值得先做。
- [ ] **4.3-⑨ 前端可用性**（开源第一印象）：生产构建连不上后端（`src/api.ts:4` `const BASE = '/api'` 只活在 Vite 代理里，后端路由本身无该前缀）→ `import.meta.env.VITE_API_BASE ?? '/api'` + RUNBOOK 写清托管方式；长任务无取消无进度（`App.tsx:162` 只有一行「处理中…」，三个 `fetch` 无 `signal`）→ 先加 `AbortController` + 已用秒数；两条招牌预设依赖本仓之外的本机 OCR/ASR（`presets.config.js:65,90`）→ desc 里标注需另起服务。
- [ ] **4.3-⑩ 前端小项**：`table_kind` 未知值被静默显示成「数据表」（`ResultPanel.tsx:97`）；传输错误塞进左栏共用 state（`App.tsx:23,70,179`），报错位置与按钮分居两栏；`api.ts:15-19` 强转不判形状且无 ErrorBoundary（今天 UI 路径不可达，是隐患）；`presets.ts:27-29` 让 `kind: ''` 过校验；`/api` 绝对路径与预设的 `BASE_URL` 在子路径部署下不一致；`Health` 13 个字段只用 1 个（`ffmpeg_available` / `allow_http_source` 等本可在启动时就提示）。

---

## 5 已确认无需动（防重复核查与过度加固）

**别再核查这些**：

- `.env` 未入库且不在任何提交历史（四重验证）；tracked 文件里无真实凭据。
- `backend/data/scanned.pdf`（19.5 MB）未跟踪（`.gitignore:15`）；`backend/data/debug/`、`dist/`、`node_modules/`、`__pycache__` 均未入库。
- 体积健康：tracked 共 1.93 MB、`.git` 4.03 MB、历史最大 blob 123 KB。
- 样例外无真实个人信息：`screenshot.png` 由 `scripts/make_fixtures.py` 程序绘制，邮箱 / 电话是占位，`table.csv` 的人名是单字。
- 前端无 XSS 面（`dangerouslySetInnerHTML|innerHTML|eval(` 全目录 0 命中）；产物里无后端地址、无密钥。
- 预设取不回 / 条目写错都不白屏（`presets.ts` 汇总 `problems` + 空态用 `null` 区分）；同管线按钮 `disabled={!canRun || loading}` 无并发竞态；结果恒带 `meta.source`。
- 契约逐字段一致：`types.ts` 的 `Meta` 25 / `Usage` 8 / `Timings` 4 / `ConvertResponse` 7 与 `routers/convert/schema.py` 一致，`Health` 13 与 `routers/probe/router.py` 一致；九个模态取值三处一致；convert/probe 路由路径一致；capability marker 与 `pytest.ini` 登记一致（RAG 域已删）。
- 三条错误通道（硬失败 / 降级 / 模型阶段）在前后端各自分得清。
- 下载层已做对的：重定向逐跳重校验、DNS 返回的**每个**地址都判（`::ffff:127.0.0.1` 实测也被拒）、`verify=True` 恒开、流式写入边写边计数并删半成品、上传与下载共用同一上限、错误码构造期白名单强校验。
- 压缩包只读目录**不解压**；ZIP 指纹只读中央目录；落盘名不可控（`mkstemp` / `upload.bin`），无路径穿越面。
- `start.py` 的编排本身有信息：依赖点名、端口预检、子进程早退识别、Ctrl+C 收整棵进程树。
- 无进程内可变状态（`mkdtemp` 按请求唯一），将来加多 worker 不会状态不一致；正常路径 `finally` 整树清理。

**反向结论（这些取舍站得住，别动）**：共用一份抽取内核、契约做成叶子模块、一格式一文件、上界单点登记、`extract` 不放任何 OCR ——「两条路逐字节可对比」这条主线真的守住了。

---

## 6 执行顺序

| 批次 | 内容 | 前置 |
|---|---|---|
| 0 | 第 2 节 12 条文档修订（纯文档，`pytest tests -q` 复核） | 无 |
| 1 | 第 4.1 / 4.2 发布阻断（许可、预设换源、轮换 Key、清理个人路径） | 无 |
| 2 | 第 3.1 生产阻断 A-①…⑦ | 无（A-⑥ 的 AGPL 需先定许可证） |
| 3 | 第 3.2 / 3.3 加固 | 批次 2 落地后 |
| 4 | 第 3.4（六条裁决落地） | 第 1 节的裁决 |
| 5 | 第 4.3 工程化补件 | 与批次 1 并行 |

**改动规矩**：动代码前后各跑一次 `cd backend && python -m pytest tests -q`（要后端在 8020 应答）；贵缝 `-m model` 花钱，由用户决定何时跑；动文档后把结论同步进 `docs/adr.md` / `docs/architecture.md`，不要只在本文件里记。
