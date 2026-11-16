"""自测：判定模型（设计值±容差）与区间阈值 —— 对齐现场 In-Vision 口径。

现场实测（视频 2017538603.mp4，t=300s）：
    类型 = X方向距离，起始/终止对象 = 04/04，基准 = 03，
    设计值 1 mm，容差上限 +0.08 mm，容差下限 -0.08 mm
    检测框：下阈值 111 / 上阈值 255，最小面积 25、最大面积 999999、最小宽度 2、最小高度 2

本自测覆盖：
  1. _apply_judge 四种口径（range / max / min / design）与边界值
  2. design + abs（结果取绝对值）与对称容差 tol
  3. find_blobs 区间阈值 vs 单阈值；阈值限幅 1~254
  4. find_blobs 的最小宽度/最小高度/最小圆度/面积过滤
  5. 端到端：用 field_demo 配方在合成帧上跑一帧，判定 OK
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from visionapp.core import Frame  # noqa: E402
from visionapp.ops import Item, _apply_judge, find_blobs  # noqa: E402
from visionapp.recipe import Pipeline, Recipe  # noqa: E402
from visionapp.sources import open_source  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


def test_judge() -> None:
    print("[1] 判定口径（design = 设计值±容差）")
    j = {"kind": "design", "design": 1.0, "tol_upper": 0.08, "tol_lower": -0.08}
    check("中心值 1.000 → OK", _apply_judge(j, 1.000) is True)
    check("上限边界 1.080 → OK", _apply_judge(j, 1.080) is True)
    check("下限边界 0.920 → OK", _apply_judge(j, 0.920) is True)
    check("超上限 1.081 → NG", _apply_judge(j, 1.081) is False)
    check("超下限 0.919 → NG", _apply_judge(j, 0.919) is False)
    check("现场例子 实测1.433 → NG", _apply_judge(j, 1.433) is False)

    j_abs = dict(j, abs=True)
    check("abs: 1.08 → OK", _apply_judge(j_abs, 1.08) is True)
    check("abs: 0.92 → OK", _apply_judge(j_abs, 0.92) is True)
    check("abs: 1.10 → NG", _apply_judge(j_abs, 1.10) is False)

    j_sym = {"kind": "design", "design": 10.0, "tol": 0.5}
    check("对称容差 ±0.5: 10.5 → OK", _apply_judge(j_sym, 10.5) is True)
    check("对称容差 ±0.5: 10.6 → NG", _apply_judge(j_sym, 10.6) is False)

    check("range 兼容", _apply_judge({"kind": "range", "min": 2, "max": 2}, 2) is True)
    check("max 兼容", _apply_judge({"kind": "max", "value": 5}, 6) is False)
    check("min 兼容", _apply_judge({"kind": "min", "value": 5}, 4) is False)
    check("无判定 → None", _apply_judge(None, 1.0) is None)
    check("无值 → None", _apply_judge(j, None) is None)


def test_threshold_clamp() -> None:
    print("[2] 阈值 0/255 硬拦截（本树不静默钳到 1~254 后继续测）")
    from visionapp.ops import Item
    from visionapp.tuning import threshold_block_reason
    frame = make_frame()
    check("0 有拦截原因", "拦截" in threshold_block_reason(0, None))
    check("255 有拦截原因", "拦截" in threshold_block_reason(111, 255))
    blocked = find_blobs(frame, Item(id="z", type="find_blobs", params={
        "threshold": 10, "threshold_high": 255, "morph_kernel": 0, "min_area": 1,
    }), {})
    check("255 不定位", blocked.blocked and blocked.blobs == [] and blocked.ok is False, blocked.message)
    blocked0 = find_blobs(frame, Item(id="z0", type="find_blobs", params={
        "threshold": 0, "morph_kernel": 0, "min_area": 1,
    }), {})
    check("0 不定位", blocked0.blocked and blocked0.blobs == [], blocked0.message)


def make_frame() -> Frame:
    """黑底：两个大亮块(61x81) + 一条细线(201x3) + 一个小圆(r=6)。"""
    img = np.zeros((240, 320, 3), np.uint8)
    cv2.rectangle(img, (40, 60), (100, 140), (220, 220, 220), -1)
    cv2.rectangle(img, (180, 60), (240, 140), (220, 220, 220), -1)
    cv2.rectangle(img, (40, 200), (240, 202), (220, 220, 220), -1)
    cv2.circle(img, (280, 200), 6, (220, 220, 220), -1)
    return Frame.create(img)


def test_find_blobs() -> None:
    print("[3] find_blobs 区间阈值与过滤")
    frame = make_frame()

    it = Item(id="b1", type="find_blobs",
              params={"threshold_low": 111, "threshold_high": 254,
                      "morph_kernel": 0, "min_area": 25, "max_area": 999999})
    n = len(find_blobs(frame, it, {}).blobs)
    check("区间阈值 111~254 找到 4 个目标", n == 4, f"实际 {n}")

    it_single = Item(id="b2", type="find_blobs",
                     params={"threshold": 128, "morph_kernel": 0, "min_area": 25})
    n2 = len(find_blobs(frame, it_single, {}).blobs)
    check("单阈值（旧配方）仍可用", n2 == 4, f"实际 {n2}")

    it_w = Item(id="b3", type="find_blobs",
                params={"threshold_low": 111, "threshold_high": 254, "morph_kernel": 0,
                        "min_area": 25, "min_width": 20, "min_height": 20})
    n3 = len(find_blobs(frame, it_w, {}).blobs)
    check("最小宽度/高度 20 → 只剩 2 个大块", n3 == 2, f"实际 {n3}")

    it_c0 = Item(id="b4a", type="find_blobs",
                 params={"threshold_low": 111, "threshold_high": 254, "morph_kernel": 0,
                         "min_area": 25, "min_circularity": 0.01})
    n4 = len(find_blobs(frame, it_c0, {}).blobs)
    check("圆度阈值 0.01 不过滤 → 4 个（细线圆度≈0.04，0.05 就会滤掉）", n4 == 4, f"实际 {n4}")

    it_c9 = Item(id="b4b", type="find_blobs",
                 params={"threshold_low": 111, "threshold_high": 254, "morph_kernel": 0,
                         "min_area": 25, "min_circularity": 0.99})
    n5 = len(find_blobs(frame, it_c9, {}).blobs)
    check("圆度阈值 0.99 全部滤掉 → 0 个", n5 == 0, f"实际 {n5}")

    it_area = Item(id="b5", type="find_blobs",
                   params={"threshold_low": 111, "threshold_high": 254, "morph_kernel": 0,
                           "min_area": 25, "max_area": 50})
    n6 = len(find_blobs(frame, it_area, {}).blobs)
    check("最大面积 50 滤掉大块 → 0 个", n6 == 0, f"实际 {n6}")

    it_judge = Item(id="b6", type="find_blobs",
                    params={"threshold_low": 111, "threshold_high": 254, "morph_kernel": 0,
                            "min_area": 25},
                    judge={"kind": "range", "min": 4, "max": 4})
    check("数量判定 4~4 → OK", find_blobs(frame, it_judge, {}).ok is True)


def test_pipeline() -> None:
    print("[4] 端到端：field_demo 配方（设计值±容差，合成帧源）")
    path = os.path.join(BASE, "recipes", "field_demo.json")
    if not os.path.exists(path):
        fails.append("recipes/field_demo.json 缺失")
        print("  [FAIL] 配方文件缺失")
        return
    recipe = Recipe.load(path)
    src = open_source("synthetic", fps=0.0, jitter_px=0.0)
    with src:
        frame = src.read()
    result, annotated = Pipeline(recipe).run(frame, annotate=True)
    for iid, r in result.results.items():
        print(f"     {iid:10s} value={r.value!r} {r.unit} ok={r.ok}")
    check("field_demo 整帧判 OK", result.ok is True)
    check("标注图生成成功", annotated is not None and annotated.shape == frame.bgr.shape)


def main() -> int:
    test_judge()
    test_threshold_clamp()
    test_find_blobs()
    test_pipeline()
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
