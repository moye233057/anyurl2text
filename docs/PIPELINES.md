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

## 2. 通用模块

任何类型都经过这五段。各类型的差别只在「步骤 4 的处理器」以及「步骤 4′ 拿什么素材给模型」。

### 2.1 取字节（`step1_download.py` / `step3_prepare.py`）

两个入口只有取字节的方式不同，之后完全合流。

| 入口 | 关键步骤 |
|---|---|
| URL | 去空格并把空格换成 `%20` → 校验协议（只收 http/https；`ALLOW_HTTP_SOURCE=false` 时 `http://` 直接 422）与主机 → 手工逐跳跟随重定向（每跳重新校验协议与地址）→ 流式写盘 |
| 上传 | 分块写进 `upload.bin`，边写边累计字节，与下载共用同一个大小上限 |

安全与边界（不可协商）：DNS 返回的每个地址都判定，命中私网 / 回环 / 链路本地 / 保留 / 组播一律 422；地址无法识别亦拒；**解析一次并钉 IP 建连**（Host/SNI 仍用原主机名）；重定向逐跳重检，**禁止 https→http**；字节数在写入过程中累计，超 `MAX_DOWNLOAD_BYTES`（默认 200MB）立刻中断并删半成品（413）；超时按剩余预算递减（504）；TLS 校验恒开，证书问题直接失败。

文件名取 `Content-Disposition` 的 `filename=`，取不到退回 URL 路径末段。

### 2.2 判型（`step2_detect.py`）

固定顺序 **文件头 → 文件名扩展名 → Content-Type**，越靠前越可靠。文件头最可靠但只覆盖容器格式；认不出时先信文件名（URL 路径）里的后缀——CDN 常给 `application/octet-stream` 这种没信息量的类型，路径后缀通常更可信；两者都没有才用 Content-Type 兜底。容器头只能定到「容器」，还要往里看一层：

| 文件头 | 内部依据 | 落到的格式 |
|---|---|---|
| OLE（`D0CF11E0`） | 复合文档目录里的流名（`WordDocument` / `Workbook`·`Book` / `PowerPoint Document`）；读不出或不认识才退回扩展名（那一步也认 `wps`）、Content-Type | doc / xls / ppt，都认不出才是 `ole` |
| ZIP（`PK\x03\x04`） | 中央目录条目指纹 | `word/document.xml`→docx；`xl/workbook.xml`→xlsx（另有 `xl/vbaProject.bin` 则 xlsm）；`ppt/presentation.xml`→pptx；`AndroidManifest.xml`→apk |
| ZIP | 根级 `mimetype` 条目 | odt / ods / odp |
| ZIP | `Payload/*.app/Info.plist` | ipa |
| ZIP | 以上都不命中或容器读不出 | 维持 `zip`（加密包与坏包本来就该走压缩包路径） |
| `RIFF` | 第 8-12 字节子类型 | webp / wav / avi |
| `ftyp` | major brand | heic / m4a / mp4 |
| 文本类（`<` 起头） | 声明与根元素 | xml / svg / html |

OLE 那一行是**读容器内部**得出、不是猜：`pipelines/steps/ole_directory.py` 按 OLE 规范读目录流——头偏移 30 是扇区大小以 2 为底的对数、偏移 48 是目录流首个扇区号，目录项 128 字节、名字是 UTF-16LE；目录流常跨多个扇区，所以按 FAT 链一节节走（只看第一节会漏掉后面的条目）。任何解析异常都静默退回，识别这一层绝不抛异常。已知边界：头里的 DIFAT 数组只覆盖前 109 个 FAT 扇区，再大且目录流恰在后段的文件可能读不全——那种情况按「读不出」退回扩展名，不会误判成别的家族成员。

认不出扩展名返回 `("", Detector.UNKNOWN)`，不做猜测。得到的 `ext` 决定两件事：响应的 `type`（`modality_from_extension`）与步骤 4 走哪个处理器。

识别结果会被补成临时文件的真实后缀（`_align_extension`）——部分解析库按后缀挑引擎（openpyxl 只认 `.xlsx/.xlsm`，pyexcel-io 按后缀选读取器），不补则识别对了也读不出来。

### 2.3 抽取（`step4_extract.dispatch()` + `processors/`）

`dispatch()` 先按**原始文件名**拦掉不是文档本体的噪音——Office 锁文件（`~$` 前缀）与 `.DS_Store` / `Thumbs.db`：它们有正常的扩展名、内容却不是文档，交给处理器只会得到一句「解析失败」；命中直接返回 `degrade_code="temporary_lock_file"` 的降级产出。判定用的是**原始文件名**而不是落盘名，因为下载落盘名已被 `_align_extension` 换成 `dl_xxxx.docx`。

剩下是一张扁平分派表，扩展名命中哪一行就走哪个 `extract_*()`：

