"""迈德威视 MVSDK 适配层。

官方库（SDK 自带的 Python 绑定 mvsdk.py，以及 C 接口）：
- Windows x64：MVCAMSDK_X64.dll；x86：MVCAMSDK.dll
- Linux：libMVSDK.so

取图和设参走这些导出函数，不经过海康 / 大恒 / 巴斯勒：
CameraEnumerateDevice、CameraInit、CameraUnInit、CameraSetAeState、
CameraSetTriggerMode、CameraSetExposureTime、CameraSetAnalogGain、
CameraSetFrameRate、CameraSetIspOutFormat、CameraPlay、
CameraGetImageBuffer、CameraImageProcess、CameraReleaseImageBuffer。

触发模式来自 SDK 头文件 emSdkSnapMode：0 连续，1 软触发，2 硬件触发。
CameraSetExposureTime 的单位是微秒（官方示例把 30 ms 写成 30 * 1000，
帧头 uiExpTime 也注明单位 us）。

库不在、枚举为空或序列号对不上时直接失败。不会改用合成图或 UVC。
"""

from __future__ import annotations

import os
import sys
from ctypes import (
    CDLL, POINTER, byref, c_char, c_char_p, c_double, c_float, c_int, c_ubyte,
    c_uint, c_void_p, memmove, sizeof,
)
from ctypes import Structure
from typing import Any

import cv2
import numpy as np

from .core import Frame, SourceInfo

# 铭牌：迈德威视 MV-GE500M-TPO，序列号 058071510393，网口。
BRAND = "迈德威视"
MODEL = "MV-GE500M-TPO"
SERIAL = "058071510393"
INTERFACE = "GigE"

# 官网产品搜索没有这只型号的规格页。分辨率、靶面、曝光/增益/帧率范围不从型号猜。
SPEC_PENDING = "待手册确认"

# CameraDefine.h / mvsdk.py 里的图像格式宏。
CAMERA_MEDIA_TYPE_MONO8 = 0x01000000 | 0x00080000 | 0x0001
CAMERA_MEDIA_TYPE_BGR8 = 0x02000000 | 0x00180000 | 0x0015

CONTINUOUS = 0
EXTERNAL_TRIGGER = 2
TIMEOUT = -12


class MindVisionError(RuntimeError):
    """官方 SDK 缺失、打开失败或设参被拒绝。"""


class tSdkCameraDevInfo(Structure):
    _fields_ = [
        ("acProductSeries", c_char * 32),
        ("acProductName", c_char * 32),
        ("acFriendlyName", c_char * 32),
        ("acLinkName", c_char * 32),
        ("acDriverVersion", c_char * 32),
        ("acSensorType", c_char * 32),
        ("acPortType", c_char * 32),
        ("acSn", c_char * 32),
        ("uInstance", c_uint),
    ]


class tSdkFrameHead(Structure):
    _fields_ = [
        ("uiMediaType", c_uint),
        ("uBytes", c_uint),
        ("iWidth", c_int),
        ("iHeight", c_int),
        ("iWidthZoomSw", c_int),
        ("iHeightZoomSw", c_int),
        ("bIsTrigger", c_int),
        ("uiTimeStamp", c_uint),
        ("uiExpTime", c_uint),
        ("fAnalogGain", c_float),
        ("iGamma", c_int),
        ("iContrast", c_int),
        ("iSaturation", c_int),
        ("fRgain", c_float),
        ("fGgain", c_float),
        ("fBgain", c_float),
    ]


def _text(buf: bytes | str) -> str:
    if isinstance(buf, str):
        return buf.split("\x00", 1)[0]
    raw = bytes(buf).split(b"\x00", 1)[0]
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _summary(devices: list[dict[str, Any]]) -> str:
    if not devices:
        return "（没有枚举到相机）"
    return "；".join(
        f"{d.get('product') or '?'} sn={d.get('sn') or '?'} {d.get('port') or ''}".strip()
        for d in devices
    )


