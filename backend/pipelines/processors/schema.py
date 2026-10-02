# -*- coding: utf-8 -*-
"""
处理器层的输入与产出契约。

输入：`PreparedSource`——已落盘、类型已知的源（步骤 1-3 的产出，见 `pipelines/steps/`）。
产出：`extract_*` 返回 `ExtractResult`（交付文本 + 素材清单），`enhance_*` 返回 `EnhanceOutcome`。

- `ExtractResult.content` 是**唯一来源**：不调模型、也不感知调用方是不是 understand
  （ADR 0002/0007：同一格式的确定性产出只有这一份实现）。
- `EnhanceOutcome` 的三项只能追加：模型产出**永远不碰 `content`**——这是「两条路逐字节可对比」
  的前提，`scripts/check_structure.py` 会查。
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from routers.convert.schema import AudioMeta, Detector, ImageMeta

@dataclass
class PreparedSource:
    """已落盘、类型已知的源：步骤 1-3 的产出，也是处理器层的输入。"""

    path: Path
    source: str
    file_name: str
    mime: Optional[str]
    size_bytes: int
    ext: str
    detector: Detector
    page_url: str

@dataclass
class ExtractContext:
    """
    处理器需要的运行上下文。

    属性：
        work_dir — 落盘素材的临时目录，随请求清理。
        prepare_assets — 是否准备可供模型处理的素材（内嵌图、扫描件页面图）。
            它是能力开关而不是调用方身份：抽取路径传 False 省掉无用的渲染开销，
            understand 传 True。两者产出的 content 不受它影响。
    """

    work_dir: Path
    prepare_assets: bool = False

@dataclass
class EmbeddedAsset:
    """
    抽取过程中落盘的、可供模型处理的素材。

    属性：
        kind — "image" 表示文档内嵌图，"page" 表示无文字层文档渲染出的整页图，
            "audio_track" 表示从音频/视频里抽出的音轨（视频要先抽音轨才能送语音识别）。
        label — 供日志与 notes 的短标签，如「第3页」「内嵌图2」。
        path — 素材文件路径，位于本次请求的临时目录，随请求一起清理。
        order — 在源里的出现顺序；模型结果按它拼回正文，勿依赖完成顺序。
    """

    kind: str
    label: str
    path: Path
    order: int

@dataclass
class ExtractResult:
    """一个处理器的确定性产出。字段与响应契约的 meta 对齐，由管线合并。"""

    content: str = ""
    engines: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    assets: List[EmbeddedAsset] = field(default_factory=list)

    pages: Optional[int] = None
    pages_used: Optional[int] = None
    images: Optional[int] = None
    images_used: Optional[int] = None
    entry_count: Optional[int] = None
    image_meta: Optional[ImageMeta] = None
    audio_meta: Optional[AudioMeta] = None

    degraded: bool = False
    degrade_reason: Optional[str] = None

@dataclass
class EnhanceOutcome:
    """
    一次模型增强的产出（ADR 0007）。

    属性：
        ocr_text — 模型从图像里读出的文字；只有它进 `meta.ocr_text`。
        asr_text — 本机语音识别服务从音轨转写出的文字；只有它进 `meta.asr_text`。
        description — 独立图片的客观描述；图里没有文字时它就是摘要的来源。
        notes — 过程说明（几页判为空白、几页重试过），由管线放进 `meta.notes`。
    四项都不碰 content：content 只在 `extract_*` 里产生，这是两条路可对比的前提。
    """

    ocr_text: Optional[str] = None
    asr_text: Optional[str] = None
    description: Optional[str] = None
    notes: List[str] = field(default_factory=list)
