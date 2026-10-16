# -*- coding: utf-8 -*-
"""
处理器层的输入与产出字段约定（ADR-0007：落在 `utils/`，与路由字段约定 `routers/convert/schema.py` 区分开）。

也放 Detector / ImageMeta / AudioMeta / ErrorInfo：它们既是 HTTP meta 碎片，也被处理器产出引用；
放在 utils 才能让本文件不依赖 routers（依赖单向：routers → utils，不是反过来）。

输入：`PreparedSource`——已落盘、类型已知的源（步骤 1-3 的产出，见 `pipelines/steps/`）。
产出：`extract_*` 返回 `ExtractResult`（交付文本 + 素材清单），`enhance_*` 返回 `EnhanceOutcome`。

- `ExtractResult.content` 是**唯一来源**：不调模型、也不感知调用方是不是 understand
  （ADR 0002/0007：同一格式的确定性产出只有这一份实现）。
- `EnhanceOutcome` 供 understand 模型阶段使用；并进响应 `content` 由 `merge_understand_delivery()` 在组装前完成（ADR-0002）。
"""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Union

from pydantic import BaseModel, Field


class Detector(str, Enum):
    """扩展名 / Content-Type / 文件头，以及都没认出来。"""

    EXTENSION = "extension"
    CONTENT_TYPE = "content_type"
    MAGIC = "magic"
    UNKNOWN = "unknown"


class ImageMeta(BaseModel):
    """图片自身的结构信息。extract 对图片的全部产出就是这些（ADR 0004）。"""

    width: Optional[int] = None
    height: Optional[int] = None
    mode: Optional[str] = None
    format: Optional[str] = None
    frames: Optional[int] = Field(None, description="动图帧数；静态图为 None")
    dpi: Optional[str] = None
    exif: dict = Field(default_factory=dict, description="只挑常用拍摄参数，不整包倾倒")


class AudioMeta(BaseModel):
    """
    音轨自身的结构信息：音频源的全部确定性产出就是这些，视频则报它音轨的参数。

    视频容器里没有可确定性抽取的东西，时长与语音都在音轨上，所以视频也填这一块
    （见 ADR 0008）：`format` 是音轨的实际容器格式，未必等于源文件的扩展名。
    """

    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    format: Optional[str] = Field(None, description="音轨容器格式，如 WAV / MP3")


class ErrorInfo(BaseModel):
    """
    一次失败或降级的结构化说明。

    `code` 是**稳定机器名**（`configs/errors.py` 的码表），给程序看、不许改；
    `message` 是给使用者看的文案，会改。`param` 指出哪个入参或字段出错（`url` / `upload`），
    没有明确归属时为 null。
    """

    code: str = Field(..., description="稳定错误码，见 configs/errors.py；是字段约定的一部分，不随文案改")
    message: str = Field(..., description="面向使用者的说明，可能随时改措辞")
    param: Optional[str] = Field(None, description="出错的是哪个入参或字段；无明确归属时为 null")


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
    """一个处理器的确定性产出。字段与响应字段约定的 meta 对齐，由处理流程合并。"""

    content: str = ""
    engines: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    assets: List[EmbeddedAsset] = field(default_factory=list)

    pages: Optional[int] = None
    pages_used: Optional[int] = None
    # 没处理到的页码（1 基，升序）。今天只有 PDF 的整页渲染上限会产生它：命中的上界名在
    # limits_hit（rendered_page_max），这里补的是「具体哪几页」——「输出缺页」必须机器读得出。
    unprocessed_pages: List[int] = field(default_factory=list)
    images: Optional[int] = None
    images_used: Optional[int] = None
    entry_count: Optional[int] = None
    # 交付文本里**唯一**一张表的表头行数，取自**源自己的标记**（网页 / EPUB 的 <th> 与 <thead>）。
    # 没有表、有多张表、或该格式的源不标表头时是 None——与 0 区别开：0 表示「查过，源明确没标」。
    # 不做「首行就是表头」这类启发式：猜错会吃掉一行数据，而本项目的取向是宁可回落也不猜。
    table_header_rows: Optional[int] = None
    # 交付文本里**唯一**一张表的类型，口径照 anydoc 的 `TableKind`：单格表是 `layout`（排版壳）、
    # 其余是 `data`。没有表、有多张表、或判不出时是 None——本仓不造「数据表 vs 印刷目录表」的
    # 启发式（anydoc 自己也没有：它的 Layout 只在单元测试里被构造过），见 `table_kind.py`。
    table_kind: Optional[str] = None
    image_meta: Optional[ImageMeta] = None
    audio_meta: Optional[AudioMeta] = None

    degraded: bool = False
    degrade_reason: Optional[str] = None
    # 降级的稳定机器名（`configs/errors.py` 的 DEGRADE_CODES）：degraded 为真时应有值。
    # 与 degrade_reason 分工同 error：code 给程序看、不许改；reason 给使用者看、会改措辞。
    degrade_code: Optional[str] = None
    # 模型阶段失败的结构化说明（`configs/errors.py` 的 MODEL_FAILURE_CODES）：由 understand 填，
    # 抽取产出仍然交付——它是一条独立通道，与 degraded 互不影响。
    model_error: Optional[ErrorInfo] = None
    # 命中的上界名（`configs/limits.py` 的 LIMIT_NAMES）：处理器只记名字、不写文案，
    # 文案由 step6_assemble 按表里的模板统一生成——改说法不必动十几个处理器。
    limits_hit: List[str] = field(default_factory=list)

@dataclass
class EnhanceOutcome:
    """
    一次模型增强的产出（ADR 0007）。

    属性：
        ocr_text — 模型从图像里读出的文字；只有它进 `meta.ocr_text`。
        asr_text — 本机语音识别服务从音轨转写出的文字；只有它进 `meta.asr_text`。
        description — 独立图片的客观描述；图里没有文字时它就是摘要的来源。
        notes — 过程说明（几页判为空白、几页重试过），由处理流程放进 `meta.notes`。
        limits_hit — 增强阶段命中的上界名（如送模型前 `max_long_edge`），由处理流程并进 `meta.limits_hit`。
    五项都不碰 content：content 只在 `extract_*` 里产生，这是两条路可对比的前提。
    """

    ocr_text: Optional[str] = None
    asr_text: Optional[str] = None
    description: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    limits_hit: List[str] = field(default_factory=list)

@dataclass
class GridSpan:
    """表格的一个 origin 格位：内容与跨度只挂在这里（跨度至少 1）。"""

    text: str = ""
    col_span: int = 1
    row_span: int = 1

@dataclass
class CoveredSlot:
    """
    被某个跨度覆盖的逻辑格子，指回覆盖它的 origin。

    它**不持有文本**——这就是「每个逻辑格位恰好出现一次」的全部含义：合并只影响坐标。
    """

    origin_row: int
    origin_col: int

# 一个逻辑格子要么是 origin 要么是被覆盖者；一张表就是一个逻辑格子矩阵（构造与渲染见 `utils/excel/grid.py`）
CellSlot = Union[GridSpan, CoveredSlot]
TableGrid = List[List[CellSlot]]
