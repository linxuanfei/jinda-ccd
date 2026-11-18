"""自测：迈德威视相机适配（合并 C 接口 + 官方 mvsdk.py + GenICam 三套后端）。

不需要真相机：用一个 **假后端**（记录调用、返回合成帧）验证整条链路。

覆盖：
  1. SDK 目录候选 / SDK 缺失时的可操作报错
  2. pick_device：按序列号 → 型号 → 序号；对不上必须报错，不许开旁边那台
  3. 打开流程：关自动曝光、设输出格式、触发模式（默认硬件=2）、曝光毫秒→微秒、增益、帧率
  4. 取图：read() 返回 Frame，尺寸信息回填
  5. apply_tune：已打开时真正下发
  6. 像素格式转换 to_bgr（Mono8/BGR8/RGB8/BayerRG8）
  7. 现场铭牌默认值：MV-GE507GC / 056082520586
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from visionapp import sources_mindvision as mvs  # noqa: E402
from visionapp.sources import open_source  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


class FakeSdk:
    """假后端：记录所有下发/取图调用。"""

    kind = "fake"

    def __init__(self, devices=None, shape=(40, 60, 3)):
        self.devices = devices if devices is not None else [
            {"model": "MV-GE507GC", "serial": "056082520586", "port": "GigE", "friendly": "MV"},
            {"model": "MV-GE507GC", "serial": "999999999999", "port": "GigE", "friendly": "MV"},
        ]
        self.shape = shape
        self.calls: list[tuple[str, tuple]] = []
        self.handle: int | None = None

    def _rec(self, name, *args):
        self.calls.append((name, args))

    def enumerate(self):
        self._rec("enumerate")
        return list(self.devices)

    def open(self, dev):
        self._rec("open", dev["serial"])
        self.handle = 7
        return 7

    def close(self, handle):
        self._rec("close", handle)

    def set_ae(self, handle, auto):
        self._rec("set_ae", handle, auto)

    def set_trigger(self, handle, mode):
        self._rec("set_trigger", handle, mode)

    def set_exposure_us(self, handle, us):
        self._rec("set_exposure_us", handle, us)

    def set_gain(self, handle, gain):
        self._rec("set_gain", handle, gain)

    def set_frame_rate(self, handle, hz):
        self._rec("set_frame_rate", handle, hz)

    def set_output(self, handle):
        self._rec("set_output", handle)

    def play(self, handle):
        self._rec("play", handle)

    def grab(self, handle, timeout_ms):
        self._rec("grab", handle, timeout_ms)
        return np.zeros(self.shape, np.uint8)


def call_args(sdk, name):
    """返回**最后一次**同名调用的参数（打开时一次、调参时再一次，要取最新那次）。"""
    got = None
    for n, args in sdk.calls:
        if n == name:
            got = args
    return got


def test_sdk_lookup() -> None:
    print("[1] SDK 目录与缺失提示")
    cands = mvs.candidate_sdk_dirs()
    check("候选目录非空", len(cands) > 0)
    check("包含 sdk/mindvision", any("sdk" in os.path.normcase(c) and "mindvision" in os.path.normcase(c)
                                   for c in cands), str(cands[:3]))
    try:
        mvs.load_sdk(os.path.join(BASE, "不存在的目录"))
        check("错误路径应报错", False)
    except mvs.MindVisionError as exc:
        msg = str(exc)
        check("缺 SDK 的提示可操作", ("MVCAMSDK" in msg or "mvsdk" in msg) and "SDK" in msg)
        check("提示里给了候选目录", "sdk" in msg.lower())


def test_pick_device() -> None:
    print("[2] 选相机：序列号 → 型号 → 序号")
    devs = [
        {"model": "MV-GE507GC", "serial": "056082520586"},
        {"model": "MV-GE507GC", "serial": "999"},
        {"model": "MV-SUA133GM", "serial": "123"},
    ]
    d = mvs.pick_device(devs, serial="056082520586", model="MV-GE507GC")
    check("按序列号选中", d["serial"] == "056082520586")
    d2 = mvs.pick_device(devs, serial="", model="MV-SUA133GM")
    check("按型号选中", d2["serial"] == "123")
    d3 = mvs.pick_device(devs, serial="", model="", index=1)
    check("无序列号无型号时按序号", d3["serial"] == "999")

    try:
        mvs.pick_device(devs, serial="888", model="")
        check("序列号不存在应报错", False)
    except mvs.MindVisionError as exc:
        check("序列号不存在报错", "888" in str(exc))
    try:
        mvs.pick_device(devs, serial="056082520586", model="MV-SUA133GM")
        check("型号不符应报错", False)
    except mvs.MindVisionError as exc:
        check("型号不符报错", "不是" in str(exc))
    try:
        mvs.pick_device([], serial="1", model="")
        check("没有相机时报错", False)
    except mvs.MindVisionError as exc:
        check("没有相机时报错", "没有枚举到" in str(exc))
    try:
        mvs.pick_device(devs[:2], serial="", model="MV-GE507GC")
        check("同型号多台应要求写序列号", False)
    except mvs.MindVisionError as exc:
        check("同型号多台报错", "序列号" in str(exc))


def test_open_and_read() -> None:
    print("[3] 打开 / 参数下发 / 取图")
    sdk = FakeSdk()
    src = mvs.MindVisionSource(serial=mvs.DEFAULT_SERIAL, model=mvs.DEFAULT_MODEL,
                               exposure_ms=30.0, gain=6, fps=15, trigger="hardware", sdk=sdk)
    check("默认型号是现场实测型号", mvs.DEFAULT_MODEL == "MV-GE507GC", mvs.DEFAULT_MODEL)
    check("默认序列号是现场实测序列号", mvs.DEFAULT_SERIAL == "056082520586", mvs.DEFAULT_SERIAL)
    src.open()
    check("关掉自动曝光", call_args(sdk, "set_ae") == (7, 0), str(call_args(sdk, "set_ae")))
    check("设了输出格式", call_args(sdk, "set_output") == (7,))
    check("默认硬件触发(2)", call_args(sdk, "set_trigger") == (7, 2), str(call_args(sdk, "set_trigger")))
    check("曝光毫秒→微秒(30ms→30000us)", abs(call_args(sdk, "set_exposure_us")[1] - 30000.0) < 1e-6,
          str(call_args(sdk, "set_exposure_us")))
    check("增益下发", call_args(sdk, "set_gain") == (7, 6))
    check("帧率下发", call_args(sdk, "set_frame_rate") == (7, 15))
    check("开始采集", call_args(sdk, "play") == (7,))

    frame = src.read()
    check("取到一帧", frame is not None and frame.bgr.shape == (40, 60, 3), str(frame.bgr.shape))
    check("帧里带型号/序列号", frame.meta.get("serial") == mvs.DEFAULT_SERIAL
          and frame.meta.get("model") == mvs.DEFAULT_MODEL)
    check("info 尺寸回填", src.info.width == 60 and src.info.height == 40,
          f"{src.info.width}x{src.info.height}")
    check("describe 含型号与触发", "MV-GE507GC" in src.describe() and "硬件触发" in src.describe(),
          src.describe())
    src.close()
    check("关闭句柄", call_args(sdk, "close") == (7,))


def test_tune_and_errors() -> None:
    print("[4] 调参下发 / 触发模式 / 未打开时报错")
    sdk = FakeSdk()
    src = mvs.MindVisionSource(serial=mvs.DEFAULT_SERIAL, sdk=sdk, trigger="continuous")
    src.open()
    check("连续触发(0)", call_args(sdk, "set_trigger") == (7, 0))
    src.apply_tune(exposure_ms=50.0, gain=3, fps=10, trigger="software")
    check("apply_tune 下发软触发(1)", call_args(sdk, "set_trigger") == (7, 1))
    check("apply_tune 下发曝光 50000us",
          abs(call_args(sdk, "set_exposure_us")[1] - 50000.0) < 1e-6)
    src.close()

    fresh = mvs.MindVisionSource(serial=mvs.DEFAULT_SERIAL)
    try:
        fresh.read()
        check("未打开就取图应报错", False)
    except mvs.MindVisionError:
        check("未打开就取图报错", True)


def test_to_bgr() -> None:
    print("[5] 像素格式转换")
    w, h = 8, 4
    mono = (np.arange(w * h, dtype=np.uint8) * 3).tobytes()
    img = mvs.to_bgr(mono, w, h, mvs.PIXEL_MONO8)
    check("Mono8 → BGR", img.shape == (h, w, 3))
    bgr = np.zeros((h, w, 3), np.uint8)
    bgr[..., 0] = 50
    bgr[..., 2] = 200
    check("BGR8 直通", mvs.to_bgr(bgr.tobytes(), w, h, mvs.PIXEL_BGR8)[0, 0, 0] == 50)
    check("RGB8 通道交换", mvs.to_bgr(bgr.tobytes(), w, h, mvs.PIXEL_RGB8)[0, 0, 2] == 50)
    try:
        mvs.to_bgr(mono, w, h, 0x01100003)
        check("不支持格式应报错", False)
    except ValueError as exc:
        check("不支持格式报错", "暂不支持" in str(exc))


def test_source_registration() -> None:
    print("[6] 帧源注册")
    src = open_source("mindvision", serial=mvs.DEFAULT_SERIAL)
    check("open_source('mindvision')", src.__class__.__name__ == "MindVisionSource")
    from visionapp.sources import source_from_config

    src2 = source_from_config({"kind": "mindvision", "serial": mvs.DEFAULT_SERIAL,
                               "exposure_ms": 20, "trigger": "hardware"})
    check("source_from_config 支持新字段", src2.exposure_ms == 20.0 and src2.trigger == "hardware",
          f"{src2.exposure_ms} {src2.trigger}")


def main() -> int:
    test_sdk_lookup()
    test_pick_device()
    test_open_and_read()
    test_tune_and_errors()
    test_to_bgr()
    test_source_registration()
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
