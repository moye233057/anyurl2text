# -*- coding: utf-8 -*-
"""
一键启动：一个文件把后端与前端都拉起来。

职责：检查环境 → 检查端口 → 起后端与前端 → 等就绪 → 打开浏览器 → Ctrl+C 一起收掉。
两个服务仍是各自独立的进程（前后端分离不变），只是由这个脚本拉起与收尾。

运行方式：在项目根执行 `uv run python start.py`（无参数；端口读项目根 `.env`）。
首次先 `uv sync`。不在本仓 `.venv` 内时，若 PATH 有 uv 会自动 `uv run` 重入。
前端仍要 `frontend/node_modules`（缺了先 cd frontend && npm install）。
"""
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
# 默认选用的第三方服务实现（专栏下可换其它实现，并改端口 / API_URL）
SERVICE_OCR = PROJECT_DIR / "services" / "ocr" / "paddle-ocr"
SERVICE_ASR = PROJECT_DIR / "services" / "asr" / "qwen3-asr"


def _venv_python() -> Path:
    if sys.platform == "win32":
        return PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    return PROJECT_DIR / ".venv" / "bin" / "python"


def _find_uv() -> str | None:
    """PATH 优先；找不到再试官方安装器默认落点（Windows 上常见未进 PATH）。"""
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv"),
        home / ".cargo" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _ensure_uv_env() -> None:
    """
    环节控制：后端解释器必须来自本仓 uv 管理的 .venv。

    已在 .venv 内则直接继续；否则若找得到 uv，用 `uv run` 重入本脚本（必要时会 sync）；
    没有 uv 则退出并说明安装方式。
    """
    current = Path(sys.executable).resolve()
    expected = _venv_python().resolve()
    if current == expected:
        return

    uv = _find_uv()
    if uv is None:
        print("本仓用 uv 管 Python 环境（见 ADR-0013）。")
        print("请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/")
        print(f"然后在项目根执行：uv sync && uv run python {Path(__file__).name}")
        raise SystemExit(1)

    print(f"当前解释器不在本仓 .venv（{current}），改由 uv run 重入…")
    cmd = [uv, "run", "--project", str(PROJECT_DIR), "python", str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))


# dotenv 在确认进 .venv 之后再加载，避免用错环境的空壳包
_ensure_uv_env()

from dotenv import load_dotenv  # noqa: E402

# 与 app.py / vite.config.ts 读同一份项目根 .env，避免端口各写一份后互相杀进程
load_dotenv(PROJECT_DIR / ".env")
BACKEND_PORT = int(os.getenv("ANYURL2TEXT_PORT", "8020"))
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))
OCR_PORT = int(os.getenv("OCR_PORT", "8021"))
ASR_PORT = int(os.getenv("ASR_PORT", "8022"))
# 为 true 且默认 services 实现存在、其 models/ 有权重时，尝试一并拉起子进程
START_SERVICES = os.getenv("START_SERVICES", os.getenv("START_EXTENSIONS", "true")).strip().lower() in (
    "1", "true", "yes", "on",
)
HEALTH_URL = f"http://127.0.0.1:{BACKEND_PORT}/probe/health"
DOCS_URL = f"http://127.0.0.1:{BACKEND_PORT}/docs"
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}/"

# 起完之后自动开浏览器；不想被弹窗就改成 False
OPEN_BROWSER = True
READY_TIMEOUT_SEC = 90.0
POLL_INTERVAL_SEC = 0.5

# 后端「能不能跑起来」的必需依赖（可选依赖如 rarfile / py7zr / pillow-heif 缺失只会降级，不算缺）
BACKEND_REQUIRED = (
    "fastapi", "uvicorn", "httpx", "openai",
    "docx", "pptx", "openpyxl", "xlrd", "pyexcel_ods",
    "pymupdf", "PIL", "bs4", "chardet",
)


def step1_check_python() -> bool:
    """打印将使用的解释器，并检查后端依赖是否装在这个环境里。"""
    print(f"后端解释器：{sys.executable}")
    missing = [name for name in BACKEND_REQUIRED if importlib.util.find_spec(name) is None]
    if missing:
        print("这个环境缺后端依赖：" + "、".join(missing))
        print(f"在项目根执行：uv sync（清单在 {PROJECT_DIR / 'pyproject.toml'}）")
        return False
    return True


def step2_check_frontend() -> bool:
    """检查前端依赖与 npm 是否可用。"""
    if shutil.which("npm") is None:
        print("PATH 里找不到 npm：请确认 Node 已安装。")
        return False
    if not (FRONTEND_DIR / "node_modules").exists():
        print(f"缺前端依赖：先执行 cd {FRONTEND_DIR} && npm install")
        return False
    return True


def step3_check_ports() -> bool:
    """端口被占用就别再起第二个，免得两个服务抢同一个端口。"""
    required = [BACKEND_PORT, FRONTEND_PORT]
    busy = [str(port) for port in required if _port_in_use(port)]
    if busy:
        print("这些端口已被占用：" + "、".join(busy))
        print(f"可能服务已经在跑：{DOCS_URL} 、{FRONTEND_URL}")
        print("要重启就先停掉旧进程，再运行本脚本。")
        return False
    return True


