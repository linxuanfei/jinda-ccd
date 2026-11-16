"""自测：线形 ROI（D-29）—— 任意角度画线、选中红色、右键拖动、随配方保存。

覆盖：
  1. Rect.from_line / as_dict / from_any 往返，外接矩形正确
  2. clamp 越界处理
  3. 跟踪平移 shift_rect 同时移动线两端
  4. 配方保存/加载保留线形（JSON 里是 {"shape":"line","line":[...]}）
  5. 线形 ROI 参与检测：算子按外接矩形裁图（find_blobs 能测到框内的针）
  6. 界面链路：lineCreated → 写入检测项；选中时描边变红；Shift 吸附水平
"""

from __future__ import annotations

import json
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from visionapp.qtcompat import QPointF  # noqa: E402
from visionapp.qtcompat import QApplication  # noqa: E402

from visionapp.core import Frame, Rect  # noqa: E402
from visionapp.ops import find_blobs, Item  # noqa: E402
from visionapp.recipe import Recipe  # noqa: E402
from visionapp.settings import AppSettings  # noqa: E402
from visionapp.sources import open_source  # noqa: E402
from visionapp.tracking import shift_rect  # noqa: E402
from visionapp.ui import theme  # noqa: E402
from visionapp.ui.main_window import MainWindow  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


def test_core() -> None:
    print("[1] 线形 Rect 基础")
    r = Rect.from_line(100, 200, 300, 215)
    check("is_line", r.is_line() is True)
    check("端点保存", r.line == (100, 200, 300, 215), str(r.line))
    check("外接矩形", r.as_tuple() == (100, 200, 201, 16), str(r.as_tuple()))
    check("bbox() 返回矩形", r.bbox().shape == "rect" and r.bbox().as_tuple() == (100, 200, 201, 16))

    d = r.as_dict()
    check("as_dict 是 line 结构", isinstance(d, dict) and d["shape"] == "line")

    r2 = Rect.from_any(d)
    check("from_any 往返", r2.is_line() and r2.line == (100, 200, 300, 215))

    plain = Rect.from_any([10, 20, 30, 40])
    check("矩形仍走旧格式", plain.as_dict() == [10, 20, 30, 40])
    check("矩形 is_line=False", plain.is_line() is False)

    # 反向线（右下到左上）也要能算出正的外接矩形
    r3 = Rect.from_line(300, 215, 100, 200)
    check("反向线外接矩形", r3.as_tuple() == (100, 200, 201, 16), str(r3.as_tuple()))

    clamped = Rect.from_line(-50, -20, 500, 900).clamp(320, 240)
    ln = clamped.line
    check("clamp 后端点都在画面内",
          ln is not None and all(0 <= v <= 319 for v in (ln[0], ln[2]))
          and all(0 <= v <= 239 for v in (ln[1], ln[3])), str(ln))


def test_shift() -> None:
    print("[2] 跟踪平移")
    r = Rect.from_line(100, 200, 300, 210)
    s = shift_rect(r, 7, -5)
    check("线两端一起移动", s.is_line() and s.line == (107, 195, 307, 205), str(s.line))
    check("外接矩形同步", s.as_tuple() == (107, 195, 201, 11), str(s.as_tuple()))


def test_serialization(tmpdir: str) -> None:
    print("[3] 配方保存/加载")
    it = Item(id="edge", type="find_blobs", roi=Rect.from_line(50, 60, 250, 62),
              params={"threshold": 120, "min_area": 20})
    rec = Recipe(name="line_test", items=[it])
    path = os.path.join(tmpdir, "line.json")
    rec.save(path)
    raw = json.load(open(path, encoding="utf-8"))
    roi_raw = raw["items"][0]["roi"]
    check("JSON 里是 {shape:line}", isinstance(roi_raw, dict) and roi_raw.get("shape") == "line",
          str(roi_raw))
    back = Recipe.load(path)
    check("加载回来还是线", back.items[0].roi.is_line() and back.items[0].roi.line == (50, 60, 250, 62))


def test_ops() -> None:
    print("[4] 线形 ROI 参与检测（按外接矩形裁图）")
    img = np.zeros((240, 320, 3), np.uint8)
    cv2.rectangle(img, (60, 100), (80, 160), (230, 230, 230), -1)     # 框内的针
    cv2.rectangle(img, (250, 20), (270, 60), (230, 230, 230), -1)     # 框外，不该被算进去
    frame = Frame.create(img)
    it = Item(id="pins", type="find_blobs",
              roi=Rect.from_line(40, 130, 200, 132),          # 一条水平线，外接矩形高 3px
              params={"threshold": 120, "morph_kernel": 0, "min_area": 5})
    r = find_blobs(frame, it, {})
    check("线形 ROI 能测到框内目标", r.value == 1, f"实际 {r.value}")
    it.roi = Rect.from_line(40, 130, 200, 132, pad=200)   # 加厚后覆盖针的高度
    r2 = find_blobs(frame, it, {})
    check("加厚外接矩形后仍只算框内", r2.value == 1, f"实际 {r2.value}")


def test_ui() -> int:
    print("[5] 界面链路")
    app = QApplication.instance() or QApplication(sys.argv)
    settings = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
    src = open_source("synthetic", fps=0.0)
    src.open()
    win = MainWindow(recipe=Recipe(name="ui_line"), source=src, settings=settings)
    win.show()
    win.timer.stop()
    win.add_item()
    item = win.recipe.items[0]

    win.view.lineCreated.emit(Rect.from_line(100, 200, 300, 215))
    check("画线写入检测项", item.roi is not None and item.roi.is_line(),
          str(item.roi.line if item.roi else None))
    check("视图里有线段对象", item.id in win.view._lines)
    check("线段已加入场景（否则画面上看不见）",
          any(isinstance(x, type(win.view._lines[item.id])) for x in win.view.scene().items()))

    win.view.set_active_roi(item.id)
    line_item = win.view._lines[item.id]
    red = theme.color("state.ng").lower()
    check("选中后描边变红", line_item.pen().color().name().lower() == red,
          f"{line_item.pen().color().name()} vs {red}")

    win.view.set_line_mode(True)
    check("进入画线模式", win.view.is_line_mode() is True)
    p = win.view._snap_point(QPointF(100, 100), QPointF(200, 106))
    check("Shift 吸附为水平（y 不变）", abs(p.y() - 100) < 1e-6, f"{p.x()},{p.y()}")
    p2 = win.view._snap_point(QPointF(100, 100), QPointF(104, 200))
    check("Shift 吸附为垂直（x 不变）", abs(p2.x() - 100) < 1e-6, f"{p2.x()},{p2.y()}")
    p3 = win.view._snap_point(QPointF(0, 0), QPointF(100, 96))
    ang = math.degrees(math.atan2(p3.y(), p3.x()))
    check("Shift 吸附到 45°", abs(ang - 45) < 1e-6, f"{ang:.2f}°")

    # 线形 ROI 的列表文案
    win._refresh_item_list()
    text = win.list_items.item(0).text()
    check("列表显示 LINE 坐标", "LINE(" in text, text)

    win.close()
    src.close()
    return 0


def main() -> int:
    tmpdir = os.path.join(BASE, "out", "line_test")
    os.makedirs(tmpdir, exist_ok=True)
    test_core()
    test_shift()
    test_serialization(tmpdir)
    test_ops()
    test_ui()
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
