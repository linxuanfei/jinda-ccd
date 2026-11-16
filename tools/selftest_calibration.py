"""标定功能自测：两点标定 → 换算 → 判定（全部无界面/无硬件）。"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.calibration import Calibration            # noqa: E402
from visionapp.recipe import Pipeline, Recipe            # noqa: E402
from visionapp.sources import open_source                # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    fails: list[str] = []

    # ---------------- 1) 两点标定计算 ----------------
    cal = Calibration()
    mm_per_px = cal.set_by_two_points((100, 100), (500, 100), 20.0)   # 400px = 20mm
    print(f"[1] 两点标定: {mm_per_px:.6f} mm/px  ({cal.describe()})")
    if abs(mm_per_px - 0.05) > 1e-9:
        fails.append(f"标定值不对: {mm_per_px}")
    if abs(cal.px_per_mm - 20.0) > 1e-6:
        fails.append(f"px/mm 不对: {cal.px_per_mm}")
    try:
        cal.set_by_two_points((0, 0), (0, 0), 10)
        fails.append("重合点应报错")
    except ValueError:
        pass

    # ---------------- 2) 序列化 ----------------
    d = cal.to_dict()
    cal2 = Calibration.from_dict(d)
    print(f"[2] 序列化: mm_per_px={cal2.mm_per_px:.6f}  points={cal2.points_px}")
    if abs(cal2.mm_per_px - cal.mm_per_px) > 1e-9 or len(cal2.points_px) != 2:
        fails.append("标定序列化不一致")

    # ---------------- 3) 配方承载标定 + 流水线毫米换算 ----------------
    recipe = Recipe.load(os.path.join(BASE, "recipes", "sim_demo.json"))
    recipe.calibration = Calibration()
    # 用合成画面：两个 PIN 中心间距 460.8px；假定量出来是 23.04mm → 0.05 mm/px
    recipe.calibration.set_by_two_points((0, 0), (460.8, 0), 23.04)
    for it in recipe.items:
        if it.id == "pitch":
            it.params["mm_per_px"] = 0.0          # 不写死比例，靠标定
            it.judge = {"kind": "range", "min": 22.0, "max": 23.5}
        if it.id == "pin_area":
            it.params["mm2_per_px2"] = 0.0        # 靠标定

    src = open_source("synthetic", fps=0)
    src.open()
    frame = src.read()
    res, _ = Pipeline(recipe).run(frame, annotate=False)
    pitch = res.results["pitch"]
    area = res.results["pin_area"]
    print(f"[3] 标定后: pitch={pitch.value:.3f}{pitch.unit} ok={pitch.ok} | "
          f"pin_area={area.value:.4f}{area.unit}")
    if pitch.unit != "mm" or abs(pitch.value - 23.04) > 0.05:
        fails.append(f"毫米换算不对: {pitch.value}{pitch.unit}")
    if area.unit != "mm2":
        fails.append(f"面积单位不对: {area.unit}")
    if not pitch.ok:
        fails.append("标定后判定应为 OK")

    # ---------------- 4) 取消标定后应回到像素 ----------------
    recipe.calibration = Calibration()
    res2, _ = Pipeline(recipe).run(frame, annotate=False)
    pitch2 = res2.results["pitch"]
    print(f"[4] 取消标定: pitch={pitch2.value:.1f}{pitch2.unit}")
    if pitch2.unit != "px":
        fails.append(f"取消标定后应回像素: {pitch2.unit}")

    src.close()

    print()
    if fails:
        for f in fails:
            print("  [FAIL]", f)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
