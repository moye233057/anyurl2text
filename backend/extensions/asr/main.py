# -*- coding: utf-8 -*-
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from configs.log_config import logger, mid_var
from utils.runtime_env import setup_env

# 必须在导入 torch / qwen_asr 相关模块前固定可见显卡。
setup_env()

from configs.base_config import CONFIG_ENV
from utils.asr_runtime import get_runtime_info, init_asr_model, transcribe_audio
from utils.audio_input import (
    cleanup_temp_file,
    download_audio_to_temp_file,
    save_base64_audio_to_temp_file,
)


class AsrBase64Request(BaseModel):
    """base64 音频识别请求；language 留空表示自动识别语种。"""

    audio_base64: str
    language: Optional[str] = None


class AsrUrlRequest(BaseModel):
    """音频地址识别请求；language 留空表示自动识别语种。"""

    url: str
    language: Optional[str] = None


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

async def run_asr_from_temp_file(temp_path, source_label, language):
    start_time = time.perf_counter()
    try:
        logger.info(f"ASR request start: source={source_label}, language={language}")
        result = await asyncio.to_thread(transcribe_audio, temp_path, language)
    except ValueError as e:
        logger.error(f"ASR request failed: source={source_label}, error={e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"ASR request failed unexpectedly: source={source_label}, error={e}")
        raise HTTPException(status_code=500, detail="asr failed") from e

    cost = time.perf_counter() - start_time
    logger.info(
        f"ASR request finished: source={source_label}, "
        f"language={result['language']}, text_length={len(result['text'])}, cost={round(cost, 3)}"
    )
    return {
        "code": "200",
        "msg": "success",
        "data": result["text"],
        "language": result["language"],
        "cost": round(cost, 3),
    }

def add_routes(app_instance):
    """当前服务只注册 ASR 相关接口，不额外拆 routers。"""

    @app_instance.get("/health")
    async def health():
        return {
            "code": "200",
            "msg": "ok",
            "data": get_runtime_info(),
        }

    @app_instance.post("/asr-b64")
    async def asr_b64(payload: AsrBase64Request):
        temp_path = None
        try:
            temp_path = save_base64_audio_to_temp_file(payload.audio_base64)
            return await run_asr_from_temp_file(temp_path, "base64", payload.language)
        except ValueError as e:
            logger.error(f"ASR base64 input failed: {e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            cleanup_temp_file(temp_path)

    @app_instance.post("/asr-url")
    async def asr_url(payload: AsrUrlRequest):
        temp_path = None
        try:
            logger.info(f"ASR URL download start: url={payload.url}")
            temp_path = await asyncio.to_thread(download_audio_to_temp_file, payload.url)
            logger.info(f"ASR URL download finished: url={payload.url}")
            return await run_asr_from_temp_file(temp_path, "url", payload.language)
        except ValueError as e:
            logger.error(f"ASR URL input failed: url={payload.url}, error={e}")
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            cleanup_temp_file(temp_path)

def create_app():
    @asynccontextmanager
    async def lifespan(app_instance):
        # 启动阶段预加载 ASR 模型，模型加载成功后服务才开始响应请求。
        logger.info(f"Qwen3-ASR service startup: env={CONFIG_ENV}")
        init_asr_model()
        logger.info("Qwen3-ASR service ready")
        yield
        logger.info("Qwen3-ASR service shutdown")

    app_instance = FastAPI(title="Qwen3-ASR Service", lifespan=lifespan)
    add_common_middleware(app_instance)
    add_routes(app_instance)
    return app_instance

app = create_app()

if __name__ == "__main__":
    import os

    port = int(os.getenv("ASR_PORT", "8022"))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1)
