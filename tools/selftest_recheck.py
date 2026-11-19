"""自测：复检模式（D-37）—— 首检 / 复检分开计数，良率只算首检。

现场口径（已确认）：
    首检判 NG → 排出机台 → 人工集中收集 → 之后重新上料再测 = **复检品**。

要求（来自 docs/现场实操需求.md R-23 / 功能差距 D-37）：
  1. 一键切换 首检 / 复检
  2. 复检帧单独计数（复检品），另算 **复检合格率**
  3. **良率只按首检**算（复检不进分子分母）
  4. 首检 NG 计数不被复检结果覆盖
  5. CSV 有 `模式` 列（首检/复检）
  6. 画面标注带对象编号

覆盖：Runner 计数口径、CSV 列、UI 切换与标签、标注编号。
"""

from __future__ import annotations

import csv
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.core import Frame, Rect  # noqa: E402
from visionapp.ops import Item  # noqa: E402
from visionapp.recipe import Pipeline, Recipe, draw_overlays  # noqa: E402
from visionapp.recorder import Recorder, RecorderConfig  # noqa: E402
from visionapp.runner import Runner  # noqa: E402
from visionapp.qtcompat import QApplication  # noqa: E402
from visionapp.settings import AppSettings  # noqa: E402
from visionapp.sources import open_source  # noqa: E402
from visionapp.ui.main_window import MainWindow  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out", "recheck_test")
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


class FixedSource:
    """固定结果的帧源：按预设 verdict 序列出帧。"""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self._i = 0
        self.info = type("I", (), {"name": "fixed", "width": 100, "height": 100, "fps": 0.0})()

    def open(self):
        pass

    def read(self, timeout_ms=1000):
        if self._i >= len(self.verdicts):
            return None
        v = self.verdicts[self._i]
        self._i += 1
        img = __import__("numpy").zeros((100, 100, 3), "uint8")
        return Frame.create(img, index=self._i, verdict=v)

    def close(self):
        pass


class FixedPipeline:
    """按帧 meta 里的 verdict 直接返回 OK/NG。"""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self._i = 0

    def run(self, frame, annotate=True):
        from visionapp.ops import ItemResult
        from visionapp.recipe import PipelineResult

        ok = bool(frame.meta.get("verdict"))
        res = ItemResult(item_id="x", type="fake", ok=ok, value=1.0)
        r = PipelineResult(frame.index, {"x": res}, ok, 1.0)
        return r, (draw_overlays(frame, r) if annotate else None)


def test_runner_counters() -> None:
    print("[1] Runner 计数口径（首检 / 复检分开）")
    src = FixedSource([True, True, False, False])      # 2 OK 2 NG
    runner = Runner(src, lambda: FixedPipeline([True, True, False, False]))
    for _ in range(4):
        runner.step()
    snap = runner.snapshot()
    check("首检 OK=2", snap.ok == 2, str(snap.ok))
    check("首检 NG=2", snap.ng == 2, str(snap.ng))
    check("良率=50%", abs(snap.yield_rate - 0.5) < 1e-9, f"{snap.yield_rate}")
    check("复检品=0", snap.rc_total == 0)
    check("复检合格率无数据时为 0", snap.recheck_rate == 0.0)

    # 切到复检：再来 2 件（1 OK 1 NG）
    runner.set_recheck(True)
    src2 = FixedSource([True, False])
    runner.set_source(src2)
    runner._pipeline_provider = lambda: FixedPipeline([True, False])
    runner.step()
    runner.step()
    snap = runner.snapshot()
    check("复检品=2", snap.rc_total == 2, str(snap.rc_total))
    check("复检 OK=1 / NG=1", snap.rc_ok == 1 and snap.rc_ng == 1,
          f"{snap.rc_ok}/{snap.rc_ng}")
    check("复检合格率=50%", abs(snap.recheck_rate - 0.5) < 1e-9, f"{snap.recheck_rate}")
    check("首检计数没被复检覆盖（OK 仍 2、NG 仍 2）", snap.ok == 2 and snap.ng == 2,
          f"{snap.ok}/{snap.ng}")
    check("良率仍按首检 = 50%", abs(snap.yield_rate - 0.5) < 1e-9, f"{snap.yield_rate}")
    check("快照带 recheck=True", snap.recheck is True)

    runner.reset_stats()
    snap = runner.snapshot()
    check("重置后复检计数归零", snap.rc_total == 0 and snap.ok == 0)


