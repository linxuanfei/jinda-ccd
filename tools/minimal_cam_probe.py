"""最小复现：冻结后「加载 SDK → 枚举 → 打开 → 抓一帧」，结果写文件。

用来判断 -36（GigE 过滤驱动初始化失败）是"冻结环境"引起，还是 Qt/界面引起。
"""

from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)

out_path = os.path.join(BASE, "out", "minimal_cam.txt")
os.makedirs(os.path.dirname(out_path), exist_ok=True)


def log(text: str) -> None:
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)


def main() -> None:
    log("frozen=" + str(getattr(sys, "frozen", False)))
    log("sdk_env=" + str(os.environ.get("MINDVISION_SDK", "")))
    try:
        from visionapp.sources_mindvision import (
            MindVisionError, MindVisionSource, find_sdk_dir, load_sdk,
        )

        log("sdk_dir=" + str(find_sdk_dir()))
        sdk = load_sdk()
        log("backend=" + sdk.kind)
        devices = sdk.enumerate()
        log("devices=" + str([{k: v for k, v in d.items() if k != "raw"} for d in devices]))

        src = MindVisionSource(index=0, trigger="continuous", exposure_ms=30)
        try:
            src.open()
            log("open OK")
            frame = src.read(timeout_ms=3000)
            log("frame=" + str(None if frame is None else frame.bgr.shape))
            if frame is not None:
                mean = float(frame.bgr.mean())
                log("mean=" + f"{mean:.2f}")
        finally:
            try:
                src.close()
                log("close OK")
            except Exception as exc:                                # noqa: BLE001
                log("close 异常: " + str(exc))
    except MindVisionError as exc:
        log("MindVisionError: " + str(exc))
    except Exception:                                                # noqa: BLE001
        log("EXC:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
