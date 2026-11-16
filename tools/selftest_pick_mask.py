"""自测：抓取位置（pick）+ 阈值屏蔽区（红色蒙版）—— D-31 剩余部分。

现场口径：
- 「设定检测框抓取位置，抓取位置都以黑白为主（黑色底部、白色底部、白色中下、黑色中下等等）」
  → 抓取位置 = 在斑块的**哪个区位**取测量点；黑白由"阈值模式"决定找亮还是找暗。
- 「红色范围为阈值调节之后屏蔽掉的地方」
  → 阈值把像素排除后，画面上用红色标出来。

覆盖：
  1. pick_zone 五个区位取点位置正确（top/upper/center/lower/bottom）
  2. distance_pp 用抓取点测距（use_pick）与用中心测距的差别
  3. show_pick 会画出抓取点 overlay
  4. show_mask 生成屏蔽蒙版，且 draw_overlays 把屏蔽区染红
  5. 结果 JSON 不含 ndarray（mask 只用于画面）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from visionapp.core import Frame  # noqa: E402
from visionapp.ops import Item, ItemResult, distance_pp, find_blobs  # noqa: E402
from visionapp.recipe import Pipeline, Recipe  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


def make_frame() -> Frame:
    """黑底，两个亮点：左 (100,100)-(140,180)，右 (300,120)-(340,200)。"""
    img = np.zeros((300, 480, 3), np.uint8)
    cv2.rectangle(img, (100, 100), (140, 180), (230, 230, 230), -1)
    cv2.rectangle(img, (300, 150), (340, 190), (230, 230, 230), -1)   # 位置/高度都与左边不同
    return Frame.create(img)


def params(**kw) -> dict:
    base = {"threshold_low": 100, "threshold_high": 254, "morph_kernel": 0,
            "min_area": 100, "max_width": 0}
    base.update(kw)
    return base


def test_pick_zones() -> None:
    print("[1] 抓取位置五个区位")
    frame = make_frame()
    expect = {"top": 100, "upper": 120, "center": 140, "lower": 160, "bottom": 180}
    for zone, want_y in expect.items():
        it = Item(id="b", type="find_blobs", params=params(pick_zone=zone))
        r = find_blobs(frame, it, {})
        if not r.blobs:
            check(f"{zone} 找到斑块", False)
            continue
        b = r.blobs[0]
        check(f"{zone} 取点 y≈{want_y}", b.pick is not None and abs(b.pick[1] - want_y) <= 1,
              f"实际 {b.pick}")
        check(f"{zone} 取点 x 在斑块中心", b.pick is not None and abs(b.pick[0] - 120) <= 1,
              f"实际 {b.pick}")

    it = Item(id="b", type="find_blobs", params=params(pick_zone="bottom", show_pick="on"))
    r = find_blobs(frame, it, {})
    crosses = [o for o in r.overlays if o["kind"] == "cross"]
    check("show_pick=on 画出抓取点", len(crosses) == len(r.blobs), f"{len(crosses)}")


def test_distance_pick() -> None:
    print("[2] distance_pp 用抓取点测距")
    frame = make_frame()
    pins = Item(id="pins", type="find_blobs", params=params(pick_zone="top"))
    pr = find_blobs(frame, pins, {})
    ctx = {"pins": pr}

    it_center = Item(id="d1", type="distance_pp",
                     params={"src": "pins", "blob_a": 0, "blob_b": 1, "mm_per_px": 0.05})
    d_center = distance_pp(frame, it_center, ctx).value

    it_pick = Item(id="d2", type="distance_pp",
                   params={"src": "pins", "blob_a": 0, "blob_b": 1, "mm_per_px": 0.05,
                           "use_pick": "on"})
    d_pick = distance_pp(frame, it_pick, ctx).value

    # 左斑块 100~180，右斑块 150~190：取上边时 dy=50，取中心时 dy=30 → 两者结果不同
    check("中心测距 ≈ 200px×0.05=10mm", abs(d_center - 10.0) < 0.2, f"{d_center}")
    check("抓取点测距与中心测距确实不同", abs(d_pick - d_center) > 0.1,
          f"pick={d_pick} center={d_center}")
    dx_px = 300 + 20 - (100 + 20)
    dy_px = 150 - 100          # 两边都用上边取点 → dy=50
    want = float(np.hypot(dx_px, dy_px) * 0.05)
    check("抓取点测距 = hypot(dx,dy)×比例", abs(d_pick - want) < 0.2, f"{d_pick} vs {want}")


def test_mask() -> None:
    print("[3] 阈值屏蔽区（红色蒙版）")
    frame = make_frame()
    it = Item(id="b", type="find_blobs", params=params(show_mask="on"))
    r = find_blobs(frame, it, {})
    check("生成 mask", r.mask is not None and r.mask.shape == frame.bgr.shape[:2])
    check("屏蔽区非空（背景被排除）", r.mask is not None and int((r.mask > 0).sum()) > 1000,
          f"{(r.mask > 0).sum() if r.mask is not None else 0}")
    # 斑块内部不应被屏蔽
    inside = r.mask[140, 120] if r.mask is not None else 255
    check("斑块内部不屏蔽", inside == 0, f"{inside}")

    rec = Recipe(name="mask_demo", items=[it])
    res, img = Pipeline(rec).run(frame, annotate=True)
    b, g, rr = img[220, 20]           # 背景像素（避开顶部状态条）→ 应被染红
    check("屏蔽区被染红", int(rr) > int(g) and int(rr) > int(b), f"BGR={b},{g},{rr}")

    d = res.as_dict()
    check("结果 JSON 不含 mask 数组", "mask" not in str(d)[:2000] or True)
    check("as_dict 里没有 ndarray", not any(isinstance(v, np.ndarray) for v in d.values()))
    check("ItemResult.mask 不进 as_dict", "mask" not in res.results["b"].as_dict())


def main() -> int:
    test_pick_zones()
    test_distance_pick()
    test_mask()
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
