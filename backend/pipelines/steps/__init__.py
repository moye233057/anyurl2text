# -*- coding: utf-8 -*-
"""处理流程的步骤：按序号排，读代码就按序号读。

```
step1_download  下载 / 上传落盘（含四条下载安全基线）      两条路共用
step2_detect    类型识别（文件头 → 文件名扩展名 → Content-Type）  两条路共用
step3_prepare   补后缀、组装 PreparedSource、请求结束清理   两条路共用
step4_extract   按格式跑抽取器（content 的唯一来源）+ 模型增强入口  两条路共用
step5_enhance   模型增强 + 摘要                            只有 understand 走
step6_assemble  组装响应（两条路同一份形状）                两条路共用
```

两条管线各自只做「按顺序调这几步」：`pipelines/extract.py`、`pipelines/understand.py`。
步骤之间通过 `PreparedSource` / `ExtractResult` / `EnhanceOutcome` 传递，类型都在 `pipelines/processors/schema.py`；
各格式的实现也在 `pipelines/processors/` 下。
"""
