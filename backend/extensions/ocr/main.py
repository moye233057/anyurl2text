import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from configs.log_config import logger, mid_var
from utils.runtime_env import setup_env

# 必须在导入 MinerU / torch 相关 OCR 模块前完成 CUDA 动态库和模型来源设置。
setup_env()

from configs.base_config import (
    CONFIG_ENV,
    DEFAULT_OCR_LANG,
    DEFAULT_OCR_MODE,
    DEFAULT_USE_ANGLE_CLS,
    OCR_MODEL_DIR,
    OCR_MODEL_ROOT_DIR,
)
from utils.image_input import (
    cleanup_temp_file,
    download_image_to_temp_file,
    read_temp_file_bytes,
    save_base64_image_to_temp_file,
)
from utils.ocr_runtime import get_runtime_info, init_default_ocr_model, run_ocr

def add_common_middleware(app_instance):
    """添加生产环境常用中间件。"""
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app_instance.middleware("http")
    async def set_request_id_middleware(request, call_next):
        request_id = uuid.uuid4().hex
        token = mid_var.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            mid_var.reset(token)

async def run_ocr_from_temp_file(temp_path, source_label):
    file_bytes = read_temp_file_bytes(temp_path)
    if not file_bytes:
        logger.error(f"OCR request rejected: source={source_label}, image data is empty")
        raise HTTPException(status_code=400, detail="image data is empty")

    start_time = time.perf_counter()
    try:
        logger.info(
            f"OCR request start: source={source_label}, "
            f"lang={DEFAULT_OCR_LANG}, mode={DEFAULT_OCR_MODE}, "
            f"use_angle_cls={DEFAULT_USE_ANGLE_CLS}"
        )
        text_items = await asyncio.to_thread(
            run_ocr,
            file_bytes,
            DEFAULT_OCR_LANG,
            DEFAULT_OCR_MODE,
            DEFAULT_USE_ANGLE_CLS,
        )
    except ValueError as e:
        logger.error(f"OCR request failed: source={source_label}, error={e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"OCR request failed unexpectedly: source={source_label}, error={e}")
        raise HTTPException(status_code=500, detail="ocr failed") from e

    cost = time.perf_counter() - start_time
    logger.info(
        f"OCR request finished: source={source_label}, "
        f"item_count={len(text_items)}, cost={round(cost, 3)}"
    )
    return {
        "code": "200",
        "msg": "success",
        "data": "\n".join(item.get("text", "") for item in text_items if item.get("text")),
        "cost": round(cost, 3),
    }

async def read_json_payload(request):
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="request body must be json") from e

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a json object")
    return payload

def add_routes(app_instance):
    """当前服务只注册 OCR 相关接口，不额外拆 routers。"""

    @app_instance.get("/health")
    async def health():
        return {
            "code": "200",
            "msg": "ok",
            "data": get_runtime_info(),
        }

    @app_instance.post("/ocr-b64")
    async def ocr_b64(request: Request):
        payload = await read_json_payload(request)
        file_base64 = (
            payload.get("file_base64")
            or payload.get("image_base64")
            or payload.get("file")
            or payload.get("base64")
        )
        temp_path = None
        try:
            temp_path = save_base64_image_to_temp_file(file_base64)
            return await run_ocr_from_temp_file(temp_path, "base64")
        except ValueError as e:
            logger.error(f"OCR base64 input failed: {e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            cleanup_temp_file(temp_path)

    @app_instance.post("/ocr-url")
    async def ocr_url(request: Request):
        payload = await read_json_payload(request)
        url = payload.get("url")
        temp_path = None
        try:
            logger.info(f"OCR URL download start: url={url}")
            temp_path = await asyncio.to_thread(download_image_to_temp_file, url)
            logger.info(f"OCR URL download finished: url={url}")
            return await run_ocr_from_temp_file(temp_path, "url")
        except ValueError as e:
            logger.error(f"OCR URL input failed: url={url}, error={e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            cleanup_temp_file(temp_path)

def create_app():
    @asynccontextmanager
    async def lifespan(app_instance):
        # 启动阶段预加载默认 OCR 模型，模型加载成功后服务才开始响应请求。
        logger.info(
            f"PP-OCRv5 service startup: env={CONFIG_ENV}, "
            f"ocr_model_root_dir={OCR_MODEL_ROOT_DIR}, "
            f"ocr_model_dir={OCR_MODEL_DIR}"
        )
        init_default_ocr_model()
        logger.info("PP-OCRv5 default OCR model loaded")
        yield
        logger.info("PP-OCRv5 service shutdown")

    app_instance = FastAPI(title="PP-OCRv5 Service", lifespan=lifespan)
    add_common_middleware(app_instance)
    add_routes(app_instance)
    return app_instance

app = create_app()

if __name__ == "__main__":
    import os

    port = int(os.getenv("OCR_PORT", "8021"))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1)