| 分组 | 扩展名 | 处理器 |
|---|---|---|
| 文档 | pdf / docx / pptx | `pdf.py` / `docx.py` / `pptx.py` |
| 文档 | xlsx / xlsm / xls / ods / csv / tsv | `excel.py` |
| 文档 | odt / odp | `odf.py` |
| 文档 | epub | `epub.py` |
| 文档 | ofd | `ofd.py` |
| 文档 | txt / md / markdown / json / xml / rtf | `plaintext.py`（rtf 的解析器在 `rtf.py`） |
| 网页 | html / htm | `html.py` |
| 图片 | png / jpg / jpeg / gif / bmp / tif / tiff / webp / heic / heif / ico / svg | `image.py` |
| 音频与视频 | mp3 / wav / aac / flac / ogg / wma / m4a / mp4 / avi / mkv / mov / wmv / flv / webm / mpeg / mpg | `media.py` |
| 压缩包 | zip / tar / gz / tgz / bz2 / tbz2 / xz / txz / 7z / rar | `archive.py` |
| 安装包 | apk / ipa / exe | `install_package.py` |
| 降级 | 空 ext、doc / ppt / wps、其余未覆盖扩展名；另：Office 锁文件（`~$`）与 `.DS_Store` / `Thumbs.db` 在分派前拦下（`temporary_lock_file`） | `extract_file_info()`（同文件内） |

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

读素材的共用实现在 `models/reading.py`：

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
| docx | 段落 + 制表符表格 + 文本框节 + 链接段 | `iter_inner_content()` 按块级顺序 | 内嵌图批量识读 → `ocr_text` |
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
2. 总页数超过 `MAX_PDF_PAGES`（200）只处理前 N 页，并记 `max_pdf_pages`；逐页 `get_text("blocks")`，丢掉空块，按 **(y, x)** 排序后拼成一页；每页前加 `=== 第N页 ===`，页间空行。
3. 累加 `get_images(full=True)` 得到图片总数（进 `meta.images`；只扫已处理页）。
4. 文字层判定：已处理页文本合计少于 `TEXT_LAYER_MIN_CHARS`（30 字）即视为「没有文字层（疑似扫描件）」。
5. `prepare_assets=False`（extract 路）：只加一条说明 `notes`，不做任何渲染。
6. `prepare_assets=True`（understand 路）分两种互斥情况：
   - **扫描件**（无文字层且 `RENDERED_PAGE_OCR_ENABLED`）：渲染前 `min(已处理页数, RENDERED_PAGE_MAX)` 页为 PNG（`RENDERED_PAGE_DPI`，默认 150）；单页估算或实际 pixmap 超 `MAX_PDF_PAGE_PIXELS` 则跳过该页并记 `max_pdf_page_pixels`。**不再抽内嵌图**。
   - **有文字层**：按 xref 去重后取前 `MAX_EMBEDDED_IMAGES` 张**不同的**内嵌图；pixmap 超像素预算同样跳过并记命中。

`understand`：走 `reading.enhance_assets()`，页面图逐页识别（含空白页预检与切半重试）、内嵌图一次性批量识读，两者按素材顺序拼进 `meta.ocr_text`。

### 3.2 DOCX（`docx.py`）

`extract` 关键步骤：

1. `Document()`；失败降级。
2. `iter_inner_content()` 按 body **块级顺序**遍历：表格走 `_render_table()`——按 `w:tc` 收成格位规格（**不用 `row.cells`**：合并区会被返回多次、文本因此重复），交给 `processors/grid.py` 的格位网格渲染（制表符分隔、合并区文本只出现一次、覆盖位占列位、超 `MAX_TABLE_ROWS=2000` 行截断并说明），并数出**源自己标出的**表头行数（`w:trPr/w:tblHeader`，Word 用它声明「跨页重复的表头行」）；段落取文本，段落里含 `a:blip` 时在文本末追加中性占位符 `[图片 N]`。**取行与取格文字都走 XML**：`_row_elements` 会把 `w:sdt/w:sdtContent` 里的行也收进来（python-docx 的 `table.rows` 只看直属 `w:tr`，Word 的「重复表行内容控件」因此整行消失），`_cell_text` 拆 `w:sdt` 并把**格内嵌套表**按 `a / b` 行、行间空格收进这一格（旧实现按 `tc.p_lst` 取不到它们）。`w:rPr/w:vanish` 的隐藏文字照抽，处数与字数写进 `notes`。
3. 页数取 `docProps/app.xml` 里的 `<Pages>`——那是保存时的缓存渲染值，读到就在 `notes` 里注明来源，读不到置 `None`。
4. 文本框（`w:txbxContent` / VML textbox / Word 2010 shape 三类容器）单独成节 `=== 文本框内容 ===`：它们通常锚定浮动位置，硬插进正文会给错阅读顺序，直接丢又会掉信息。
5. 内嵌图总数由 `a:blip` 计数得到（记的是**引用次数**：同一张图被引 N 次算 N）；`prepare_assets=True` 时按来源关系 id（`r:embed` / `r:link`）去重，把前 `MAX_EMBEDDED_IMAGES` 张**不同的**图经 `related_parts` 取出、转 PNG 落盘成素材。
6. 超链接：走 `w:hyperlink`（表格与文本框里的也在 body 里，一起收），地址取自 `r:id` 指向的**外部**关系——正文里只留链接文字的话，「这段文字指向哪儿」就丢了；内部跳转（只有 `w:anchor`、没有地址）跳过，与网页那一路跳过 `#` 锚点同一条规矩。按地址去重后截到 `MAX_LINKS`（**复用网页那一条上界**），命中时把 `max_links` 报进 `limits_hit`；输出成末尾的 `## 链接` 段，每行 `文字 -> URL`，没有文字时只给 URL（与 `html.py` 同形）。无链接的文档因此产出一字不变。

`understand`：`reading.enhance_assets()` 批量识读内嵌图 → `meta.ocr_text`。

### 3.3 PPTX（`pptx.py`）

`extract` 关键步骤：

