# -*- coding: utf-8 -*-
"""qwen3-asr 本机服务：GET /health · POST /asr-b64。由仓库根 start.py 或本目录 python main.py 拉起。"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qwen3-asr")

MAX_AUDIO_BYTES = 100 * 1024 * 1024


class AsrB64Body(BaseModel):
    audio_base64: str
    language: Optional[str] = None


def _write_temp_audio(audio_base64: str) -> str:
    if not audio_base64 or not isinstance(audio_base64, str):
        raise ValueError("audio_base64 is required")
    raw = audio_base64.strip()
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    raw = "".join(raw.split())
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("audio_base64 is not valid base64 data") from exc
    if not data:
        raise ValueError("audio data is empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("audio data is too large")
    fd, path = tempfile.mkstemp(prefix="qwen3_asr_", suffix=".audio")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("ASR startup model_path=%s", engine.MODEL_PATH)
    engine.init_model()
    logger.info("ASR ready")
    yield


app = FastAPI(title="qwen3-asr", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"code": "200", "msg": "ok", "data": engine.runtime_info()}


@app.post("/asr-b64")
async def asr_b64(body: AsrB64Body):
    temp_path = None
    try:
        temp_path = _write_temp_audio(body.audio_base64)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(engine.transcribe, temp_path, body.language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("asr failed")
        raise HTTPException(status_code=500, detail="asr failed") from None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return {
        "code": "200",
        "msg": "success",
        "data": result["text"],
        "language": result["language"],
        "cost": round(time.perf_counter() - started, 3),
    }


if __name__ == "__main__":
    port = int(os.getenv("ASR_PORT", "8022"))
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1)
