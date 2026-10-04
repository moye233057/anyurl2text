# -*- coding: utf-8 -*-
"""
稳定错误码表。

为什么要有这个文件：错误**文案**是给使用者看的、会改；`code` 是给程序看的、**不许改**——
前端分组统计、监控告警、验收脚本都按它匹配，改一个字就是破坏性变更。所以码表机器可读，
并由一个白名单守着：不在表里的码抛不出来（`FetchError` 在构造时当场拒绝）。

命名规则：全小写 + 下划线，前缀是**模块域**（`download_` / `upload_` / `request_` …）而不是
HTTP 状态码。每条只有一个码；同一触发点不得两个码；不同触发点不共用码。

三个通道与三组码一一对应：

- 第 1 组（硬失败）→ `error.code`，HTTP 非 200，此时另两个通道必为 null；
- 第 2 组（降级）→ `meta.degrade_code`，HTTP 200（ADR-0003：降级是能力边界而不是故障）；
- 第 3 组（模型阶段失败）→ `meta.model_error.code`，HTTP 200（确定性抽取产出仍然交付）。

**第 1、3 组已落**；第 2 组目前只有音轨那几个（`FfmpegError` 与音轨读取），
各格式的解析降级随 P1-5 第 3 步补齐。每个码后面注明它的触发点，就是为了防止「同一个码两处用」。
"""

HARD_FAILURE_CODES = frozenset({
    "request_body_too_large",              # app.reject_oversized_body：按 Content-Length 提前拒（413）
    "upload_too_large",                    # step3_prepare.prepare_from_upload：上传边写边校验（413）
    "download_declared_too_large",         # step1_download._stream_to_file：Content-Length 声明超限（413）
    "download_stream_too_large",           # step1_download._stream_to_file：流式写入超限（413）
    "download_url_empty",                  # step1_download.download：URL 为空（422）
    "download_scheme_unsupported",         # step1_download._assert_safe_url：非 http/https（422）
    "download_host_missing",               # step1_download._assert_safe_url：缺主机名（422）
    "download_private_host",               # step1_download._assert_public_host：内网/保留地址（422）
    "download_dns_failed",                 # step1_download._assert_public_host：域名解析失败（502）
    "download_redirect_missing_location",  # step1_download.download：重定向缺 Location（502）
    "download_http_error",                 # step1_download.download：上游非 2xx（502）
    "download_network_error",              # step1_download.download：网络异常（502；也是默认码）
    "download_redirect_limit",             # step1_download.download：跳转次数超过上限（502）
    "download_budget_exhausted",           # step1_download._stream_to_file：请求时间预算用尽（504）
    "download_timeout",                    # step1_download.download：httpx 超时（504）
    "internal",                            # 兜底：未预期的异常；文案不回显异常文本（500）
})

# 第 2 组：降级（进 meta.degrade_code，HTTP 200）。本步先落音轨那几个，其余随第 3 步补齐。
DEGRADE_CODES = frozenset({
    "soundfile_dependency_missing",        # media.extract_media：没装 soundfile，结构信息读不出来
    "ffmpeg_dependency_missing",           # ffmpeg.extract_audio_track：没装 imageio-ffmpeg
    "ffmpeg_timeout",                      # ffmpeg.extract_audio_track：抽音轨超过 FFMPEG_TIMEOUT_SEC
    "ffmpeg_start_failed",                 # ffmpeg.extract_audio_track：ffmpeg 无法启动
    "ffmpeg_track_failed",                 # ffmpeg.extract_audio_track：源里没有音轨 / 容器损坏
    "media_track_read_failed",             # media.extract_media：抽出的音轨读不出参数
})

# 第 3 组：模型阶段失败（进 meta.model_error.code，HTTP 200）。全部由 LlmError 抛出，
# 在 understand 里一处接住——确定性抽取产出仍然交付（ADR-0002/0007 的直接推论）。
MODEL_FAILURE_CODES = frozenset({
    "llm_api_key_missing",                 # deepseek.DeepSeekClient：未配置 DEEPSEEK_API_KEY
    "llm_call_failed",                     # deepseek.chat：重试后仍失败
    "image_encode_failed",                 # images.encode_image：图片编码失败
    "ocr_input_empty",                     # paddle_ocr：输入图片为空
    "ocr_unreachable",                     # paddle_ocr：本机 OCR 服务不可达
    "ocr_bad_response",                    # paddle_ocr：响应不是 json / 不是对象（param 区分）
    "ocr_service_failed",                  # paddle_ocr：服务返回失败码
    "ocr_slice_empty",                     # paddle_ocr：长图切片后为空
    "ocr_slice_failed",                    # paddle_ocr：某一片识别失败（param 填 切片N）
    "ocr_read_image_failed",               # paddle_ocr：读图 / 读图片字节失败（param 区分）
    "asr_read_track_failed",               # qwen3_asr：读取音轨字节失败
    "asr_input_empty",                     # qwen3_asr：输入音轨为空
    "asr_unreachable",                     # qwen3_asr：本机 ASR 服务不可达
    "asr_bad_response",                    # qwen3_asr：响应不是 json / 不是对象（param 区分）
    "asr_service_failed",                  # qwen3_asr：服务返回失败码
})
