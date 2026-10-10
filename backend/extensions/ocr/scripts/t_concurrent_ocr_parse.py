import base64
import concurrent.futures
import os
import time

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
api_url = "http://172.20.2.3:8021/ocr-b64"
data_dir = os.path.join(BASE_DIR, "data")

repeat_count = 3
image_suffixes = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

def get_image_paths():
    image_paths = []
    for file_name in os.listdir(data_dir):
        file_path = os.path.join(data_dir, file_name)
        suffix = os.path.splitext(file_name)[1].lower()
        if os.path.isfile(file_path) and suffix in image_suffixes:
            image_paths.append(file_path)
    image_paths.sort()
    return image_paths

def send_request(index, file_path):
    with open(file_path, "rb") as f:
        file_base64 = base64.b64encode(f.read()).decode("utf-8")

    start_time = time.perf_counter()
    response = requests.post(api_url, json={"file_base64": file_base64})
    request_cost = time.perf_counter() - start_time

    return {
        "index": index,
        "file_name": os.path.basename(file_path),
        "status_code": response.status_code,
        "request_cost": request_cost,
        "response": response.text,
    }

def main():
    image_paths = get_image_paths()
    request_files = []
    for file_path in image_paths:
        for _ in range(repeat_count):
            request_files.append(file_path)

    request_count = len(request_files)
    max_workers = request_count
    if request_count == 0:
        print(f"no image files found: {data_dir}")
        return

    total_start_time = time.perf_counter()
    print(f"image_count: {len(image_paths)}")
    print(f"request_count: {request_count}")
    print(f"max_workers: {max_workers}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(send_request, index, file_path)
            for index, file_path in enumerate(request_files, start=1)
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            print(
                f"request {result['index']} file={result['file_name']} "
                f"status_code={result['status_code']} cost={result['request_cost']:.3f}s"
            )
            print(result["response"])

    print(f"total_cost: {time.perf_counter() - total_start_time:.3f}s")

if __name__ == "__main__":
    main()
