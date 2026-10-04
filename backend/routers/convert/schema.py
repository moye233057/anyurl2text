# -*- coding: utf-8 -*-
"""
请求与响应契约：两个接口共用的唯一事实源（ADR 0001）。

**为什么在这一层**：请求体是这个路由的入参、响应体是它返回的东西，读接口先看它的 schema 最顺。
这一份是**纯数据**——不 import 项目内任何东西，所以下面各层（`models/`、`processors/`、
`pipelines/`、`app.py`）都可以直接拿它里面的模型类当数据结构用，不会绕出循环依赖（ADR 0007）。

约定：
- `content` 是从源抽取出的文本本身；任何接口、任何参数下都不被摘要覆盖，
  也不被追加实现细节前缀。
- `summary` 只由 understand 产出，与 content 并存。
- `error` 恒存在，成功为 None——调用方不必判两种响应形状。
- `usage` 只在真的调过模型时有值；extract 恒为 None（没调模型就没有用量，0 是噪音）。
- `meta` 承载机器可读信息（识别方式、降级原因、截断声明、引擎），不塞进 content。
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

class ConvertUrlBody(BaseModel):
    """URL 入参。"""

    url: str = Field(..., description="待处理的 http/https 地址")
    query: Optional[str] = Field(
        None,
        description="可选问题。understand 用它做针对性回答；extract 不消费它（确定性抽取没有语言加工环节）。",
    )

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

    code: str = Field(..., description="稳定错误码，见 configs/errors.py；是契约的一部分，不随文案改")
    message: str = Field(..., description="面向使用者的说明，可能随时改措辞")
    param: Optional[str] = Field(None, description="出错的是哪个入参或字段；无明确归属时为 null")

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
    images: Optional[int] = Field(None, description="文档内嵌图总数")
    images_used: Optional[int] = Field(None, description="实际处理的内嵌图数")
    image: Optional[ImageMeta] = Field(None, description="源本身是图片时填充")
    audio: Optional[AudioMeta] = Field(
        None, description="源是音频或视频时填充：报的是音轨的参数"
    )
    entry_count: Optional[int] = Field(None, description="压缩包或安装包内的条目数")
    ocr_text: Optional[str] = Field(
        None,
        description="模型从图像读出的文字（独立图片、文档内嵌图、扫描件页面）。"
        "与 content 分开存放：content 在两个接口下完全一致，模型产出不混进去。",
    )
    asr_text: Optional[str] = Field(
        None,
        description="本机语音识别服务从音频/视频的音轨转写出的文字，只在 understand 那一路有值。"
        "与 ocr_text 同理不进 content：语音转写是模型产出，不是确定性抽取。",
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
    """两个接口统一的响应体。"""

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