def pick_device(devices: list[dict[str, Any]], serial: str, model: str) -> dict[str, Any]:
    """按序列号优先，其次型号。对不上就失败，不打开旁边那一台。"""
    if not devices:
        raise MindVisionError(
            "网口上没有枚举到迈德威视相机。请确认上电、网卡与相机同一网段，并且已安装 MVSDK。"
            "不会改用合成图或 UVC。"
        )
    if serial:
        hits = [d for d in devices if str(d.get("sn") or "") == serial]
        if not hits:
            raise MindVisionError(
                f"没有序列号 {serial} 的迈德威视相机。枚举到：{_summary(devices)}。"
                "不会改用其他相机或合成图。"
            )
        if model:
            named = [d for d in hits if model in str(d.get("product") or "")]
            if not named:
                raise MindVisionError(
                    f"序列号 {serial} 的型号是 {hits[0].get('product') or '未知'}，不是 {model}。"
                )
            return named[0]
        return hits[0]
    if model:
        hits = [d for d in devices if model in str(d.get("product") or "")]
        if not hits:
            raise MindVisionError(f"没有型号 {model}。枚举到：{_summary(devices)}。")
        if len(hits) > 1:
            raise MindVisionError(f"有多台 {model}，请写序列号。枚举到：{_summary(hits)}。")
        return hits[0]
    if len(devices) == 1:
        return devices[0]
    raise MindVisionError(f"有多台相机，请指定序列号。枚举到：{_summary(devices)}。")


def load_sdk(path: str | None = None):
    """加载官方 MVSDK。找不到时抛 MindVisionError。"""
    hint = (
        "未找到迈德威视官方 SDK（Linux 的 libMVSDK.so，Windows 的 MVCAMSDK_X64.dll，"
        "或 SDK 里的 mvsdk.py）。把 MINDVISION_SDK 指到库文件或 SDK 目录。"
        "没有这块库时，MV-GE500M-TPO 不会用合成图或 UVC 冒充。"
    )
    chosen = (path or os.environ.get("MINDVISION_SDK") or "").strip()
    if chosen:
        if os.path.isdir(chosen):
            if chosen not in sys.path:
                sys.path.insert(0, chosen)
            for root, _dirs, files in os.walk(chosen):
                for name in ("libMVSDK.so", "MVCAMSDK_X64.dll", "MVCAMSDK.dll"):
                    if name in files:
                        return CtypesMindVision(CDLL(os.path.join(root, name)))
            raise MindVisionError(f"{chosen} 里没有 libMVSDK.so 或 MVCAMSDK_X64.dll。{hint}")
        elif chosen.endswith(".py"):
            folder = os.path.dirname(os.path.abspath(chosen))
            if folder not in sys.path:
                sys.path.insert(0, folder)
        elif os.path.isfile(chosen):
            return CtypesMindVision(CDLL(chosen))
        else:
            raise MindVisionError(f"MINDVISION_SDK 指向的路径不存在：{chosen}。{hint}")
    try:
        import mvsdk  # type: ignore
    except ImportError:
        mvsdk = None
    if mvsdk is not None and hasattr(mvsdk, "CameraEnumerateDevice"):
        return MvsdkModule(mvsdk)
    for name in ("libMVSDK.so", "MVCAMSDK_X64.dll", "MVCAMSDK.dll"):
        try:
            return CtypesMindVision(CDLL(name))
        except OSError:
            continue
    raise MindVisionError(hint)


