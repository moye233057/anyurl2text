# 接口与响应字段约定

> **用途**：怎么调、响应每个字段是什么意思、失败长什么样。  
> **目标设计**：`POST /convert` + `ocr` / `asr` / `llm`（ADR-0014）；`ocr` 为提供商单选（ADR-0015）。  
> **现行**：仅 `/convert`·`/convert/file`；旧双路已删除。  
> **关联**：[architecture.md](./architecture.md) · [adr.md](./adr.md) · [CONTEXT.md](../CONTEXT.md)  
> **基线**：2026-10-02 · **目标设计修订**：2026-10-10

---

## 1. 端点

| 方法 | 路径 | 入参 | 说明 |
|---|---|---|---|
| POST | `/convert` | JSON | 统一转换 |
| POST | `/convert/file` | multipart | 同上，上传文件 |
| GET | `/probe/health` | `refresh` | 自身与扩展是否就绪；`capabilities` 给每个提供商的 `available` / `reason` / `detail`。健康检查结果缓存 `EXTENSION_PROBE_CACHE_SEC` 秒，`refresh=true` 强制重探 |
| POST | `/probe/test/{capability}/{provider}` | — | 真实测一次这个名字：本机独立服务探 `/health`，API 真发一次最小请求（花极小 token）。名字不在名单里 → 422 |

启动后交互式文档在 `http://127.0.0.1:8020/docs`。

### 请求字段

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `url` | 是（JSON） | — | `http` / `https` |
| `file` | 是（上传） | — | 文件本体 |
| `ocr` | 否 | `false` | 文字转写用哪个 OCR 提供商：`false` 不用 / `true` 用默认 / 提供商 id（`deepseek`、`paddle`）。同一请求只能选一个，见 §1.1（ADR-0015） |
| `asr` | 否 | `false` | 启用音视频转写 |
| `llm` | 否 | `false` | 启用 LLM：摘要 / query，以及看图支路（判型、语义描述、扫描页、内嵌图） |
| `query` | 否 | — | 仅 `llm=true` 时消费：有则针对性回答，无则通用摘要 |

空串 `query` 按「没有 query」处理。不要传字符串 `"None"` / `"null"`。

### 1.1 能力选择矩阵

「实际会跑」= 下表条件满足 **且** 该扩展可用（`/probe/health` 的 `capabilities` 里 `available=true`）。缺则跳过该行为，并在 `meta.notes`（或 `model_error`）说明，不静默假装成功。

`ocr` 是**提供商单选**（ADR-0015）：`false` 不用、`true` 用默认提供商、或直接给提供商 id。名单的唯一事实源是 `backend/configs/extensions.py`，当前是 `deepseek`（模型视觉）与 `paddle`（本机 8021）。

| 行为 | 需要什么 |
|---|---|
| 独立图片：判型 | `llm` |
| 独立图片：文字转写 | `ocr` 选一个提供商。选 `deepseek` 时由判型那次调用一并返回文字（省一次请求）；选 `paddle` 时交给本机独立服务，不需要 `llm` |
| 独立图片：语义描述 | `llm` |
| 扫描件整页渲染识别 | `llm` |
| 文档内嵌图转写 | `llm` |
| 音视频转写 | `asr` |
| 摘要 / query 回答 | `llm` |

看图（判型、语义描述、扫描页、内嵌图）只看 `llm`；`ocr` 只决定**独立文字图**的转写交给谁——本机独立服务与模型的视觉能力是同一能力的两家实现。

## 2. 响应结构

一份响应永远有这 7 个字段：

```jsonc
{
  "type": "image",
  "content": "",
  "summary": "…",
  "meta": { … },
  "usage": { … },
  "timings": { … },
  "error": null
}
```

## 3. `type`

取值：`document` / `image` / `audio` / `video` / `webpage` / `archive` / `install_package` / `file` / `error`。  
只回答「这是什么」，不回答用了哪家扩展。

## 4. `content`：交付文本

- **文档类**：始终可含确定性抽取段；按 §1.1 实际跑过的转写并入。
- **图片 / 音视频**：看图或 ASR 未实际跑时多为空串（基础信息在 `meta`）；跑过后转写/描述并入，并同时写入 `meta.ocr_text` / `meta.asr_text`。
- 摘要只进 `summary`，不替换 `content`。

表格格内换行写成 `<br>`（与 anydoc 口径一致）。

## 5. `summary`

仅 `llm=true` 且 LLM 可用、有可加工内容时可能非空。否则 `null`，原因进 `meta.notes`。

## 6. `meta`

沿用现行字段集（`source` / `ext` / `degraded` / `limits_hit` / `image` / `audio` / `ocr_text` / `asr_text` / `notes` 等）。  
`ocr_text` / `asr_text`：仅在对应能力实际产出时有值，并与并进 `content` 的转写一致。

## 7. `usage`

未产生计费 LLM 调用时为 `null`。本机 OCR/ASR 默认不计 token。  
LLM 可为远程 DeepSeek 或本机 OpenAI 兼容端点（同一客户端，靠 `DEEPSEEK_BASE_URL` 等配置切换）。

## 8. `timings`

`download_ms` / `parse_ms` / `model_ms` / `total_ms`。

## 9. 状态码

| 码 | 什么时候 |
|---|---|
| 200 | 成功，以及降级 |
| 413 | 请求体或下载超限 |
| 422 | 入参问题（含 SSRF） |
| 502 | 上游/下载失败 |
| 429 | 并发池满 |
| 504 | 预算用尽 |
| 500 | 未预期异常（`error.code` = `internal`） |

响应体始终带 `error` 字段。

## 10. 样例（目标设计语义）

### 图片 · `llm=true`

判为文字图片：文字由 `ocr` 选的提供商读出（模型视觉或本机 Paddle），`content` 与 `meta.ocr_text` 为该文本；若还需摘要则另开一次 LLM。  
`llm=false` 且 `ocr` 没选（或选的是需要模型的提供商）：看图支路不跑，`content` 为空，仅 `meta.image` 等基础信息。

### 表格 · 三开关皆 false

`content` 为确定性抽取正文；`summary` / `usage` 为 `null`。  
仅 `llm=true` 时同一 `content` 可附 `summary`。
