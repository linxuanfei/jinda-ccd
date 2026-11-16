"""界面标定流程自测（offscreen，不需要真对话框）。

覆盖：进入标定模式 → 取点 → 写入配方 → 标签刷新 → 跑一帧毫米结果 → 存盘重载保留标定。
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.qtcompat import QApplication        # noqa: E402

from visionapp.calibration import Calibration      # noqa: E402
from visionapp.recipe import Recipe                # noqa: E402
from visionapp.sources import open_source          # noqa: E402
from visionapp.ui.main_window import MainWindow    # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    app = QApplication(sys.argv)
    fails: list[str] = []

    recipe = Recipe.load(os.path.join(BASE, "recipes", "sim_demo.json"))
    for it in recipe.items:
        if it.id == "pitch":
            it.params["mm_per_px"] = 0.0
    src = open_source("synthetic", fps=0)
    src.open()

    win = MainWindow(recipe, src, path=os.path.join(BASE, "recipes", "sim_demo.json"))
    win.timer.stop()

    # 1) 进入标定模式 + 取第一个点（第二个点会弹输入框，这里直接写结果）
    win.act_calib.setChecked(True)
    if not win.view.is_calib_mode():
        fails.append("标定模式未开启")
    win._on_calib_point(100.0, 100.0)
    win.recipe.calibration.set_by_two_points((100.0, 100.0), (560.8, 100.0), 23.04)
    win.act_calib.setChecked(False)
    win._refresh_calib_label()
    print("[1] 标定标签:", win.lbl_calib.text())
    if "mm/px" not in win.lbl_calib.text():
        fails.append("标定标签没更新")

    # 2) 跑一帧，pitch 应该是毫米
    win.runner.step()
    win._poll()
    res = win.runner.snapshot().last_result
    pitch = (res or {}).get("items", {}).get("pitch", {})
    print(f"[2] 帧结果 pitch={pitch.get('value')}{pitch.get('unit')}")
    if pitch.get("unit") != "mm":
        fails.append(f"标定后 pitch 单位不是 mm: {pitch}")

    # 3) 存盘 → 重载，标定要保留
    out = os.path.join(tempfile.gettempdir(), "visionapp_calib.json")
    win.recipe.save(out)
    reloaded = Recipe.load(out)
    print(f"[3] 重载标定: {reloaded.calibration.describe()}")
    if not reloaded.calibration.valid or abs(reloaded.calibration.mm_per_px - win.recipe.calibration.mm_per_px) > 1e-9:
        fails.append("标定未随配方保存")

    # 4) 清除标定
    win.clear_calibration()
    print("[4] 清除后:", win.lbl_calib.text())
    if win.recipe.calibration.valid:
        fails.append("清除标定失败")

    src.close()
    win.close()

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
