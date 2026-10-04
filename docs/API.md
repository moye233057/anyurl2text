# 接口与响应契约

> **用途**：两个接口怎么调、响应每个字段是什么意思、失败长什么样。
> **关联**：[architecture.md](./architecture.md)（模块与落点） · [adr.md](./adr.md)（为什么这样定） · [CONTEXT.md](../CONTEXT.md)（术语表）
> **基线**：2026-10-02

---

## 1. 端点

| 方法 | 路径 | 入参 | 说明 |
|---|---|---|---|
| POST | `/extract` | JSON `{url, query?}` | 纯代码解析 |
| POST | `/extract/file` | multipart：`file`、`query?` | 同上，改成上传文件 |
| POST | `/understand` | JSON `{url, query?}` | 模型增强解析 |
| POST | `/understand/file` | multipart：`file`、`query?` | 同上，改成上传文件 |
| GET | `/probe/health` | — | 自身配置自检，不探测上游 |
| POST | `/rag/extract/file` | multipart：`file` | 检索入库用。契约见 §11，不进 ConvertResponse |

`/extract` 与 `/extract/file` 除了入参方式**完全等价**，`/understand` 同理。

启动后交互式文档在 `http://127.0.0.1:8020/docs`。

### 请求字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `url` | 是（JSON 版） | `http` / `https` 地址。含 `+`、协议非 http(s)、无主机名都会被拒 |
| `file` | 是（上传版） | 文件本体；识别以**文件名 + 内容头**为准 |
| `query` | 否 | 只有模型增强解析消费它。带 `query` 做针对性回答；不带则给通用摘要 |

`query` 为 `null` 或空串都按「没有 query」处理（后端只判真值，不做字符串归一化）——空串无法表达「要摘要但不要具体问题」。因此**不要传字符串 `"None"` / `"null"`**：它们是真值，会被当成一个真正的问题交给模型。

## 2. 响应结构

一份响应永远有这 7 个字段，形状不随成功失败变化：

```jsonc
{
  "type": "image",          // 见 §3
  "content": "",            // 见 §4
  "summary": "…",           // 见 §5
  "meta": { … },            // 见 §6
  "usage": { … },           // 见 §7；纯代码解析恒为 null
  "timings": { … },         // 见 §8
  "error": null             // 恒存在；成功为 null
}
```

## 3. `type`：内容是什么

取值：`document` / `image` / `audio` / `video` / `webpage` / `archive` / `install_package` / `file` / `error`。

`type` 只回答「这是什么」这一件事，**不回答「用什么做的」**——引擎与策略一律进 `meta`。所以同一张图不会因为内部换了识别引擎而改变 `type`。

`file` 表示「认得出它是个文件，但没有可用于它的解析路径」，此时配合 `meta.degraded` 看降级原因。`error` 表示这次没成，看 `error` 字段。

## 4. `content`：交付文本

从源抽取出的文本本身。**任何接口、任何参数下都不被覆盖，也不追加实现细节前缀。**

这条约束是可验证的：同一个源分别调 `/extract` 与 `/understand`，两次的 `content` 逐字节相同。图片、音视频这类没有文本的源，`content` 就是空串。

## 5. `summary`：模型加工结果

只有模型增强解析会产出。与 `content` 并存，两者互不影响。

- 带 `query`：针对该 query 的回答，依据只取自文本，文本里没有的会说「给定内容中没有这一信息」。
- 不带 `query`：通用内容摘要；源本身是图片时，判为语义图片就直接用图像的客观描述，判为文字图片则据读出的文字给摘要。

模型阶段没产出可用内容时 `summary` 为 `null`，并在 `meta.notes` 里说明原因。

## 6. `meta`：机器可读的现场信息

| 字段 | 何时有值 | 含义 |
|---|---|---|
| `source` | 总是 | 原始输入：URL 或上传时的文件名 |
| `ext` | 识别成功时 | 识别出的扩展名（不含点） |
| `mime` | 从 URL 下载时 | 响应头里的 Content-Type |
| `size_bytes` | 总是 | 实际落盘字节数 |
| `detector` | 总是 | 靠什么认出来的：`extension` / `content_type` / `magic` / `unknown` |
| `degraded` | 总是 | 是否走了「基础文件信息」降级产出 |
| `degrade_reason` | 降级时 | 缺的是什么依赖 |
| `truncated` | 总是 | `content` 是否被字符上限截断。等价于 `limits_hit` 里含 `max_content_chars` |
| `limits_hit` | 总是 | 本次命中的上界名，取自 `configs/limits.py` 的 `LIMIT_NAMES`（如 `max_links`、`max_rows_per_sheet`、`max_embedded_images`）；同一句命中说明按表里的模板进 `notes` |
| `pages` / `pages_used` | 文档类 | 总页数与实际处理页数；扫描件超出渲染上限时两者不等 |
| `images` / `images_used` | 文档类 | 内嵌图**引用次数**与实际落盘处理数；同一张图被引多次只处理一次（按来源部件去重），所以源里有重复引用时两者会不等 |
| `image` | 源本身是图片 | 尺寸 / 格式 / 色彩模式 / 帧数 / 常用 EXIF |
| `audio` | 源是音频或视频 | 音轨参数：时长 / 采样率 / 声道 / 音轨格式。视频也填这一块——容器里能确定性读到的只有音轨 |
| `entry_count` | 压缩包、安装包 | 容器内条目数 |
| `ocr_text` | 模型读出了文字 | 识别出的文字：独立图片由本机 Paddle OCR 读出，文档内嵌图与扫描页由视觉模型读出；**与 `content` 分开存放** |
| `asr_text` | 转写出了文字 | 本机 Qwen3-ASR 从音频/视频音轨转写出的文字，只在 `/understand` 那一路有值；同理**与 `content` 分开存放** |
| `engine` | 总是 | 实际用到的解析器或模型 |
| `notes` | 有需要说明时 | 现场说明：截断、跳过、降级、空白页等 |

