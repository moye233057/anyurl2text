# services · 仓内第三方侧车

独立进程，不进核心 `uv` 环境。`backend/extensions/` 只放 HTTP 客户端。

每个实现保持**最小文件集**（能跑即可，不做完整子项目分层）：

```text
services/<专栏>/<实现名>/
  main.py              # HTTP：/health + 本能力 *-b64
  engine.py            # 权重加载与推理
  download_models.py   # 可选：拉权重
  requirements.txt
  README.md
  models/              # 权重（不入库；可留 README.md）
```

| 能力 | 实现 | 端口 | 核心 URL |
|---|---|---|---|
| OCR | `ocr/paddle-ocr` | 8021 | `OCR_API_URL` |
| ASR | `asr/qwen3-asr` | 8022 | `ASR_API_URL` |

同一专栏可挂多套实现；**一套实现一个端口**。仓库根 `start.py` 在权重就绪时可拉起上表默认两套。
