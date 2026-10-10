import os
import time
import base64
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# api_url = "http://172.20.2.3:8021/ocr-b64"
api_url = "http://localhost:8080/paddle-ocr/ocr-b64"
file_path = os.path.join(BASE_DIR, "data", "9a872b7f-336c-478d-9be2-c3a20ca173b0.jpg")

def main():
    with open(file_path, "rb") as f:
        file_base64 = base64.b64encode(f.read()).decode("utf-8")

    start_time = time.perf_counter()
    response = requests.post(api_url, json={"file_base64": file_base64})
    request_cost = time.perf_counter() - start_time

    print(f"request_cost: {request_cost:.3f}s")
    # 原始响应中的 data 是拼接后的 OCR 文本。
    # cost 是服务端 OCR 处理耗时，单位秒。
    print(response.text)

if __name__ == "__main__":
    main()