class MvsdkModule:
    """包装 SDK 自带的 mvsdk.py。"""

    def __init__(self, mod: Any):
        self.mod = mod
        mod.CameraSdkInit(0)
        self._channels = 3

    def enumerate(self) -> list[dict[str, Any]]:
        devices = self.mod.CameraEnumerateDevice()
        out = []
        for dev in devices:
            out.append({
                "product": dev.GetProductName(),
                "sn": dev.GetSn(),
                "port": dev.GetPortType(),
                "friendly": dev.GetFriendlyName(),
                "raw": dev,
            })
        return out

    def open(self, dev: dict[str, Any]) -> int:
        try:
            return int(self.mod.CameraInit(dev["raw"], -1, -1))
        except self.mod.CameraException as exc:
            raise MindVisionError(f"CameraInit 失败（{exc.error_code}）：{exc.message}") from exc

    def close(self, handle: int) -> None:
        self.mod.CameraUnInit(handle)

    def set_ae(self, handle: int, auto: int) -> None:
        self._ok(self.mod.CameraSetAeState(handle, int(auto)), "CameraSetAeState")

    def set_trigger(self, handle: int, mode: int) -> None:
        self._ok(self.mod.CameraSetTriggerMode(handle, int(mode)), "CameraSetTriggerMode")

    def set_exposure_us(self, handle: int, microseconds: float) -> None:
        self._ok(self.mod.CameraSetExposureTime(handle, float(microseconds)), "CameraSetExposureTime")

    def set_gain(self, handle: int, gain: int) -> None:
        self._ok(self.mod.CameraSetAnalogGain(handle, int(gain)), "CameraSetAnalogGain")

    def set_frame_rate(self, handle: int, hz: int) -> None:
        fn = getattr(self.mod, "CameraSetFrameRate", None)
        if fn is None:
            raise MindVisionError("这份 mvsdk 没有 CameraSetFrameRate，帧率没有下发。")
        self._ok(fn(handle, int(hz)), "CameraSetFrameRate")

    def set_output(self, handle: int) -> None:
        err = self.mod.CameraSetIspOutFormat(handle, CAMERA_MEDIA_TYPE_BGR8)
        if err == 0:
            self._channels = 3
            return
        err = self.mod.CameraSetIspOutFormat(handle, CAMERA_MEDIA_TYPE_MONO8)
        if err == 0:
            self._channels = 1
            return
        raise MindVisionError(f"CameraSetIspOutFormat 失败（{err}）。")

    def play(self, handle: int) -> None:
        self._ok(self.mod.CameraPlay(handle), "CameraPlay")

    def grab(self, handle: int, timeout_ms: int) -> np.ndarray | None:
        try:
            raw, head = self.mod.CameraGetImageBuffer(handle, int(timeout_ms))
        except self.mod.CameraException as exc:
            if exc.error_code == TIMEOUT:
                return None
            raise MindVisionError(f"CameraGetImageBuffer 失败（{exc.error_code}）：{exc.message}") from exc
        width, height = int(head.iWidth), int(head.iHeight)
        channels = self._channels
        buf = self.mod.CameraAlignMalloc(width * height * channels, 16)
        try:
            self._ok(self.mod.CameraImageProcess(handle, raw, buf, head), "CameraImageProcess")
            self.mod.CameraReleaseImageBuffer(handle, raw)
            data = (c_ubyte * (width * height * channels)).from_address(buf)
            arr = np.frombuffer(data, dtype=np.uint8).copy()
        finally:
            self.mod.CameraAlignFree(buf)
        if channels == 1:
            return cv2.cvtColor(arr.reshape(height, width), cv2.COLOR_GRAY2BGR)
        return arr.reshape(height, width, channels)

    def _ok(self, code: int, name: str) -> None:
        if int(code) != 0:
            message = ""
            getter = getattr(self.mod, "CameraGetErrorString", None)
            if getter is not None:
                message = str(getter(int(code)) or "")
            raise MindVisionError(f"{name} 失败，SDK 返回 {code} {message}".strip())