## 7. `usage`：真实用量与折算成本

纯代码解析恒为 `null`——没调模型就没有用量，给 0 是噪音。

| 字段 | 含义 |
|---|---|
| `model` | 模型名 |
| `calls` | 本次请求的调用次数 |
| `prompt_tokens` / `cached_tokens` / `completion_tokens` / `reasoning_tokens` | 真实 token，缓存命中单独记 |
| `cost_cny` | 按单价折算的人民币金额 |
| `price_effective_from` | 所用单价表的生效日 |

成本按「缓存命中 / 未命中 / 输出」三档分别计价，并区分峰谷时段，所以不要把 `prompt_tokens` 乘一个单价去核对。

本机 Paddle OCR 不走 token 计费，也不产生 DeepSeek 账单，所以 `usage` 只反映 DeepSeek 调用：独立图片走 OCR 那一支也只算判型这一次。
本机 Qwen3-ASR 同理，不计 token：音频/视频那一类只有摘要那一次 DeepSeek 调用计入 `usage`。

## 8. `timings`：分段墙钟

`download_ms` / `parse_ms` / `model_ms` / `total_ms`。用来区分「慢在网络上」和「慢在模型上」。

## 9. 状态码

| 码 | 什么时候 |
|---|---|
| 200 | 成功，**以及降级**（降级是正常结果） |
| 413 | 请求体或下载内容超过上限 |
| 422 | 入参问题：URL 为空、协议不是 http(s)、缺主机名、命中内网或保留地址 |
| 502 | 上游失败：HTTP 错误码、域名解析失败、重定向异常、网络错误 |
| 504 | 下载超时 |
| 500 | 未预期异常：`/rag/extract/file` 的兜底分支（见 §11），convert 两个接口不走这条 |

**响应体始终带 `error` 字段**，所以「只看状态码」和「只看 body」两种消费方式都能用。

## 10. 两个真实样例

### 图片（模型增强解析）

图片类是最能看出两条路差别的一类：纯代码那一路 `content` 为空。模型那一路先判型——下面这张赛事海报文字密集，判为**文字图片**，`meta.ocr_text` 是本机 Paddle OCR 读出的全文（实测 185 字），`summary` 据它生成；照片、插画那类判为**语义图片**，产出是模型给的客观描述、`meta.ocr_text` 为 `null`。（这张海报就在界面左栏的预设样例里，点一下即可复现。）

```jsonc
// POST /understand  { "url": "https://…/poster.jpg" }
{
  "type": "image",
  "content": "",                     // 图片没有可抽取的文本
  "summary": "2026 年 5 月 14—17 日举行的中国商学院 EMBA 足球联合会第六届全国邀请赛宣传海报……",
  "meta": {
    "source": "https://…/poster.jpg",
    "ext": "jpg",
    "mime": "image/jpeg",
    "size_bytes": 213094,            // 界面显示 208.1 KB
    "detector": "magic",
    "degraded": false,
    "degrade_reason": null,
    "truncated": false,
    "image": { "width": 1440, "height": 810, "mode": "RGB", "format": "JPEG", "exif": {} },
    "engine": ["Pillow"],
    "ocr_text": "中国商学院高级工商管理硕士（EMBA）足球联合会\n第六届全国邀请赛\n指导单位：陕西省体育局\n…（实测 185 字，此处省略）",
    "notes": ["判为文字图片，已交本机 Paddle OCR 读出 185 字"]
  },
  "usage": { "model": "deepseek-flash", "calls": 2, … },   // 判型 1 次 + 摘要 1 次；Paddle OCR 不计 token
  "timings": { "download_ms": 108, "parse_ms": 0, "model_ms": …, "total_ms": … },
  "error": null
}
```

同一份源调 `/extract` 会得到结构完全一样的响应，但 `summary` 为 `null`、`usage` 为 `null`、`timings.model_ms` 为 0，`content` 同样是空串。

音频与视频那份响应的形状与图片同理，差别只有三处：`type` 是 `audio` / `video`，结构信息在 `meta.audio`，转写文字在 `meta.asr_text`（`content` 同样为空串）。

### 表格（两条路的 `content` 相同）

```jsonc
// POST /extract 与 POST /understand 分别调一次
{
  "type": "document",
  "content": "=== SHEET: Sheet1 ===\n货品skc ID\t下架原因\t备注\n…",   // 两次完全一致，实测 882 字符
  "meta": { "ext": "csv", "detector": "extension", "engine": ["csv"], … }
}
```

纯代码那一路 `usage` 为 `null`；模型那一路给出摘要与用量（实测：模型耗时 3030 ms、缓存命中后成本约 ¥0.0032）。

## 11. RAG 抽取（独立契约）

`POST /rag/extract/file` 给检索入库用，**不**走 ConvertResponse 的 7 字段。见 [adr.md](./adr.md) 的 ADR-0009。

```jsonc
{
  "kind": "pdf",                 // pdf / 扩展名 / error
  "pages": [{ "page": 1, "text": "…" }],
  "text": null,                  // 无印刷目录时才有值
  "catalogue_ranges": [{ "title": "…", "start_page": 14, "end_page": 20 }],
  "error": null                  // 恒存在；成功为 null
}
```

PDF 抽出印刷目录：`kind=pdf`，带 `pages` 与 `catalogue_ranges`。否则：`kind` 为扩展名，带 `text`（extract 内核的交付文本，不截断、不调模型）。失败时 `kind=error` 或仍带扩展名，`error` 非空。
