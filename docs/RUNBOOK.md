# 运行 · 配置 · 验收

> **用途**：首次启动见 [README 的快速开始](../README.md#快速开始)；这份讲跑起来之后的配置项、样例生成、验收清单与故障处置。
> **关联**：[architecture.md](./architecture.md)（模块与落点） · [API.md](./API.md)（字段契约）
> **基线**：2026-09-12

---

## 1. 配置项

全部配置从**项目根目录**的 `.env` 读取（不是 `backend/`）。缺文件不报错，只是所有项取默认值——启动日志第二行会打出它实际读了哪个路径，认准那一行。

### 必填

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | 不填则模型增强解析失败并说明原因，纯代码解析不受影响 |

### 常用

| 变量 | 默认 | 作用 | 什么时候改 |
|---|---|---|---|
| `DEEPSEEK_MODEL` | `deepseek-flash` | 模型名 | 换模型时。用一个模型同时处理文本与图片 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 模型端点 | 换供应商 |
| `THINKING_ENABLED` | `false` | 思考模式 | 想让摘要质量更高时打开。**默认关闭是刻意的**：思维链按输出侧计费，输出价约为输入未命中价的 4 倍 |
| `THINKING_EFFORT` | `high` | 思考模式的推理力度（随请求发成 `reasoning_effort`） | 只在 `THINKING_ENABLED` 打开时才发出去：想更快更省就调低，想更深就调高 |
| `ANYURL2TEXT_PORT` | `8020` | 后端端口 | 改这里要同步改 `frontend/vite.config.ts` 的代理目标。本机连号：本服务 8020、OCR 8021、ASR 8022 |
| `ALLOW_PRIVATE_HOSTS` | `false` | 允许下载内网 / 回环地址 | 仅在本机串本地文件服务调试时打开；对外可访问的部署保持 `false` |
| `ALLOW_HTTP_SOURCE` | `true` | 是否接受 `http://` 源。`false` 时只收 https，`http://` 在发起连接之前就被 422 拒掉（`download_http_not_allowed`）。**默认 `true` 就是既有行为** | 想让传输一律走加密时设 `false`；本机串内网的 HTTP 服务保持 `true` |
| `OCR_API_URL` | `http://127.0.0.1:8021/ocr-b64` | 本机 Paddle OCR 服务地址（独立图片判型为文字图片时由它读字） | 换了 OCR 服务端口，或部署到别处时 |
| `ASR_API_URL` | `http://127.0.0.1:8022/asr-b64` | 本机 Qwen3-ASR 服务地址（音频与视频的音轨由它转写） | 换了 ASR 服务端口，或部署到别处时 |
| `ASR_MAX_AUDIO_SECONDS` | 600 | 送转写的音轨时长上限；超过就跳过并写进 `meta.notes` | 想转写更长录音时。**先把 ASR 服务的 `max_new_tokens` 调大**，否则上游会把尾部静默丢掉 |

### 成本相关

想控花费时按这个顺序调。

| 变量 | 默认 | 影响 |
|---|---|---|
| `MAX_MODEL_INPUT_CHARS` | 120000 | 送进模型的文本上限，直接决定摘要那一次调用的输入 token |
| `MAX_EMBEDDED_IMAGES` | 3 | 每份文档最多 OCR 几张**不同的**内嵌图（同一张图被引多次只算一张） |
| `RENDERED_PAGE_MAX` | 10 | 扫描件最多渲染几页 |
| `MAX_CONTENT_CHARS` | 200000 | `content` 的字符上限（也会影响送模型的量） |
| `LLM_MAX_TOKENS` | 8192 | 单次模型输出上限。长文档摘要看着被截断就提高它 |
| `DEBUG_ARTIFACTS` | `false` | 打开后把中间产物留档到 `backend/data/debug/`，用于排查 |

### 其余

| 变量 | 默认 | 作用 |
|---|---|---|
| `ANYURL2TEXT_HOST` | `127.0.0.1` | 监听地址；要让局域网访问改成 `0.0.0.0` |
| `RELOAD` | `true` | 自动重载：改 `backend/` 下的 `.py` 立即生效。`python app.py` 与 `start.py` 起的后端都认这个开关 | 不想多一个监督进程就设 `false`（那时改完要手动重启）。注意它只监视 `.py`：改 `.env` 仍要重启 |
| `REQUEST_BUDGET_SEC` | 120 | 单请求总时间预算 |
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
| `MAX_REPEATED_SLOTS` | 100 | `backend/configs/limits.py` | odf 的 `number-*-repeated` 重复次数上限 |
| `MAX_TEXT_BYTES` | 20971520（20 MB） | `backend/configs/limits.py` | 文本类文件的体积上限，超过则只读头部，避免超级大的日志整份进内存 |
| `MAX_LONG_EDGE` | 1600 | `backend/configs/limits.py` | 送模型前图片长边缩到多少像素 |
| `PREVIEW_FIRST_PAGES` | 15 | `backend/configs/limits.py` | RAG 预览取前几页 |
| `TEXT_LAYER_MIN_CHARS` | 30 | `backend/pipelines/processors/pdf.py` | **阈值不是上界**：判定「这份 PDF 没有文字层」的字符数下限，低于它走页面渲染 |

## 2. 启动服务

一条命令把后端与前端都拉起来（两个服务仍是各自独立的进程，只是由一个脚本拉起与收尾）：

```bash
python start.py          # 项目根目录执行；无参数
```

它按顺序做这些事：打印用的是哪个解释器 → 检查后端依赖与 `frontend/node_modules` → 检查 8020 / 5173 是否已被占用（占用就直接说明「可能已经在跑」并退出，不会起第二个）→ 起后端（`python app.py`，cwd=`backend/`）与前端（`npm run dev`，cwd=`frontend/`）→ 等到两个都真的能应答 → 打开浏览器 → 按 Ctrl+C 把两个进程一起收掉（Windows 上用 `taskkill /T`，否则 npm 拉起的 node 会变成占着端口的孤儿进程）。

不想自动开浏览器就把文件顶部的 `OPEN_BROWSER` 改成 `False`。只想起后端、或要分开调试，仍照 [README 的快速开始](../README.md#快速开始) 分别启动。

## 3. 生成样例

```bash
cd backend
python scripts/make_fixtures.py
```

输出到 `backend/data/`，与随仓库提供的文本类样例同级：`plain.txt`、`table.csv`、`sample.json`、`feed.xml`、`page.html`、`sample.rtf`、`notes.md` 这 7 个是**仓库自带的手写样例**，脚本既不生成也不覆盖它们（`notes.md` 还是 `/extract` 验收用例的必需样例）。需要后端依赖已安装（脚本用到 python-docx / python-pptx / openpyxl / pymupdf / pillow）。

生成的东西：带表格与内嵌图的 docx、含空单元格的 xlsx、六页 pptx、**含合并单元格的 docx / xlsx / pptx（`merged.*`，横向与纵向各一处）**、**同一张图被引 3 次的 docx / pptx / pdf（`repeated.*`，另有一张只引 1 次）**、**带合并表格与两级列表的 `structured.odt`**、**含超链接的 `hyperlink.docx`（同一地址写两次 + 一条没有文字的链接，验证链接去重与截断）**、十页有文字层的 PDF、**三页无文字层的扫描件**、odt、odp、压缩包、APK 结构样本、老版 `.doc` 头样本、16kHz 单声道 wav、由它封装的 mp4。

`.apk` 与 `.doc` 只是**结构样本**，用来验证类型识别与降级分支，不是真的安装包与 Word 文档。
音视频样例是一段**正弦音、没有说话声**，只用于验证结构信息与「抽音轨 → 送本机 ASR」这条链；要验收转写质量请自备一段真实录音。
mp4 那一份需要 `imageio-ffmpeg`（可选依赖），没装时脚本跳过它并说明原因。

## 4. 界面怎么用

打开前端后是三栏：

- **左栏**：输入。切「远程 URL / 本地文件」，可点预设样例一键填入 URL（同时复制到剪贴板）。

预设样例由 `frontend/public/presets.config.js` 配置：一个带注释的顶层文件，页面启动时按 URL 取回来，
所以**改它不用改前端代码**：dev 下刷新页面即生效，跑构建产物时要重新 build（或直接改 `dist/` 里那份副本）。
条目字段是 `kind`（标题）/ `desc`（描述）/ `url`（链接），顺序即界面顺序；字段缺失、类型不对或 url 不是 http(s)
的条目会被跳过，界面在列表下方列出原因。文件取不回来（被删、语法错）时同样出提示，不会白屏。
- **中栏**：纯代码解析。右上角「运行」只跑这一条。
- **右栏**：模型增强解析。有自己的「运行」按钮、摘要与用量区块。

两条管线**互不牵动**：点哪栏的运行就只跑哪栏，另一栏保持原状。所以可以先用纯代码那一路确认文件本身没问题，再单独跑模型那一路。

## 5. 验收清单

改完代码后逐条走一遍，每条都有可核查的判据。前两条是命令（可重复执行、退出码非 0 即失败），后面是人工判断的部分。

| # | 做什么 | 判据 |
|---|---|---|
| 0a | 后端在跑时 `cd backend && python -m pytest tests -q` | 自检缝全过：五条结构判据（`tests/test_structure.py`）+ 各格式抽取 + 判型 + 通用件（不联网下载、不调模型、不花钱）。**这一条里有 26 条 HTTP 用例（`tests/test_extract_contract.py`），所以后端必须在跑** |
| 0b | 起服务后 `cd backend && python -m pytest tests -m model -q` | 贵缝全过：两条路 `content` 逐字节一致、模型增强产出、独立图片判型分流、音视频转写（真调模型，且本机 OCR（8021）/ ASR（8022）在跑） |

**别为了跑测试另起一个后端**：端口只有一个，第二个起不来（`10048`）或者两个互相抢端口。改完后端代码时，开着自动重载的那个服务会自己加载新代码（见第 6 节），直接在它上面跑 0a/0b 就行；服务没开就先按第 2 节起一个，再跑。

pytest 的缓存是关掉的（`backend/pytest.ini` 里的 `-p no:cacheprovider`），所以跑完不会在 `backend/` 下留 `.pytest_cache` 或 `pytest-cache-files-*`。代价是没有 `--lf` / `--ff`——本套件跑一次只要几秒，直接全跑。

两条 0 号命令之外都是人工核查。样例缺失时新套件**跳过**该条而不是失败——样例由 `python scripts/make_fixtures.py` 生成（见第 3 节）。

| # | 做什么 | 判据 |
|---|---|---|
| 1 | 访问 `/probe/health` | `model_key_configured` 为 `true`；启动日志里配置文件路径指向项目根目录的 `.env` |
| 2 | `/extract` 一个纯文本样例 | `type` 为 `document`，`content` 非空，`usage` 为 `null` |
| 3 | `/understand` 一张含文字的图（如 `screenshot.png`，需先起 Paddle OCR 服务） | `meta.ocr_text` 非空，`meta.notes` 里说明判为文字图片；`usage` 非 `null` 且 `cost_cny` 大于 0 |
| 3b | `/understand` 一张没有文字的图（如 `photo.png`） | `meta.ocr_text` 为 `null`、`summary` 是图像的客观描述，`meta.notes` 里说明判为语义图片 |
| 4 | 同一个文档分别调两个接口 | 两次的 `content` **逐字节相同**（字符数一致是最低要求） |
| 5 | `/extract` 扫描件 PDF | `content` 为空，`meta.notes` 里说明「没有文字层」 |
| 6 | `/understand` 同一个扫描件 | `meta.ocr_text` 非空，`meta.notes` 里出现「整页渲染已覆盖页面上的图，不再单独抽内嵌图」 |
| 7 | `/extract` 一段 wav 或 mp4 | `type` 为 `audio` / `video`，`content` 为空，`meta.audio` 有时长与采样率，`meta.degraded` 为 `false`，`usage` 为 `null` |
| 7b | `/understand` 同一份音频（需先起 Qwen3-ASR 服务） | `meta.asr_text` 有值，或 `meta.notes` 说明「音轨里没有说话声」；`timings.model_ms` 大于 0；`content` 与第 7 条的逐字节相同 |
| 8 | 界面上点一个预设样例 | URL 被填入输入框、该预设高亮；点中栏「运行」后右栏仍显示未运行 |

第 4 条是本项目最核心的不变量：它一旦不成立，「两条路可对比」这个前提就没了。

## 6. 故障处置

### 改了后端代码没生效

后端**默认开了自动重载**（`RELOAD` 默认 `true`）：改完 `backend/` 下的 `.py` 会自动重载，日志里出现 `Reloading...`，直接在这个服务上跑验收就行。别拿还在跑的旧进程当成新代码已通过。

想退回手动重启就把 `RELOAD` 设成 `false`，再重启 `python app.py` 或 `python start.py`。两种情况下的四条边界一样：

- **只监视 `.py`**：改 `.env`、`frontend/`、样例文件都不触发重载，仍要手动重启。
- **它多起一个监督进程**（父进程看文件、子进程才是服务）。`start.py` 用 `taskkill /T` 收树，Ctrl+C 仍能一次收干净；手工起的进程要按 Ctrl+C 停，别只关窗口。
- **`RELOAD` 自身改了也要重启**：它是在启动时读的。
- 本机没装 `watchfiles` 时 uvicorn 走 `StatReload`（轮询 `mtime`），照样生效，只是比事件式慢一点；装了 `watchfiles`（`uvicorn[standard]` 自带）才支持 include/exclude 过滤。

开了自动重载之后，改完后端就**直接在这个服务上跑验收**（第 5 节的 0a/0b），不要再起第二个后端——端口只有一个，第二个会因 `10048` 起不来。

### `start.py` 说端口已被占用

8020 或 5173 上已经有进程在监听，脚本不会起第二个。先确认是不是旧的服务还在跑（`Get-NetTCPConnection -LocalPort 8020 -State Listen`），要重启就先停掉它。

### 结构自查报「文件超预算」或「依赖方向不对」

结构判据在 `backend/tests/test_structure.py`；依赖方向与单文件预算（总行数 ≤ 250、代码行 ≤ 150）的理由见 [architecture.md](./architecture.md) 第 4 节，单文件预算**不适用于** `backend/scripts/`（一次性生成脚本，`make_fixtures.py` 近 500 行）与 `backend/tests/`。加东西前先想清楚它属于哪一层，别往 `pipelines/` 里塞格式实现。

### 改了前端代码，页面行为还是旧的

看 `self-react` 的 `references/troubleshooting.md`：那里的流程是「先确认 dev server 到底给的是新版还是旧版（直接取模块源码看一眼）→ 更新文件 mtime 触发重编译 → 才考虑重启」，比重启更快定位。改完记得硬刷新（Ctrl+Shift+R）。

### 模型增强解析报「未配置 DEEPSEEK_API_KEY」

`.env` 不在项目根目录。放到 `anyurl2text/.env`（与 `README.md` 同级），不是 `backend/.env`。

### 含文字的图报「Paddle OCR 服务不可达」

独立图片判型为文字图片后要调本机 OCR 服务（`OCR_API_URL`，默认 `http://127.0.0.1:8021/ocr-b64`）。
先确认它起着：`Individual/paddle-ocr` 下 `python main.py`，`GET /health` 应返回 `"code": "200"`；
端口不同就改 `.env` 的 `OCR_API_URL`。服务没起不会让请求 500——那一张图的模型增强失败，
原因写进 `meta.notes`，`summary` 相应缺失。

### 音频/视频报「Qwen3-ASR 服务不可达」

音频与视频的音轨要交给本机 ASR 服务（`ASR_API_URL`，默认 `http://127.0.0.1:8022/asr-b64`）。
先确认它起着：`Individual/qwen3-asr` 下 `python main.py`（模型加载要十几秒），`GET /health` 应返回 `"code": "200"`；
端口不同就改 `.env` 的 `ASR_API_URL`。服务没起不会让请求 500——这一类源的转写失败，原因写进 `meta.notes`，
`summary` 相应缺失，`meta.audio` 仍然有值（音轨参数是纯代码读出来的，不依赖服务）。

### 视频与 m4a/aac/wma 报「未安装 imageio-ffmpeg」

这几个容器要先抽音轨才能读，靠 `imageio-ffmpeg` 自带的静态 ffmpeg，不需要系统装 ffmpeg：

```bash
pip install imageio-ffmpeg
```

装之前/装不上时这一类走降级（`meta.degraded` 为 `true`），mp3/wav/flac/ogg 这些 libsndfile 直接读得动的音频不受影响。
`/probe/health` 的 `ffmpeg_available` 就是这项能力的开关状态。

### 下载失败，说命中内网或保留地址

这是 SSRF 防护在工作。要串本机起的本地文件服务时，把 `ALLOW_PRIVATE_HOSTS` 临时设为 `true`；对外可访问的部署保持 `false`。

### 下载失败，说「只允许 https」

`ALLOW_HTTP_SOURCE` 被设成了 `false`。它默认是 `true`（http 与 https 都收，与既有行为一致）；
要放回 `http://` 就把它设回 `true` 并重启（改 `.env` 不触发自动重载，见第 6 节）。

### 请求返回 413

超过 `MAX_DOWNLOAD_BYTES`（默认 200 MB）。上传的超限是在读请求体之前就拒掉的，不会先把文件缓冲到磁盘。

### 前端所有请求都失败

先查端口：`frontend/vite.config.ts` 里代理的目标端口必须与后端 `.env` 的 `ANYURL2TEXT_PORT` 一致。这两处是手工同步的，改一边忘一边的症状就是「前端全挂、控制台只有网络错误」。
