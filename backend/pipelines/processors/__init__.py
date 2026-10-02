# -*- coding: utf-8 -*-
"""
各格式的处理器实现：一种格式一个文件，文件名一眼看懂（`excel.py` / `html.py` / `docx.py` …）。

约定（ADR 0002/0007）：
- `extract_<格式>(path, ctx)` 只做确定性的事，不调模型、不感知调用方，两条路共用同一份，
  它是 `content` 的唯一来源；
- 需要模型处理时在同一个文件里加 `enhance_<格式>(...)`，只能在抽取产出之上追加，
  **不得改动 `content`**。

两个入口在步骤文件里：抽取与增强的分派都在 `pipelines/steps/step4_extract.py`；
`reading.py` 是各格式共用的读素材实现，`schema.py` 是输入与产出的结构。
"""
