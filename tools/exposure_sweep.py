"""曝光/增益扫一遍，看画面到底能不能亮起来（真机联调用）。

用法：C:\\py38x86\\python.exe tools\\exposure_sweep.py
输出：out\\sweep\\exp{曝光}ms_g{增益}.png + 每张的平均/最大值
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.imgio import imwrite  # noqa: E402
from visionapp.sources_mindvision import MindVisionSource  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out", "sweep")

CASES = [
    (10, 0), (30, 0), (100, 0), (200, 0), (400, 0), (800, 0),
    (30, 10), (100, 10), (200, 10), (100, 20),
]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    print("曝光ms  增益   平均   最大   非零%   存图")
    for exp, gain in CASES:
        src = MindVisionSource(index=0, trigger="continuous", exposure_ms=exp, gain=gain)
        try:
            src.open()
            frame = src.read(timeout_ms=3000)
        except Exception as exc:                              # noqa: BLE001
            print(f"{exp:>6} {gain:>5}  打开/取图失败: {exc}")
            continue
        finally:
            try:
                src.close()
            except Exception:                                 # noqa: BLE001
                pass
        if frame is None:
            print(f"{exp:>6} {gain:>5}  取图超时")
            continue
        img = frame.bgr
        mean = float(img.mean())
        mx = int(img.max())
        nz = float((img > 20).mean() * 100)
        path = os.path.join(OUT, f"exp{exp:03d}ms_g{gain}.png")
        imwrite(path, img)
        print(f"{exp:>6} {gain:>5} {mean:>6.1f} {mx:>6} {nz:>6.1f}   {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
