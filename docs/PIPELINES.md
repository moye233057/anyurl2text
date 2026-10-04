# 各类型的处理链路

> **用途**：按输入类型查这条源走了哪几步、关键判定与上限在哪、产出落到哪个字段。调试某一种格式先读这份。
> **关联**：[architecture.md](./architecture.md)（模块落点） · [adr.md](./adr.md)（为什么这样拆） · [API.md](./API.md)（字段契约） · [CONTEXT.md](../CONTEXT.md)（术语表）
> **基线**：2026-10-02
> **代码基准**：`backend/pipelines/`（steps 六步 + processors 一个格式一个文件）

---

## 1. 总链路

一份源从入口到响应只有一条主干；`extract` 与 `understand` 的差别只在主干上多走两小步。

```text
POST /extract | /extract/file | /understand | /understand/file
  │
  ▼ routers/convert/router.py            收参；把 PipelineResult.status 写进 HTTP 状态码
  │
  ▼ pipelines/extract.py | pipelines/understand.py   只做「按序号调步骤」
  │
  ├─ 步骤 1+2+3   step3_prepare.prepare_from_url() / prepare_from_upload()
  │                 └ 内部依次走 step1_download（取字节）→ step2_detect（判型）→ 补后缀、组装 PreparedSource
  │
  ├─ 步骤 4       step4_extract.run_extraction(prepare_assets)
  │                 └ dispatch() → processors/<格式>.py 的 extract_*()
  │                    产出 content（交付文本，唯一来源）+ assets（素材清单）
  │
  ├─ 步骤 4′      step4_extract.enhance()        ← 只有 understand 走
  │                 └ 按格式把素材交模型读：独立图片判型、扫描页逐页、内嵌图批量、音轨转写
  │
  ├─ 步骤 5       step5_enhance.summarize()      ← 只有 understand 走
  │                 └ 交付文本 + 识别文本 → summarize.txt 或 answer.txt
  │
  └─ 步骤 6       step6_assemble.build_response() → ConvertResponse
                    content 按上限截断并声明；meta / usage / timings / error 一次填齐
  │
  ▼ finally       step3_prepare.cleanup()   整树删除本次请求的临时目录（DEBUG_ARTIFACTS 打开时先留档）
```

两条路的差异只有两处：

| | `extract` | `understand` |
|---|---|---|
| 步骤 4 的 `prepare_assets` | `False` | `True` |
| 步骤 4′ 与步骤 5 | 不走 | 走 |
| `content` | 完全相同（这是设计前提） | 完全相同 |
| 模型产出去处 | 无 | `summary` / `meta.ocr_text` / `meta.asr_text` / `meta.notes` |

失败不抛给调用方：入口阶段失败走 `step6_assemble.error_response()`（`type="error"`，带语义状态码），步骤 4 之后的失败转成 `meta.notes` 里的说明，HTTP 仍是 200。

RAG 抽取不走上表。`POST /rag/extract/file` 由 `pipelines/rag_extract.py` 编排：准备与 extract 相同；PDF 先 pymupdf4llm 分页，只在**前 `PREVIEW_FIRST_PAGES`（15 页）**里找印刷目录，抽出则直接返回；否则复用步骤 4 的 `content` 作为 `text`。契约见 [API.md §11](./API.md)。

## 2. 通用模块

任何类型都经过这五段。各类型的差别只在「步骤 4 的处理器」以及「步骤 4′ 拿什么素材给模型」。

### 2.1 取字节（`step1_download.py` / `step3_prepare.py`）

两个入口只有取字节的方式不同，之后完全合流。

| 入口 | 关键步骤 |
|---|---|
| URL | 去空格并把空格换成 `%20` → 校验协议（只收 http/https）与主机 → 手工逐跳跟随重定向（每跳重新校验协议与地址）→ 流式写盘 |
| 上传 | 分块写进 `upload.bin`，边写边累计字节，与下载共用同一个大小上限 |

安全与边界（不可协商）：DNS 返回的每个地址都判定，命中私网 / 回环 / 链路本地 / 保留 / 组播一律 422；字节数在写入过程中累计，超 `MAX_DOWNLOAD_BYTES`（默认 200MB）立刻中断并删半成品（413）；超时按剩余预算递减（504），不出现「连接与读取各自用满」；TLS 校验恒开，证书问题直接失败。

文件名取 `Content-Disposition` 的 `filename=`，取不到退回 URL 路径末段。

