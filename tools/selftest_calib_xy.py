"""自测：X/Y 分别标定 + 标定值直接修改（D-34）。

现场口径（视频 / 操作说明）：
- 「X 水平、Y 垂直」分开标定；
- 「设定值为 1，现检测结果为 1.433 → 点相机标定改标定值」——支持用真实值反算。

覆盖：
  1. set_xy / 序列化往返 / 旧配方（只有 mm_per_px）兼容
  2. set_by_two_points 的 x / y / auto 三种方向
  3. set_by_known_value 反算（1 vs 1.433）
  4. distance_pp 在不同 X/Y 比例下的换算
  5. area_of_blob 面积换算（sx*sy）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from visionapp.calibration import Calibration  # noqa: E402
from visionapp.core import Frame  # noqa: E402
from visionapp.ops import Blob, Item, ItemResult, area_of_blob, distance_pp  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


def test_set_xy() -> None:
    print("[1] 直接设置 X/Y 比例")
    c = Calibration()
    check("空标定 valid=False", c.valid is False)
    c.set_xy(0.05, 0.1)
    check("valid=True", c.valid is True)
    check("sx=0.05", abs(c.sx - 0.05) < 1e-12)
    check("sy=0.1", abs(c.sy - 0.1) < 1e-12)
    check("旧字段 mm_per_px 同步为 X", abs(c.mm_per_px - 0.05) < 1e-12)
    check("describe 含 X/Y", "X" in c.describe() and "Y" in c.describe())

    d = c.to_dict()
    c2 = Calibration.from_dict(d)
    check("序列化往返：sx", abs(c2.sx - 0.05) < 1e-9)
    check("序列化往返：sy", abs(c2.sy - 0.1) < 1e-9)

    old = Calibration.from_dict({"mm_per_px": 0.02})
    check("旧配方只有 mm_per_px → sx=sy=0.02", abs(old.sx - 0.02) < 1e-12 and abs(old.sy - 0.02) < 1e-12)


def test_two_points() -> None:
    print("[2] 两点标定（X / Y / auto）")
    c = Calibration()
    c.set_by_two_points((100, 100), (300, 100), 10.0, axis="x")      # 水平 200px = 10mm
    check("X 方向：0.05 mm/px", abs(c.sx - 0.05) < 1e-9, f"实际 {c.sx}")
    check("Y 未单独标时沿用 X", abs(c.sy - 0.05) < 1e-9)

    c2 = Calibration()
    c2.set_by_two_points((100, 100), (100, 300), 40.0, axis="y")     # 垂直 200px = 40mm
    check("Y 方向：0.2 mm/px", abs(c2.sy - 0.2) < 1e-9, f"实际 {c2.sy}")

    c3 = Calibration()
    c3.set_by_two_points((0, 0), (200, 100), 10.0, axis="auto")      # dx>dy → 按 X
    check("auto 判为 X：0.05", abs(c3.sx - 0.05) < 1e-9)

    try:
        Calibration().set_by_two_points((5, 5), (5, 5), 1.0)
        fails.append("重合点没有报错")
        print("  [FAIL] 重合点没有报错")
    except ValueError:
        print("  [OK]   重合点正确报错")


def test_reverse() -> None:
    print("[3] 用实测值反算（现场：设定 1 / 实测 1.433）")
    c = Calibration()
    c.set_xy(0.05, 0.05)
    k = c.set_by_known_value(1.0, 1.433, axis="x")
    expect = 0.05 * (1.0 / 1.433)
    check("比例缩放系数 ≈ 1/1.433", abs(k - 1 / 1.433) < 1e-9)
    check("新 sx 正确", abs(c.sx - expect) < 1e-12, f"实际 {c.sx}")
    check("Y 同步缩放", abs(c.sy - expect) < 1e-12)

    # 反算后：同一段像素，测出来应该正好等于真实值 1.0mm
    px = 1.433 / 0.05          # 这段像素在原比例下正好测成 1.433mm
    check("反算后同一段像素测出 1.000mm", abs(px * c.sx - 1.0) < 1e-9, f"实际 {px * c.sx}")

    try:
        Calibration().set_by_known_value(0, 1.0)
        fails.append("真实值 0 未报错")
        print("  [FAIL] 真实值 0 未报错")
    except ValueError:
        print("  [OK]   真实值 0 正确报错")


def test_distance_xy() -> None:
    print("[4] distance_pp 用 X/Y 分别换算")
    frame = Frame.create(np.zeros((100, 100, 3), np.uint8))
    frame.meta.update({"x_mm_per_px": 0.05, "y_mm_per_px": 0.1})

    src = ItemResult(item_id="pins", type="find_blobs")
    src.blobs = [Blob(cx=10, cy=50, area=100, bbox=(0, 0, 10, 10)),
                 Blob(cx=110, cy=50, area=100, bbox=(0, 0, 10, 10))]
    it = Item(id="dx", type="distance_pp", params={"src": "pins", "blob_a": 0, "blob_b": 1})
    r = distance_pp(frame, it, {"pins": src})
    check("纯 X 方向 100px × 0.05 = 5mm", abs(r.value - 5.0) < 1e-9, f"实际 {r.value}")

    src2 = ItemResult(item_id="pins", type="find_blobs")
    src2.blobs = [Blob(cx=50, cy=10, area=100, bbox=(0, 0, 10, 10)),
                  Blob(cx=50, cy=110, area=100, bbox=(0, 0, 10, 10))]
    r2 = distance_pp(frame, it, {"pins": src2})
    check("纯 Y 方向 100px × 0.1 = 10mm", abs(r2.value - 10.0) < 1e-9, f"实际 {r2.value}")

    it_param = Item(id="dp", type="distance_pp",
                    params={"src": "pins", "blob_a": 0, "blob_b": 1, "mm_per_px": 0.2})
    r3 = distance_pp(frame, it_param, {"pins": src})
    check("显式 mm_per_px 覆盖标定：100px×0.2=20mm", abs(r3.value - 20.0) < 1e-9, f"实际 {r3.value}")


def test_area_xy() -> None:
    print("[5] area_of_blob 面积换算（sx × sy）")
    frame = Frame.create(np.zeros((100, 100, 3), np.uint8))
    frame.meta.update({"x_mm_per_px": 0.05, "y_mm_per_px": 0.1})
    src = ItemResult(item_id="pins", type="find_blobs")
    src.blobs = [Blob(cx=10, cy=10, area=10000, bbox=(0, 0, 100, 100))]
    it = Item(id="area", type="area_of_blob", params={"src": "pins", "index": 0})
    r = area_of_blob(frame, it, {"pins": src})
    check("10000 px² × (0.05×0.1) = 50 mm²", abs(r.value - 50.0) < 1e-9, f"实际 {r.value}")
    check("单位 mm2", r.unit == "mm2")


def main() -> int:
    test_set_xy()
    test_two_points()
    test_reverse()
    test_distance_xy()
    test_area_xy()
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