1. `Presentation()`；失败降级。
2. 逐张幻灯片，`_walk_shapes()` **递归展开组合形状**（不递归会漏掉成组排版的正文）。
3. 每个形状按「文本框文本 → 表格（制表符）」顺序追加；`PICTURE` 类形状计数（引用次数），`prepare_assets=True` 时按图片部件的 `sha1` 去重后，把未超上限的不同图转 PNG 落盘成素材（**不能按 rId 去重**：rId 只在页内唯一，而 python-pptx 在包级按内容复用同一个 image part，同一张图跨页时 rId 可以不同）。
4. 每页前加 `=== 幻灯片 N ===`；`pages` 与 `pages_used` 都是幻灯片数。**隐藏幻灯片照抽**（`p:sld@show="0"` 只是放映时跳过），是哪几张写进 `notes`。
5. 表格里的格内换行写成 `<br>`、制表符换成空格（格位方言见 §3.4 末的 `processors/cell_text.py`：真换行会把这一行切成两行、同表列数对不上）；合并区**续格里的残留文字并回合并单元格**（产出方有时把文字留在续格，anydoc 直接丢，本仓并回来并在 `notes` 报处数），已出现在 origin 里的不重复并。

**不做无效图过滤**（小于 100x100、近似纯色这类）：跳过判断只能靠猜，而漏掉的往往正是流程图或截图；代价由 `MAX_EMBEDDED_IMAGES` 上限兜住。

`understand`：`reading.enhance_assets()` 批量识读页内图片 → `meta.ocr_text`。

### 3.4 表格族 xlsx / xlsm / xls / ods / csv / tsv（`excel.py`）

`extract` 关键步骤：

1. 按扩展名选解析器：`xlsx/xlsm` → openpyxl（先 `read_only=True, data_only=True`，失败退回常规模式）；`xls` → xlrd；`ods` → `pyexcel_ods.get_data`；`csv` → 先 `read_text_file` 探测编码解码，再嗅探分隔符后交给 `csv.reader`。解析异常直接降级。
2. **隐藏工作表照读**——`sheet_state` 只管界面显示，不是「没有内容」（用户 2026-10-04 拍板：隐藏内容一律照抽，例外只有「会把系统打爆」与「解析超过 60 秒」）。逐行**边读边停**，读到 `MAX_ROWS_TO_READ=5001` 行即止（多读一行是为了让渲染层能区分「还有后续行」与「这就是最后一行」）。
3. `_render_rows()` 统一渲染，四种格式只有这一套方言：`=== SHEET: 名 ===` 加制表符分隔的行；列宽从格位网格读（**合并区覆盖的空列也算列宽**，不会再被当空尾列裁掉）；跳过整行全空的行；单表超 `MAX_ROWS_PER_SHEET=5000` 行截断并说明。**行尾缺格补空**（OOXML 允许一行提前结束、pptx 生产者也常省略尾部续格，不补的话这一行的制表符个数与同表其他行不同、后面所有列位都错开）；**CSV / TSV 除外**——锯齿行是源文件里的真实形状，传 `pad_short_rows=False` 保真。格位网格另有**三条预算**——跨度展开 `max_grid_expansion`、格位数 `max_grid_slots`、文字量 `max_grid_text_chars`：命中时**不落该格**并把名字交回 `limits_hit`；四条表格路径（docx / odf / excel / pptx）都会上报（2026-10-04 修掉了 excel / pptx「只交网格、不交命中的名字」的静默）。
4. 合并区按容器分家、都归到同一份格位网格：`xlsx / xlsm` 由 `excel.read_merge_rects` 从包内 XML 读（`read_only=True` 的 worksheet 没有 `merged_cells`，只在常规模式读会让两条读取路径对同一文件给出不同 `content`，违反 ADR-0002）；`xls` 取 xlrd 的 `merged_cells`——**它只在 `formatting_info=True` 时才有值**，且坐标是开区间且行在前，要过 `grid.merge_rect` 换算；`ods` 由 `excel.read_ods_merge_rects` 从 `content.xml` 读（`pyexcel-ods` 丢跨度），并把「不给覆盖位留位置」的值行按覆盖位对齐（该库对 `covered-table-cell` 不产出值，不对齐则后面的值整体左移）。ODF 行收集走 `utils/excel/odf_table_rows`，docx 走直属 `w:tr`；**行组（表头行组、`table:table-rows`、`table:table-row-group`）会往里走一层**——只取直属行会让整组内容消失。
5. **表类型**（`meta.table_kind`）：交付文本只有一张表时给 `data` / `layout`，口径照 anydoc 的 `TableKind`——1×1 单格表算 `layout`（排版壳）。判据是**渲染好的行**（`table_kind.classify`），所以四条表格路径口径一致。
6. 空单元格输出空串（openpyxl 的空单元格是 `None`，直接 `str()` 会写出字面量 `None` 污染整列）；**浮点**单元格按 15 位有效数字写（`f"{value:.15g}"`）——`str(0.1 + 0.2)` 会写出 `0.30000000000000004` 这种二进制误差长尾，而整数与字符串一律原样不动（整数因此不会平白多出小数点）。
7. `pages / pages_used` 记工作表数（含隐藏表）；没有内容时写一条 `notes`。
8. **隐藏状态另登记进 `notes`**（内容照抽，登记是为了不静默）：整表隐藏的工作表写明 `hidden` / `veryHidden`，隐藏行与隐藏列按连续段写，例如 `隐藏内容照抽：工作表「可见表」有隐藏行（第 2 行、第 4-5 行）、隐藏列（B 列）`。`xlsx / xlsm` 由 `excel.read_xlsx_visibility` 从包内 XML 读——**`read_only=True` 的 worksheet 没有 `row_dimensions` / `column_dimensions`**（实测同一份文件常规模式给出 `hidden_rows=[2]`、只读模式是空的），而值必须走只读模式，所以隐藏状态自己解包；`xls` 取 xlrd 的 `visibility` / `rowinfo_map` / `colinfo_map`（BOUNDSHEET 编码：0 可见、1 隐藏、2 非常隐藏）；**`ods` 走 `excel.read_ods_visibility`**（见下）。登记段数超过 `max_visibility_ranges=24` 时只登记前 24 段并报出该上界名——**钳的只是登记，正文一格不少**。