### 2.2 判型（`step2_detect.py`）

固定顺序 **文件头 → 文件名扩展名 → Content-Type**，越靠前越可靠。文件头最可靠但只覆盖容器格式；认不出时先信文件名（URL 路径）里的后缀——CDN 常给 `application/octet-stream` 这种没信息量的类型，路径后缀通常更可信；两者都没有才用 Content-Type 兜底。容器头只能定到「容器」，还要往里看一层：

| 文件头 | 内部依据 | 落到的格式 |
|---|---|---|
| OLE（`D0CF11E0`） | 扩展名或 Content-Type | doc / xls / ppt / wps，都认不出才是 `ole` |
| ZIP（`PK\x03\x04`） | 中央目录条目指纹 | `word/document.xml`→docx；`xl/workbook.xml`→xlsx（另有 `xl/vbaProject.bin` 则 xlsm）；`ppt/presentation.xml`→pptx；`AndroidManifest.xml`→apk |
| ZIP | 根级 `mimetype` 条目 | odt / ods / odp |
| ZIP | `Payload/*.app/Info.plist` | ipa |
| ZIP | 以上都不命中或容器读不出 | 维持 `zip`（加密包与坏包本来就该走压缩包路径） |
| `RIFF` | 第 8-12 字节子类型 | webp / wav / avi |
| `ftyp` | major brand | heic / m4a / mp4 |
| 文本类（`<` 起头） | 声明与根元素 | xml / svg / html |

认不出扩展名返回 `("", Detector.UNKNOWN)`，不做猜测。得到的 `ext` 决定两件事：响应的 `type`（`modality_from_extension`）与步骤 4 走哪个处理器。

识别结果会被补成临时文件的真实后缀（`_align_extension`）——部分解析库按后缀挑引擎（openpyxl 只认 `.xlsx/.xlsm`，pyexcel-io 按后缀选读取器），不补则识别对了也读不出来。

### 2.3 抽取（`step4_extract.dispatch()` + `processors/`）

`dispatch()` 是一张扁平分派表，扩展名命中哪一行就走哪个 `extract_*()`：

| 分组 | 扩展名 | 处理器 |
|---|---|---|
| 文档 | pdf / docx / pptx | `pdf.py` / `docx.py` / `pptx.py` |
| 文档 | xlsx / xlsm / xls / ods / csv | `excel.py` |
| 文档 | odt / odp | `odf.py` |
| 文档 | txt / md / markdown / json / xml / rtf | `plaintext.py`（rtf 的解析器在 `rtf.py`） |
| 网页 | html / htm | `html.py` |
| 图片 | png / jpg / jpeg / gif / bmp / tif / tiff / webp / heic / heif / ico / svg | `image.py` |
| 音频与视频 | mp3 / wav / aac / flac / ogg / wma / m4a / mp4 / avi / mkv / mov / wmv / flv / webm / mpeg / mpg | `media.py` |
| 压缩包 | zip / tar / gz / tgz / bz2 / tbz2 / xz / txz / 7z / rar | `archive.py` |
| 安装包 | apk / ipa / exe | `install_package.py` |
| 降级 | 空 ext、doc / ppt / wps、其余未覆盖扩展名 | `extract_file_info()`（同文件内） |

所有 `extract_*` 的共同约定：

- 返回 `ExtractResult`：`content`（交付文本）、`engines`（用到的解析库）、`notes`（过程说明）、`assets`（素材清单）、计数与元信息字段、降级标记。
- 不调模型，也不感知调用方是哪个接口。`content` 只有这一份实现（ADR 0002/0007）。
- `ExtractContext.prepare_assets` 是**能力开关**，不是调用方身份：为真时才落盘可供模型读的素材（内嵌图、扫描页、音轨），为假时省掉渲染与转码开销。开关不影响 `content`。
- 自己吞异常：打开或解析失败时返回 `degraded=True` 的结果并写 `degrade_reason`，不让整条请求 500。

降级产出（`extract_file_info()`）：`content=""`、`engines=["基础文件信息"]`、`degraded=True`、`degrade_reason` 说明缺的是「能力边界」还是「故障」，文件头十六进制预览进 `notes`。它是正常结果，HTTP 仍是 200。

### 2.4 模型增强与摘要（`step4_extract.enhance()` / `step5_enhance.py` / `reading.py`）

`enhance()` 按格式分三条：

