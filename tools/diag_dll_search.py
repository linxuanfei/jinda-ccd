"""验证：DLL 搜索路径被限制成「只搜 System32」时，相机 CameraInit 会不会报 -36。

PyInstaller 打包后的 exe 会在启动时调用 SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32)，
这可能是「exe 里打不开相机、而 python 里正常」的原因。
本脚本在 python 里模拟这个限制，再恢复，看 -36 是否复现/消失。
"""

from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000

k32 = ctypes.windll.kernel32


def try_open(tag: str) -> None:
    from visionapp.sources_mindvision import MindVisionError, MindVisionSource

    src = MindVisionSource(index=0, trigger="continuous", exposure_ms=30)
    try:
        src.open()
        f = src.read(timeout_ms=2000)
        print(f"[{tag}] 打开成功, 帧={None if f is None else f.bgr.shape}")
    except MindVisionError as exc:
        print(f"[{tag}] 失败: {exc}")
    finally:
        try:
            src.close()
        except Exception:                                   # noqa: BLE001
            pass


print("1) 正常情况下：")
try_open("正常")

print("2) 限制成只搜 System32（模拟 PyInstaller）：")
k32.SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32)
try_open("受限")

print("3) 恢复默认搜索目录后：")
k32.SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)
try_open("恢复")