ODS 的隐藏是**样式**说了算（整表 `table:display="false"`、行列 `table:visibility="collapse"`），`pyexcel-ods` 照读不筛，所以 `hidden.ods` 的内容不少；**可见性由 `excel.read_ods_visibility` 解 `content.xml` 的自动样式表读出来**（值那条路只给值，样式一概丢掉），行列按 `number-*-repeated` 展开后压成 0 基半开区间，与 xlsx / xls 的产出同形——三份容器对同一形状给出同一段正文与同一条登记（有用例钉住）。

ODS 与 CSV 也在这个文件里：这一族都按行列渲染成同一种文本表格，不必为了名字精确再拆开。

CSV 的分隔符嗅探规则（确定性，实现只在 `excel._sniff_delimiter` 一处）：取前 20 行**非空**行当样本，对 `,` `;` `\t` `|` 四个候选各解一次，只考虑「确实把行列开了」的候选（样本里存在多于 1 列的行）；在其中选**列数最一致**的那个（与出现次数最多的列数相同的行最多），平局按候选顺序决定（逗号优先）；一个都不满足就退回逗号。字段里出现的制表符 / 分号 / 竖线因此只是普通字符，普通逗号 CSV 的产出逐字节不变。

**格内文本的方言只有一处定义**：`utils/excel/cell_text.py`。一个格位只能占交付文本的一行一列，所以格里的换行一律写成 `<br>`（anydoc 的 `render_cell` 口径：`src/render/markdown/table.rs:87-101`）、制表符一律换成空格——真制表符会凭空多出一列、真换行会把这一行 TSV 拆成两行；段内空白仍压成单空格，纯空白段丢掉。六条表格路径都走它：docx（`w:p` 之间、以及 `w:br` / `w:cr` 软换行）、pptx（`cell.text` 的 `\n` 与 `\v`）、excel（单元格值里的 `\n`）、odf（`text:p` 之间）、网页（格内 `<br>` 元素）、rtf（`\par` / `\line` 之后）。嵌套表在格内串成 `甲 / 乙`、行间也是 `<br>`。**源里本来就写着字面 `<br>` 的文本与之不可区分**（照 anydoc，不做转义）。

**网格的两条登记**（`grid.grid_notes`，四条表格路径共用，文案只有一处）：
- **孤儿覆盖位**：`covered()` 认领不到合并起点时（产出方把合并块首行删了 / 部分复制粘贴 / 导出器怪癖，或我们自己的预算裁掉了起点格的跨度），该位置退化成空格位并计数 → `有 N 个覆盖位没有对应的合并起点，已按空格处理（列位不受影响）`。它把「文件本来就没内容」与「我们没解析对」分开，是合并解析缺口的症状指标。
- **续格文字并回**：续格（docx 的 `vMerge` 继续格、pptx 的 `is_spanned`、ODF 的 `covered-table-cell`）自带文字时并进 origin 的文本末尾（已出现在 origin 里的不重复并）→ `覆盖格里的残留文字已并入合并单元格（N 处）`。anydoc 直接丢这类文字，本仓取向是尽量抽出来。

`understand`：本类不产生素材，`_has_model_work()` 靠非空 `content` 放行，直接进步骤 5 摘要。

### 3.5 ODF 文本族 odt / odp（`odf.py`）

`extract` 关键步骤：

1. `zipfile` 经 `utils/zip_read.read_member` 取出容器内的 `content.xml`（计入解压总量预算）；容器损坏、条目缺失或超 `MAX_ZIP_UNCOMPRESSED_BYTES` → 降级。
2. 标准库 `ElementTree` 从 `office:body` **按文档结构递归**——不用全局 `root.iter()`，那个会把所有 `text:p` 拉平，表格因此坍成「一段一行」（列对应关系丢失）、嵌套列表的层级也没了。遇到 `table:table` 与 `text:list` 交给各自的渲染并**不再往下走**（否则单元格与列表项里的段落会被再收一遍），其余容器继续递归。
3. `table:table` → 格位网格（`grid.py`），与 docx / pptx / xlsx 同一份渲染与不变式：`number-columns-spanned` / `number-rows-spanned` → `place(span)`、`covered-table-cell` → `covered()`、`number-columns-repeated` / `number-rows-repeated` 循环落位；超 `MAX_TABLE_ROWS=2000` 行截断并说明。**收行要往里走一层**：`table:table-row` 不只直接挂在表下，`table:table-header-rows`（表头行组）、`table:table-rows`、`table:table-row-group` 都会再包一层——只取直属行的话整组内容凭空消失（实测表头行组里的文字一个字都没进正文）。表头行**照渲**，同时按 `table:table-header-rows` 里的行数（含 `number-rows-repeated`）报出「源自己标出的表头行数」。
4. `text:list` → Markdown 列表，每深一层多缩进两个空格（`LIST_INDENT`）；`text:list-header` 与 `text:list-item` 同等对待。
5. `text:p` / `text:h` 取文本；`text:h` 按 `outline-level` 转成 Markdown 记号标题（上限 6 级），标题数写进 `notes`；行之间空行分隔。