| 分支 | 条件 | 实现 |
|---|---|---|
| 独立图片 | 源本身是图片且没有素材清单 | `image.enhance_image()`：判型后分流到描述或本机 OCR |
| 音频与视频 | 源是音视频 | `media.enhance_media()`：拿步骤 4 抽好的音轨送本机 ASR |
| 其余 | 有素材清单的文档与网页 | `reading.enhance_assets()`：`kind="page"` 逐页识别，`kind="image"` 批量识读 |

读素材的共用实现在 `processors/reading.py`：

- 页面图：先做**确定性空白页预检**（缩到 64x64 灰度看亮度极差，≤8 判空白，直接跳过、不花模型调用）→ 整页识别 → 结果短于 `RENDERED_PAGE_RETRY_MIN_CHARS`（20 字）时切上下两半各识别一次，只有合计更长才采用；空白页与重试页数写进 `notes`。
- 内嵌图：一次请求带上全部图（受 `MAX_EMBEDDED_IMAGES` 限制），要求模型按 `index` 返回 JSON，**index 对不上的结果丢弃**，再按素材顺序拼回去。
- 送模型的图片统一走 `models/images.py` 的 `encode_image()`：长边超 1600 先等比缩小，统一转 JPEG 后编成 data URL。从不把 URL 交给服务端代下载。

模型出口（`models/`，一个模型一个文件）：

| 出口 | 何时用 | 记账 |
|---|---|---|
| `deepseek.py`（视觉 + 文本，`deepseek-flash`） | 判型、页面识别、内嵌图识读、摘要、按 query 回答 | 记账（`usage`） |
| `paddle_ocr.py`（本机 `POST /ocr-b64`） | 独立图片判为文字图片后读字 | 不记账 |
| `qwen3_asr.py`（本机 `POST /asr-b64`） | 音轨转写 | 不记账 |

`deepseek.chat()` 统一负责：显式给 `max_tokens`、默认关思考模式、空内容与异常按 `LLM_MAX_RETRIES` 重试、每次调用记进账本。提示词模板都在 `models/prompts/`：`classify_image.txt`、`read_scanned_page.txt`、`read_embedded_images.txt`、`summarize.txt`、`answer.txt`。

步骤 5 摘要（`step5_enhance.summarize()`）：

1. 没有 query 且已有图像描述 → 直接复用该描述，省掉一次纯文本调用。
2. 否则把交付文本、识别文本、转写文本按序拼成模型输入；带 query 时**必须**把图像描述也带上（否则「读懂了图却因为没文本答不出来」）。
3. 拼出的文本超过 `MAX_MODEL_INPUT_CHARS`（12 万字符）就截断，并由调用方在 `notes` 里声明。
4. 带 query 用 `answer.txt`，不带用 `summarize.txt`；思考模式按 `THINKING_ENABLED`（默认关）。

`understand` 只在有活可干时才进模型阶段（`_has_model_work()`）：扩展名不是 svg，且满足「交付文本非空 / 有素材 / 源本身是图片或音视频」之一。否则写一条 `notes` 说明跳过。模型阶段抛 `LlmError` 时只把原因写进 `notes`，请求仍成功。

### 2.5 组装（`step6_assemble.py`）

- `content` 超 `MAX_CONTENT_CHARS`（20 万字符）按上限截断，`meta.truncated=true` 并在 `notes` 里声明，不静默截断。
- `type` 由 `ext` 反推；`error` 非空时改判 `error`。
- `meta` 一次填齐：源信息、识别方式、降级标记、页数 / 图片数 / 条目数、`image` / `audio`、`ocr_text` / `asr_text`、引擎、`notes`。
- `timings` 分四段：`download_ms`、`parse_ms`、`model_ms`、`total_ms`。
- `PipelineResult` 把响应体与 HTTP 状态码一起返回——错误语义在抛出的地方最清楚，不让路由去猜。

## 3. 各类型的处理链路

先看速查表，再看下面各类型的细节。

