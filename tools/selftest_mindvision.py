"""迈德威视驱动自测（offscreen）。

用可替换的假 SDK 覆盖打开、取图、下发曝光/增益/帧率、切换触发，
以及官方库缺失时的失败。不连接真实相机，也不把合成图当成工业相机画面。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from visionapp.camera_cfg import (
    BRAND_MINDVISION, MODEL_MV_GE500M_TPO, update_camera_numbers,
)
from visionapp.mindvision import (
    EXTERNAL_TRIGGER, CONTINUOUS, MindVisionError, MindVisionSource, SERIAL, pick_device,
)
from visionapp.sources import open_source, source_from_config


class FakeSdk:
    def __init__(self, devices):
        self.devices = devices
        self.calls: list[tuple] = []
        self.closed = False

    def enumerate(self):
        self.calls.append(("enumerate",))
        return list(self.devices)

    def open(self, dev):
        self.calls.append(("open", dev.get("sn"), dev.get("product")))
        return 7

    def set_ae(self, handle, auto):
        self.calls.append(("ae", handle, auto))

    def set_output(self, handle):
        self.calls.append(("output", handle))

    def set_trigger(self, handle, mode):
        self.calls.append(("trigger", handle, mode))

    def set_exposure_us(self, handle, microseconds):
        self.calls.append(("exposure", handle, float(microseconds)))

    def set_gain(self, handle, gain):
        self.calls.append(("gain", handle, int(gain)))

    def set_frame_rate(self, handle, hz):
        self.calls.append(("fps", handle, int(hz)))

    def play(self, handle):
        self.calls.append(("play", handle))

    def grab(self, handle, timeout_ms):
        self.calls.append(("grab", handle, timeout_ms))
        image = np.zeros((6, 8, 3), np.uint8)
        image[0, 0, 0] = 123
        return image

    def close(self, handle):
        self.calls.append(("close", handle))
        self.closed = True


def _plate():
    return {
        "product": "MV-GE500M-TPO",
        "sn": SERIAL,
        "port": "GigE",
        "friendly": "MindVision",
        "raw": "dev",
    }


def main() -> int:
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
        if not ok:
            fails.append(f"{name}: {detail}" if detail else name)

    sdk = FakeSdk([_plate()])
    cam = open_source(
        "mindvision",
        serial=SERIAL,
        model=MODEL_MV_GE500M_TPO,
        sdk=sdk,
    )
    check("not synthetic", type(cam).__name__ == "MindVisionSource")
    cam.open()
    check("opened by serial", ("open", SERIAL, MODEL_MV_GE500M_TPO) in sdk.calls)
    check("default hardware trigger", ("trigger", 7, EXTERNAL_TRIGGER) in sdk.calls)
    check("played", ("play", 7) in sdk.calls)
    frame = cam.read(timeout_ms=50)
    check(
        "grab from sdk",
        frame is not None and int(frame.bgr[0, 0, 0]) == 123 and frame.meta.get("source") == "mindvision",
        str(None if frame is None else frame.meta.get("source")),
    )
    check("info serial", cam.info.serial == SERIAL and cam.info.width == 8 and cam.info.height == 6)

    cam.apply_tune(exposure_ms=12.5, gain=4, fps=8, trigger="continuous")
    check("exposure us", ("exposure", 7, 12500.0) in sdk.calls)
    check("gain sent", ("gain", 7, 4) in sdk.calls)
    check("fps sent", ("fps", 7, 8) in sdk.calls)
    check("switched continuous", ("trigger", 7, CONTINUOUS) in sdk.calls and cam.trigger == "continuous")
    cam.close()
    check("closed", sdk.closed)

    other = FakeSdk([{
        "product": "MV-OTHER",
        "sn": "000",
        "port": "GigE",
        "friendly": "x",
        "raw": "nope",
    }])
    missed = MindVisionSource(serial=SERIAL, model=MODEL_MV_GE500M_TPO, sdk=other)
    try:
        missed.open()
        check("wrong serial rejected", False, "open returned")
    except MindVisionError as exc:
        check("wrong serial rejected", SERIAL in str(exc) and "合成" in str(exc), str(exc))
        check("wrong serial did not play", not any(call[0] == "play" for call in other.calls))

    try:
        gone = MindVisionSource(sdk_path="/tmp/mindvision-sdk-missing-for-test")
        gone.open()
        check("missing sdk fails", False, "open returned")
    except MindVisionError as exc:
        text = str(exc)
        check("missing sdk fails", "SDK" in text and ("合成" in text or "UVC" in text or "libMVSDK" in text), text)

    built = source_from_config({
        "kind": "mindvision",
        "serial": SERIAL,
        "model": MODEL_MV_GE500M_TPO,
    })
    check("config is mindvision", isinstance(built, MindVisionSource) and not isinstance(built, type(open_source("synthetic"))))
    check("brand visible", BRAND_MINDVISION == "迈德威视" and MODEL_MV_GE500M_TPO == "MV-GE500M-TPO")
    import inspect
    import visionapp.sources as sources_mod
    src_text = inspect.getsource(sources_mod)
    check(
        "official mindvision backend",
        "from .mindvision import MindVisionSource" in src_text and "sources_mindvision" not in src_text
        and "MvCameraControl" not in src_text,
    )

    holder = FakeSdk([_plate()])
    live = MindVisionSource(serial=SERIAL, model=MODEL_MV_GE500M_TPO, sdk=holder)
    live.open()
    cfg = {"kind": "mindvision", "trigger": "hardware", "brand": BRAND_MINDVISION}
    mode = update_camera_numbers(
        cfg, exposure=3, gain=2, fps=6,
        brand=BRAND_MINDVISION, model=MODEL_MV_GE500M_TPO, camera=live,
    )
    check("tune pushes", mode == "continuous" and ("exposure", 7, 3000.0) in holder.calls and ("fps", 7, 6) in holder.calls)

    try:
        pick_device([], SERIAL, MODEL_MV_GE500M_TPO)
        check("empty enum", False)
    except MindVisionError as exc:
        check("empty enum", "不会改用合成图" in str(exc))

    print()
    if fails:
        for item in fails:
            print("  [FAIL]", item)
        print(f"RESULT: FAIL ({len(fails)})")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
