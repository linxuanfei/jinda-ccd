"""自测：多选 + 批量对齐（D-30）。

覆盖：
  1. 纯函数 align.py：上/下/左/右对齐、等大小、水平/垂直等间距
  2. 线形 ROI 参与对齐时两端一起动；等大小跳过线
  3. 边界：<2 个对象、等间距 <3 个、未知动作 → 明确报错
  4. 界面链路：多选（Ctrl 点选语义）→ batch_align → 撤销对齐
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.qtcompat import QApplication  # noqa: E402

from visionapp.align import ACTION_LABELS, align_rects  # noqa: E402
from visionapp.core import Rect  # noqa: E402
from visionapp.recipe import Recipe  # noqa: E402
from visionapp.settings import AppSettings  # noqa: E402
from visionapp.sources import open_source  # noqa: E402
from visionapp.ui.main_window import MainWindow  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


def base_rects() -> dict[str, Rect]:
    return {
        "a": Rect(100, 100, 50, 50),
        "b": Rect(200, 160, 60, 40),
        "c": Rect(300, 220, 40, 60),
    }


def test_aligns() -> None:
    print("[1] 四个方向对齐")
    out = align_rects(base_rects(), "align_top")
    check("上对齐：y 都等于最小 y", {r.y for r in out.values()} == {100},
          str([r.as_tuple() for r in out.values()]))

    out = align_rects(base_rects(), "align_bottom")
    check("下对齐：底边都等于最大底边", {r.y + r.h for r in out.values()} == {280},
          str([r.y + r.h for r in out.values()]))

    out = align_rects(base_rects(), "align_left")
    check("左对齐：x 都等于最小 x", {r.x for r in out.values()} == {100})

    out = align_rects(base_rects(), "align_right")
    check("右对齐：右边都等于最大右边", {r.x + r.w for r in out.values()} == {340},
          str([r.x + r.w for r in out.values()]))

    check("原字典没被改动", base_rects()["b"].y == 160)


def test_size_and_space() -> None:
    print("[2] 等大小 / 等间距")
    out = align_rects(base_rects(), "same_size", order=["b", "a", "c"])
    check("等大小：宽高都等于第一个选中(b 60x40)",
          all((r.w, r.h) == (60, 40) for r in out.values()),
          str([(r.w, r.h) for r in out.values()]))
    check("等大小保持中心不动", out["a"].x + out["a"].w / 2 == 125,
          f"{out['a'].x + out['a'].w / 2}")

    out3 = {"a": Rect(0, 100, 20, 20), "b": Rect(100, 100, 20, 20), "c": Rect(300, 100, 20, 20)}
    res = align_rects(out3, "space_h")
    centers = sorted(r.x + r.w / 2 for r in res.values())
    check("水平等间距：三个中心等距", abs((centers[1] - centers[0]) - (centers[2] - centers[1])) < 0.51,
          str(centers))
    check("水平等间距：首尾不动", res["a"].x == 0 and res["c"].x == 300)

    out4 = {"a": Rect(100, 0, 20, 20), "b": Rect(100, 100, 20, 20), "c": Rect(100, 300, 20, 20)}
    res = align_rects(out4, "space_v")
    centers = sorted(r.y + r.h / 2 for r in res.values())
    check("垂直等间距：中心等距", abs((centers[1] - centers[0]) - (centers[2] - centers[1])) < 0.51,
          str(centers))


def test_lines_and_errors() -> None:
    print("[3] 线形 ROI / 边界")
    line = Rect.from_line(50, 200, 250, 210)
    rects = {"a": Rect(100, 100, 50, 50), "ln": line}
    out = align_rects(rects, "align_top")
    ln = out["ln"]
    check("线形 ROI 上对齐：整体平移到外接矩形顶边=100",
          ln.is_line() and ln.y == 100, str(ln.as_tuple()))
    check("线形 ROI 角度/长度不变（斜率保持 10）",
          ln.line is not None and (ln.line[3] - ln.line[1]) == 10, str(ln.line))

    out2 = align_rects({"a": Rect(0, 0, 10, 10), "ln": line}, "same_size", order=["a", "ln"])
    check("等大小跳过线形 ROI", out2["ln"].line == line.line, str(out2["ln"].line))

    for action, n in (("align_top", 1), ("space_h", 2)):
        items = {f"r{i}": Rect(i * 10, i * 10, 10, 10) for i in range(n)}
        try:
            align_rects(items, action)
            check(f"{ACTION_LABELS[action]} 对象不足时报错", False)
        except ValueError:
            check(f"{ACTION_LABELS[action]} 对象不足时报错", True)

    try:
        align_rects(base_rects(), "nope")
        check("未知动作报错", False)
    except ValueError:
        check("未知动作报错", True)


def test_ui() -> None:
    print("[4] 界面链路")
    app = QApplication.instance() or QApplication(sys.argv)
    settings = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
    src = open_source("synthetic", fps=0.0)
    src.open()
    win = MainWindow(recipe=Recipe(name="ui_align"), source=src, settings=settings)
    win.show()
    win.timer.stop()
    for rect in (Rect(100, 100, 50, 50), Rect(200, 160, 60, 40), Rect(300, 220, 40, 60)):
        win.add_item()
        win.recipe.items[-1].roi = rect
    win._refresh_item_list()
    win._sync_view()

    win.select_all_rois()
    check("全选 3 个", len(win.view.selection()) == 3, str(win.view.selection()))

    win.batch_align("align_left")
    check("界面批量左对齐生效", {it.roi.x for it in win.recipe.items} == {100},
          str([it.roi.x for it in win.recipe.items]))

    win.undo_align()
    check("撤销对齐恢复原位",
          {(it.roi.x, it.roi.y) for it in win.recipe.items} == {(100, 100), (200, 160), (300, 220)},
          str([it.roi.as_tuple() for it in win.recipe.items]))

    win.view.set_selection([])
    win.batch_align("align_top")          # 没有选中 → 退回全部
    check("未选中时退回全部对象", {it.roi.y for it in win.recipe.items} == {100},
          str([it.roi.y for it in win.recipe.items]))

    win.clear_selection()
    check("清空选择", win.view.selection() == [])

    # 连画：勾上后每拖一个框就新建一个检测项
    before = len(win.recipe.items)
    win.act_multi_draw.setChecked(True)
    for r in (Rect(10, 10, 30, 30), Rect(60, 10, 30, 30), Rect(110, 10, 30, 30)):
        win.view.roiCreated.emit(r)
    win.act_multi_draw.setChecked(False)
    check("连画 3 个框 → 新增 3 个检测项", len(win.recipe.items) == before + 3,
          f"{before} -> {len(win.recipe.items)}")

    win.close()
    src.close()


def main() -> int:
    test_aligns()
    test_size_and_space()
    test_lines_and_errors()
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