| 类型 | `content` 是什么 | `extract` 路线 | `understand` 额外做什么 |
|---|---|---|---|
| pdf | 逐页文本块（含页标记） | 文字层抽取；无文字层时渲染页面为素材 | 扫描页逐页识别 / 内嵌图批量识读 → `ocr_text` |
| docx | 段落 + 制表符表格 + 文本框节 | `iter_inner_content()` 按块级顺序 | 内嵌图批量识读 → `ocr_text` |
| pptx | 逐页文本 + 表格 | 逐页递归展开形状 | 页内图片批量识读 → `ocr_text` |
| 表格族 | 逐表制表符文本 | 各格式原生库读单元格 | 无素材，直接进摘要 |
| odt / odp | 段落 + 标题 + 表格（制表符）+ 嵌套列表 | 按结构递归读 ZIP 内 `content.xml` | 无素材，直接进摘要 |
| 纯文本族 | 文本本身（json/xml 美化） | 解码直读；rtf 走控制字解析 | 无素材，直接进摘要 |
| html / htm | 标题 + 摘要 + 正文 + 链接 | BeautifulSoup 剥标签取文本 | 无素材，直接进摘要 |
| 图片 | 空串 | 只给尺寸 / 格式 / EXIF | 先判型：语义图给描述，文字图交本机 OCR → `ocr_text` |
| 音频 / 视频 | 空串 | 报音轨参数（时长 / 采样率 / 声道） | 抽音轨送本机 ASR → `asr_text` |
| 压缩包 | 条目清单 | 只读目录，不解压 | 无素材，直接进摘要 |
| 安装包 | 文件头 + 指纹行 + 条目清单 | 容器条目 + 结构特征 | 无素材，直接进摘要 |
| 降级 | 空串 | 只报「这是什么、多大」 | 无内容可加工时跳过模型 |

### 3.1 PDF（`pdf.py`）

`extract` 关键步骤：

1. `pymupdf.open()`；打不开返回降级。`needs_pass` 且空密码认证失败 → 降级「PDF 受密码保护」。
2. 逐页 `get_text("blocks")`，丢掉空块，按 **(y, x)** 排序后拼成一页；每页前加 `=== 第N页 ===`，页间空行。
3. 累加 `get_images(full=True)` 得到图片总数（进 `meta.images`）。
4. 文字层判定：全部页文本合计少于 `TEXT_LAYER_MIN_CHARS`（30 字）即视为「没有文字层（疑似扫描件）」。
5. `prepare_assets=False`（extract 路）：只加一条说明 `notes`，不做任何渲染。
6. `prepare_assets=True`（understand 路）分两种互斥情况：
   - **扫描件**（无文字层且 `RENDERED_PAGE_OCR_ENABLED`）：渲染前 `min(页数, RENDERED_PAGE_MAX)` 页为 PNG（`RENDERED_PAGE_DPI`，默认 150），`pages_used` 记为已渲染页数。**不再抽内嵌图**——整页渲染已覆盖页面上的图，两条都走会让同一页文字被识别两遍、付两份钱。
   - **有文字层**：按 xref 去重后取前 `MAX_EMBEDDED_IMAGES` 张**不同的**内嵌图（xref → PNG，`images_used` 记数）——同一个图像对象出现在 N 页上只落一次盘。

`understand`：走 `reading.enhance_assets()`，页面图逐页识别（含空白页预检与切半重试）、内嵌图一次性批量识读，两者按素材顺序拼进 `meta.ocr_text`。

### 3.2 DOCX（`docx.py`）

`extract` 关键步骤：

1. `Document()`；失败降级。
2. `iter_inner_content()` 按 body **块级顺序**遍历：表格走 `_render_table()`——按 `w:tc` 收成格位规格（**不用 `row.cells`**：合并区会被返回多次、文本因此重复），交给 `processors/grid.py` 的格位网格渲染（制表符分隔、合并区文本只出现一次、覆盖位占列位、超 `MAX_TABLE_ROWS=2000` 行截断并说明）；段落取文本，段落里含 `a:blip` 时在文本末追加中性占位符 `[图片 N]`。
3. 页数取 `docProps/app.xml` 里的 `<Pages>`——那是保存时的缓存渲染值，读到就在 `notes` 里注明来源，读不到置 `None`。
4. 文本框（`w:txbxContent` / VML textbox / Word 2010 shape 三类容器）单独成节 `=== 文本框内容 ===`：它们通常锚定浮动位置，硬插进正文会给错阅读顺序，直接丢又会掉信息。
5. 内嵌图总数由 `a:blip` 计数得到（记的是**引用次数**：同一张图被引 N 次算 N）；`prepare_assets=True` 时按来源关系 id（`r:embed` / `r:link`）去重，把前 `MAX_EMBEDDED_IMAGES` 张**不同的**图经 `related_parts` 取出、转 PNG 落盘成素材。

`understand`：`reading.enhance_assets()` 批量识读内嵌图 → `meta.ocr_text`。

### 3.3 PPTX（`pptx.py`）

`extract` 关键步骤：

