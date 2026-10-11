# 运行 · 配置 · 验收

> **用途**：首次启动见 [README 的启动方式](../README.md#启动方式)；这份讲跑起来之后的配置项、样例生成、验收清单与故障处置。
> **关联**：[architecture.md](./architecture.md)（模块与落点） · [API.md](./API.md)（字段契约）
> **基线**：2026-09-12

---

## 1. 配置项

全部配置从**项目根目录**的 `.env` 读取（不是 `backend/`）。缺文件不报错，只是所有项取默认值——启动日志第二行会打出它实际读了哪个路径，认准那一行。

### 必填

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | 不填则 LLM 扩展不可用并说明原因；确定性抽取不受影响 |

### 常用

| 变量 | 默认 | 作用 | 什么时候改 |
|---|---|---|---|
| `DEEPSEEK_MODEL` | `deepseek-flash` | 模型名 | 换模型时。用一个模型同时处理文本与图片 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 模型端点 | 换供应商，或指向本机 OpenAI 兼容服务（见下方「本机 LLM」） || `THINKING_ENABLED` | `false` | 思考模式 | 想让摘要质量更高时打开。**默认关闭是刻意的**：思维链按输出侧计费，输出价约为输入未命中价的 4 倍 |
| `THINKING_EFFORT` | `high` | 思考模式的推理力度（随请求发成 `reasoning_effort`） | 只在 `THINKING_ENABLED` 打开时才发出去：想更快更省就调低，想更深就调高 |
| `ANYURL2TEXT_PORT` | `8020` | 后端端口 | `start.py` 与 `vite.config.ts` 同读项目根 `.env`，改完重启即可。本机连号：本服务 8020、OCR 8021、ASR 8022 |
| `FRONTEND_PORT` | `5173` | 前端 dev server 端口 | 与后端端口一样读同一份 `.env`；Vite 开了 `strictPort`，被占用会直接失败而不是换端口 |
| `ALLOW_PRIVATE_HOSTS` | `false` | 允许下载内网 / 回环地址 | 仅在本机串本地文件服务调试时打开；对外可访问的部署保持 `false` |
| `ALLOW_HTTP_SOURCE` | `true` | 是否接受 `http://` 源。`false` 时只收 https，`http://` 在发起连接之前就被 422 拒掉（`download_http_not_allowed`）。**默认 `true` 就是既有行为** | 想让传输一律走加密时设 `false`；本机串内网的 HTTP 服务保持 `true` |
| `OCR_API_URL` | `http://127.0.0.1:8021/ocr-b64` | 当前选用的 OCR 服务（默认 `services/ocr/paddle-ocr`） | 换实现或端口时 |
| `ASR_API_URL` | `http://127.0.0.1:8022/asr-b64` | 当前选用的 ASR 服务（默认 `services/asr/qwen3-asr`） | 换实现或端口时 |
| `ASR_MAX_AUDIO_SECONDS` | 600 | 送转写的音轨时长上限；超过则跳过转写（视频/容器音频会先探时长，超限不抽完整 wav；抽取时仍带 `-t` 封顶）。命中写进 `limits_hit` / `meta.notes` | 想转写更长录音时。**先把 ASR 服务的 `max_new_tokens` 调大**，否则上游会把尾部静默丢掉 |

### 成本相关

想控花费时按这个顺序调。单价表与第三方成本闸在 `extensions/deepseek.py`（2026-10-10 按官方 Flash 价核对）。

| 变量 | 默认 | 影响 |
|---|---|---|
| `MAX_MODEL_INPUT_CHARS` | 120000 | 送进模型的文本上限，直接决定摘要那一次调用的输入 token |
| `MAX_EMBEDDED_IMAGES` | 3 | 每份文档最多 OCR 几张**不同的**内嵌图（同一张图被引多次只算一张） |
| `RENDERED_PAGE_MAX` | 10 | 扫描件最多渲染几页 |
| `MAX_CONTENT_CHARS` | 200000 | `content` 的字符上限（也会影响送模型的量） |
| `LLM_MAX_TOKENS` | 8192 | 单次模型输出上限。长文档摘要看着被截断就提高它 |
| `DEEPSEEK_COST_LIMIT_ENABLED` | `false` | 是否开启「单请求 DeepSeek 费用上限」；本机 OCR/ASR 不计费、不读此开关 |
| `DEEPSEEK_MAX_COST_CNY` | `1.0` | 开启后：累计 `cost_cny` 达此值则中止后续 DeepSeek 调用 |
| `DEEPSEEK_TOKEN_LIMIT_ENABLED` | `false` | 是否开启「单请求 DeepSeek token 上限」 |
| `DEEPSEEK_MAX_TOKENS_PER_REQUEST` | `200000` | 开启后：prompt+completion 累计达此值则中止后续 DeepSeek 调用 |
| `DEBUG_ARTIFACTS` | `false` | 打开后把中间产物留档到 `backend/data/debug/`，用于排查 |

### 其余

| 变量 | 默认 | 作用 |
|---|---|---|
| `ANYURL2TEXT_HOST` | `127.0.0.1` | 监听地址；要让局域网访问改成 `0.0.0.0` |
| `RELOAD` | `true` | 自动重载：改 `backend/` 下的 `.py` 立即生效。`cd backend` 后 `uv run python app.py`，或仓库根 `uv run python start.py`，都认这个开关 | 不想多一个监督进程就设 `false`（那时改完要手动重启）。注意它只监视 `.py`：改 `.env` 仍要重启 |
| `REQUEST_BUDGET_SEC` | 120 | 单请求全链路时间预算（下载+解析+模型） |
| `DOWNLOAD_BUDGET_SEC` | 20 | 下载层单独上限（不超过全链路剩余） |
| `CONCURRENCY_FAST` / `CONCURRENCY_MEDIA` | 32 / 8 | 文档类等快池与图片/音视频慢池并发；满员 HTTP 429 |
| `EGRESS_LLM_CONCURRENCY` | 64 | DeepSeek 出口同时在途调用上限（进程内信号量）。默认很宽，本机自用几乎碰不到；只有极大请求量才需要调低。OCR/ASR 只复用连接、不加这道闸 |
| `CONNECT_TIMEOUT_SEC` / `READ_TIMEOUT_SEC` | 10 / 30 | 连接与读取超时（秒） |
| `MAX_DOWNLOAD_BYTES` | 209715200（200 MB） | 下载与上传的内容上限 |
| `MAX_REDIRECTS` | 5 | 重定向跟随上限 |
| `ANYURL2TEXT_USER_AGENT` | 桌面 Chrome UA | 下载时的 UA |
| `RENDERED_PAGE_OCR_ENABLED` | `true` | 扫描件页面渲染总开关 |
| `RENDERED_PAGE_DPI` | 150 | 渲染 DPI。小字识别不清时提高（模型侧会把图缩到约 1300×1300 总像素，过高没有收益） |
| `RENDERED_PAGE_RETRY_MIN_CHARS` | 20 | 整页识别结果短于此值就判定「没认出来」，切成上下两半重试 |
| `LLM_TIMEOUT_SEC` / `LLM_MAX_RETRIES` | 180 / 3 | 模型调用超时与重试次数 |
| `OCR_TIMEOUT_SEC` | 60 | 单次 Paddle OCR 请求超时（纵向长图会切片并发，按片计时） |
| `ASR_TIMEOUT_SEC` | 300 | 单次语音转写请求超时；长音轨转写慢，所以比 OCR 宽 |
| `FFMPEG_TIMEOUT_SEC` | 120 | 抽音轨超时（本地 CPU 转码，与模型超时分开计） |
| `START_SERVICES` | `true` | `start.py` 是否尝试拉起默认 OCR/ASR（权重目录非空才起） | 只要核心时设 `false`，再按 `services/` README 手动起 |

### 本机 LLM（OpenAI 兼容端点）

出口仍是 `backend/extensions/deepseek.py`：把三件套指到本机即可，不必改代码。示例（本机 [Ollama](https://ollama.com/) 开了 OpenAI 兼容口，模型名按你 `ollama list` 的为准）：

```env
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_MODEL=qwen2.5:7b
DEEPSEEK_API_KEY=ollama
```

改完重启后端。单价表仍按 DeepSeek 官方 Flash 记；本机端点的 `usage.cost_cny` 仅作形状占位，不要当真账。其它兼容服务（vLLM、LM Studio 等）同理：换 `BASE_URL` / `MODEL` / `API_KEY`。

### 模块内常量（不是 `.env` 变量）

写死的上界现在**只有一处定义**：`backend/configs/limits.py`。改代码才变；列在这里是为了让上限类
数字都有出处，别再当成 `.env` 项去找。同一张表还登记了可配置的那些（值读 `settings`），
并规定命中时处理器只报名字、文案由表里的模板生成。

| 常量 | 取值 | 所在文件 | 作用 |
|---|---|---|---|
| `MAX_ENTRIES` | 500 | `backend/configs/limits.py` | `content` 里最多列出的压缩包条目数，超出只报数量 |
| `MAX_LINKS` | 100 | `backend/configs/limits.py` | 网页与 docx 的链接清单上限，防止导航区把正文淹掉 |
| `MAX_ROWS_PER_SHEET` | 5000 | `backend/configs/limits.py` | 单张表最多渲染的行数 |
| `MAX_ROWS_TO_READ` | 5001（`MAX_ROWS_PER_SHEET + 1`） | `backend/configs/limits.py` | 读取上限比渲染上限多一行，用来区分「还有后续行」与「这是最后一行」 |
| `MAX_TABLE_ROWS` | 2000 | `backend/configs/limits.py` | 单个表格最多渲染的行数（docx 与 odf 共用这一处，不再各写一份） |
| `MAX_GRID_EXPANSION` | 200000 | `backend/configs/limits.py` | 表格跨度展开的格位总量上限 |
| `MAX_GRID_SLOTS` | 1000000 | `backend/configs/limits.py` | 单张表的格位数上限（实测一个格位约 100 字节，100 万格位约 100 MB） |
| `MAX_GRID_TEXT_CHARS` | 5000000 | `backend/configs/limits.py` | 单张表的文字量上限（交付侧的 `max_content_chars` 是组装时事后截断，峰值已经发生） |
| `MAX_REPEATED_SLOTS` | 100 | `backend/configs/limits.py` | odf 的 `number-*-repeated` 重复次数上限 |
| `MAX_VISIBILITY_RANGES` | 24 | `backend/configs/limits.py` | 隐藏行列的**登记段数**上限：只钳 `notes` 篇幅，隐藏工作表与隐藏行列的内容一律照抽 |
| `MAX_TEXT_BYTES` | 20971520（20 MB） | `backend/configs/limits.py` | 文本类文件的体积上限，超过则只读头部，避免超级大的日志整份进内存 |
| `MAX_ZIP_UNCOMPRESSED_BYTES` | 20971520（20 MB） | `backend/configs/limits.py` | EPUB / OFD / ODT·ODP 一次请求内 ZIP 成员解压字节总量；读前预检声明体积，再用流式封顶 |
| `MAX_LONG_EDGE` | 1600 | `backend/configs/limits.py` | 送模型前图片长边缩到多少像素 |
| `MAX_PDF_PAGES` | 200 | `backend/configs/limits.py` | PDF 抽取最多处理多少页 |
| `MAX_PDF_PAGE_PIXELS` | 36000000 | `backend/configs/limits.py` | 单页渲染 / 内嵌图 pixmap 像素上限（宽×高） |
| `MAX_IMAGE_PIXELS` | 40000000 | `backend/configs/limits.py` | 读图像素预算；超过拒绝解码 |
| `MAX_OCR_SLICES` | 40 | `backend/configs/limits.py` | 纵向长图 OCR 最多切多少片 |
| `TEXT_LAYER_MIN_CHARS` | 30 | 名字见 `limits.py` 的 `NON_LIMIT_THRESHOLDS`；**值**在 `backend/pipelines/processors/pdf.py` | **判定阈值，不是上界**：上界值只在 `limits.py`；阈值只登记名字，数值仍在各自模块 |

## 2. 启动服务

一条命令把后端与前端都拉起来（两个服务仍是各自独立的进程，只是由一个脚本拉起与收尾）：

```bash
uv sync                  # 首次或改完 pyproject.toml 后
uv run python start.py   # 项目根目录执行；无参数
```

它按顺序做这些事：确认解释器是本仓 `.venv`（否则有 uv 则自动重入）→ 打印解释器并检查后端依赖与 `frontend/node_modules` → 检查 `.env` 里的 `ANYURL2TEXT_PORT` / `FRONTEND_PORT`（默认 8020 / 5173）是否已被占用（占用就直接说明「可能已经在跑」并退出，不会起第二个）→ 起后端（同解释器跑 `app.py`，cwd=`backend/`）与前端（`npm run dev`，cwd=`frontend/`）→ 等到两个都真的能应答 → 打开浏览器 → 按 Ctrl+C 把两个进程一起收掉（Windows 上用 `taskkill /T`，否则 npm 拉起的 node 会变成占着端口的孤儿进程）。

不想自动开浏览器就把文件顶部的 `OPEN_BROWSER` 改成 `False`。只想起后端、或要分开调试，仍照 [README 的启动方式](../README.md#启动方式) 分别启动。

### 日志与 request id

- 每条日志是单行 JSON（stdout + 落盘），字段含 `request_id`（无请求时为 `-`）。
- 每次 HTTP 请求自动生成或沿用请求头 `X-Request-ID`，响应头带回同名；access 日志形如 `GET /probe/health status=200 duration_ms=1.2`（**不含 query**）。
- 转换接口打 URL 时只留 scheme+host+path，query / fragment 不进日志。
- 落盘：`backend/data/logs/anyurl2text.log`，单文件约 10MB、保留 5 个备份（轮转）；目录在 `.gitignore` 里。

### 对外部署（下载者自配）

本仓默认按**本机开发**起：`RELOAD` 默认 `true`、单进程、`start.py` 拉 Vite 开发服。公开仓库**不附带** Dockerfile、systemd 单元或一整套反代配置——生产环境由谁下载谁在自己的机器 / 编排里配（ADR-0012）。

运维常见做法是：在生产机上设一个「当前是生产」的环境变量（名字由你们的规范定，例如 `APP_ENV=production`），再据此加载另一份 `.env` 或注入下列项。**本服务代码不读「是不是生产」这个开关**；它只认下面这些已有配置。

| 你要什么 | 怎么做 |
|---|---|
| 关掉改代码自动重启 | `.env` 设 `RELOAD=false`，再启后端（改 `.env` 本身也不触发重载） |
| 多进程扛并发 | 不要用 `start.py`；`cd backend` 后 `uv run uvicorn app:app --host 0.0.0.0 --port 8020 --workers 4`。worker 数按 CPU 与内存自定 |
| 非本机访问 | `ANYURL2TEXT_HOST=0.0.0.0`（并自行处理网络暴露面；本仓默认不做鉴权，见 architecture「明确不做」） |
| 前端静态托管 | `cd frontend && npm run build`，用 nginx / Caddy 等托管 `frontend/dist/`；API 反代到后端。不要把 Vite `npm run dev` 当生产前端 |
| 请求体再挡一道 | 反代设体积上限，见下文「请求返回 413」里的 `client_max_body_size` 示例 |

验收：生产配置下进程日志不应再出现「自动重载已打开」；前端用的是 build 产物而不是 5173 开发服。

## 3. 生成样例

```bash
cd backend
uv run python scripts/make_fixtures.py
```

输出到 `backend/data/`，与随仓库提供的文本类样例同级：`plain.txt`、`table.csv`、`sample.json`、`feed.xml`、`page.html`、`sample.rtf`、`notes.md` 这 7 个是**仓库自带的手写样例**，脚本既不生成也不覆盖它们（`notes.md` 还是 `/convert` 验收用例的必需样例）。需要后端依赖已安装（脚本用到 python-docx / python-pptx / openpyxl / pymupdf / pillow）。
生成的东西：带表格与内嵌图的 docx、含空单元格的 xlsx、六页 pptx、**含合并单元格的 docx / xlsx / pptx（`merged.*`，横向与纵向各一处）**、**表格边界形状（阶段 20：`merged_tail.docx` 行尾缺格；`merged.ods` / `merged.xls` 横纵合并各一处，各带一张只有尾部合并的表）**、**隐藏工作表与隐藏行列（阶段 21：`hidden.xlsx` 隐藏第 2、4-5 行与 B 列 + hidden / veryHidden 各一张表；`hidden.xls` 同形状走 BOUNDSHEET；`hidden.ods` 的隐藏表走样式 `table:display="false"`）**、**源标表头（阶段 22：`header.docx` 首行带 `w:tblHeader`；`header.odt` 首行在 `table:table-header-rows` 行组里）**、**同一张图被引 3 次的 docx / pptx / pdf（`repeated.*`，另有一张只引 1 次）**、**带合并表格与两级列表的 `structured.odt`**、**含超链接的 `hyperlink.docx`（同一地址写两次 + 一条没有文字的链接，验证链接去重与截断）**、**三章的 `sample.epub`（spine 顺序与 manifest / zip 顺序都故意不同，另带跨章与章内引用、样式表）**、三页有文字层的 PDF（标题加六节、两节一页，正文逐页不同）、**三页无文字层的扫描件**、odt、odp、**两页的 `sample.ofd`（页序取自文档根、zip 写入顺序相反，第 2 页带一个图像对象）**、**制表符分隔的 `sample.tsv`（一格含逗号与分号）**、压缩包、APK 结构样本、老版 `.doc` 头样本、16kHz 单声道 wav、由它封装的 mp4。

`.apk` 与 `.doc` 只是**结构样本**，用来验证类型识别与降级分支，不是真的安装包与 Word 文档。
音视频样例是一段**正弦音、没有说话声**，只用于验证结构信息与「抽音轨 → 送本机 ASR」这条链；要验收转写质量请自备一段真实录音。
mp4 那一份需要 `imageio-ffmpeg`（可选依赖），没装时脚本跳过它并说明原因。

## 4. 界面怎么用

打开前端后是**单路**：左侧选源与能力开关，右侧看结果。

- **输入**：切「远程 URL / 本地文件」，可点预设样例：本仓样例走本地上传，远程条目填入 URL（并尝试复制到剪贴板）。
- **能力选择**：`LLM` / `ASR` 是开关，`OCR` 是提供商单选（模型视觉 / 本机 Paddle），同一能力只能选一个。看图（判型、语义描述、扫描页、内嵌图）只要 LLM；音视频转写要 ASR；摘要要 LLM。不可用项在界面上置灰，标题上给「可用 N / M」。矩阵见 [API.md](./API.md) §1.1。
- **「测试」按钮**：展开「扩展能力」后每行右侧有它，调 `POST /probe/test/{capability}/{provider}`——**点下去会真发一次调用**：侧车只是本机 `GET /health`（不花钱），API 会发一次最小请求（花极小 token，视觉提供商还带一张 64×64 的图，验的就是「这家 API 收不收图」）。通过后该行变为可选并自动选上；失败原因显示在该行下面，悬停也能看到。
- **运行**：一次请求打 `POST /convert`（或 `/convert/file`），开关随请求带上。

预设样例由 `frontend/public/presets.config.js` 配置：一个带注释的顶层文件，页面启动时按 URL 取回来，
所以**改它不用改前端代码**：dev 下刷新页面即生效，跑构建产物时要重新 build（或直接改 `dist/` 里那份副本）。
条目字段是 `kind`（标题）/ `desc`（描述）/ `file` 或 `url`（二选一），顺序即界面顺序；字段缺失、类型不对、
同时写了两者、或 url / file 不合法的条目会被跳过，界面在列表下方列出原因。配置取不回来（被删、语法错）时同样出提示，不会白屏。
本仓样例文件在 `frontend/public/samples/`。

## 5. 验收清单

改完代码后逐条走一遍，每条都有可核查的判据。前两条是命令（可重复执行、退出码非 0 即失败），后面是人工判断的部分。

| # | 做什么 | 判据 |
|---|---|---|
| 0a | 后端在跑时 `cd backend` 后 `uv run python -m pytest tests -q` | 自检缝全过：**5 条结构判据 + 2 条自检 = 7 项**（`tests/test_structure.py`；单文件行数门禁已按 ADR-0010 废除）+ 各格式抽取 + 判型 + 通用件（不联网下载、不调模型、不花钱）。**这一条里有 13 条 HTTP 用例（`tests/test_extract_contract.py`），所以后端必须在跑**；只要进程内自检、不起服务时加 `--ignore=tests/test_extract_contract.py` |
| 0b | 起服务后 `cd backend` 后 `uv run python -m pytest tests -m model -q` | 贵缝全过：ADR-0011 正文对比、模型增强产出、独立图片判型分流、音视频转写（真调模型，且本机 OCR（8021）/ ASR（8022）在跑） |

**别为了跑测试另起一个后端**：端口只有一个，第二个起不来（`10048`）或者两个互相抢端口。改完后端代码时，开着自动重载的那个服务会自己加载新代码（见第 6 节），直接在它上面跑 0a/0b 就行；服务没开就先按第 2 节起一个，再跑。

pytest 的缓存是关掉的（`backend/pytest.ini` 里的 `-p no:cacheprovider`），所以跑完不会在 `backend/` 下留 `.pytest_cache` 或 `pytest-cache-files-*`。代价是没有 `--lf` / `--ff`——本套件跑一次只要几秒，直接全跑。

两条 0 号命令之外都是人工核查。样例缺失时新套件**跳过**该条而不是失败——样例由 `cd backend` 后 `uv run python scripts/make_fixtures.py` 生成（见第 3 节）。

### 怎么看测试结果

**这套测试分三缝，先认清自己要跑哪一缝**（每一条的 docstring 开头就写着它的「行为 / 来源 / 预期值来源」）：

| 缝 | 覆盖什么 | 前置 | 命令（cwd=`backend/`） |
|---|---|---|---|
| 自检缝 | 处理器产出、判型、通用件、结构判据、错误码 | 无（几秒） | `cd backend` → `uv run python -m pytest tests --ignore=tests/test_extract_contract.py` |
| HTTP 缝 | 真实服务上的代表格式 + 契约形状（13 条） | **后端在 8020 应答** | `cd backend` → `uv run python -m pytest tests/test_extract_contract.py -v` |
| 贵缝 | `/convert` 开关矩阵、模型增强、音视频转写 | 真模型 + 本机 8021/8022 | `cd backend` → `uv run python -m pytest tests -m model` |

**按能力域看**（跑完的汇总就照这个口径给，别再只数条数）：

| 能力域（`-m` 直接用） | 覆盖项 | 观测信号（断言的是什么，不是状态码） |
|---|---|---|
| `capability_detect` | 105 | `(扩展名, 判据来源, 模态)`——决定这个文件送哪个处理器、响应的 `type` |
| `capability_document` | 30 | 正文内容 / 页数 / 内嵌图数 / 文本框节 |
| `capability_table` | 74 | 合并单元格不重复、行宽一致、分隔符、数值格式、网格不变式与边界形状、网页/EPUB/docx/ODF/RTF 的源标表头行数、表类型（data / layout）、隐藏工作表与隐藏行列照抽、ODF 行内空列与行尾空格、xlsx 覆盖位与 ODS 重复列坐标、PPTX 残留文字 |
| `capability_media` | 7 | 图片结构信息、内嵌图去重与名额、音轨参数 |
| `capability_container` | 2 | 压缩包 / 安装包的条目清单 |
| `capability_links` | 3 | 链接是否进交付文本、去重与上限 |
| `capability_epub` | 8 | spine 顺序、章节标记、内部引用消歧、CSS 不进正文 |
| `capability_degrade` | 18 | 降级码是不是那一个、HTTP 仍是 200 |
| `capability_limits` | 16 | 命的哪条上界、文案是否来自同一模板 |
| `capability_errors` | 14 | 码表白名单、`error` 对象、模型失败通道、`internal` 兜底 |
| `capability_http` | 13 | 真实服务上的代表格式 + 契约形状（**只抽查代表，不重复进程内矩阵**） |
| `capability_utils` | 34 | 解码顺序、峰谷计价、账本累计、第三方成本闸 |
| `capability_structure` | 7 | 依赖方向、扩展名覆盖、`__init__` 无逻辑、上界值只准写一处、前端契约镜像（另含 2 条自检；行数门禁已废） |
| `capability_understand` | 16 | 进程内 + 假客户端：`/convert` 开关矩阵、合并 OCR、记账、模型失败通道、跳过扩展 |

```powershell
cd backend
uv run python -m pytest tests -m capability_epub -v          # 只跑 EPUB 这一个能力域
uv run python -m pytest tests -m "capability_table or capability_limits" -v
```

**别只报条数**：15 种格式各一条是**清单驱动的覆盖**，它算 1 个能力点里的 15 个覆盖项。汇报与验收按「**能力域 × 覆盖项**」说（跑完的汇总就是按域给的）。加能力域要同时登记进 `backend/pytest.ini` 的 `markers`，否则 `--strict-markers` 会挡下。

**跑完一次会留三份东西给人看**（不是只有 `passed` 的数量）：

1. 终端结尾的「按能力域汇总」：每个能力域一行「N 个覆盖项：通过 x、未通过 y」——**先看哪个域红了**；紧接「没通过的用例」逐条列出用例名 + **「这条在测什么」** + 用时；
2. `--durations=5` 标出最慢的五条——「跑了十几分钟」时一眼看出是谁；
3. **全量清单落盘**到 `backend/data/debug/last_test_run.txt`：**按能力域分组**，每条给状态、用时、用例名与「这条在测什么」（`data/debug/` 在 `.gitignore` 里）。`-ra` 另会把跳过/失败的原因收尾列出。

想看每条用例的实时名字加 `-v`（注意 `-q` 与 `-v` 相抵，别一起用）；只想跑一条用 `-k 关键字`，例如 `uv run python -m pytest tests -k epub -v`（须先 `cd backend`）。

**卡住、或只看到一行行 `F` 与一个数字**：先探后端，`Invoke-WebRequest http://127.0.0.1:8020/probe/health -TimeoutSec 5 -UseBasicParsing`。两种坏法要分清——**没在听**（连接被拒，用例立刻失败）与**连着却不回话**（半死的旧进程占着端口，每条 HTTP 用例都要等满超时才红）。后者用 `Get-NetTCPConnection -LocalPort 8020 -State Listen | Select-Object OwningProcess` 找到占用进程，结束掉再按第 2 节重起。HTTP 缝现在有启动探活前置：后端不应答会**当场**用一条可读消息点明，不会让你等十几分钟；单次请求上限也压到 20 秒（本地上传口正常 1 秒内返回）。

| # | 做什么 | 判据 |
|---|---|---|
| 1 | 访问 `/probe/health` | `model_key_configured` 为 `true`；启动日志里配置文件路径指向项目根目录的 `.env` |
| 2 | `/convert` 纯文本样例，三开关全关 | `type` 为 `document`，`content` 非空，`usage` 为 `null` |
| 3 | `/convert` 含文字图（如 `screenshot.png`），`ocr`+`llm` 开（需 `services/ocr/paddle-ocr`） | `meta.ocr_text` 非空，`meta.notes` 说明判为文字图片；`usage` 非 `null` |
| 3b | `/convert` 无文字图（如 `photo.png`），`ocr`+`llm` 开 | `meta.ocr_text` 为 `null`、`summary` 是图像客观描述，`meta.notes` 说明判为语义图片 |
| 4 | 同一文档：全关再跑一次，再只开 `llm` | 两次确定性 `content` 一致；开 `llm` 时才可能有非空 `summary` |
| 5 | `/convert` 扫描件 PDF，三开关全关 | `content` 为空，`meta.notes` 说明「没有文字层」 |
| 6 | 同一扫描件，`ocr`+`llm` 开 | `meta.ocr_text` 非空，`meta.notes` 出现「整页渲染已覆盖页面上的图，不再单独抽内嵌图」 |
| 7 | `/convert` wav/mp4，三开关全关 | `type` 为 `audio` / `video`，`content` 为空，`meta.audio` 有时长与采样率，`meta.degraded` 为 `false`，`usage` 为 `null` |
| 7b | 同一音频，只开 `asr`（需 `services/asr/qwen3-asr`） | `meta.asr_text` 有值，或 `meta.notes` 说明「音轨里没有说话声」；`content` 可含转写 |
| 8 | 界面点预设样例并「运行」 | 源填好、预设高亮；结果区出现本次响应（开关状态与请求一致） |

## 6. 故障处置

### 改了后端代码没生效

后端**默认开了自动重载**（`RELOAD` 默认 `true`）：改完 `backend/` 下的 `.py` 会自动重载，日志里出现 `Reloading...`，直接在这个服务上跑验收就行。别拿还在跑的旧进程当成新代码已通过。

想退回手动重启就把 `RELOAD` 设成 `false`，再重启（`cd backend` 后 `uv run python app.py`，或仓库根 `uv run python start.py`）。两种情况下的四条边界一样：

- **只监视 `.py`**：改 `.env`、`frontend/`、样例文件都不触发重载，仍要手动重启。
- **它多起一个监督进程**（父进程看文件、子进程才是服务）。`start.py` 用 `taskkill /T` 收树，Ctrl+C 仍能一次收干净；手工起的进程要按 Ctrl+C 停，别只关窗口。
- **`RELOAD` 自身改了也要重启**：它是在启动时读的。
- 本机没装 `watchfiles` 时 uvicorn 走 `StatReload`（轮询 `mtime`），照样生效，只是比事件式慢一点；装了 `watchfiles`（`uvicorn[standard]` 自带）才支持 include/exclude 过滤。

开了自动重载之后，改完后端就**直接在这个服务上跑验收**（第 5 节的 0a/0b），不要再起第二个后端——端口只有一个，第二个会因 `10048` 起不来。

### `start.py` 说端口已被占用

8020 或 5173 上已经有进程在监听，脚本不会起第二个。先确认是不是旧的服务还在跑（`Get-NetTCPConnection -LocalPort 8020 -State Listen`），要重启就先停掉它。

### 结构自查报「文件超预算」或「依赖方向不对」

结构判据在 `backend/tests/test_structure.py`；依赖方向见 [architecture.md](./architecture.md) 第 4 节。单文件行数门禁已废除（ADR-0010）：`processors/` 按「一格式族一文件」收拢，不为过门禁拆文件。加东西前先想清楚它属于哪一层，别往 `pipelines/` 里塞跨格式公共逻辑（表格公共进 `utils/excel/`，模型侧进 `extensions/`）。

### 改了前端代码，页面行为还是旧的

先确认 dev server 到底给的是新版还是旧版（浏览器里直接打开对应模块源码看一眼）→ 更新文件 mtime 触发重编译 → 仍不对再重启。改完记得硬刷新（Ctrl+Shift+R）。

### 模型增强解析报「未配置 DEEPSEEK_API_KEY」

`.env` 不在项目根目录。放到 `anyurl2text/.env`（与 `README.md` 同级），不是 `backend/.env`。

### 含文字的图报「Paddle OCR 服务不可达」

独立图片判型为文字图片后要调本机 OCR 服务（`OCR_API_URL`，默认 `http://127.0.0.1:8021/ocr-b64`，实现见 `services/ocr/paddle-ocr`）。
客户端契约见 `backend/extensions/paddle_ocr.py`：先确认服务已起且 `GET /health` 返回 `"code": "200"`；
端口不同就改 `.env` 的 `OCR_API_URL`。服务没起不会让请求 500——那一张图的模型增强失败，
原因写进 `meta.notes`，`summary` 相应缺失。

### 音频/视频报「Qwen3-ASR 服务不可达」

音频与视频的音轨要交给本机 ASR 服务（`ASR_API_URL`，默认 `http://127.0.0.1:8022/asr-b64`，实现见 `services/asr/qwen3-asr`）。
客户端契约见 `backend/extensions/qwen3_asr.py`：先确认服务已起（模型加载可能要十几秒）且 `GET /health` 返回 `"code": "200"`；
端口不同就改 `.env` 的 `ASR_API_URL`。服务没起不会让请求 500——这一类源的转写失败，原因写进 `meta.notes`，
`summary` 相应缺失，`meta.audio` 仍然有值（音轨参数是纯代码读出来的，不依赖服务）。

### 视频与 m4a/aac/wma 报「未安装 imageio-ffmpeg」

这几个容器要先抽音轨才能读，靠 `imageio-ffmpeg` 自带的静态 ffmpeg，不需要系统装 ffmpeg。该包装在 `pyproject.toml` 主依赖里，项目根执行 `uv sync` 即有；不要对本仓再单独 `pip install`。

装之前/装不上时这一类走降级（`meta.degraded` 为 `true`），mp3/wav/flac/ogg 这些 libsndfile 直接读得动的音频不受影响。
`/probe/health` 的 `ffmpeg_available` 就是这项能力的开关状态。

### 下载失败，说命中内网或保留地址

这是 SSRF 防护在工作。要串本机起的本地文件服务时，把 `ALLOW_PRIVATE_HOSTS` 临时设为 `true`；对外可访问的部署保持 `false`。

### 下载失败，说「只允许 https」

`ALLOW_HTTP_SOURCE` 被设成了 `false`。它默认是 `true`（http 与 https 都收，与既有行为一致）；
要放回 `http://` 就把它设回 `true` 并重启（改 `.env` 不触发自动重载，见第 6 节）。

### 请求返回 413

超过 `MAX_DOWNLOAD_BYTES`（默认 200 MB）。上传侧两道：

1. **声明就超大** → 一字节都不收，直接 413（`request_body_too_large`）。
2. **没报大小、或边收边发现超了** → 中间件累计收到的字节，到上限就断开（同样 413），避免框架先把整包写进系统临时目录；业务落盘还有一道同名上限。

挂到 nginx / Caddy 等反代后面时，**再设一道请求体上限**（建议 ≥ 本服务的 `MAX_DOWNLOAD_BYTES` + 约 1MB 余量）。例如 nginx：

```nginx
client_max_body_size 201m;   # 与 MAX_DOWNLOAD_BYTES=200MB 对齐，略留余量
```

反代先挡，应用再挡；只配应用、不配反代时，超大请求仍可能先打满反代自己的缓冲。

### 前端所有请求都失败

先查端口：`start.py`、后端与 `vite.config.ts` 都读项目根 `.env` 的 `ANYURL2TEXT_PORT` / `FRONTEND_PORT`。改完须重启两端；若只起了其中一端、或旧进程仍占着旧端口，症状是「前端全挂、控制台只有网络错误」。
