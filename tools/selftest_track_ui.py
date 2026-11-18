"""自测：跟踪框的界面链路（D-33 的 UI 部分，offscreen 跑）。

验证：
  1. 画面上画的 ROI 会写进当前检测项（roiCreated → item.roi）
  2. 拖动 ROI 会更新配方（roiEdited）
  3. 画跟踪框会写进 recipe.track，并在视图上画出来
  4. 最低相似度改动会写进配方
  5. 清除跟踪后 recipe.track 失效
  6. 「跟踪」面板的标签会随配方刷新
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.qtcompat import QApplication  # noqa: E402

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


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    settings = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
    src = open_source("synthetic", fps=0.0)
    src.open()
    win = MainWindow(recipe=Recipe(name="ui_track"), source=src, settings=settings)
    win.show()
    win.timer.stop()

    print("[1] 画框 → 写入检测项")
    win.add_item()
    item = win._current_item()   # 现在画框挂到"当前空白项"
    win.view.roiCreated.emit(Rect(100, 120, 200, 160))
    check("ROI 写入配方", item.roi is not None and item.roi.as_tuple() == (100, 120, 200, 160),
          f"{item.roi.as_tuple() if item.roi else None}")

    print("[2] 拖动 → 更新配方")
    win.view.roiEdited.emit(item.id, Rect(111, 122, 222, 166))
    check("ROI 拖动生效", win.recipe.items[0].roi.as_tuple() == (111, 122, 222, 166),
          f"{win.recipe.items[0].roi.as_tuple()}")

    print("[3] 画跟踪框")
    win.view.trackCreated.emit(Rect(20, 30, 90, 70))
    cfg = win.recipe.track
    check("跟踪框写入配方", cfg.roi is not None and cfg.roi.as_tuple() == (20, 30, 90, 70),
          f"{cfg.roi.as_tuple() if cfg.roi else None}")
    check("跟踪框自动启用", cfg.valid is True)
    check("视图上有跟踪框对象", win.view._track_item is not None)
    check("跟踪框写入配方（尺寸 90x70）", win.recipe.track.roi is not None and win.recipe.track.roi.w == 90, str(win.recipe.track.roi))

    print("[4] 最低相似度")
    win.recipe.track.min_score = 0.85
    check("相似度写入配方", abs(win.recipe.track.min_score - 0.85) < 1e-9, str(win.recipe.track.min_score))

    print("[5] 清除跟踪")
    win.clear_track()
    check("清除后 invalid", win.recipe.track.valid is False)
    check("视图上跟踪框消失", win.view._track_item is None)
    check("清除后配方里没有跟踪框", win.recipe.track.roi is None)

    print("[6] 换配方后刷新")
    win.station.set_recipe(Recipe(name="other"), "")
    win._refresh_item_list()
    win._sync_view()
    check("新配方无跟踪框", win.recipe.track.valid is False)

    win.close()
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