1. `Presentation()`；失败降级。
2. 逐张幻灯片，`_walk_shapes()` **递归展开组合形状**（不递归会漏掉成组排版的正文）。
3. 每个形状按「文本框文本 → 表格（制表符）」顺序追加；`PICTURE` 类形状计数（引用次数），`prepare_assets=True` 时按图片部件的 `sha1` 去重后，把未超上限的不同图转 PNG 落盘成素材（**不能按 rId 去重**：rId 只在页内唯一，而 python-pptx 在包级按内容复用同一个 image part，同一张图跨页时 rId 可以不同）。
4. 每页前加 `=== 幻灯片 N ===`；`pages` 与 `pages_used` 都是幻灯片数。

**不做无效图过滤**（小于 100x100、近似纯色这类）：跳过判断只能靠猜，而漏掉的往往正是流程图或截图；代价由 `MAX_EMBEDDED_IMAGES` 上限兜住。

`understand`：`reading.enhance_assets()` 批量识读页内图片 → `meta.ocr_text`。

### 3.4 表格族 xlsx / xlsm / xls / ods / csv（`excel.py`）

`extract` 关键步骤：

1. 按扩展名选解析器：`xlsx/xlsm` → openpyxl（先 `read_only=True, data_only=True`，失败退回常规模式）；`xls` → xlrd；`ods` → `pyexcel_ods.get_data`；`csv` → 先 `read_text_file` 探测编码解码，再交给 `csv.reader`。解析异常直接降级。
2. 只读可见工作表（`sheet_state == "visible"`）；逐行**边读边停**，读到 `MAX_ROWS_TO_READ=5001` 行即止（多读一行是为了让渲染层能区分「还有后续行」与「这就是最后一行」）。
3. `_render_rows()` 统一渲染，四种格式只有这一套方言：`=== SHEET: 名 ===` 加制表符分隔的行；列宽从格位网格读（**合并区覆盖的空列也算列宽**，不会再被当空尾列裁掉）；跳过整行全空的行；单表超 `MAX_ROWS_PER_SHEET=5000` 行截断并说明。xlsx / xlsm 的合并区由 `xlsx_merges.py` 从包内 XML 读：`read_only=True` 的 worksheet 没有 `merged_cells`，而只在常规模式读会让两条读取路径对同一文件给出不同 `content`（违反 ADR-0002）。
4. 空单元格输出空串（openpyxl 的空单元格是 `None`，直接 `str()` 会写出字面量 `None` 污染整列）。
5. `pages / pages_used` 记工作表数；没有可见内容时写一条 `notes`。

ODS 与 CSV 也在这个文件里：这一族都按行列渲染成同一种文本表格，不必为了名字精确再拆开。

`understand`：本类不产生素材，`_has_model_work()` 靠非空 `content` 放行，直接进步骤 5 摘要。

### 3.5 ODF 文本族 odt / odp（`odf.py`）

`extract` 关键步骤：

1. `zipfile` 取出容器内的 `content.xml`；容器损坏或条目缺失 → 降级。
2. 标准库 `ElementTree` 从 `office:body` **按文档结构递归**——不用全局 `root.iter()`，那个会把所有 `text:p` 拉平，表格因此坍成「一段一行」（列对应关系丢失）、嵌套列表的层级也没了。遇到 `table:table` 与 `text:list` 交给各自的渲染并**不再往下走**（否则单元格与列表项里的段落会被再收一遍），其余容器继续递归。
3. `table:table` → 格位网格（`grid.py`），与 docx / pptx / xlsx 同一份渲染与不变式：`number-columns-spanned` / `number-rows-spanned` → `place(span)`、`covered-table-cell` → `covered()`、`number-columns-repeated` / `number-rows-repeated` 循环落位；超 `MAX_TABLE_ROWS=2000` 行截断并说明。
4. `text:list` → Markdown 列表，每深一层多缩进两个空格（`LIST_INDENT`）；`text:list-header` 与 `text:list-item` 同等对待。
5. `text:p` / `text:h` 取文本；`text:h` 按 `outline-level` 转成 Markdown 记号标题（上限 6 级），标题数写进 `notes`；行之间空行分隔。

已知边界：只还原文字，不还原样式、分栏与列宽；`number-*-repeated` 的重复次数有上限 `MAX_REPEATED_SLOTS=100`，防的是 ODF 拿它把一行补到上千列时把网格撑爆（尾部空格既不占可见列宽也不带内容，钳掉不影响渲染）。（`ods` 不在这里——表格必须按行列位置还原，走 `excel.py`。）

