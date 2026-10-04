# anyurl2text

把任意 URL 或本地文件转成**尽可能保留原始信息**的文本。

同一份源有两条路：一条只靠代码抽取，稳定、快、不花钱；一条在这份结果之上叠加模型能力，慢且计费，但能读图、能还原扫描件、能按问题作答。两条路产出的正文 **逐字节一致**，所以可以直接对比。

| | 纯代码解析 | 模型增强解析 |
|---|---|---|
| 接口 | `POST /extract` | `POST /understand` |
| 调用模型 | 否 | 是 |
| 典型耗时 | 毫秒到数百毫秒 | 秒级起 |
| 费用 | 0 | 按 token 计，响应里给出折算金额 |
| 图片 | 只给尺寸、格式、EXIF 等结构信息 | 先判型：含文字的图交本机 Paddle OCR 读字，语义图给客观内容描述 |
| 扫描件 PDF | 正文为空，并在 `meta` 里声明「没有文字层」 | 逐页渲染交给视觉模型，还原出全文 |
| 文档内嵌图 | 标出位置占位 | 读出图里的文字 |
| 音频 / 视频 | 只给时长、采样率、声道等音轨参数 | 抽音轨后交本机 Qwen3-ASR 转写，文字进 `meta.asr_text` |
| 带 `query` | 不消费（会明确说明被忽略） | 针对问题作答；不带则给通用摘要 |

两个接口的入参完全相同：URL 走 `POST /{接口}` 传 JSON，本地文件走 `POST /{接口}/file` 传 multipart。字段含义见 [docs/API.md](./docs/API.md)。

另有 `POST /rag/extract/file`：给检索入库用，契约独立。PDF 抽出印刷目录则返回页列表与目录区间，否则返回交付文本。不调用模型。

## 快速开始

需要 **Python 3.9+**（代码只用到 `zoneinfo`、`ElementTree.indent` 这类 3.9 起的标准库特性；开发与验证环境是 3.13）与 **Node 18+**（Vite 5 的要求）。

一条命令起两个服务（在项目根目录执行，Ctrl+C 一起停）：

```bash
python start.py                   # 后端 http://127.0.0.1:8020/docs · 前端 http://localhost:5173
```

它用**你运行它的那个 Python 环境**起后端，所以请先在这个环境里装好后端依赖。要分开调试、或只想起其中一个，照下面两步来：

```bash
# 后端
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env        # Windows: copy ..\.env.example ..\.env
#   编辑项目根目录的 .env，填 DEEPSEEK_API_KEY
#   注意：.env 必须在项目根目录，不是 backend/
python app.py                     # http://127.0.0.1:8020/docs

# 前端（另开一个终端）
cd frontend
npm install
npm run dev                       # http://127.0.0.1:5173
```

没有 Key 也能起：纯代码解析完全不受影响，模型增强解析会返回明确原因。配置项全表见 [docs/RUNBOOK.md](./docs/RUNBOOK.md)。

界面是三栏：左栏输入（可一键填入预设样例），中栏纯代码解析，右栏模型增强解析。**两栏各有自己的「运行」按钮，点哪栏只跑哪栏**，所以可以先用纯代码那一路确认文件本身没问题，再单独跑模型那一路。

左栏的预设样例来自 `frontend/public/presets.config.js`——一个带注释的顶层配置文件，改它**不用改代码**：开发服务器下刷新页面即生效，跑的是构建产物时要重新 build（或直接改 `dist/` 里那份副本）。字段含义与校验规则写在那个文件的头部。

生成演示用的样例文件：

```bash
cd backend
python scripts/make_fixtures.py   # 输出到 backend/data/
```

## 支持哪些输入

以「纯 Python 库能否做到」划线。能做的完整实现，缺依赖的**降级为文件基础信息，且不算错误**。

| 类别 | 格式 | 处理 |
|---|---|---|
| 文档 | pdf、docx、pptx、xlsx、xlsm、xls、ods、odt、odp、epub、ofd、rtf、csv、tsv、txt、md、markdown、json、xml | 完整解析（版面顺序、表格、文本框、内嵌图位置、docx 超链接、EPUB 按 spine 拼章节、OFD 按文档根声明的页序取正文） |
| 网页 | html、htm | 标题 + 摘要 + 正文 + 去重链接 |
| 图片 | png、jpg、jpeg、gif、bmp、tif、tiff、webp、heic、heif、ico、svg | 结构信息（尺寸 / 格式 / 色彩模式 / 帧数 / EXIF） |
| 音频 | mp3、wav、aac、flac、ogg、wma、m4a | 结构信息（时长 / 采样率 / 声道 / 音轨格式） |
| 视频 | mp4、avi、mkv、mov、wmv、flv、webm、mpeg、mpg | 同上，报的是它音轨的参数 |
| 压缩包 | zip、tar、gz、tgz、bz2、tbz2、xz、txz、7z、rar | 条目清单（**不解压内容**），识别密码保护 |
| 安装包 | apk、ipa、exe | 文件头 + 容器条目 + APK/IPA 结构指纹 |
| **降级** | 老版 Office（doc/ppt/wps） | 只返回文件类型与大小，`meta.degraded` 与 `degrade_reason` 说明缺的是什么；Office 锁文件（`~$` 前缀）与 `.DS_Store` / `Thumbs.db` 这类噪音文件同样判为「不是文档本体」（`degrade_code="temporary_lock_file"`） |

