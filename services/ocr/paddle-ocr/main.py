# -*- coding: utf-8 -*-
"""paddle-ocr 本机服务：GET /health · POST /ocr-b64。由仓库根 start.py 或本目录 python main.py 拉起。"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request

import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("paddle-ocr")

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _decode_b64(text: str) -> bytes:
    if not text or not isinstance(text, str):
        raise ValueError("file_base64 is required")
    raw = text.strip()
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    raw = "".join(raw.split())
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("file_base64 is not valid base64 data") from exc
    if not data:
        raise ValueError("image data is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image data is too large")
    return data


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("OCR startup model_dir=%s", engine.OCR_MODEL_DIR)
    engine.init_model()
    logger.info("OCR model ready")
    yield


app = FastAPI(title="paddle-ocr", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"code": "200", "msg": "ok", "data": engine.runtime_info()}


@app.post("/ocr-b64")
async def ocr_b64(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="request body must be json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a json object")

    file_b64 = (
        payload.get("file_base64")
        or payload.get("image_base64")
        or payload.get("file")
        or payload.get("base64")
    )
    try:
        file_bytes = _decode_b64(file_b64)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    started = time.perf_counter()
    try:
        items = await asyncio.to_thread(engine.run_ocr, file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("ocr failed")
        raise HTTPException(status_code=500, detail="ocr failed") from None

    text = "\n".join(item.get("text", "") for item in items if item.get("text"))
    return {
        "code": "200",
        "msg": "success",
        "data": text,
        "cost": round(time.perf_counter() - started, 3),
    }


if __name__ == "__main__":
    port = int(os.getenv("OCR_PORT", "8021"))
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1)