`understand`：无素材，直接进摘要。

### 3.6 纯文本族 txt / md / json / xml / rtf（`plaintext.py`）

先统一解码：`read_text_file()` 按 `utf-8-sig`（吃 BOM）→ `utf-8` → `chardet` 探测 → `utf-8 + errors=replace` 依次尝试，保证不抛异常；单文件读取上限 20MB，超出截读。

| 扩展名 | 关键步骤 |
|---|---|
| txt / md / markdown | 原文直读。**Markdown 不做 HTML 转换**——标题层级、列表、表格、链接都是信息，再加工只会掉信息 |
| json | `json.loads` 后 `indent=2` 美化；解析失败原样返回，不因格式问题丢内容 |
| xml | `ET.fromstring` + `ET.indent` + `tostring` 缩进输出并**保留属性**；解析失败原样返回 |
| rtf | `rtf.py` 的 `rtf_to_text()` 逐字符解析控制字：处理 `\uN`（含负值 UTF-16 码位）与 `\'hh` 十六进制转义（按 utf-8 → gbk → cp1252 → latin-1 解码），**整组跳过** `fonttbl` / `colortbl` / `stylesheet` / `pict` 等可忽略目的地（否则字体名会混进正文），`\par` / `\line` 还原成换行，最后折叠连续空行 |

`understand`：无素材，直接进摘要。

### 3.7 网页 html / htm（`html.py`）

`extract` 关键步骤：

1. `read_text_file()` 解码 → `BeautifulSoup(html.parser)`。
2. 先 `decompose()` 掉 `script` / `style` / `noscript` / `iframe` / `svg`。
3. 取 `<title>` 与 `<meta name="description">`。
4. 链接：遍历 `a[href]`，跳过 `#` / `javascript:` / `mailto:` / `tel:` 开头，用 `urljoin(page_url, href)` 补成绝对链接，按绝对地址去重；**先把全部链接收完再截断**，这样 `notes` 才说得清「总共多少条」（`MAX_LINKS=100`）。
5. 正文 `get_text(separator="\n")` 后清理连续空行——带分隔符是为了避免块级元素粘连成一行。
6. 组装 `content`：`# 标题` + `> description` + 正文 + `## 链接`。

`page_url` 是步骤 1 落盘时的**最终 URL**（重定向之后），相对链接靠它补全；上传的 HTML 没有它，链接保持原样。

`understand`：无素材，直接进摘要。

### 3.8 图片（`image.py`）

`extract` 关键步骤（只给结构信息，不给文字，ADR 0004）：

- **SVG** 走独立分支：`ElementTree` 解析 `width/height`（缺则退回 `viewBox` 的第 3、4 个值），统计元素总数，产出 `ImageMeta(format="SVG", mode="vector")` 与一条说明——矢量图不需要光栅化就能报结构。
- **位图**：Pillow 打开并 `load()`，取宽高、色彩模式、格式、帧数（>1 才填）、DPI，以及白名单里的 10 项 EXIF（厂商 / 机型 / 软件 / 方向 / 两个时间 / 曝光 / 光圈 / ISO / 焦距；**不整包倒出**，避免带出 GPS）。附加说明：HEIC 无 `pillow-heif` 时提示解码可能不完整；ICO 列出内含尺寸；多帧图提示只报首帧。解码失败 → 降级。
- `content` 恒为空串：纯代码对一张图拿不到文字。

`understand` 走 `enhance_image()`：

1. `encode_image()` 编码后调视觉模型，提示词 `classify_image.txt`，`json_mode=True`，**一次调用同时拿到判型和描述**。
2. `kind == "semantic"` → 直接用这次调用给出的 `description`（`EnhanceOutcome.description`），不再 OCR。
3. 其余情况（判为文字图片，或**判型输出解析不出来**）→ 交本机 Paddle OCR 读字进 `ocr_text`。拿不准就 OCR：本机服务不花 token，漏掉整页文字代价更大。
4. 本机 OCR 出口对纵向超长图（高宽比 ≥ 2.5）按 1400 高切段、留 120 重叠、4 路并发识别后按序拼回（任一切片失败即整图失败，避免半张文字混进来）；其余图整图原字节发送。
5. 步骤 5：没有 query 且已有 `description` 时直接复用为 `summary`。

### 3.9 音频与视频（`media.py`）

`extract` 关键步骤：