音频与视频的语音转写要本机另起一个 ASR 服务（`Individual/qwen3-asr`，默认 `http://127.0.0.1:8022`）；
视频与 m4a/aac/wma 这些容器还要先抽音轨，用 `imageio-ffmpeg` 自带的 ffmpeg，**不要求系统装 ffmpeg**——
装不上时这一类退回降级，其余格式不受影响。转写出的文字进 `meta.asr_text`，不进 `content`（见下一条取舍）。

## 几个关键取舍

- **摘要不与正文混放**：摘要进 `summary`，`content` 任何情况下都不被覆盖——所以两条路的正文能逐字节对比。
- **模型读出的文字也不进正文**：图像识别的文字进 `meta.ocr_text`，语音转写的文字进 `meta.asr_text`。它们都是模型产出，混进 `content` 就没法比了。
- **响应形状恒定**：7 个固定字段，`error` 恒存在（成功为 `null`），调用方不必判两种形状；失败时它是 `{code, message, param}`，`code` 是稳定机器名（码表见 `backend/configs/errors.py`），文案改措辞也不影响按 `code` 分组。
- **成本按真实 token 记账**：缓存命中 / 未命中 / 输出三档分别计价，带峰谷时段与单价生效日；纯代码那一路 `usage` 为 `null`（没调模型就没有用量，给 0 是噪音）。
- **下载层四条基线不可协商**：SSRF 防护、流式写盘时的大小上限、TLS 不降级、请求体在解析前早拒。
- **降级是能力边界而非故障**：缺依赖的类型返回「这是什么、多大」，HTTP 仍是 200。
- **扫描件逐页渲染补全**：带页数上限，空白页在调用模型之前就被判掉。

每条仍有效的决策理由与被否掉的备选方案见 [docs/adr.md](./docs/adr.md)；被取代或废弃的选定见 [docs/HISTORY.md](./docs/HISTORY.md)。

## 文档

| 文档 | 看它解决什么问题 |
|---|---|
| [docs/architecture.md](./docs/architecture.md) | 项目是什么、明确不做什么、模块落在哪、一次请求如何走完 |
| [docs/API.md](./docs/API.md) | 两个接口怎么调、响应每个字段什么意思、失败长什么样 |
| [docs/adr.md](./docs/adr.md) | 为什么这样设计，以及为什么不那样（只记仍有效的） |
| [docs/HISTORY.md](./docs/HISTORY.md) | 被取代或废弃的选定（问「为什么曾经那样」时读） |
| [docs/RUNBOOK.md](./docs/RUNBOOK.md) | 配置项全表、样例生成、验收清单、故障处置 |
| [docs/EXTENDING.md](./docs/EXTENDING.md) | 要加格式 / 加模型调用 / 改契约时该动哪里 |
| [docs/PIPELINES.md](./docs/PIPELINES.md) | 每种输入类型各走了哪几步、判定与上限在哪、产出落到哪个字段 |
| [CONTEXT.md](./CONTEXT.md) | 术语表：本项目的词有确切含义，改代码前先对齐 |

## 已知边界

- 老版 Office 只返回文件基础信息（见上表）。音视频要本机 ASR 服务起着，且视频与容器音频还要 ffmpeg（`imageio-ffmpeg`）能抽音轨，缺任一项都退回降级并在 `meta.notes` 里说明。
- 压缩包只列条目，不解压、不递归。
- 扫描件页面渲染默认最多 10 页，超出部分会在 `meta.notes` 里说明。
- `content` 默认上限 20 万字符，送模型的输入上限 12 万字符；两者都会在 `meta.truncated` 与 `meta.notes` 里声明。
- 送转写的音轨默认上限 10 分钟（`ASR_MAX_AUDIO_SECONDS`），超过就跳过并说明；ASR 服务自身的 `max_new_tokens` 决定单次转写的实际长度。
- 模型调用用现行模型名 `deepseek-flash`（旧的 `deepseek-v4-flash` / `vision-exp` 已下线，仅作兼容路由）。
