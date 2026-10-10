import os
import time
import requests

# api_url = "http://172.20.2.3:8021/ocr-url"
api_url = "http://localhost:8080/paddle-ocr/ocr-url"

# image_url: 待 OCR 的图片地址。
# 联调时建议用可访问的 OSS/CDN 图片地址覆盖：
# OCR_IMAGE_URL="https://example.com/test.jpg" python scripts/t_ocr_url_parse.py
image_url = "https://cdn.example.com/sample/image.jpg"

def main():
    start_time = time.perf_counter()
    response = requests.post(api_url, json={"url": image_url})
    request_cost = time.perf_counter() - start_time

    print(f"request_cost: {request_cost:.3f}s")
    print(f"image_url: {image_url}")
    # 原始响应中的 data 是拼接后的 OCR 文本。
    # cost 是服务端 OCR 处理耗时，单位秒。
    print(response.text)

if __name__ == "__main__":
    main()
