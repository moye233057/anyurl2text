# -*- coding: utf-8 -*-
"""
anyurl2text 服务入口。

职责只有三件：加载 .env、装配路由与 CORS、启动时打印访问入口与配置自检。
业务逻辑在 pipelines/ 与 processors/，路由只做参数接收与状态码设置。

注意：`.env` 必须在导入 configs.settings **之前**加载——配置模块在 import 期就
读取环境变量，顺序反了会全部落到默认值，而且症状是「配置看起来没生效」这种难查的问题。
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
# 只读项目根目录的 .env：位置固定才能解释「配置为什么没生效」，
# 启动日志会把实际用的路径打出来，省得去猜。
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import configs.settings as settings  # noqa: E402
from configs.log_config import logger  # noqa: E402
from pipelines.steps.step6_assemble import error_response  # noqa: E402
from routers.convert.router import router as convert_router  # noqa: E402
from routers.probe.router import router as probe_router  # noqa: E402
from routers.rag.router import router as rag_router  # noqa: E402
from routers.rag.schema import RagExtractResponse  # noqa: E402

# multipart 的边界与各段头部会带来额外字节，额度上留出余量；
# 真正的文件上限仍是 MAX_DOWNLOAD_BYTES，在落盘过程中按实际字节数把关。
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时打印访问入口与关键配置状态，省得每次翻文档找端口。"""
    docs_url = f"http://{settings.API_HOST}:{settings.API_PORT}/docs"
    logger.info(f"anyurl2text 已启动，接口文档: {docs_url}")
    logger.info(f"配置文件: {ENV_FILE if ENV_FILE.exists() else '(未找到，全部使用默认值)'}")
    logger.info(
        f"模型={settings.DEEPSEEK_MODEL} 思考模式={'开' if settings.THINKING_ENABLED else '关'} "
        f"扫描件页面渲染={'开' if settings.RENDERED_PAGE_OCR_ENABLED else '关'} "
        f"自动重载={'开' if settings.RELOAD else '关'}"
    )
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        logger.info("未配置 DEEPSEEK_API_KEY：/understand 会失败并说明原因，/extract 不受影响")
    yield

app = FastAPI(
    title="anyurl2text",
    version="0.1.0",
    description=(
        "任意 URL 或本地文件转文本。\n\n"
        "- `POST /extract`：纯代码确定性抽取，不调用模型，稳定且快。\n"
        "- `POST /understand`：在 extract 之上叠加模型能力（认字、描述、摘要）。\n"
        "- `POST /rag/extract/file`：给检索入库用；印刷目录 PDF 吐页与区间，否则吐交付文本。\n\n"
        "extract 与 understand 共用同一份请求与响应契约，`content` 逐字节一致，"
        "差别只在是否产生模型费用——因此同一个源可以两条路各跑一次直接对比。"
    ),
    lifespan=lifespan,
)

# 按 Content-Length 提前拒绝超大请求体。
# 不这么做的话，Starlette 会先把整个 multipart 落到临时文件，路由里的大小检查才开始生效——
# 磁盘那时已经被写满，返回的 413 来得太晚。必须在读请求体之前拦。
@app.middleware("http")
async def reject_oversized_body(request: Request, call_next):
    declared = request.headers.get("content-length")
    limit = settings.MAX_DOWNLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
    if declared and declared.isdigit() and int(declared) > limit:
        logger.info(f"请求体过大被拒绝 declared={declared} limit={limit}")
        message = f"请求体过大: {declared} 字节，超过上限 {limit} 字节"
        if request.url.path.startswith("/rag"):
            body = RagExtractResponse(kind="error", error=message).model_dump(mode="json")
            return JSONResponse(status_code=413, content=body)
        result = error_response("(请求体在解析前被拒绝)", message, 0, 413)
        return JSONResponse(status_code=413, content=result.response.model_dump(mode="json"))
    return await call_next(request)

# 本地开发放开跨域：前端跑在 5173，后端在 8020。
# allow_credentials 必须为 False——浏览器不允许「通配来源 + 携带凭证」的组合，
# 写成 True 只会让请求被拒，且是个常见的配置事故。
# CORS 最后添加 = 最外层，这样上面那个 413 响应也会带上跨域头，
# 浏览器里看到的是可读的错误消息而不是一个 CORS 报错。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(convert_router)
app.include_router(probe_router)
app.include_router(rag_router)

if __name__ == "__main__":
    if settings.RELOAD:
        # reload 必须传 import 字符串——传 app 对象时 uvicorn 直接报错「You must pass the
        # application as an import string」。这就要求 `app` 能被 import：监督进程另起子进程，
        # 子进程的 cwd 与 sys.path 都继承自这里，所以两者都显式指到 backend/，
        # 于是 `python app.py`（cwd=backend/，start.py 就是这么起的）与从项目根跑
        # `python backend/app.py` 都成立。
        os.chdir(BACKEND_DIR)
        sys.path.insert(0, str(BACKEND_DIR))
        # 不设 reload_includes / reload_excludes：本机没装 watchfiles 时 uvicorn 退回
        # StatReload（轮询 mtime），那两个参数只会打一行「装了 watchfiles 才有效」的警告。
        # StatReload 只扫 `*.py`，所以改 .env 或样例不会误触发重载。
        logger.info(f"自动重载已打开（RELOAD=true），监视 {BACKEND_DIR} 下的 .py")
        uvicorn.run(
            "app:app",
            host=settings.API_HOST,
            port=settings.API_PORT,
            reload=True,
            reload_dirs=[str(BACKEND_DIR)],
        )
    else:
        uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
