# -*- coding: utf-8 -*-
"""对 3 条固定测试音频逐条打 /asr-url 接口，打印识别文本。

使用前注意：
- 服务需先启动（默认 8022），脚本第一步就做健康检查，服务没起会立刻报错；
- 音频是远程 OSS 地址，由服务端自己下载，本地不落任何文件；
- 三条地址只用于本地联调，不要把识别结果当业务数据。
运行：conda run --no-capture-output -n asr python scripts/t_asr_url_parse.py
"""

import time

import requests

# 待测服务地址；联调其它环境时改这两行。
HEALTH_URL = "http://127.0.0.1:8022/health"
ASR_URL = "http://127.0.0.1:8022/asr-url"

# 固定待测音频：两条 mp3 + 一条 wav，覆盖不同来源与格式。
TEST_AUDIO_URLS = [
    "https://it-snail-prd.oss-cn-shenzhen.aliyuncs.com/enterprise-wechat-context/enterprise/voice/4df94aa054ead04cada4bd283ec3a271.mp3",
    "https://insnail-asr-nls.oss-cn-shenzhen.aliyuncs.com/multimodal2text/splits/f23c19aa-4dae-11f1-aace-ea3dfd60b749/temp_f40e38af-e26b-41b4-b7b9-06b9b7225977.wav",
    "https://aike-12times.sinan.top/ap/m/in/101223/chat/20260517/16/177899550621811328.mp3",
]

# 60 秒音频在 6GB 显卡上要做分块推理，超时给宽松些。
ASR_REQUEST_TIMEOUT = 600


def step0_check_service():
    """阶段0：确认服务已就绪，产出模型与设备信息，便于判断后面识别结果是否可信。"""
    response = requests.get(HEALTH_URL, timeout=30)
    response.raise_for_status()
    runtime_info = response.json()["data"]
    print(f"[阶段0] 服务就绪：{HEALTH_URL}")
    print(f"[阶段0] 运行时信息：{runtime_info}")
    return runtime_info


def step1_request_asr(audio_urls):
    """阶段1：对每条测试音频打一次 /asr-url，产出识别文本与耗时。"""
    for index, url in enumerate(audio_urls, start=1):
        audio_name = url.rsplit("/", 1)[-1]
        print(f"[阶段1 {index}/{len(audio_urls)}] {audio_name}")
        start_time = time.perf_counter()
        response = requests.post(ASR_URL, json={"url": url}, timeout=ASR_REQUEST_TIMEOUT)
        request_cost = time.perf_counter() - start_time
        # 响应里的 data 是识别文本，language 是模型判定的语种，cost 是服务端识别耗时。
        print(f"    http_status: {response.status_code}, request_cost: {request_cost:.3f}s")
        print(f"    response: {response.text}")


if __name__ == "__main__":
    # 阶段0：健康检查 → 服务运行时信息
    step0_check_service()
    # 阶段1：打 /asr-url → 识别文本
    step1_request_asr(TEST_AUDIO_URLS)