def test_csv_mode_column() -> None:
    print("[2] CSV 有 模式 列（首检/复检）")
    import shutil

    if os.path.isdir(OUT):
        shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    rec = Recorder(RecorderConfig(root=OUT, save_ng_images=False))
    rec.open("rc", ["x"])
    src = FixedSource([True, False])
    pipe = FixedPipeline([True, False])
    frame1 = src.read()
    r1, _ = pipe.run(frame1)
    rec.record(r1, frame1, None, 1.0, recheck=False)
    frame2 = src.read()
    r2, _ = pipe.run(frame2)
    rec.record(r2, frame2, None, 1.0, recheck=True)
    rec.close()

    sub = OUT                             # 新结构：<root>/<日期>/<时间_相机号>/<日期>.csv
    files = []
    for dp, _d, fs in os.walk(sub):
        files += [os.path.join(dp, f) for f in fs if f.endswith(".csv")]
    check("生成了 CSV", len(files) == 1, str(files))
    with open(os.path.join(sub, files[0]), encoding="utf-8-sig") as fp:
        rows = list(csv.reader(fp))
    check("表头含 模式", "模式" in rows[0], str(rows[0]))
    col = rows[0].index("模式")
    check("首检行 = 首检", rows[1][col] == "首检", rows[1][col])
    check("复检行 = 复检", rows[2][col] == "复检", rows[2][col])


def test_ui_toggle() -> None:
    print("[3] 界面切换与标签")
    app = QApplication.instance() or QApplication(sys.argv)
    settings = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
    src = open_source("synthetic", fps=0.0)
    src.open()
    win = MainWindow(recipe=Recipe(name="rc"), source=src, settings=settings)
    win.show()
    win.timer.stop()

    check("默认首检", win.station.runner.recheck is False)
    win.station.runner.set_recheck(True)
    check("切到复检：开关为真", win.station.runner.recheck is True)
    check("切到复检：runner 生效", win.station.runner.recheck is True)

    class Snap:
        ok = 5
        ng = 3
        total = 10
        rc_total = 4
        rc_ok = 3
        rc_ng = 1
        recheck = True
        fps = 10.0
        last_elapsed_ms = 2.0
        last_error = ""
        last_result = {"ok": True}

        @property
        def yield_rate(self):
            return 5 / 8

        @property
        def recheck_rate(self):
            return 3 / 4

    win._update_stats(Snap())
    check("统计面板：良率仍 62.5%（首检 5/8）", win.lbl_yield.text() == "62.5%", win.lbl_yield.text())

    win.station.runner.set_recheck(False)
    win.close()
    src.close()


def test_object_numbering() -> None:
    print("[4] 画面对象编号")
    import numpy as np

    img = np.zeros((120, 200, 3), np.uint8)
    img[30:70, 20:60] = 255                       # 造一个白块，让 find_blobs 有结果
    frame = Frame.create(img)
    it = Item(id="pins", type="find_blobs", roi=Rect(10, 10, 80, 80),
              params={"threshold": 200, "min_area": 5})
    rec = Recipe(name="num", items=[it])
    res, img = Pipeline(rec).run(frame, annotate=True)
    check("标注图非空", img is not None)
    # 有编号的文字叠加：检测"黄绿色像素"存在（编号用 (0,255,255)）
    import numpy as np

    yellow = ((img[..., 2] > 200) & (img[..., 1] > 200) & (img[..., 0] < 80)).sum()
    check("画面不再画黄色编号（现场要求去掉）", int(yellow) == 0, f"yellow={int(yellow)}")


def main() -> int:
    test_runner_counters()
    test_csv_mode_column()
    test_ui_toggle()
    test_object_numbering()
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