class CtypesMindVision:
    """直接加载 libMVSDK.so / MVCAMSDK_X64.dll。"""

    def __init__(self, lib: CDLL):
        self.lib = lib
        self._channels = 3
        self._bind()
        self._ok(self.lib.CameraSdkInit(c_int(0)), "CameraSdkInit")

    def _bind(self) -> None:
        lib = self.lib
        lib.CameraSdkInit.argtypes = [c_int]
        lib.CameraSdkInit.restype = c_int
        lib.CameraEnumerateDevice.argtypes = [POINTER(tSdkCameraDevInfo), POINTER(c_int)]
        lib.CameraEnumerateDevice.restype = c_int
        lib.CameraInit.argtypes = [POINTER(tSdkCameraDevInfo), c_int, c_int, POINTER(c_int)]
        lib.CameraInit.restype = c_int
        lib.CameraUnInit.argtypes = [c_int]
        lib.CameraUnInit.restype = c_int
        lib.CameraSetAeState.argtypes = [c_int, c_int]
        lib.CameraSetAeState.restype = c_int
        lib.CameraSetTriggerMode.argtypes = [c_int, c_int]
        lib.CameraSetTriggerMode.restype = c_int
        lib.CameraSetExposureTime.argtypes = [c_int, c_double]
        lib.CameraSetExposureTime.restype = c_int
        lib.CameraSetAnalogGain.argtypes = [c_int, c_int]
        lib.CameraSetAnalogGain.restype = c_int
        lib.CameraSetIspOutFormat.argtypes = [c_int, c_uint]
        lib.CameraSetIspOutFormat.restype = c_int
        lib.CameraPlay.argtypes = [c_int]
        lib.CameraPlay.restype = c_int
        lib.CameraGetImageBuffer.argtypes = [c_int, POINTER(tSdkFrameHead), POINTER(c_void_p), c_uint]
        lib.CameraGetImageBuffer.restype = c_int
        lib.CameraImageProcess.argtypes = [c_int, c_void_p, c_void_p, POINTER(tSdkFrameHead)]
        lib.CameraImageProcess.restype = c_int
        lib.CameraReleaseImageBuffer.argtypes = [c_int, c_void_p]
        lib.CameraReleaseImageBuffer.restype = c_int
        if hasattr(lib, "CameraGetErrorString"):
            lib.CameraGetErrorString.argtypes = [c_int]
            lib.CameraGetErrorString.restype = c_char_p

    def enumerate(self) -> list[dict[str, Any]]:
        count = c_int(32)
        found = (tSdkCameraDevInfo * 32)()
        self._ok(self.lib.CameraEnumerateDevice(found, byref(count)), "CameraEnumerateDevice")
        devices = []
        for index in range(max(0, count.value)):
            raw = tSdkCameraDevInfo()
            memmove(byref(raw), byref(found[index]), sizeof(raw))
            devices.append({
                "product": _text(raw.acProductName),
                "sn": _text(raw.acSn),
                "port": _text(raw.acPortType),
                "friendly": _text(raw.acFriendlyName),
                "raw": raw,
            })
        return devices

    def open(self, dev: dict[str, Any]) -> int:
        handle = c_int()
        self._ok(self.lib.CameraInit(byref(dev["raw"]), -1, -1, byref(handle)), "CameraInit")
        return int(handle.value)

    def close(self, handle: int) -> None:
        self.lib.CameraUnInit(c_int(handle))

    def set_ae(self, handle: int, auto: int) -> None:
        self._ok(self.lib.CameraSetAeState(c_int(handle), c_int(auto)), "CameraSetAeState")

    def set_trigger(self, handle: int, mode: int) -> None:
        self._ok(self.lib.CameraSetTriggerMode(c_int(handle), c_int(mode)), "CameraSetTriggerMode")

    def set_exposure_us(self, handle: int, microseconds: float) -> None:
        self._ok(
            self.lib.CameraSetExposureTime(c_int(handle), c_double(float(microseconds))),
            "CameraSetExposureTime",
        )

    def set_gain(self, handle: int, gain: int) -> None:
        self._ok(self.lib.CameraSetAnalogGain(c_int(handle), c_int(int(gain))), "CameraSetAnalogGain")

    def set_frame_rate(self, handle: int, hz: int) -> None:
        fn = getattr(self.lib, "CameraSetFrameRate", None)
        if fn is None:
            raise MindVisionError("这份 SDK 没有导出 CameraSetFrameRate，帧率没有下发。")
        try:
            code = fn(c_int(handle), c_int(int(hz)))
        except AttributeError as exc:
            raise MindVisionError("这份 SDK 没有导出 CameraSetFrameRate，帧率没有下发。") from exc
        self._ok(code, "CameraSetFrameRate")

    def set_output(self, handle: int) -> None:
        if self.lib.CameraSetIspOutFormat(c_int(handle), c_uint(CAMERA_MEDIA_TYPE_BGR8)) == 0:
            self._channels = 3
            return
        if self.lib.CameraSetIspOutFormat(c_int(handle), c_uint(CAMERA_MEDIA_TYPE_MONO8)) == 0:
            self._channels = 1
            return
        raise MindVisionError("CameraSetIspOutFormat 失败。")

    def play(self, handle: int) -> None:
        self._ok(self.lib.CameraPlay(c_int(handle)), "CameraPlay")

    def grab(self, handle: int, timeout_ms: int) -> np.ndarray | None:
        head = tSdkFrameHead()
        raw = c_void_p()
        code = self.lib.CameraGetImageBuffer(c_int(handle), byref(head), byref(raw), c_uint(int(timeout_ms)))
        if code == TIMEOUT:
            return None
        self._ok(code, "CameraGetImageBuffer")
        width, height = int(head.iWidth), int(head.iHeight)
        if width <= 0 or height <= 0:
            self.lib.CameraReleaseImageBuffer(c_int(handle), raw)
            raise MindVisionError(f"取到的图像尺寸无效：{width}x{height}")
        channels = self._channels
        out = (c_ubyte * (width * height * channels))()
        try:
            self._ok(
                self.lib.CameraImageProcess(c_int(handle), raw, c_void_p(out), byref(head)),
                "CameraImageProcess",
            )
        finally:
            self.lib.CameraReleaseImageBuffer(c_int(handle), raw)
        arr = np.frombuffer(out, dtype=np.uint8).copy()
        if channels == 1:
            return cv2.cvtColor(arr.reshape(height, width), cv2.COLOR_GRAY2BGR)
        return arr.reshape(height, width, channels)

    def _ok(self, code: int, name: str) -> None:
        if int(code) != 0:
            message = ""
            if hasattr(self.lib, "CameraGetErrorString"):
                raw = self.lib.CameraGetErrorString(c_int(int(code)))
                if raw:
                    message = _text(raw)
            raise MindVisionError(f"{name} 失败，SDK 返回 {code} {message}".strip())


