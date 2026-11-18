"""运行引擎 / 记录器 自测（无需界面）。

验证：
  1. 连续运行计数（OK/NG/良率）
  2. CSV 落盘与内容
  3. NG 图像保存
  4. 单次运行
  5. 运行中切换配方
"""

from __future__ import annotations

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.recorder import Recorder, RecorderConfig      # noqa: E402
from visionapp.recipe import Pipeline, Recipe                # noqa: E402
from visionapp.runner import MODE_SINGLE, Runner             # noqa: E402
from visionapp.sources import open_source                    # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out", "selftest_run")


def wait_until(fn, timeout: float = 15.0, interval: float = 0.02) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(interval)
    return False


def main() -> int:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)

    failures: list[str] = []

    # ---------------- 1) 连续运行（全部 OK） ----------------
    recipe = Recipe.load(os.path.join(BASE, "recipes", "sim_demo.json"))
    src = open_source("synthetic", fps=0)
    src.open()
    rec = Recorder(RecorderConfig(root=OUT, csv_enabled=True, save_ng_images=True), recipe.name)
    runner = Runner(src, lambda: Pipeline(recipe), rec)
    rec.open(recipe.name, [it.id for it in recipe.items])

    runner.start("continuous")
    wait_until(lambda: runner.snapshot().total >= 20)
    runner.stop()
    snap = runner.snapshot()
    print(f"[1] 连续运行: total={snap.total} ok={snap.ok} ng={snap.ng} "
          f"良率={snap.yield_rate*100:.0f}% fps={snap.fps:.1f}")
    if snap.total < 20:
        failures.append("连续运行帧数不足")
    if snap.ng != 0:
        failures.append(f"预期全 OK，实际 NG={snap.ng}")

    csv_path = ""
    for dp, _d, fs in os.walk(OUT):
        for f in fs:
            if f.endswith(".csv"):
                csv_path = os.path.join(dp, f)
                break
        if csv_path:
            break
    if not os.path.exists(csv_path):
        failures.append(f"CSV 未生成: {csv_path}")
    else:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        print(f"[2] CSV: {len(rows)} 行（含表头），表头={rows[0]}")
        if len(rows) < 21:
            failures.append(f"CSV 行数不足: {len(rows)}")
        if rows[0][0] != "时间" or len(rows[0]) < 5:
            failures.append(f"CSV 表头异常: {rows[0]}")

    # ---------------- 3) NG 判定 + 存图 ----------------
    ng_recipe = Recipe.load(os.path.join(BASE, "recipes", "sim_demo.json"))
    for it in ng_recipe.items:
        if it.id == "pitch":
            it.judge = {"kind": "range", "min": 100.0, "max": 200.0}   # 人为判 NG
    runner.set_source(src)
    rec2 = Recorder(RecorderConfig(root=OUT, csv_enabled=True, save_ng_images=True), ng_recipe.name)
    runner2 = Runner(src, lambda: Pipeline(ng_recipe), rec2)
    rec2.open(ng_recipe.name, [it.id for it in ng_recipe.items])
    runner2.start("continuous")
    wait_until(lambda: runner2.snapshot().total >= 5)
    runner2.stop()
    snap2 = runner2.snapshot()
    # 新路径：<root>/<日期>/<时间_相机号>/NG/*.png —— 直接数所有 png
    n_imgs = 0
    for dp, _d, fs in os.walk(OUT):
        if os.path.basename(dp).upper() == "NG":
            n_imgs += len([f for f in fs if f.lower().endswith(".png")])
    print(f"[3] NG 场景: total={snap2.total} ng={snap2.ng} 存图={n_imgs} 张")
    if snap2.ng < 5 or n_imgs < 5:
        failures.append(f"NG 存图异常: ng={snap2.ng}, imgs={n_imgs}")

    # ---------------- 4) 单次运行 ----------------
    runner3 = Runner(src, lambda: Pipeline(recipe), None)
    runner3.start(MODE_SINGLE)
    wait_until(lambda: not runner3.running, timeout=5)
    s3 = runner3.snapshot()
    print(f"[4] 单次运行: total={s3.total} running={s3.running}")
    if s3.total != 1:
        failures.append(f"单次运行应只跑 1 帧，实际 {s3.total}")

    # ---------------- 5) 运行中切换配方 ----------------
    cur = {"r": Pipeline(recipe)}
    runner4 = Runner(src, lambda: cur["r"], None)
    runner4.start("continuous")
    wait_until(lambda: runner4.snapshot().total >= 3)
    cur["r"] = Pipeline(ng_recipe)          # 运行中切换
    before = runner4.snapshot().ng
    wait_until(lambda: runner4.snapshot().ng > before, timeout=5)
    runner4.stop()
    s4 = runner4.snapshot()
    print(f"[5] 运行中切配方: total={s4.total} ng={s4.ng}（切换后应开始出现 NG）")
    if s4.ng == 0:
        failures.append("运行中切换配方无效")

    rec.close()
    rec2.close()
    src.close()

    print()
    if failures:
        for f in failures:
            print("  [FAIL]", f)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
