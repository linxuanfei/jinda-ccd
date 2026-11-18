"""列出 / 打开迈德威视相机（现场 GIGE 相机联调用）。

用法：
    # 只枚举（看型号 + 序列号 + 端口）
    C:\\py38x86\\python.exe tools\\list_cameras.py

    # 打开第 0 台，连续采集抓一帧存盘（不接触发信号时用这个）
    C:\\py38x86\\python.exe tools\\list_cameras.py --open 0 --trigger continuous

    # 按序列号打开，设曝光/增益，抓 3 帧存盘
    C:\\py38x86\\python.exe tools\\list_cameras.py --serial 058071510393 --open 0 ^
        --trigger continuous --exposure 20 --gain 0 --save 3

    # 只想确认某块 SDK 能不能加载
    C:\\py38x86\\python.exe tools\\list_cameras.py --sdk sdk\\mindvision

注意：现场那份 MVCAMSDK.dll 是 32 位，必须用 32 位 Python（C:\\py38x86）或 32 位 exe。
64 位 Python 会明确提示“位数不匹配”。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.imgio import imwrite  # noqa: E402
from visionapp.sources_mindvision import (  # noqa: E402
    MindVisionSource, MindVisionError, find_sdk_dir, load_sdk, sdk_missing_help,
)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdk", default="", help="SDK 目录或库文件（默认自动找 sdk/mindvision）")
    ap.add_argument("--open", type=int, default=-1, help="打开枚举到的第 N 台并抓帧")
    ap.add_argument("--serial", default="", help="按序列号打开")
    ap.add_argument("--model", default="", help="按型号打开（默认不限制）")
    ap.add_argument("--trigger", default="continuous",
                    choices=["continuous", "hardware", "software"], help="触发模式，抓帧用 continuous")
    ap.add_argument("--exposure", type=float, default=None, help="曝光，毫秒")
    ap.add_argument("--gain", type=float, default=None, help="增益")
    ap.add_argument("--save", type=int, default=1, help="抓几帧存盘（默认 1）")
    ap.add_argument("--out", default="out/camera", help="存图目录")
    args = ap.parse_args()

    # ---- 1) SDK
    folder = args.sdk or find_sdk_dir() or ""
    print(f"SDK: {folder or '未找到'}")
    if not folder:
        print(sdk_missing_help())
        return 2
    try:
        sdk = load_sdk(folder)
    except MindVisionError as exc:
        print(f"[SDK 加载失败] {exc}")
        return 2
    print(f"后端: {sdk.kind}")

    # ---- 2) 枚举
    try:
        devices = sdk.enumerate()
    except MindVisionError as exc:
        print(f"[枚举失败] {exc}")
        return 2
    if not devices:
        print("没枚举到相机。逐项检查：")
        print("  1) 相机供电（PoE 注入器 / 12V 电源）")
        print("  2) 网线（Cat6，插在相机 GIGE 口和电脑网口上），网口灯亮不亮")
        print("  3) 网卡与相机同网段（相机多半是 169.254.x 或 192.168.x，先用 APIPA 看能不能互通）")
        print("  4) 相机没被原厂软件（2pin.exe / In-Vision）占用")
        print("  5) 防火墙放行（见 tools\\tune_nic.ps1）")
        return 1
    print(f"找到 {len(devices)} 台：")
    for i, d in enumerate(devices):
        print(f"  #{i} model={d.get('model') or '?'}  SN={d.get('serial') or '?'}  "
              f"port={d.get('port') or '?'}  friendly={d.get('friendly') or ''}")

    if args.open < 0:
        return 0

    # ---- 3) 打开 + 抓帧
    idx = args.open if 0 <= args.open < len(devices) else 0
    src = MindVisionSource(
        index=idx,
        serial=args.serial or str(devices[idx].get("serial") or ""),
        model=args.model,                       # 空 = 不校验型号
        exposure_ms=args.exposure,
        gain=args.gain,
        trigger=args.trigger,
        sdk_path=folder,
    )
    print(f"\n打开: {src.describe()}")
    try:
        src.open()
    except MindVisionError as exc:
        print(f"[打开失败] {exc}")
        return 2

    os.makedirs(os.path.join(BASE, args.out), exist_ok=True)
    got = 0
    t0 = time.perf_counter()
    for n in range(max(1, args.save)):
        frame = src.read(timeout_ms=3000)
        if frame is None:
            print("取图超时。硬件触发模式下没有触发信号就不会出图，改用 --trigger continuous 再试。")
            break
        path = os.path.join(BASE, args.out, f"frame_{n:03d}.png")
        imwrite(path, frame.bgr)
        h, w = frame.bgr.shape[:2]
        print(f"  第 {n + 1} 帧: {w}x{h}  → {path}")
        got += 1
    dt = time.perf_counter() - t0
    if got:
        print(f"\n抓到 {got} 帧，用时 {dt:.2f}s ≈ {got / dt:.1f} fps（含存盘开销）")
    src.close()
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
