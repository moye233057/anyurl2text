// 与后端 routers/convert/schema.py 一一对应的类型定义。
// 契约只有一处事实源在 Python 侧；这里保持同名字段，改动时两边一起改。

export type Modality =
  | 'document'
  | 'image'
  | 'audio'
  | 'video'
  | 'webpage'
  | 'archive'
  | 'install_package'
  | 'file'
  | 'error'

export interface ImageMeta {
  width?: number | null
  height?: number | null
  mode?: string | null
  format?: string | null
  frames?: number | null
  dpi?: string | null
  exif: Record<string, string>
}

export interface AudioMeta {
  duration_seconds?: number | null
  sample_rate?: number | null
  channels?: number | null
  /** 音轨容器格式（如 WAV / MP3）；视频报的是它音轨的格式，未必等于源文件扩展名 */
  format?: string | null
}

export interface Meta {
  source: string
  ext?: string | null
  mime?: string | null
  size_bytes: number
  detector: string
  degraded: boolean
  degrade_reason?: string | null
  /** 降级的稳定机器名，见后端 configs/errors.py 的 DEGRADE_CODES；与 degrade_reason 分工同 error 的 code / message */
  degrade_code?: string | null
  /** 模型阶段失败的结构化说明；此时抽取产出仍然交付、error 仍为 null */
  model_error?: ErrorInfo | null
  truncated: boolean
  /** 本次命中的上界名，见后端 configs/limits.py；等价关系：truncated 等价于含 max_content_chars */
  limits_hit: string[]
  pages?: number | null
  pages_used?: number | null
  images?: number | null
  images_used?: number | null
  entry_count?: number | null
  image?: ImageMeta | null
  audio?: AudioMeta | null
  ocr_text?: string | null
  asr_text?: string | null
  engine: string[]
  notes: string[]
}

export interface Usage {
  model: string
  calls: number
  prompt_tokens: number
  cached_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cost_cny: number
  price_effective_from?: string | null
}

export interface Timings {
  download_ms: number
  parse_ms: number
  model_ms: number
  total_ms: number
}

export interface ErrorInfo {
  /** 稳定错误码，见后端 configs/errors.py；是契约的一部分，不随文案改 */
  code: string
  /** 面向使用者的说明，措辞可能随时变 */
  message: string
  /** 出错的是哪个入参或字段；无明确归属时为 null */
  param?: string | null
}

export interface ConvertResponse {
  type: Modality
  content: string
  summary?: string | null
  meta: Meta
  usage?: Usage | null
  timings: Timings
  error?: ErrorInfo | null
}

export interface Health {
  status: string
  model: string
  model_key_configured: boolean
  thinking_enabled: boolean
  rendered_page_ocr: { enabled: boolean; max_pages: number }
  ocr_service: { url: string; timeout_sec: number }
  asr_service: { url: string; timeout_sec: number; max_audio_seconds: number }
  /** 抽音轨用的 ffmpeg 由 imageio-ffmpeg 提供；false 表示音频/视频这一类降级 */
  ffmpeg_available: boolean
  allow_private_hosts: boolean
  max_download_bytes: number
  max_content_chars: number
}
