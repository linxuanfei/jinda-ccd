"""多机位（6 路）自测：产线配置、并发运行、合并判定、界面网格。"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.qtcompat import QApplication          # noqa: E402

from visionapp.settings import AppSettings          # noqa: E402
from visionapp.stations import LineConfig, StationManager   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def wait_until(fn, timeout: float = 15.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    fails: list[str] = []
    settings = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))

    def getter(key, default=None):
        return {"recorder_cfg": settings.recorder_cfg,
                "output_cfg": settings.output_cfg,
                "alarm_cfg": settings.alarm_cfg}.get(key, default)

    line = LineConfig.load(os.path.join(BASE, "config", "line.json"))
    print(f"[0] 产线: {line.name}  机位数={len(line.stations)}  合并规则={line.merge_rule}")
    if len(line.stations) != 6:
        fails.append(f"机位数应为 6，实际 {len(line.stations)}")

    mgr = StationManager(line, BASE, getter)
    errs = mgr.open_all()
    print(f"[1] 打开相机: 错误={errs or '无'}")
    if errs:
        fails.append(f"相机打开失败: {errs}")

    # ---------------- 2) 每机位跑一帧 ----------------
    for st in mgr.stations:
        st.open_recorder()
        st.runner.step()
    snaps = mgr.snapshots()
    totals = mgr.totals()
    print(f"[2] 单帧: 各机位帧数={[s.total for s in snaps]} 合计={totals}")
    if any(s.total != 1 for s in snaps):
        fails.append(f"单帧各机位帧数不对: {[s.total for s in snaps]}")
    merged = mgr.merged_ok()
    print(f"[3] 合并判定(all_ok) = {merged}")
    if merged is not True:
        fails.append(f"全 OK 时应为 True，实际 {merged}")

    # ---------------- 4) 某机位人为 NG，测试三种合并规则 ----------------
    bad = mgr.stations[2]
    bad.recipe.items[1].judge = {"kind": "range", "min": 100.0, "max": 200.0}
    bad.runner.step()
    print(f"[4] 机位3 人为 NG: merged(all_ok)={mgr.merged_ok()}")
    if mgr.merged_ok() is not False:
        fails.append("all_ok 下应判定失败")

    mgr.line.merge_rule = "any_ok"
    print(f"    merged(any_ok)={mgr.merged_ok()}")
    if mgr.merged_ok() is not True:
        fails.append("any_ok 下应为 True")

    mgr.line.merge_rule = "per_station"
    print(f"    merged(per_station)={mgr.merged_ok()}")
    if mgr.merged_ok() is not None:
        fails.append("per_station 下应返回 None")

    mgr.line.merge_rule = "all_ok"
    bad.recipe.items[1].judge = {"kind": "range", "min": 22.0, "max": 23.5}
    bad.runner.step()

    # ---------------- 5) 6 路并发连续运行 ----------------
    mgr.reset if False else None
    for st in mgr.stations:
        st.runner.reset_stats()
    mgr.start_all("continuous")
    ok = wait_until(lambda: all(s.total >= 5 for s in mgr.snapshots()), timeout=20)
    mgr.stop_all()
    snaps = mgr.snapshots()
    totals = mgr.totals()
    print(f"[5] 并发运行: 各机位={[s.total for s in snaps]} 合计={totals} 用时={ok}")
    if not ok:
        fails.append(f"6 路并发跑不到 5 帧: {[s.total for s in snaps]}")
    if totals["ng"] != 0:
        fails.append(f"不该有 NG: {totals}")

    # ---------------- 6) 界面：6 个画面块 ----------------
    from visionapp.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication(sys.argv)
    line2 = LineConfig.load(os.path.join(BASE, "config", "line.json"))
    win = MainWindow(line=line2, settings=settings)
    win.timer.stop()
    print(f"[6] 界面: 画面块={len(win.tiles)}  机位下拉={win.cb_station.count()}")
    if len(win.tiles) != 6 or win.cb_station.count() != 6:
        fails.append("界面机位网格数量不对")

    win.cb_station.setCurrentIndex(2)
    if win.active != 2:
        fails.append("切换机位失败")
    win.manager.open_all()
    win.tick = None       # 确认没有旧接口依赖
    win.start_run()
    time.sleep(1.5)
    win._poll()
    win.stop_run()
    merged = win.manager.merged_ok()
    print(f"[7] 界面运行: 整线={merged}  各机位帧数={[s.total for s in win.manager.snapshots()]}")
    if merged is None:
        fails.append("界面运行后没有整线判定")
    win.close()
    mgr.close_all()

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
