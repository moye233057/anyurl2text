import os
import sys

from configs.base_config import CUDA_VISIBLE_DEVICES

CUDA_LIB_PATH_READY_ENV = "PP_OCRV5_CUDA_LIB_PATH_READY"
TORCH_FORCE_WEIGHTS_ONLY_LOAD_ENV = "TORCH_FORCE_WEIGHTS_ONLY_LOAD"

def configure_cuda_library_path_and_reexec():
    """确保 PyTorch wheel 自带的 nvjitlink 动态库优先于系统 CUDA 被加载。"""
    if os.environ.get(CUDA_LIB_PATH_READY_ENV) == "1":
        return

    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    nvjitlink_lib_dir = os.path.join(
        sys.prefix,
        "lib",
        python_version,
        "site-packages",
        "nvidia",
        "nvjitlink",
        "lib",
    )
    if not os.path.isdir(nvjitlink_lib_dir):
        return

    current_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{nvjitlink_lib_dir}:{current_library_path}"
    os.environ[CUDA_LIB_PATH_READY_ENV] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)

def setup_env():
    configure_cuda_library_path_and_reexec()
    if CUDA_VISIBLE_DEVICES:
        os.environ["CUDA_VISIBLE_DEVICES"] = CUDA_VISIBLE_DEVICES
    # 这是 MinerU 自身的模型来源开关，不表示服务部署环境。
    os.environ["MINERU_MODEL_SOURCE"] = "local"
    # 官方 PyTorch 开关：让第三方库里未显式传参的 torch.load 按 weights_only=True 加载权重。
    os.environ.setdefault(TORCH_FORCE_WEIGHTS_ONLY_LOAD_ENV, "1")