已知边界：只还原文字，不还原样式、分栏与列宽；`number-*-repeated` 的重复次数有上限 `MAX_REPEATED_SLOTS=100`，防的是 ODF 拿它把一行补到上千列时把网格撑爆（尾部空格既不占可见列宽也不带内容，钳掉不影响渲染）。（`ods` 不在这里——表格必须按行列位置还原，走 `excel.py`。）

`understand`：无素材，直接进摘要。

### 3.6 纯文本族 txt / md / json / xml / rtf（`plaintext.py`）

先统一解码：`read_text_file()` 按 `utf-8-sig`（吃 BOM）→ `utf-8` → `chardet` 探测 → `utf-8 + errors=replace` 依次尝试，保证不抛异常；单文件读取上限 20MB，超出截读。**`rtf` 是例外**：它在读盘之前就分给 `rtf.extract_rtf(path)`（自己读一次盘，表格要重建网格），不走这里的 `read_text_file`。

| 扩展名 | 关键步骤 |
|---|---|
| txt / md / markdown | 原文直读。**Markdown 不做 HTML 转换**——标题层级、列表、表格、链接都是信息，再加工只会掉信息 |
| json | `json.loads` 后 `indent=2` 美化；解析失败原样返回，不因格式问题丢内容 |
| xml | `ET.fromstring` + `ET.indent` + `tostring` 缩进输出并**保留属性**；解析失败原样返回 |
| rtf | `rtf.py` 逐字符解析控制字：处理 `\uN`（含负值 UTF-16 码位）——**按 `\ucN` 声明跳过 fallback 字符**（默认 1 个；问号、`\'hh` 转义、普通字符都算一个单位，只认 `?` 会让 `\uc1` 的参数漏成正文）——与 `\'hh` 十六进制转义（按 utf-8 → gbk → cp1252 → latin-1 解码），**整组跳过** `fonttbl` / `colortbl` / `stylesheet` / `pict` 等可忽略目的地（否则字体名会混进正文），`\par` / `\line` 还原成换行，最后折叠连续空行。**表格走同文件内的 `build_lines`**：`\trowd` / `\cellx` / `\cell` / `\row` 按 anydoc 的边界聚类还原成网格列——右边界先归一化成严格递增，再把**全部行的右边界**贪心聚类成列（容差 10 twips，**与簇的种子比**，否则 `\cellx` 的 1-2 twips 抖动会把整行并成一簇），横向合并折成列跨度（文本首尾直拼）、纵向链按**精确列区间**相等匹配、无主的续格变回可见格；`\trhdr` 是源自己标的表头行（进 `meta.table_header_rows`）。列位由 `grid.py` 渲染，与 docx 同一份；`plaintext.py` 在读盘之前把 rtf 交给 `extract_rtf(path)`（表头行数与行数上限要随结果带回） |

`understand`：无素材，直接进摘要。

### 3.7 网页 html / htm（`html.py`）

`extract` 关键步骤：

1. `read_text_file()` 解码 → `BeautifulSoup(html.parser)`。
2. 先 `decompose()` 掉 `script` / `style` / `noscript` / `iframe` / `svg`。
3. 取 `<title>` 与 `<meta name="description">`。
4. **表格先换成哨兵行**：`html_tables.render_tables()` 把每个 `<table>` 渲成制表符块（`colspan` / `rowspan` 交给格位网格，被吞掉的格由网格自动补覆盖位；`rowspan="0"` 按「跨到本行组末尾」处理），元素换成 `@@TABLE-N@@` 占住它在文档里的位置。**顺序不能颠倒**：正文清洗会 strip 每一行，先换回会把行首空列吃掉。
5. 链接：遍历 `a[href]`，跳过 `#` / `javascript:` / `mailto:` / `tel:` 开头，用 `urljoin(page_url, href)` 补成绝对链接，按绝对地址去重；**先把全部链接收完再截断**，这样 `notes` 才说得清「总共多少条」（`MAX_LINKS=100`）。
6. 正文 `get_text(separator="\n")` 后清理连续空行（带分隔符是为了避免块级元素粘连成一行），清洗完再把哨兵行换回制表符块。
7. 组装 `content`：`# 标题` + `> description` + 正文 + `## 链接`。

`page_url` 是步骤 1 落盘时的**最终 URL**（重定向之后），相对链接靠它补全；上传的 HTML 没有它，链接保持原样。

表格的**表头行数**取自源自己标出的标记（行在 `<thead>` 里、或该行单元格全是 `<th>`），交付文本里**唯一一张表**时进 `meta.table_header_rows`；多张表时不猜、给 `null` 并在 `notes` 里说明张数。不做「首行即表头」这类启发式——猜错会吃掉一行数据。EPUB 章节走同一条路、同样规则。

`understand`：无素材，直接进摘要。

### 3.8 图片（`image.py`）

`extract` 关键步骤（只给结构信息，不给文字，ADR 0004）：

