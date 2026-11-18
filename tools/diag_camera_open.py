"""诊断：同时/连续打开同一台相机，会不会出现 -36（filter driver init）。

界面里同时有「画面块」和「运行引擎」两处会用相机，可能各开一次；
如果第二次 CameraInit 报 -36，就是这里的问题。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.sources_mindvision import MindVisionError, MindVisionSource  # noqa: E402


def try_open(label: str, **kw):
    src = MindVisionSource(index=0, trigger="continuous", exposure_ms=30, **kw)
    try:
        src.open()
        f = src.read(timeout_ms=2000)
        print(f"  {label}: 打开成功, 取帧={None if f is None else f.bgr.shape}")
        return src
    except MindVisionError as exc:
        print(f"  {label}: 失败 -> {exc}")
        return None


print("[1] 连续打开-关闭 3 次（同一台）")
for i in range(3):
    src = try_open(f"第{i + 1}次")
    if src is not None:
        src.close()

print("[2] 同时打开 2 个实例（模拟 画面块 + 引擎）")
a = MindVisionSource(index=0, trigger="continuous", exposure_ms=30)
b = MindVisionSource(index=0, trigger="continuous", exposure_ms=30)
try:
    a.open()
    print("  A 打开成功")
except MindVisionError as exc:
    print("  A 失败 ->", exc)
try:
    b.open()
    print("  B 打开成功")
    fa = a.read(timeout_ms=2000)
    fb = b.read(timeout_ms=2000)
    print("  A 取帧:", None if fa is None else fa.bgr.shape, " B 取帧:", None if fb is None else fb.bgr.shape)
except MindVisionError as exc:
    print("  B 失败 ->", exc)
for s in (a, b):
    try:
        s.close()
    except Exception:                                       # noqa: BLE001
        pass
