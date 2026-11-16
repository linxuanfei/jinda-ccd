"""模拟相机跑通检测流水线（命令行演示）。

用法：
  python tools\\demo_sim_camera.py                     # 合成画面 30 帧
  python tools\\demo_sim_camera.py --frames 60 --jitter 3
  python tools\\demo_sim_camera.py --source folder --path "E:\\lin\\mv\\Bin" --frames 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.imgio import imwrite as imwrite_safe     # noqa: E402
from visionapp.recipe import Pipeline, Recipe          # noqa: E402
from visionapp.sources import open_source               # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="synthetic", choices=["synthetic", "folder"])
    ap.add_argument("--path", default="", help="folder 源时的图片目录")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--fps", type=float, default=0.0, help="0 = 不限速")
    ap.add_argument("--jitter", type=float, default=0.0, help="合成画面的抖动（像素，模拟真实波动）")
    ap.add_argument("--recipe", default="recipes/sim_demo.json")
    ap.add_argument("--out", default="out/demo")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    recipe_path = args.recipe if os.path.isabs(args.recipe) else os.path.join(base, args.recipe)
    if not os.path.exists(recipe_path):
        print(f"[ERR] 找不到配方: {recipe_path}")
        return 2
    recipe = Recipe.load(recipe_path)
    print(f"配方: {recipe.name}  检测项 {len(recipe.items)} 个")
    for it in recipe.items:
        print(f"  - {it.id:12s} {it.type:15s} {it.label()}")

    if args.source == "synthetic":
        src = open_source("synthetic", fps=args.fps, jitter_px=args.jitter)
    else:
        if not args.path:
            print("[ERR] folder 源需要 --path")
            return 2
        src = open_source("folder", path=args.path, loop=True, fps=args.fps)

    os.makedirs(os.path.join(base, args.out), exist_ok=True)
    pipeline = Pipeline(recipe)

    n = 0
    ok_count = 0
    errors: list[float] = []
    times: list[float] = []
    t_start = time.perf_counter()

    with src:
        info = src.info
        print(f"帧源: {info.name}  {info.width}x{info.height}  fps={info.fps}")
        while n < args.frames:
            frame = src.read()
            if frame is None:
                break
            result, annotated = pipeline.run(frame, annotate=True)
            n += 1
            ok_count += 1 if result.ok else 0
            times.append(result.elapsed_ms)

            truth = frame.meta.get("truth") or {}
            pitch = result.results.get("pitch")
            if pitch and truth.get("distance_px") is not None and pitch.unit == "px":
                errors.append(abs(pitch.value - truth["distance_px"]))

            if annotated is not None and n <= 3:
                out_png = os.path.join(base, args.out, f"frame_{frame.index:03d}.png")
                imwrite_safe(out_png, annotated)

            if n % 10 == 0 or n == 1:
                det = {k: (f"{v.value:.3f}{v.unit}" if v.value is not None else "-")
                       for k, v in result.results.items()}
                print(f"  帧 {n:3d}: {'OK' if result.ok else 'NG'}  {det}  {result.elapsed_ms:.1f}ms")

    wall = time.perf_counter() - t_start
    print("\n===== 汇总 =====")
    print(f"帧数        : {n}")
    print(f"OK 帧       : {ok_count}/{n}")
    if times:
        print(f"单帧处理    : 平均 {sum(times)/len(times):.2f} ms  最大 {max(times):.2f} ms")
    print(f"实际帧率    : {n/wall:.1f} fps" if wall > 0 else "")
    if errors:
        print(f"与真值偏差  : 平均 {sum(errors)/len(errors):.3f} px  最大 {max(errors):.3f} px")

    # 保存最后一帧结果 JSON
    if n > 0:
        with open(os.path.join(base, args.out, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result.as_dict(), f, ensure_ascii=False, indent=2)
        print(f"结果 JSON   : {os.path.join(base, args.out, 'result.json')}")
        print(f"标注图      : {os.path.join(base, args.out, 'frame_*.png')}")
    return 0 if ok_count == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