- **SVG** 走独立分支：`ElementTree` 解析 `width/height`（缺则退回 `viewBox` 的第 3、4 个值），统计元素总数，产出 `ImageMeta(format="SVG", mode="vector")` 与一条说明——矢量图不需要光栅化就能报结构。
- **位图**：经 `open_image` 打开（读前宽×高超过 `MAX_IMAGE_PIXELS` 直接拒并记 `max_image_pixels`），再 `load()`，取宽高、色彩模式、格式、帧数（>1 才填）、DPI，以及白名单里的 10 项 EXIF（**不整包倒出**）。附加说明：HEIC 无 `pillow-heif` 时提示解码可能不完整；ICO 列出内含尺寸；多帧图提示只报首帧。解码失败 → 降级。
- `content` 恒为空串：纯代码对一张图拿不到文字。

`understand` 走 `enhance_image()`：

1. `encode_image()` 编码后调视觉模型，提示词 `classify_image.txt`，`json_mode=True`，**一次调用同时拿到判型和描述**。
2. `kind == "semantic"` → 直接用这次调用给出的 `description`（`EnhanceOutcome.description`），不再 OCR。
3. 其余情况（判为文字图片，或**判型输出解析不出来**）→ 交本机 Paddle OCR 读字进 `ocr_text`。拿不准就 OCR：本机服务不花 token，漏掉整页文字代价更大。
4. 本机 OCR 出口对纵向超长图（高宽比 ≥ 2.5）按 1400 高切段、留 120 重叠、最多 `MAX_OCR_SLICES` 片、4 路并发识别后按序拼回（超片数截断并记 `max_ocr_slices`；任一切片失败即整图失败）；其余图整图原字节发送。
5. 步骤 5：没有 query 且已有 `description` 时直接复用为 `summary`。

### 3.9 音频与视频（`media.py`）

`extract` 关键步骤：

1. `soundfile` 未安装 → 降级（缺的只是这一类的结构信息，不影响其他格式）。
2. 取音轨：`mp3 / wav / flac / ogg` 直接用源文件；**视频全部**与 `m4a / aac / wma` 这几个 libsndfile 不认的容器先用 `utils/ffmpeg.py` 抽成 16kHz 单声道 wav（`imageio-ffmpeg` 自带的静态 ffmpeg，不要求系统安装；失败或超时抛 `FfmpegError` → 降级）。抽之前先 `probe_duration_seconds`：已超 `ASR_MAX_AUDIO_SECONDS` 则**跳过转码**，只报探测时长并记 `asr_max_audio_seconds`；仍抽取时带 ffmpeg `-t` 封顶，避免探不出时长时先写满临时 wav。
3. `soundfile.info()` 读时长、采样率、声道数、音轨格式，填进 `meta.audio`（有探测时长时优先报源时长）；抽过音轨的额外说明「这里报的是音轨的参数」。
4. `content` 恒为空：视频容器里没有可确定性抽取的文本，语音是模型产出。
5. `prepare_assets=True` 且未因超限跳过时，把音轨登记成 `assets` 里的 `kind="audio_track"`——**一次请求只转码一次**，`enhance_media()` 直接复用它。

`understand` 走 `enhance_media()`：

1. 从 `assets` 取音轨；没有（降级）→ 只写 `notes`，不发请求。
2. 时长超过 `ASR_MAX_AUDIO_SECONDS`（600 秒）→ 不转写（抽取阶段多半已跳过转码 / 未登记 assets）；在 `notes` / `limits_hit` 说明（ASR 单次输出有 token 上限，长音轨尾部会被静默丢掉，本项目不静默截断送转写）。
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

### 3.12 EPUB（`epub.py`）

EPUB 也是 ZIP 容器，真实身份写在容器里：识别那一步读 `META-INF/container.xml`（`configs/formats.py` 的 `ZIP_ENTRY_FLAVORS` 最后一条）判到 `epub`，优先于普通 `zip`。

`extract` 关键步骤：

1. 打开 ZIP → 读 `META-INF/container.xml` 取 rootfile 的 `full-path`（OPF）；缺这个文件、容器损坏或受保护（DRM）→ 降级 `epub_parse_failed`。
2. 解析 OPF：`manifest` 只用来建 `id → href` 表，**章节按 `spine` 的 `itemref` 顺序**取——manifest 的声明顺序与 zip 的写入顺序都不代表阅读顺序，按它们拼会把章节排错；不在 spine 里的项（封面、样式、目录页）一概不产出。href 相对 **OPF 所在目录**解析，所以 `OEBPS/content.opf` 里的 `ch1.xhtml` 指的是 `OEBPS/ch1.xhtml`。
3. 每章是 XHTML，用 BeautifulSoup 取正文：`script` / `style` / `noscript` / `iframe` / `svg` 与 `<head>` 整块丢掉。**CSS 一律不进交付文本**——`<link rel="stylesheet">` 与内联 `<style>` 都不是这份文档的内容（`<title>` 已进了分章标记，留着会重复出现）。**表格走与网页同一条路**（`html_tables.py`）：先换哨兵行、清洗后回填，所以章内表格也是制表符块而不是被拉平。
4. 章节块形如 `=== 章节 N: <标题> ===`；标题取该章 `<title>`，没有就退第一个 `h1`–`h6`，都没有只写章号。
5. 链接：外部 `http(s)` 保留绝对地址；**内部引用提升成文档级目标**——EPUB 的 `#frag` 只在**单章内**有意义，跨章拼成一整篇之后不带上章路径，「这个锚点指向哪儿」就是歧义。所以章内自链 `#sec` → `ch2.xhtml#sec`，相对引用按该章所在目录解析（`../ch2.xhtml#sec2` → `ch2.xhtml#sec2`），`javascript` / `mailto` / `tel` 跳过。按目标去重后截到 `MAX_LINKS=100`（命中报 `max_links`），输出成末尾的 `## 链接` 段，与 `html.py` / `docx.hyperlink_lines` 同形。
6. 单章读不出来（容器截断、成员 CRC 坏、加密）**只跳过那一章**并在 `notes` 里点名章节与原因，其余章照常交付；一章都读不出来才整份降级。这里按 `except Exception` 接，因为 zipfile 对损坏数据抛的是 `zlib.error`、对加密成员抛 `RuntimeError`，都不在 `BadZipFile` 之下——按类型列举一定会漏（实测过）。spine 章数超过 `MAX_ENTRIES=500` 时只读前 500 章并报 `max_entries`。成员读取走 `utils/zip_read.read_member`（声明体积预检 + 流式封顶）；一次请求解压总量超 `MAX_ZIP_UNCOMPRESSED_BYTES` 时停读后续章并报 `max_zip_uncompressed_bytes`，必要结构（container / OPF）超限则整份降级。

