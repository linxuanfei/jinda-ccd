"""调机自测（offscreen）。

覆盖：HPro 标称±公差、阈值 0/255 拦截、X/Y 标定、跟踪平移、
Ctrl 多框与对齐、相机路数、未登录不能自行升级角色。
不使用、不打印任何口令或摘要。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from visionapp.calibration import Calibration
from visionapp.camera_cfg import trigger_of, update_camera_numbers
from visionapp.core import Frame, Rect
from visionapp.hpro_import import _parse_judge, load_mapping
from visionapp.ops import Blob, Item, ItemResult, distance_pp, find_blobs
from visionapp.recipe import Pipeline, Recipe
from visionapp.stations import LineConfig, StationConfig
from visionapp.tuning import (
    align_rois,
    bilateral_window,
    clamp_horizontal_line,
    moved_rect,
    sample_point,
    scale_for_reading,
    selection_after_draw,
    shielded_count,
)
from visionapp.ui.image_view import ImageView
from visionapp.ui.main_window import MainWindow
from visionapp.ui.security_dialog import LoginDialog

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _blobs_row(xs, y=0.0, area=10.0):
    return [Blob(cx=float(x), cy=float(y), area=area, bbox=(int(x), int(y), 2, 2)) for x in xs]


def main() -> int:
    from visionapp.qtcompat import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
        if not ok:
            fails.append(name if not detail else f"{name}: {detail}")

    mapping = load_mapping(os.path.join(BASE, "config", "hpro_types.json"))
    judge = _parse_judge(["0 1 0 0 1.0 1.0 0.08"], mapping["judge"])
    check("hpro ±", judge == {"kind": "range", "min": 0.92, "max": 1.08}, str(judge))
    for mode, spec in mapping["judge"]["modes"].items():
        if str(mode).startswith("_"):
            continue
        expr = str(spec.get("expr", ""))
        if str(mode) in ("0", "1", "2"):
            check(f"hpro expr {mode}", expr == "low - tol ~ low + tol", expr)
    lo, hi = bilateral_window(1.0, -0.2, 0.2)
    check("signed tol", abs(lo - 0.8) < 1e-9 and abs(hi - 1.2) < 1e-9, f"{lo},{hi}")

    bright = np.full((48, 64, 3), 200, np.uint8)
    frame = Frame.create(bright)
    blocked = find_blobs(
        frame,
        Item(id="b", type="find_blobs", params={
            "threshold": 10, "threshold_high": 255, "blur": 0, "morph_kernel": 0, "min_area": 1,
        }),
        {},
    )
    check("block 255", blocked.blocked and blocked.ok is False and blocked.blobs == [], blocked.message)
    blocked0 = find_blobs(
        frame,
        Item(id="b0", type="find_blobs", params={
            "threshold": 0, "blur": 0, "morph_kernel": 0, "min_area": 1,
        }),
        {},
    )
    check("block 0", blocked0.blocked and blocked0.blobs == [], blocked0.message)
    alive = find_blobs(
        frame,
        Item(id="okb", type="find_blobs", params={
            "threshold": 128, "blur": 0, "morph_kernel": 0, "min_area": 1,
        }),
        {},
    )
    check("threshold 128 still finds", alive.blocked is False and len(alive.blobs) == 1, alive.message)

    pipe = Pipeline(Recipe(items=[
        Item(id="pins", type="find_blobs", params={
            "threshold": 10, "threshold_high": 255, "blur": 0, "morph_kernel": 0, "min_area": 1,
        }),
        Item(id="gap", type="distance_pp", params={
            "src": "pins", "axis": "x", "mm_per_px": 0.01, "nominal": 1,
            "tol_lower": 0.08, "tol_upper": 0.08,
        }),
    ]))
    result, _img = pipe.run(frame, annotate=False)
    gap = result.results["gap"]
    check("255 blocks measure", gap.blocked and gap.ok is False and gap.value is None, gap.message)

    box = (20, 20, 40, 40)
    expect = {
        "黑色底部": (40.0, 70.0),
        "白色底部": (40.0, 60.0),
        "白色中下": (40.0, 50.0),
        "黑色中下": (40.0, 55.0),
        "白色中上": (40.0, 30.0),
        "白色左侧": (20.0, 40.0),
    }
    for name, pt in expect.items():
        got = sample_point(box, name)
        check(f"polarity {name}", got == pt, str(got))
    # 框高不是 40 时，黑色底部不能再是底边 + 10 像素。
    tall = sample_point((0, 0, 100, 80), "黑色底部")
    check("black bottom ratio", tall == (50.0, 100.0), str(tall))
    import tempfile
    from visionapp.imgio import imread as imread_safe
    from visionapp.imgio import imwrite as imwrite_safe
    cn_dir = tempfile.mkdtemp(prefix="中文路径_")
    cn_path = os.path.join(cn_dir, "样张.png")
    sample = np.zeros((8, 8, 3), np.uint8)
    sample[1, 2] = (0, 0, 255)
    check("cn imwrite", imwrite_safe(cn_path, sample) and os.path.exists(cn_path))
    back = imread_safe(cn_path)
    check("cn imread", back is not None and int(back[1, 2, 2]) == 255, str(None if back is None else back[1, 2]))
    gray = np.zeros((80, 120), np.uint8)
    gray[20:60, 20:60] = 250
    count = shielded_count(gray, 100, 254)
    check("red mask", count == 8000, str(count))
    color = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    located = find_blobs(
        Frame.create(color),
        Item(id="p", type="find_blobs", params={
            "threshold": 100, "threshold_high": 254, "polarity": "白色底部",
            "blur": 0, "morph_kernel": 0, "min_area": 1,
        }),
        {},
    )
    pt = (located.blobs[0].cx, located.blobs[0].cy) if located.blobs else None
    check("polarity on blob", pt == (40.0, 60.0) and "屏蔽 8000" in located.message, f"{pt} {located.message}")

    src = ItemResult(item_id="s", type="find_blobs", blobs=_blobs_row([0.0, 143.3], y=4.0))
    item = Item(id="x", type="distance_pp", params={
        "src": "s", "axis": "x", "mm_per_px": 0.01, "datum": "O1", "nominal": 1,
        "tol_lower": 0.08, "tol_upper": 0.08,
    }, judge={"kind": "range", "min": 0.92, "max": 1.08})
    bad = distance_pp(Frame.create(np.zeros((8, 8, 3), np.uint8)), item, {"s": src})
    check("1.433 ng", abs(bad.value - 1.433) < 1e-3 and bad.ok is False, f"{bad.value} {bad.ok}")
    scale = scale_for_reading(143.3, 1.0)
    check("scale", abs(scale - (1.0 / 143.3)) < 1e-12, str(scale))
    cal = Calibration()
    check("axis only not valid yet", cal.valid is False)
    cal.set_axis(143.3, 1.0, "x")
    check("x axis makes valid", cal.valid is True and cal.mm_per_px <= 0, f"valid={cal.valid} mm={cal.mm_per_px}")
    cal.set_axis(10.0, 50.0, "y")
    frame2 = Frame.create(np.zeros((8, 8, 3), np.uint8))
    frame2.meta["mm_per_px_x"] = cal.mm_per_px_x
    frame2.meta["mm_per_px_y"] = cal.mm_per_px_y
    item.params["mm_per_px"] = 0
    good = distance_pp(frame2, item, {"s": src})
    check("1.000 ok", abs(good.value - 1.0) < 1e-3 and good.ok is True, f"{good.value} {good.ok}")
    check("y scale stays out of x", abs(good.value - 1.0) < 1e-3 and cal.mm_per_px_y > 1, f"{good.value} y={cal.mm_per_px_y}")

    ysrc = ItemResult(item_id="s", type="find_blobs", blobs=[
        Blob(0, 0, 10, (0, 0, 1, 1)), Blob(7, 100, 10, (0, 0, 1, 1)),
    ])
    yitem = Item(id="y", type="distance_pp", params={
        "src": "s", "axis": "y", "mm_per_px": 0.01, "nominal": 1,
        "tol_lower": 0.08, "tol_upper": 0.08,
    })
    yres = distance_pp(Frame.create(np.zeros((8, 8, 3), np.uint8)), yitem, {"s": ysrc})
    check("y stick", abs(yres.value - 1.0) < 1e-6 and "贴板" in yres.message, yres.message)

    xs = [i * 100 for i in range(8)]
    chain = ItemResult(item_id="s", type="find_blobs", blobs=_blobs_row(xs))
    stepped = Item(id="chain", type="distance_pp", params={
        "src": "s", "blob_a": 0, "blob_b": 7, "axis": "x", "each_step": "是",
        "mm_per_px": 0.01, "nominal": 1, "tol_lower": 0.08, "tol_upper": 0.08,
    })
    chain_res = distance_pp(Frame.create(np.zeros((8, 8, 3), np.uint8)), stepped, {"s": chain})
    check(
        "each step",
        len(chain_res.steps) == 7 and abs(chain_res.steps[0]["value"] - 1.0) < 1e-6 and chain_res.ok is True,
        str(len(chain_res.steps)),
    )

    def shot(dx: int, width: int = 360) -> Frame:
        img = np.zeros((160, width, 3), np.uint8)
        cv2.rectangle(img, (40 + dx, 40), (79 + dx, 79), (250, 250, 250), -1)
        return Frame.create(img)

    def tracked(dx: int):
        recipe = Recipe(name="track")
        recipe.track = {
            "enabled": True,
            "roi": [0, 0, 120, 120],
            "min_score": 0.5,
            "search_margin": 160,
            "apply_to": ["box"],
        }
        recipe.items = [Item(id="box", type="find_blobs", roi=Rect(10, 12, 20, 18), params={
            "threshold": 128, "morph_kernel": 0, "min_area": 1,
        })]
        pipeline = Pipeline(recipe)
        pipeline.run(shot(0), annotate=False)
        second = shot(dx)
        result, _img = pipeline.run(second, annotate=False)
        moved = next(it.roi.x for it in pipeline.last_items if it.id == "box")
        return recipe.items[0].roi.x, moved, result, second.meta.get("track_note", "")

    saved_x, x20, res20, note20 = tracked(20)
    check("track +20", saved_x == 10 and abs(x20 - 30) <= 1 and res20.ok is not False, f"{saved_x} {x20} {note20}")
    saved_x, x120, res120, note120 = tracked(120)
    check("track +120", saved_x == 10 and abs(x120 - 130) <= 1 and res120.track and res120.track.get("ok"), f"{x120} {note120}")
    saved_x, x200, res200, note200 = tracked(200)
    check(
        "track fail ng",
        saved_x == 10 and x200 == 10 and res200.ok is False and "__track__" in res200.results,
        f"{x200} {note200} ok={res200.ok}",
    )
    dumped = Recipe(name="track", items=[], track=res200.track and {"taught_xy": [1, 2], "base_rois": {}, "enabled": True, "roi": [0, 0, 120, 120]})
    blob = dumped.to_dict()["track"]
    check("no runtime track in recipe", "taught_xy" not in blob and "base_rois" not in blob, str(blob))

    check("hline back", clamp_horizontal_line(10, 40, 5, 90) == (10, 40, 10, 40))
    check("hline forward", clamp_horizontal_line(10, 40, 80, 12) == (10, 40, 80, 40))
    view = ImageView()
    view.draw_tool = "hline"
    line = view.rubber_rect(10, 40, 80, 12)
    check("view hline", (line.x, line.y, line.h) == (10, 40, 1) and line.w == 70, str(line.as_tuple()))
    spaced = align_rois([(0, 0, 10, 10), (10, 4, 8, 8), (100, 1, 6, 6)], "hspace")
    check("hspace", [r[0] for r in spaced] == [0, 50, 100], str([r[0] for r in spaced]))
    sized = align_rois([(0, 0, 10, 10), (3, 3, 4, 4), (8, 1, 2, 2)], "equal")
    check("equal", all((r[2], r[3]) == (10, 10) for r in sized))
    topped = align_rois([(0, 0, 10, 10), (5, 6, 10, 10)], "top")
    check("top", [r[1] for r in topped] == [0, 0])
    bottomed = align_rois([(0, 0, 10, 10), (4, 3, 10, 4)], "bottom")
    check("bottom edges", [r[1] + r[3] for r in bottomed] == [10, 10])
    lefted = align_rois([(0, 0, 10, 10), (6, 2, 10, 10)], "left")
    check("left", [r[0] for r in lefted] == [0, 0])
    righted = align_rois([(0, 0, 10, 10), (2, 2, 4, 4)], "right")
    check("right edges", [r[0] + r[2] for r in righted] == [10, 10])
    check("ctrl add", selection_after_draw(["a"], "b", True) == ["a", "b"])
    check("ctrl replace", selection_after_draw(["a", "b"], "c", False) == ["c"])
    moved = moved_rect(30, 30, 20, 20, 40, 40, 55, 48)
    check("right drag", moved == (45, 38, 20, 20), str(moved))

    src_cfg = {"kind": "synthetic"}
    check("trigger default", trigger_of(src_cfg) == "hardware")
    mode = update_camera_numbers(src_cfg, exposure=10)
    check("exposure stores continuous", mode == "continuous" and src_cfg.get("exposure") == 10)
    check("no driver payload", "sdk" not in src_cfg and "model" not in src_cfg)
    line_cfg = LineConfig.load(os.path.join(BASE, "config", "line.json"))
    check("six cameras", len(line_cfg.stations) >= 6, str(len(line_cfg.stations)))
    before = len(line_cfg.stations)
    line_cfg.stations.append(StationConfig(id=99, name="机位99"))
    again = len(LineConfig.load(os.path.join(BASE, "config", "line.json")).stations)
    check("seventh camera", len(line_cfg.stations) == before + 1 and again == before)

    win = MainWindow()
    win.timer.stop()
    check("boot operator", win.role == "operator" and not win.editor_panel.isEnabled(), win.role)
    original_exec = LoginDialog.exec
    LoginDialog.exec = lambda self: False
    try:
        stations_before = len(win.manager.line.stations)
        attempts = win.login_attempts
        win.open_line_config()
        win.open_security()
        win.align_selected("left")
        win.open_camera_tune()
        win.calibrate_axis()
        check("login called", win.login_attempts > attempts, str(win.login_attempts))
        check("role unchanged", win.role == "operator")
        check("stations unchanged", len(win.manager.line.stations) == stations_before)
        check("no one-click promote", not hasattr(win, "login_admin") and not hasattr(win, "open_admin"))
    finally:
        LoginDialog.exec = original_exec
        win.close()

    print()
    if fails:
        for item in fails:
            print("  [FAIL]", item)
        print(f"RESULT: FAIL ({len(fails)})")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