class MindVisionSource:
    """迈德威视网口相机。sdk 参数只给测试注入假库，正常路径会 load_sdk()。"""

    def __init__(
        self,
        serial: str = SERIAL,
        model: str = MODEL,
        trigger: str = "hardware",
        exposure_ms: float | None = None,
        gain: float | None = None,
        fps: float | None = None,
        sdk: Any = None,
        sdk_path: str = "",
    ):
        self.serial = serial or ""
        self.model = model or MODEL
        self.trigger = trigger or "hardware"
        self.exposure_ms = None if exposure_ms is None else float(exposure_ms)
        self.gain = None if gain is None else float(gain)
        self.fps = None if fps is None else float(fps)
        self._sdk = sdk
        self._sdk_path = sdk_path or ""
        self._handle: int | None = None
        self._info = SourceInfo(
            name=f"MindVision {self.model}",
            width=0,
            height=0,
            fps=float(self.fps or 0),
            serial=self.serial,
        )

    def open(self) -> None:
        if self._sdk is None:
            self._sdk = load_sdk(self._sdk_path or None)
        devices = self._sdk.enumerate()
        dev = pick_device(devices, self.serial, self.model)
        self._handle = int(self._sdk.open(dev))
        self.serial = str(dev.get("sn") or self.serial)
        self.model = str(dev.get("product") or self.model)
        self._sdk.set_ae(self._handle, 0)
        self._sdk.set_output(self._handle)
        self._push(self.trigger, self.exposure_ms, self.gain, self.fps)
        self._sdk.play(self._handle)
        self._info = SourceInfo(
            name=f"MindVision {self.model}",
            width=0,
            height=0,
            fps=float(self.fps or 0),
            serial=self.serial,
        )

    def apply_tune(
        self,
        exposure_ms: float | None = None,
        gain: float | None = None,
        fps: float | None = None,
        trigger: str = "continuous",
    ) -> None:
        """调试曝光、增益、帧率时由界面调用。SDK 在场就真正下发。"""
        if self._handle is None or self._sdk is None:
            raise MindVisionError("迈德威视相机还没打开，参数不能下发。")
        self._push(trigger, exposure_ms, gain, fps)

    def _push(self, trigger: str, exposure_ms, gain, fps) -> None:
        mode = EXTERNAL_TRIGGER if trigger == "hardware" else CONTINUOUS
        self._sdk.set_trigger(self._handle, mode)
        self.trigger = "hardware" if mode == EXTERNAL_TRIGGER else "continuous"
        if exposure_ms is not None:
            self.exposure_ms = float(exposure_ms)
            self._sdk.set_exposure_us(self._handle, self.exposure_ms * 1000.0)
        if gain is not None:
            self.gain = float(gain)
            self._sdk.set_gain(self._handle, int(round(self.gain)))
        if fps is not None:
            self.fps = float(fps)
            self._sdk.set_frame_rate(self._handle, int(round(self.fps)))
            self._info.fps = self.fps

    def read(self, timeout_ms: int = 2000) -> Frame | None:
        if self._handle is None or self._sdk is None:
            raise MindVisionError("迈德威视相机还没打开。")
        image = self._sdk.grab(self._handle, int(timeout_ms))
        if image is None:
            return None
        self._info.width = int(image.shape[1])
        self._info.height = int(image.shape[0])
        return Frame.create(
            image,
            source="mindvision",
            serial=self.serial,
            model=self.model,
            trigger=self.trigger,
        )

    def close(self) -> None:
        if self._handle is not None and self._sdk is not None:
            self._sdk.close(self._handle)
        self._handle = None

    @property
    def info(self) -> SourceInfo:
        return self._info
