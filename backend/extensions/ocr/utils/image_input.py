import base64
import os
import tempfile
from urllib.parse import urlparse

import requests

DOWNLOAD_CONNECT_TIMEOUT = 10
DOWNLOAD_READ_TIMEOUT = 30
DOWNLOAD_CHUNK_SIZE = 65536
MAX_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_HEADERS = {
    "User-Agent": "paddle-ocr/1.0",
}

def _write_temp_file(file_bytes):
    if not file_bytes:
        raise ValueError("image data is empty")
    if len(file_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("image data is too large")

    fd, temp_path = tempfile.mkstemp(prefix="paddle_ocr_", suffix=".img")
    with os.fdopen(fd, "wb") as f:
        f.write(file_bytes)
    return temp_path

def save_base64_image_to_temp_file(file_base64):
    if not file_base64 or not isinstance(file_base64, str):
        raise ValueError("file_base64 is required")

    base64_text = file_base64.strip()
    if base64_text.startswith("data:") and "," in base64_text:
        base64_text = base64_text.split(",", 1)[1]
    base64_text = "".join(base64_text.split())

    try:
        file_bytes = base64.b64decode(base64_text, validate=True)
    except Exception as e:
        raise ValueError("file_base64 is not valid base64 data") from e

    return _write_temp_file(file_bytes)

def download_image_to_temp_file(url):
    if not url or not isinstance(url, str):
        raise ValueError("url is required")

    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an http or https image url")

    temp_path = None
    downloaded_size = 0
    try:
        response = requests.get(
            url,
            stream=True,
            timeout=(DOWNLOAD_CONNECT_TIMEOUT, DOWNLOAD_READ_TIMEOUT),
            headers=DOWNLOAD_HEADERS,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        normalized_content_type = content_type.lower()
        is_image_content = normalized_content_type.startswith("image/")
        is_binary_content = normalized_content_type.startswith("application/octet-stream")
        if content_type and not is_image_content and not is_binary_content:
            raise ValueError(f"url content type is not image: {content_type}")

        fd, temp_path = tempfile.mkstemp(prefix="paddle_ocr_url_", suffix=".img")
        with os.fdopen(fd, "wb") as f:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                downloaded_size += len(chunk)
                if downloaded_size > MAX_IMAGE_BYTES:
                    raise ValueError("downloaded image is too large")
                f.write(chunk)

        if downloaded_size == 0:
            raise ValueError("downloaded image is empty")
        return temp_path
    except requests.RequestException as e:
        cleanup_temp_file(temp_path)
        raise ValueError(f"download image failed: {e}") from e
    except Exception:
        cleanup_temp_file(temp_path)
        raise

def read_temp_file_bytes(temp_path):
    with open(temp_path, "rb") as f:
        return f.read()

def cleanup_temp_file(temp_path):
    if not temp_path:
        return
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception:
        pass