1. `soundfile` 未安装 → 降级（缺的只是这一类的结构信息，不影响其他格式）。
2. 取音轨：`mp3 / wav / flac / ogg` 直接用源文件；**视频全部**与 `m4a / aac / wma` 这几个 libsndfile 不认的容器先用 `utils/ffmpeg.py` 抽成 16kHz 单声道 wav（`imageio-ffmpeg` 自带的静态 ffmpeg，不要求系统安装；失败或超时抛 `FfmpegError` → 降级）。
3. `soundfile.info()` 读时长、采样率、声道数、音轨格式，填进 `meta.audio`；抽过音轨的额外说明「这里报的是音轨的参数」。
4. `content` 恒为空：视频容器里没有可确定性抽取的文本，语音是模型产出。
5. `prepare_assets=True` 时把音轨登记成 `assets` 里的 `kind="audio_track"`——**一次请求只转码一次**，`enhance_media()` 直接复用它。

`understand` 走 `enhance_media()`：

1. 从 `assets` 取音轨；没有（降级）→ 只写 `notes`，不发请求。
2. 时长超过 `ASR_MAX_AUDIO_SECONDS`（600 秒）→ 不转写，在 `notes` 里说明（ASR 单次输出有 token 上限，长音轨尾部会被静默丢掉）。
3. 否则把音轨字节 base64 发本机 Qwen3-ASR 的 `/asr-b64`，文字进 `meta.asr_text`，语种与字数写进 `notes`；没有说话声时只写说明。

### 3.10 压缩包（`archive.py`）

全链路零模型、不需要系统工具。**只读目录，不解压内容**——压缩炸弹与递归解压的风险在这里挡掉。

| 扩展名 | 关键步骤 |
|---|---|
| zip | `zipfile.infolist()` 取全部条目名；`flag_bits & 0x1` 判定是否加密并写 `notes` |
| tar / tgz / gz / bz2 / tbz2 / xz / txz | `tarfile.open(path, "r:*")` 自动识别压缩；抛 `TarError` 时退回单流压缩分支 |
| 单流压缩（.gz/.bz2/.xz 不是 tar 包） | 只验证能否打开并声明「单文件压缩流，未解压」，条目清单无意义 |
| 7z | `py7zr` 取条目名与 `needs_password()` |
| rar | `rarfile`（纯 Python 头解析，列目录不需要 unrar）；装不上时降级为「只识别格式，无法列出条目」 |

条目渲染统一走 `entries_to_content()`：表头 `# <KIND> 条目清单（共 N 条）`，最多列 `MAX_ENTRIES=500` 条，超出只报数量；条目总数进 `meta.entry_count`。

`understand`：无素材，直接进摘要。

### 3.11 安装包 apk / ipa / exe（`install_package.py`）

与压缩包的区别在「读什么」：除了条目清单，还要按特征文件判到具体的包类型。

1. `read_header_hex()` 取文件头十六进制预览，先写进 `content`。
2. `exe`：只报「PE 可执行文件（MZ 头），只读文件头，不解析内部结构」，`entry_count=0`。
3. `apk / ipa` 本质是 ZIP：`zipfile.infolist()` 取条目名，坏包 → 降级。
   - apk：命中 `AndroidManifest.xml` / `classes.dex` / `resources.arsc` 记为 Android APK；含 `assets/flutter_assets/` 前缀条目则记为 Flutter APK；一个特征都不中时说「疑似 Android 安装包，但缺少特征文件」。
   - ipa：找 `Payload/*.app/Info.plist`，命中即「识别为 iOS IPA」并报出主 plist 路径。
4. 最终 `content` = 指纹行 + 空行 + 条目清单（复用 `archive.entries_to_content()`）。

`understand`：无素材，直接进摘要。

### 3.12 降级（`extract_file_info()`）

触发条件三选一：扩展名为空（`Detector.UNKNOWN`）、命中老版 Office 集合（doc / ppt / wps，Linux 侧缺的是 LibreOffice 转换器）、`dispatch()` 里没有匹配的分支。

产出：`content=""`、`engines=["基础文件信息"]`、`degraded=True`、`degrade_reason` 用中文说清缺的是什么（调用方要能区分「能力边界」与「故障」），文件头十六进制进 `notes`；`type` 为 `file`（只有入口阶段失败才是 `error`），HTTP 200。不写「这是一个…文件」这类模板句进 `content`——机器没法解析，事实全部进 `meta`。

