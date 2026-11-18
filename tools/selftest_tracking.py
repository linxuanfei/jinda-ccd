"""自测：跟踪框（D-33）—— 产品位置偏移补偿。

覆盖：
  1. Tracker 学习参考图 / 首帧偏移为 0
  2. 产品整体平移后，(dx,dy) 能准确找回（搜索余量内）
  3. 相似度低于阈值 → 跟踪失败（ok=False，不返回错误坐标）
  4. TrackConfig 序列化往返 / valid 判定
  5. Pipeline 集成：产品平移后，开启跟踪 → 测量值不变；关闭跟踪 → 测不到/测错
  6. apply_to 只对指定对象生效
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from visionapp.core import Frame, Rect  # noqa: E402
from visionapp.recipe import Pipeline, Recipe  # noqa: E402
from visionapp.tracking import TrackConfig, Tracker, shift_rect  # noqa: E402
from visionapp.ops import Item  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


def make_product(offset: tuple[int, int] = (0, 0), size=(320, 240)) -> np.ndarray:
    """黑底 + 两个白针（检测目标）+ 一块彩色纹理（跟踪特征），两块区域不重叠。"""
    ox, oy = offset
    img = np.zeros((size[1], size[0], 3), np.uint8)
    # 检测目标：两根白针（y 90~190）
    cv2.rectangle(img, (60 + ox, 90 + oy), (100 + ox, 190 + oy), (230, 230, 230), -1)
    cv2.rectangle(img, (160 + ox, 90 + oy), (200 + ox, 190 + oy), (230, 230, 230), -1)
    # 跟踪特征：左上角一小块纹理（y 25~62），与针不重叠
    for i, (bx, by, bw, bh) in enumerate([(20, 25, 22, 14), (50, 28, 18, 18),
                                         (24, 45, 16, 16), (55, 46, 20, 12)]):
        cv2.rectangle(img, (bx + ox, by + oy), (bx + ox + bw, by + oy + bh),
                      (90 + i * 40, 200 - i * 30, 120 + i * 20), -1)
    return img


def test_tracker() -> None:
    print("[1] Tracker 定位")
    cfg = TrackConfig(roi=Rect(15, 20, 70, 50), min_score=0.5, search_margin=60)
    tr = Tracker(cfg)

    base = Frame.create(make_product((0, 0)))
    r0 = tr.locate(base)
    check("首帧学习参考图，偏移 0", r0.ok and abs(r0.dx) < 0.6 and abs(r0.dy) < 0.6,
          f"{r0.dx},{r0.dy} {r0.message}")
    check("首帧 learned=True", r0.learned is True)

    r1 = tr.locate(Frame.create(make_product((12, -7))))
    check("平移(+12,-7) 找回", r1.ok and abs(r1.dx - 12) <= 1 and abs(r1.dy + 7) <= 1,
          f"dx={r1.dx} dy={r1.dy}")
    check("相似度高", r1.score > 0.8, f"score={r1.score:.3f}")

    r2 = tr.locate(Frame.create(make_product((-12, 15))))
    check("平移(-12,+15) 找回", r2.ok and abs(r2.dx + 12) <= 1 and abs(r2.dy - 15) <= 1,
          f"dx={r2.dx} dy={r2.dy}")

    blank = np.zeros((240, 320, 3), np.uint8)
    r3 = tr.locate(Frame.create(blank))
    check("画面全黑 → 跟踪失败", r3.ok is False, f"score={r3.score:.3f} {r3.message}")
    check("失败时不返回错误坐标", r3.dx == 0.0 and r3.dy == 0.0)


def test_config() -> None:
    print("[2] TrackConfig")
    cfg = TrackConfig(roi=Rect(10, 20, 100, 80), min_score=0.8, apply_to=["pins"])
    d = cfg.to_dict()
    c2 = TrackConfig.from_dict(d)
    check("序列化往返 roi", c2.roi is not None and c2.roi.as_tuple() == (10, 20, 100, 80))
    check("序列化往返 min_score", abs(c2.min_score - 0.8) < 1e-9)
    check("序列化往返 apply_to", c2.apply_to == ["pins"])
    check("valid=True", c2.valid is True)
    check("无 roi → invalid", TrackConfig().valid is False)
    check("框太小 → invalid", TrackConfig(roi=Rect(0, 0, 4, 4)).valid is False)

    r = shift_rect(Rect(10, 10, 20, 20), 5.4, -3.6)
    check("shift_rect 四舍五入", r.as_tuple() == (15, 6, 20, 20), f"{r.as_tuple()}")
    check("shift_rect(None) → None", shift_rect(None, 1, 1) is None)


def build_recipe(with_track: bool, apply_to: list[str] | None = None) -> Recipe:
    pins = Item(id="pins", type="find_blobs",
                roi=Rect(50, 80, 170, 130),
                params={"threshold_low": 100, "threshold_high": 255, "morph_kernel": 0,
                        "min_area": 100, "min_width": 10, "min_height": 10},
                judge={"kind": "range", "min": 2, "max": 2})
    pitch = Item(id="pitch", type="distance_pp",
                 params={"src": "pins", "blob_a": 0, "blob_b": 1, "mm_per_px": 0.05})
    rec = Recipe(name="track_test", items=[pins, pitch])
    if with_track:
        rec.track = TrackConfig(roi=Rect(15, 20, 70, 50), min_score=0.5,
                                search_margin=60, apply_to=apply_to or [])
    return rec


def test_pipeline() -> None:
    print("[3] Pipeline 集成（产品平移 20,10 后测量不变）")
    base = Frame.create(make_product((0, 0)))
    shifted = Frame.create(make_product((20, 10)))

    # 无跟踪：ROI 固定，产品跑了 → 数量或数值会变
    rec_no = build_recipe(False)
    p_no = Pipeline(rec_no)
    r_base, _ = p_no.run(base, annotate=False)
    r_shift, _ = p_no.run(shifted, annotate=False)
    v_base = r_base.results["pitch"].value
    v_shift = r_shift.results["pitch"].value
    print(f"     无跟踪：base={v_base} shifted={v_shift}")

    # 有跟踪：第二帧自动补偿
    rec_yes = build_recipe(True)
    p_yes = Pipeline(rec_yes)
    r1, _ = p_yes.run(base, annotate=False)
    r2, _ = p_yes.run(shifted, annotate=False)
    check("跟踪帧带 track 信息", r2.track is not None and r2.track.get("ok") is True)
    check("补偿偏移约 (20,10)",
          abs(r2.track["dx"] - 20) <= 1 and abs(r2.track["dy"] - 10) <= 1,
          f"{r2.track}")
    check("补偿后数量仍为 2", r2.results["pins"].value == 2, f"{r2.results['pins'].value}")
    check("补偿后间距与首帧一致",
          r2.results["pitch"].value is not None
          and abs(r2.results["pitch"].value - r1.results["pitch"].value) < 1e-6,
          f"{r1.results['pitch'].value} vs {r2.results['pitch'].value}")

    # 跟踪失败 → 整帧 NG
    rec_fail = build_recipe(True)
    p_fail = Pipeline(rec_fail)
    p_fail.run(base, annotate=False)
    blank = Frame.create(np.zeros((240, 320, 3), np.uint8))
    rfail, _ = p_fail.run(blank, annotate=False)
    check("跟踪失败 → 整帧 NG", rfail.ok is False)
    check("失败结果里带原因", "__track__" in rfail.results)

    print("[4] apply_to 只作用于指定对象")
    rec_sub = build_recipe(True, apply_to=["pins"])
    p_sub = Pipeline(rec_sub)
    p_sub.run(base, annotate=False)
    r_sub, _ = p_sub.run(shifted, annotate=False)
    check("apply_to 命中时仍能测到", r_sub.results["pins"].value == 2)

    rec_none = build_recipe(True, apply_to=["nothing_here"])
    p_none = Pipeline(rec_none)
    p_none.run(base, annotate=False)
    r_none, _ = p_none.run(shifted, annotate=False)
    print(f"     apply_to 不匹配：pins={r_none.results['pins'].value}")
    # 直接看 Pipeline 本帧实际用的 ROI：命中时跟着偏移，不命中时保持原坐标
    roi_hit = next(it.roi for it in p_sub.last_items if it.id == "pins")
    roi_miss = next(it.roi for it in p_none.last_items if it.id == "pins")
    check("apply_to 命中 → ROI 跟随平移(+20,+10)",
          abs(roi_hit.x - 70) <= 1 and abs(roi_hit.y - 90) <= 1, f"{roi_hit.as_tuple()}")
    check("apply_to 不命中 → ROI 保持原坐标",
          abs(roi_miss.x - 50) <= 1 and abs(roi_miss.y - 80) <= 1, f"{roi_miss.as_tuple()}")
    check("不命中时也照样给出跟踪偏移", p_none.last_track is not None and p_none.last_track["ok"] is True)


def main() -> int:
    test_tracker()
    test_config()
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