def _service_weights_ready(service_dir: Path) -> bool:
    """服务自己的 models/ 下除 README 外还有文件，才视为已备权重。"""
    models_dir = service_dir / "models"
    if not models_dir.is_dir():
        return False
    for path in models_dir.rglob("*"):
        if path.is_file() and path.name.lower() != "readme.md":
            return True
    return False


def _start_service(label: str, service_dir: Path, port: int) -> subprocess.Popen | None:
    """条件满足时拉起 services 下某实现的子进程；已在听端口则跳过。"""
    main_py = service_dir / "main.py"
    if not main_py.is_file():
        print(f"跳过 {label}：缺少 {main_py.relative_to(PROJECT_DIR)}")
        return None
    if not _service_weights_ready(service_dir):
        print(f"跳过 {label}：{service_dir.relative_to(PROJECT_DIR) / 'models'} 无权重（见该服务 README）")
        return None
    if _port_in_use(port):
        print(f"{label} 端口 {port} 已在听，复用现有进程")
        return None
    print(f"启动 {label}（子进程，cwd={service_dir.relative_to(PROJECT_DIR)}）…")
    # 服务自带环境：此处仅作占位；生产应改用该服务专用解释器
    return subprocess.Popen([sys.executable, "main.py"], cwd=str(service_dir))


def step4_start_backend() -> subprocess.Popen:
    """起后端（cwd 必须是 backend/，app.py 用相对导入找同级包）。"""
    print("启动后端…")
    return subprocess.Popen([sys.executable, "app.py"], cwd=str(BACKEND_DIR))


def step5_start_frontend() -> subprocess.Popen:
    """起前端 dev server。"""
    print("启动前端…")
    npm = shutil.which("npm")
    return subprocess.Popen([npm, "run", "dev"], cwd=str(FRONTEND_DIR))


def step6_wait_ready(backend: subprocess.Popen, frontend: subprocess.Popen) -> bool:
    """等两个服务真的能应答；哪个进程先退出就直接报出来，不装作在等。"""
    deadline = time.monotonic() + READY_TIMEOUT_SEC
    backend_ready = frontend_ready = False
    while time.monotonic() < deadline:
        if backend.poll() is not None:
            print(f"后端进程已退出（退出码 {backend.returncode}），错误见它上面的日志。")
            return False
        if frontend.poll() is not None:
            print(f"前端进程已退出（退出码 {frontend.returncode}），错误见它上面的日志。")
            return False
        backend_ready = backend_ready or _http_ok(HEALTH_URL)
        frontend_ready = frontend_ready or _http_ok(FRONTEND_URL)
        if backend_ready and frontend_ready:
            print(f"后端就绪：{DOCS_URL}")
            print(f"前端就绪：{FRONTEND_URL}")
            return True
        time.sleep(POLL_INTERVAL_SEC)
    print(
        f"等待就绪超时（{READY_TIMEOUT_SEC:.0f} 秒）："
        f"后端 {'已起' if backend_ready else '未起'}、前端 {'已起' if frontend_ready else '未起'}"
    )
    return False


def step7_open_browser() -> None:
    """打开浏览器（可用顶部 OPEN_BROWSER 关掉）。"""
    if OPEN_BROWSER:
        webbrowser.open(FRONTEND_URL)


def run() -> int:
    """只做按序启动：逐步检查与启动，Ctrl+C 时把子进程一起收掉。"""
    if not step1_check_python() or not step2_check_frontend() or not step3_check_ports():
        return 1

    backend = None
    frontend = None
    ocr_proc = None
    asr_proc = None
    try:
        if START_SERVICES:
            ocr_proc = _start_service("OCR (paddle-ocr)", SERVICE_OCR, OCR_PORT)
            asr_proc = _start_service("ASR (qwen3-asr)", SERVICE_ASR, ASR_PORT)
        else:
            print("START_SERVICES=false：不自动拉起 services（可按 services/*/README 手动起）")
        backend = step4_start_backend()
        frontend = step5_start_frontend()
        if not step6_wait_ready(backend, frontend):
            return 1
        step7_open_browser()
        print("服务在跑，按 Ctrl+C 一起停止。")
        while True:
            if backend.poll() is not None or frontend.poll() is not None:
                print("有服务退出了，一并收尾。")
                return 1
            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在停止…")
        return 0
    finally:
        _stop(frontend)
        _stop(backend)
        _stop(ocr_proc)
        _stop(asr_proc)


def _stop(process) -> None:
    """
    结束子进程树。

    Windows 上用 `taskkill /T`：npm 会再拉起 node，只结束父进程会留下孤儿进程占着 5173 端口。
    """
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _port_in_use(port: int) -> bool:
    """TCP 连得上就说明有人在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _http_ok(url: str) -> bool:
    """能取到 HTTP 响应（哪怕是 404）就算服务已经起来了。"""
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(run())
