# -*- coding: utf-8 -*-
"""
转换接口的 HTTP 契约主体（ConvertResponse / Meta / Usage 等）。

落在 `utils/` 是为了让 `pipelines/` 组装响应时不必 import `routers`（依赖单向，ADR-0010）。
`routers/convert/schema.py` 再导出本模块，OpenAPI 与旧 import 路径仍可用。
"""
from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from configs.extensions import CAPABILITY_OCR, normalize_choice
from utils.processor_schema import AudioMeta, Detector, ErrorInfo, ImageMeta


class ConvertUrlBody(BaseModel):
    """URL 入参。"""

    url: str = Field(..., description="待处理的 http/https 地址")
    ocr: Union[bool, str] = Field(
        False,
        description="文字转写用哪个 OCR 提供商：false 不用、true 用默认，或直接给 id（deepseek / paddle）。"
        "同一请求只能选一个。看图（判型、语义描述、扫描页、内嵌图）只要 llm=true 就会跑，不看这一项。",
    )
    asr: bool = Field(False, description="启用音视频转写")
    llm: bool = Field(False, description="启用 LLM：摘要 / query，以及看图支路（判型、描述、扫描页、内嵌图）")
    query: Optional[str] = Field(
        None,
        description="可选问题。仅 llm=true 时消费：有则针对性回答，无则通用摘要。",
    )

    @field_validator("ocr")
    @classmethod
    def _check_ocr(cls, value: Union[bool, str]) -> Union[bool, str]:
        """名单只在 `configs/extensions.py` 一处；非法名字抛 ValueError，由 FastAPI 转成 422。"""
        return normalize_choice(CAPABILITY_OCR, value)


class Modality(str, Enum):
    """内容的模态。只回答「这是什么」，不回答「用什么做的」。"""

    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    WEBPAGE = "webpage"
    ARCHIVE = "archive"
    INSTALL_PACKAGE = "install_package"
    FILE = "file"
    ERROR = "error"


class Meta(BaseModel):
    """机器可读的抽取现场信息。"""

    source: str = Field(..., description="原始输入：URL 或上传文件名")
    ext: Optional[str] = Field(None, description="识别出的扩展名，不含点")
    mime: Optional[str] = None
    size_bytes: int = 0
    detector: Detector = Detector.UNKNOWN
    degraded: bool = Field(False, description="是否走了降级产出（基础文件信息）")
    degrade_reason: Optional[str] = None
    degrade_code: Optional[str] = Field(
        None, description="降级的稳定机器名（configs/errors.py 的 DEGRADE_CODES）；未降级为 null"
    )
    model_error: Optional[ErrorInfo] = Field(
        None,
        description="模型阶段失败的结构化说明（code 取自 configs/errors.py 的 MODEL_FAILURE_CODES）；"
        "此时确定性抽取产出仍然交付，HTTP 仍 200、error 仍为 null",
    )
    truncated: bool = Field(False, description="content 是否被字符上限截断")

    limits_hit: List[str] = Field(
        default_factory=list,
        description="本次命中的上界名，取自 configs/limits.py 的 LIMIT_NAMES；"
        "命中说明同时按模板写进 notes。truncated 等价于命中 max_content_chars。",
    )

    pages: Optional[int] = Field(None, description="文档总页数")
    pages_used: Optional[int] = Field(None, description="实际处理的页数")
    unprocessed_pages: List[int] = Field(
        default_factory=list,
        description="没处理到的页码（1 基，升序）；今天由 PDF 的整页渲染上限产生，"
        "命中原因名在 limits_hit（rendered_page_max）",
    )
    images: Optional[int] = Field(None, description="文档内嵌图总数")
    images_used: Optional[int] = Field(None, description="实际处理的内嵌图数")
    image: Optional[ImageMeta] = Field(None, description="源本身是图片时填充")
    audio: Optional[AudioMeta] = Field(
        None, description="源是音频或视频时填充：报的是音轨的参数"
    )
    entry_count: Optional[int] = Field(None, description="压缩包或安装包内的条目数")
    table_header_rows: Optional[int] = Field(
        None,
        description="交付文本里**唯一**一张表中，源自己标出的表头行数（网页 / EPUB 的 <th> 与 <thead>、"
        "docx 的 w:tblHeader、ODF 的 table:table-header-rows）。"
        "没有表、有多张表、或该格式的源不标表头时为 null——与 0 区别开：0 表示查过、源明确没标表头。"
        "不做「首行即表头」这类启发式（猜错会吃掉一行数据）。",
    )
    table_kind: Optional[str] = Field(
        None,
        description="交付文本里**唯一**一张表的类型，取值 data / layout，口径照 anydoc 的 TableKind："
        "单格表（1×1）算 layout——它通常是文本框里当容器用的排版壳，不是数据表；其余算 data。"
        "没有表、有多张表时为 null。**不做**「数据表 vs 印刷目录表」这类推断（猜错会误导按它分支的调用方）。",
    )
    ocr_text: Optional[str] = Field(
        None,
        description="从图像读出的文字（独立图片、文档内嵌图、扫描件页面）；"
        "llm=true 时并进 content（ADR-0011/0014）。文字转写用哪个提供商由请求的 ocr 决定。",
    )
    asr_text: Optional[str] = Field(
        None,
        description="本机 ASR 从音轨转写的文字；asr=true 且跑过时有值，并进 content（ADR-0011/0014）。",
    )

    engine: List[str] = Field(default_factory=list, description="实际用到的解析器或模型")
    notes: List[str] = Field(default_factory=list, description="给使用者看的现场说明")


class Usage(BaseModel):
    """本次调用真实的模型用量与折算成本，按模型分别记账。"""

    model: str
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_cny: float = 0.0
    price_effective_from: Optional[str] = None


class Timings(BaseModel):
    """分段墙钟，用于区分「慢在网络上」与「慢在模型上」。"""

    download_ms: int = 0
    parse_ms: int = 0
    model_ms: int = 0
    total_ms: int = 0


class ConvertResponse(BaseModel):
    """转换接口统一的响应体。"""

    type: Modality
    content: str = ""
    summary: Optional[str] = None
    meta: Meta
    usage: Optional[Usage] = None
    timings: Timings
    error: Optional[ErrorInfo] = Field(
        None, description="恒存在；成功与降级都是 null，失败时是 {code, message, param}"
    )


def truncate_content(text: str, limit: int) -> tuple:
    """
    按字符上限截断抽取文本。

    功能：统一截断规则，阈值只有一处定义。
    应用场景：build_response 组装响应时调用一次——处理器不管截断，免得每加一个格式就多一份阈值。
    输入：text — 抽取结果；limit — 字符上限（<=0 表示不限制）。
    输出：(文本, 是否发生截断)，调用方要把截断事实写进 meta.truncated。
    """
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True
