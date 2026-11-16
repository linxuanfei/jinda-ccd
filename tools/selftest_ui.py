"""GUI 逻辑自测（无显示器，offscreen 模式）。

验证：加载配方 → 跑帧 → 新增检测项 → 设 ROI → 存盘 → 重新加载 → 数据一致。
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.qtcompat import QApplication        # noqa: E402

from visionapp.core import Rect                    # noqa: E402
from visionapp.recipe import Recipe                # noqa: E402
from visionapp.sources import open_source          # noqa: E402
from visionapp.ui.main_window import MainWindow    # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    app = QApplication(sys.argv)

    recipe = Recipe.load(os.path.join(BASE, "recipes", "sim_demo.json"))
    src = open_source("synthetic", fps=0)
    src.open()

    win = MainWindow(recipe, src, path=os.path.join("recipes", "sim_demo.json"))
    win.timer.stop()

    def step():
        win.runner.step()
        win._poll()

    for _ in range(3):
        step()
    print("status  :", win.lbl_verdict.text(), "total =", win.lbl_total.text())
    print("rows    :", win.tbl.rowCount())

    # 新增检测项 + 设 ROI
    win.add_item()
    new_item = win.recipe.items[-1]
    new_item.roi = Rect(100, 120, 300, 200)
    new_item.params["threshold"] = 99
    win._refresh_item_list(select=len(win.recipe.items) - 1)
    win._sync_view()
    print("items   :", [it.id for it in win.recipe.items])
    print("rois    :", [None if it.roi is None else it.roi.as_tuple() for it in win.recipe.items])

    # 跑一帧，确认新项不炸
    step()
    print("after add:", win.lbl_verdict.text(), "total =", win.lbl_total.text())

    # 存盘 + 重载
    out = os.path.join(tempfile.gettempdir(), "visionapp_selftest.json")
    win.recipe.save(out)
    reloaded = Recipe.load(out)
    print("reloaded:", len(reloaded.items), "items;",
          "last roi =", reloaded.items[-1].roi.as_tuple() if reloaded.items[-1].roi else None,
          "; threshold =", reloaded.items[-1].params.get("threshold"))

    ok = (
        len(reloaded.items) == len(win.recipe.items)
        and reloaded.items[-1].roi is not None
        and reloaded.items[-1].roi.as_tuple() == (100, 120, 300, 200)
        and reloaded.items[-1].params.get("threshold") == 99
    )
    print("RESULT  :", "PASS" if ok else "FAIL")

    src.close()
    win.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
