"""运行路径解析：源码运行 / PyInstaller 打包成 exe 运行，两种模式都支持。

- 源码运行：项目根 = visionapp 包的上一级；配置、配方、结果都写在项目里。
- exe 运行：只读资源（config/recipes/samples/docs）打进 exe 的 `_internal`，
  首次启动时复制到 exe 旁边；结果/日志/配置也写在 exe 旁边（便携、免安装）。
"""

from __future__ import annotations

import os
import shutil
import sys

# exe 首次启动时从内置资源释放到可写目录的内容
_SEED_DIRS = ("config", "recipes", "samples", "docs")
_RUNTIME_DIRS = ("results", "logs", "out")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _source_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir() -> str:
    """内置只读资源目录（exe 里是 _internal，源码运行时是项目根）。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return _source_root()


def app_dir() -> str:
    """可写数据目录：exe 所在目录；源码运行时是项目根。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _source_root()


def ensure_data_dir() -> str:
    """确保可写数据目录可用（exe 首次运行会把内置配置/配方复制出来）。

    返回可写数据目录，可直接给 os.path.join 当 BASE_DIR 用。
    """
    root = app_dir()
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        # exe 放在只读位置（如 Program Files）时退回到用户目录
        root = os.path.join(os.path.expanduser("~"), "VisionApp")
        os.makedirs(root, exist_ok=True)

    if not is_frozen():
        return root

    src = bundle_dir()
    if src and os.path.isdir(src):
        for name in _SEED_DIRS:
            s = os.path.join(src, name)
            d = os.path.join(root, name)
            if os.path.isdir(s) and not os.path.isdir(d):
                try:
                    shutil.copytree(s, d)
                except OSError:
                    pass
    for name in _RUNTIME_DIRS:
        try:
            os.makedirs(os.path.join(root, name), exist_ok=True)
        except OSError:
            pass
    return root
