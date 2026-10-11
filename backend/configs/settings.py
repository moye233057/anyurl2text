# -*- coding: utf-8 -*-
"""
anyurl2text 运行配置。

使用前注意事项：
- 所有可变项从 `.env` 读取；`app.py` 启动时加载一次，模块内不再重复 load_dotenv。
- 必需的密钥（DEEPSEEK_API_KEY）只在调用模型时才校验，缺失时给明确报错，
  不在 import 期抛 KeyError（否则整个服务起不来，接口一也被连坐）。
- 单价表带生效日期：DeepSeek 调价频繁，改价时新增一条记录而不是改旧值，
  历史调用才能按当时单价复核。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
# 自建样例与调试产物都放在服务目录下（backend/data），与 backend/scripts 的生成脚本同级。
DATA_DIR = BASE_DIR / "data"
DEBUG_DIR = DATA_DIR / "debug"
# 提示词模板目录：模型调用处直接读文件，不做一层 load_prompt 之类的包装
PROMPTS_DIR = BASE_DIR / "extensions" / "prompts"

# ---------- HTTP 服务 ----------
API_HOST = os.getenv("ANYURL2TEXT_HOST", "127.0.0.1")
API_PORT = int(os.getenv("ANYURL2TEXT_PORT", "8020"))
# 自动重载：改完后端 .py 立即生效，省掉手动重启。uvicorn 的 reload 会额外起一个监督进程
# （父进程只负责看文件、子进程才是真服务）；本机自用默认打开，不想多这个进程就设 false。
RELOAD = os.getenv("RELOAD", "true").strip().lower() in ("1", "true", "yes", "on")

# ---------- 请求生命周期 ----------
# 全链路预算（下载 + 解析 + 模型）；任一步超时即终止该请求。
REQUEST_BUDGET_SEC = float(os.getenv("REQUEST_BUDGET_SEC", "120"))
# 下载层单独上限，且不超过全链路剩余时间。
DOWNLOAD_BUDGET_SEC = float(os.getenv("DOWNLOAD_BUDGET_SEC", "20"))
# 并发：文档/表格等快池；独立图片与音视频慢池（满员时路由返回 429）。
CONCURRENCY_FAST = int(os.getenv("CONCURRENCY_FAST", "32"))
CONCURRENCY_MEDIA = int(os.getenv("CONCURRENCY_MEDIA", "8"))
# DeepSeek 出口同时在途调用上限（进程内信号量）。默认很宽：本机自用几乎碰不到；
# 只有部署到极大请求量时才需要调低。OCR/ASR 是本机辅服务，只复用 httpx、不加紧闸。
EGRESS_LLM_CONCURRENCY = int(os.getenv("EGRESS_LLM_CONCURRENCY", "64"))

# ---------- 下载层 ----------
CONNECT_TIMEOUT_SEC = float(os.getenv("CONNECT_TIMEOUT_SEC", "10"))
READ_TIMEOUT_SEC = float(os.getenv("READ_TIMEOUT_SEC", "30"))
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", str(200 * 1024 * 1024)))
MAX_REDIRECTS = int(os.getenv("MAX_REDIRECTS", "5"))
DOWNLOAD_CHUNK_BYTES = 65536

# SSRF 防护开关。默认拒绝回环/内网/链路本地地址；置 true 仅供本机串本地文件服务调试。
# 不要在任何对外可访问的部署里打开。
ALLOW_PRIVATE_HOSTS = os.getenv("ALLOW_PRIVATE_HOSTS", "false").strip().lower() in ("1", "true", "yes", "on")

# 是否接受 http:// 源。默认 true = http 与 https 都收，与既有行为完全一致；
# 置 false 后 http:// 在发起连接之前就被 422 拒掉（码 `download_http_not_allowed`），只收 https。
ALLOW_HTTP_SOURCE = os.getenv("ALLOW_HTTP_SOURCE", "true").strip().lower() in ("1", "true", "yes", "on")

USER_AGENT = os.getenv(
    "ANYURL2TEXT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
)

# ---------- 文本抽取 ----------
# content 的字符上限；超出时截断并在 meta.truncated 声明。取值可配，不写死在代码里。
MAX_CONTENT_CHARS = int(os.getenv("MAX_CONTENT_CHARS", "200000"))
# 送进模型的文本上限：比 content 上限更低，避免一次摘要把成本顶上去。
# 触发时会在 meta.notes 里声明截断，不静默。
MAX_MODEL_INPUT_CHARS = int(os.getenv("MAX_MODEL_INPUT_CHARS", "120000"))
# 单次抽取里 OCR 文档内嵌图的最大张数（仅 understand 生效）。
MAX_EMBEDDED_IMAGES = int(os.getenv("MAX_EMBEDDED_IMAGES", "3"))
# 扫描件 PDF 逐页渲染的上限；0 表示关闭该能力。
RENDERED_PAGE_OCR_ENABLED = os.getenv("RENDERED_PAGE_OCR_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
RENDERED_PAGE_MAX = int(os.getenv("RENDERED_PAGE_MAX", "10"))
# 页面渲染 DPI：越高越清晰，但视觉模型会把图缩到约 1300x1300 总像素，过高无收益。
RENDERED_PAGE_DPI = int(os.getenv("RENDERED_PAGE_DPI", "150"))
# 整页识别结果短于该字符数时，判定为「没认出来」，触发切半重试。
RENDERED_PAGE_RETRY_MIN_CHARS = int(os.getenv("RENDERED_PAGE_RETRY_MIN_CHARS", "20"))

# ---------- 模型 ----------
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 现行模型名：deepseek-flash 一个模型同时支持文本与图片输入。
# 旧的 deepseek-v4-flash / deepseek-v4-flash-vision-exp 已下线，仅作兼容路由，不要使用。
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
LLM_TIMEOUT_SEC = float(os.getenv("LLM_TIMEOUT_SEC", "180"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
# 单次模型调用输出上限。必须显式给：官方默认非思考模式只有 8K，长文摘要会被静默截断。
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))
# 思考模式默认开启且 effort 默认 high，思维链按输出侧计费，所以默认显式关闭。
THINKING_ENABLED = os.getenv("THINKING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
THINKING_EFFORT = os.getenv("THINKING_EFFORT", "high")

# ---------- 扩展提供商的选择 ----------
# 能力 → 提供商的静态名单在 configs/extensions.py；这里只放「默认选谁」与探活超时。
# 留空 = 用名单里声明的默认（is_default=True 的那一个）；请求里传 true 时用这里的值。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "")
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "")
# 侧车探活超时（秒）：本机 /health 是毫秒级响应，0.5 秒足够。
# 不能给大——/probe/health 有**硬性时限**：start.py 的就绪探测是 urlopen(timeout=2)，
# 健康检查慢过 2 秒就会被判成「后端没起」（2026-10-11 踩到：4 秒的健康检查让 start.py 90 秒后放弃）。
EXTENSION_PROBE_TIMEOUT_SEC = float(os.getenv("EXTENSION_PROBE_TIMEOUT_SEC", "0.5"))
# 探活结果的缓存秒数：/probe/health 会被就绪探测与前端反复调用，每次真探一遍既慢又吵。
# 缓存到这一刻；用户点「刷新可用性」时带 refresh 强制重探。
EXTENSION_PROBE_CACHE_SEC = float(os.getenv("EXTENSION_PROBE_CACHE_SEC", "10"))

# ---------- 本机 OCR 服务 ----------
# 文字转写选了 paddle 时交给它读字（出口见 extensions/paddle_ocr.py）；选 deepseek 则由模型读。
# 服务不计 token，usage 只反映 DeepSeek 调用；服务起不来时那一张图的增强会失败并写进 meta.notes。
OCR_API_URL = os.getenv("OCR_API_URL", "http://127.0.0.1:8021/ocr-b64")
OCR_TIMEOUT_SEC = float(os.getenv("OCR_TIMEOUT_SEC", "60"))

# ---------- 本机语音识别服务 ----------
# 音频与视频的音轨交给它转写（出口见 extensions/qwen3_asr.py）。
# 与 OCR 同一套约定：不计 token、服务起不来只影响这一类源的增强并写进 meta.notes。
ASR_API_URL = os.getenv("ASR_API_URL", "http://127.0.0.1:8022/asr-b64")
ASR_TIMEOUT_SEC = float(os.getenv("ASR_TIMEOUT_SEC", "300"))
# 送转写的音轨时长上限（秒）；超过就跳过转写并在 meta.notes 里说明。
# 设它是为了不静默截断：ASR 服务单次输出有 token 上限，长音轨的尾部会被悄悄丢掉。
ASR_MAX_AUDIO_SECONDS = float(os.getenv("ASR_MAX_AUDIO_SECONDS", "600"))
# 抽音轨的超时（秒）；转码是本地 CPU 活，与模型超时分开计。
FFMPEG_TIMEOUT_SEC = float(os.getenv("FFMPEG_TIMEOUT_SEC", "120"))

# ffmpeg 二进制路径：由 imageio-ffmpeg 自带，不依赖系统安装。
# 在这里解析一次是因为它既是配置也是运行时能力：缺失时音视频那一类降级，其余格式不受影响；
# 放在 configs 而不是 utils，是为了让 HTTP 层（探针）能合法地报出这项能力。
try:  # pragma: no cover - 取决于部署环境
    import imageio_ffmpeg as _imageio_ffmpeg

    FFMPEG_EXE = _imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = None

# ---------- 调试 ----------
# 打开后把中间产物（下载原文件、渲染出的页面图）落到 backend/data/debug/。默认关闭。
DEBUG_ARTIFACTS = os.getenv("DEBUG_ARTIFACTS", "false").strip().lower() in ("1", "true", "yes", "on")

# DeepSeek 单价表与第三方成本闸在 extensions/deepseek.py（一种模型一个文件）。