已知边界：只还原文字与链接，不还原样式与版式；`pages` 这一族不适用（为 `null`）。

`understand`：本类不产生素材，`_has_model_work()` 靠非空 `content` 放行，直接进步骤 5 摘要。

### 3.13 OFD（`ofd.py`）

OFD 也是 ZIP 容器，真实身份写在容器里：识别那一步读**根级 `OFD.xml`**（`configs/formats.py` 的 `ZIP_ENTRY_FLAVORS` 最后一条）判到 `ofd`，优先于普通 `zip`。

`extract` 关键步骤：

1. 打开 ZIP → 读 `OFD.xml` 取 `DocRoot`（通常是 `Doc_0/Document.xml`）；容器损坏、缺 `OFD.xml` 或缺 `DocRoot` → 降级 `ofd_parse_failed`。
2. 解析文档根取 `Pages/Page` 序列：**页序以这份声明为准**，`BaseLoc` 给出该页 `Content.xml` 的路径。规范说它相对文档根，但导出件也有按容器根写的，两种都认；一个都没写、或写出来的路径在容器里全不存在时，退回「容器里所有以 `Content.xml` 结尾的条目」，按名字里的数字升序（`Page_2` 排在 `Page_10` 前）。
3. 每页取正文：按文档顺序遍历 `TextObject`，把它的 `TextCode` 片段拼成一行——版式文档里长行就是这么拆的，各片段带自己的 X 坐标。
4. 页块形如 `=== 第N页 ===`（与 PDF 同形），页间空行分隔；`pages` 是声明的页数、`pages_used` 是真读到的页数。
5. 单页 XML 非法**只跳过那一页**并在 `notes` 里点名原因，其余页照常交付；一页都读不出才整份降级（`ofd_content_failed`）。元素按 local-name 匹配：规范里前缀是 `ofd:`，各厂商导出时的前缀与命名空间声明并不统一。成员读取同 EPUB，走 `utils/zip_read`；解压总量超限时停读后续页并报 `max_zip_uncompressed_bytes`，`OFD.xml` / 文档根超限则整份降级。

已知边界：只还原文字与页序，不还原版面坐标、字形与矢量图形；图像对象只计数（在 `notes` 里说明），图像内容不进交付文本——认字是 understand 的事（ADR-0004）。

`understand`：本类不产生素材，`_has_model_work()` 靠非空 `content` 放行，直接进步骤 5 摘要。

### 3.14 降级（`extract_file_info()`）

触发条件四选一：文件名是 Office 锁文件 / 系统噪音文件（`~$` 前缀、`.DS_Store`、`Thumbs.db`，`degrade_code="temporary_lock_file"`）、扩展名为空（`Detector.UNKNOWN`）、命中老版 Office 集合（doc / ppt / wps，Linux 侧缺的是 LibreOffice 转换器）、`dispatch()` 里没有匹配的分支。

产出：`content=""`、`engines=["基础文件信息"]`、`degraded=True`、`degrade_reason` 用中文说清缺的是什么（调用方要能区分「能力边界」与「故障」），文件头十六进制进 `notes`；`type` 为 `file`（只有入口阶段失败才是 `error`），HTTP 200。不写「这是一个…文件」这类模板句进 `content`——机器没法解析，事实全部进 `meta`。

`understand`：`content` 空、无素材、源也不是图片或音视频 → `_has_model_work()` 判否，写一条 `notes` 说明跳过模型，`summary` 为 `null`。

## 4. 相关上限一览

**上界值只有一个定义处**：`backend/configs/limits.py` 的 `LIMITS` 表。可配置的值读 `settings`（环境变量），
写死的直接定义在那里——两个来源在同一个文件里汇合，上界值不再有第三个来源。

**判定阈值不是上界**：只在 `NON_LIMIT_THRESHOLDS` 登记名字；数值仍在各自模块（如 `pdf.py` 的文字层下限、`reading.py` 的切半重试阈值、`settings` 里与判定相关的项）。不要把「阈值写在哪」与「上界值写在哪」混成一处。