`understand`：`content` 空、无素材、源也不是图片或音视频 → `_has_model_work()` 判否，写一条 `notes` 说明跳过模型，`summary` 为 `null`。

## 4. 相关上限一览

**只有一个定义处**：`backend/configs/limits.py` 的 `LIMITS` 表。可配置的值读 `settings`（环境变量），
写死的直接定义在那里——两个来源在同一个文件里汇合，不再有第三个。

表里每条登记了值、单位、命中行为（`error` / `truncate` / `clamp`）、强制点（按函数名，不写行号——
行号会随任何一次编辑腐烂）与命中文案模板。**处理器命中时只把名字记进 `result.limits_hit`**，
文案由 `step6_assemble` 按模板统一生成：改说法只动一处，不必翻十几个处理器。

| 配置项 | 默认 | 作用点 |
|---|---|---|
| `MAX_DOWNLOAD_BYTES` | 200MB | 下载与上传边写边校验 |
| `REQUEST_BUDGET_SEC` / `CONNECT_TIMEOUT_SEC` / `READ_TIMEOUT_SEC` | 120 / 10 / 30 秒 | 下载层预算与超时 |
| `MAX_REDIRECTS` | 5 | 逐跳重校验的最大跳数 |
| `MAX_CONTENT_CHARS` | 20 万字符 | 步骤 6 的 `content` 截断 |
| `MAX_MODEL_INPUT_CHARS` | 12 万字符 | 步骤 5 送模型的输入截断 |
| `MAX_EMBEDDED_IMAGES` | 3 张 | **不同的**内嵌图落盘与批量识读上限（同一张图被引多次只算一张） |
| `RENDERED_PAGE_MAX` / `RENDERED_PAGE_DPI` | 10 页 / 150 | 扫描件页面渲染 |
| `RENDERED_PAGE_RETRY_MIN_CHARS` | 20 字 | 整页识别失败的切半重试**阈值**（不是上界，见 `NON_LIMIT_THRESHOLDS`） |
| `ASR_MAX_AUDIO_SECONDS` / `FFMPEG_TIMEOUT_SEC` | 600 秒 / 120 秒 | 转写与抽音轨 |
| `MAX_ENTRIES` | 500 条 | 压缩包条目清单 |
| `MAX_ROWS_PER_SHEET` / `MAX_ROWS_TO_READ` | 5000 / 5001 行 | 表格读取上限；后者比前者多一行，用来区分「还有后续行」与「这是最后一行」 |
| `MAX_TABLE_ROWS` | 2000 行 | docx 与 odf 的表格渲染上限（**共用一处**，不再各写一份） |
| `MAX_GRID_EXPANSION` | 200000 | 表格跨度展开的格位总量上限 |
| `MAX_REPEATED_SLOTS` | 100 | odf：`number-*-repeated` 的重复次数上限，防尾部补位把网格撑爆 |
| `MAX_LINKS` | 100 条 | 网页链接清单 |
| `TEXT_LAYER_MIN_CHARS` | 30 字 | PDF 文字层判定**阈值**（不是上界） |
| `MAX_TEXT_BYTES` | 20MB | 文本类读取上限 |
| `MAX_LONG_EDGE` | 1600 | 送模型前图片长边缩放目标 |
| `PREVIEW_FIRST_PAGES` | 15 页 | RAG 预览页数 |

**哪些会出现在 `meta.limits_hit` 里**：行为是 `truncate` 的**大部分**——真的少读了用户的字节 / 行 / 页 / 张。
`error` 类（下载体积、跳转数）本身就是失败响应，用户直接从 `error` 与状态码看到；`clamp` 类
（图片等比缩放、ODF 补位次数）是常规归一化，命中不代表数据丢失，报出来只会变成噪声——
它们登记在表里备查，但不报告。

**还没接上的三个**（`configs/limits.py` 的 `NOT_REPORTED_YET`）：`max_text_bytes`、
`preview_first_pages`、`llm_max_tokens`。它们语义上也会截断，只是强制点拿不到 `result`——
`textfile.read_text_file()` 是返回字符串的工具函数、RAG 预览走自己的响应契约、
`llm_max_tokens` 要看模型的 `finish_reason` 才判得出有没有截断。**别把这一节读成已全覆盖。**

`.env` 变量与全部取值见 [RUNBOOK.md](./RUNBOOK.md) 的配置项全表，写死的上界见紧随其后的模块内常量表，判定见 [adr.md](./adr.md)。
