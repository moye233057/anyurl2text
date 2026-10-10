# 扩展权重目录

本目录存放 OCR / ASR 等扩展的**模型权重文件**（体积大，已由仓库根 `.gitignore` 忽略除本 README 外的内容）。

```text
backend/extensions/models/
  ├── …paddle OCR 权重…
  └── Qwen3-ASR-1.7B/    # 示例
```

下载与目录细则见仓库根 `optional/ocr`、`optional/asr` 的 README。  
**不要**在本目录放 Python 包（勿加 `__init__.py`），以免与 `extensions` 包内的模块导入冲突。