表里每条登记了值、单位、命中行为（`error` / `truncate` / `clamp`）、强制点（按函数名，不写行号——
行号会随任何一次编辑腐烂）与命中文案模板。**处理器命中时只把名字记进 `result.limits_hit`**，
文案由 `step6_assemble` 按模板统一生成：改说法只动一处，不必翻十几个处理器。

### 4.1 资源防护公共件 · ZIP 解压预算（`utils/zip_read.py`）

这是 [CONTEXT.md](../CONTEXT.md) 里说的**资源防护公共件**：不解析 EPUB/OFD/ODF 本身，只保证「解压进进程的字节」有上界。值登记为 `max_zip_uncompressed_bytes`（默认 20MB）；强制逻辑只在这一处，`epub.py` / `ofd.py` / `odf.py` 共用。

**为什么要单独一层**：这三族格式本质是 ZIP。原先 `ZipFile.read(name)` 会把某个成员整份解压进内存，且多次 `read` 互不记账。下载层的 `MAX_DOWNLOAD_BYTES` 只限**外壳**体积，挡不住「壳不大、里面解出来巨大」。压缩炸弹常见两种套路——声明巨大、压后很小；或声明很小、实际膨胀——都必须在解压路径上挡。

**怎么挡（两道闸 + 一份预算）**：一次抽取请求共用一个 `ZipReadBudget`；每个成员走 `read_member`：

1. **声明预检**：读前看 `ZipInfo.file_size`，已超过剩余额度 → 拒读、不解压（挡经典「声明巨大」）。
2. **流式封顶**：`ZipFile.open()` 最多读「剩余 + 1」字节；实际长度超剩余 → 拒读（挡「声明撒谎、实际膨胀」；不解到超额才停）。
3. **跨成员累计**：多份中等成员加总仍会计入同一额度。

命中后的产品行为与其它上界同构：

| 情形 | 行为 |
|---|---|
| 必要成员超限（`container.xml` / OPF / `OFD.xml` / 文档根 / `content.xml`） | 整份**降级**——残缺 XML 不能截断着用 |
| 已有章节/页再超限 | **截断**后续，名字进 `limits_hit`（`max_zip_uncompressed_bytes`） |

**优点（新人读模块时对照）**：内存峰值可预期；比只限下载更贴威胁模型；比整份 `read()` 更安全；与 `limits.py` 报告链路一致；尽量保住已读产出；三族格式不复制三份预检逻辑。

**刻意不做**：普通 zip/tar「只列目录」的路径本来就不解压成员，不走这里；Excel 等其它 ZIP 容器若要对齐，接同一工具即可，不要另写一套。

### 4.2 资源防护公共件 · 请求体早拒（`utils/body_limit.py`）

与下载层同一形状，挡的是**上传入口**：

| 原来 | 现在 |
|---|---|
| 只看「声明有多大」；没声明就整包先接下来 | 声明超限 → 直接拒；否则边收边数，到上限就停 |
| 超大请求可能先写满系统临时目录再 413 | 413 发生在框架写满临时目录之前 |

中间件在 `app.py`；额度 = `MAX_DOWNLOAD_BYTES` + 约 1MB multipart 余量。业务落盘（`prepare_from_upload`）仍按文件实字节再挡一道。生产再在反代设请求体上限（RUNBOOK「请求返回 413」）。

### 4.3 配置与写死上界表

| 配置项 | 默认 | 作用点 |
|---|---|---|
| `MAX_DOWNLOAD_BYTES` | 200MB | 下载与上传边写边校验；请求体早拒同用此量级（另加 multipart 余量） |
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
| `MAX_LINKS` | 100 条 | 网页与 docx 的链接清单 |
| `TEXT_LAYER_MIN_CHARS` | 30 字 | PDF 文字层判定**阈值**（不是上界） |
| `MAX_TEXT_BYTES` | 20MB | 文本类读取上限 |
| `MAX_ZIP_UNCOMPRESSED_BYTES` | 20MB | EPUB / OFD / ODT·ODP 的 ZIP 解压字节总量（`utils/zip_read.py`） |
| `MAX_LONG_EDGE` | 1600 | 送模型前图片长边缩放目标 |
| `MAX_PDF_PAGES` / `MAX_PDF_PAGE_PIXELS` | 200 页 / 3.6e7 像素 | PDF 页数与单页渲染预算 |
| `MAX_IMAGE_PIXELS` / `MAX_OCR_SLICES` | 4e7 像素 / 40 片 | 读图像素预算与长图 OCR 切片上限 |

**哪些会出现在 `meta.limits_hit` 里**（按行为逐条，不要一刀切）：

- **`error`**：下载体积、跳转数等——失败响应本身就说清楚了，不必再进 `limits_hit`。
- **`truncate`**：少读了字节 / 行 / 页 / 张的，应上报（含 `max_text_bytes`、`llm_max_tokens` 等已接好的）。
- **`clamp` 且上报**：`max_visibility_ranges`（只钳登记段数，正文不少）；`max_long_edge`（送模型前长边缩放，`encode_image` 回传命中名，经 `EnhanceOutcome.limits_hit` 并进 meta）。
- **`clamp` 且当前不上报**：`max_repeated_slots`（ODF 补位次数，登记在表里备查）。

加新上界时：登记进 `LIMITS` 的同时接上报告链路（或在本名单里写明「故意不报告」）。

`.env` 变量与全部取值见 [RUNBOOK.md](./RUNBOOK.md) 的配置项全表，写死的上界见紧随其后的模块内常量表，判定见 [adr.md](./adr.md)。
