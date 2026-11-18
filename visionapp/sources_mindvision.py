"""迈德威视（MindVision）相机帧源 —— 支持两代官方 SDK。

现场相机（2026-11-17 实测枚举）：**MV-GE507GC**（网口 GigE，**彩色**，**2448×2048**），\n序列号 `056082520586`，IP 会随网络在 `192.168.3.150` / `10.0.0.250` 之间变。

迈德威视有两代接口，本模块**两个都支持**，按现场装的是哪套自动选：

| 接口 | 库 | 典型函数 | 适用 |
| --- | --- | --- | --- |
| **经典 C 接口**（首选） | `MVCAMSDK_X64.dll` / `libMVSDK.so` | `CameraEnumerateDevice` / `CameraInit` / `CameraGetImageBuffer` / `CameraImageProcess` | MV-GE / MV-SU 等全系，GigE 必须这套 |
| 官方 Python 绑定 | SDK 自带 `mvsdk.py` | 同上，Python 封装 | 装了官方 Python 例程时 |
| GenICam 风格（兼容旧适配） | `MvCameraControl_class.py` + `MvCameraControl.dll` | `MV_CC_EnumDevices` / `MV_CC_GetOneFrameTimeout` | 新版 SDK |

SDK 查找顺序（找到哪套用哪套）：

1. 环境变量 `MINDVISION_SDK`（可指库文件、`mvsdk.py`、或 SDK 目录）
2. `<exe/项目根>/sdk/mindvision/`（推荐放这里，会随 exe 打包）
3. `<exe/项目根>/_deps/mindvision/`
4. `sys.path` 里已有的 `mvsdk` / `MvCameraControl_class`
5. 系统 PATH / 安装目录里的 `MVCAMSDK_X64.dll`

设计原则（与现场要求一致）：
- **按型号 + 序列号锁定相机**；对不上就报错停下，**不偷偷打开旁边那台**，也不用合成图/UVC 冒充；
- 曝光按**微秒**下发（界面填毫秒，×1000）；
- 触发模式：`0` 连续采集 / `1` 软触发 / `2` 硬件触发（默认硬件）；
- 没有 SDK 时给"该放哪个文件"的可操作提示，不是 AttributeError。
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import CDLL, POINTER, byref, c_char, c_char_p, c_double, c_float, c_int
from ctypes import c_ubyte, c_uint, c_void_p, memmove, sizeof, Structure
from typing import Any

import cv2
import numpy as np

from .core import Frame, SourceInfo
from .paths import app_dir, bundle_dir, is_frozen
from .sources import FrameSource

# ---------------------------------------------------------------- 现场相机
DEFAULT_MODEL = "MV-GE507GC"
DEFAULT_SERIAL = "056082520586"

# CameraDefine.h 里的图像格式宏
CAMERA_MEDIA_TYPE_MONO8 = 0x01000000 | 0x00080000 | 0x0001
CAMERA_MEDIA_TYPE_BGR8 = 0x02000000 | 0x00180000 | 0x0015

CONTINUOUS = 0
SOFT_TRIGGER = 1
EXTERNAL_TRIGGER = 2
TIMEOUT = -12

SDK_DIR_ENV = "MINDVISION_SDK"
_C_LIB_NAMES = ("MVCAMSDK_X64.dll", "MVCAMSDK.dll", "libMVSDK.so")
_GENICAM_MARKER = "MvCameraControl_class.py"

# MVSDK 常见返回码 → 现场能看懂的说明（raw 报文太抽象）
SDK_ERROR_HINTS = {
    -1: "一般性错误（检查相机是否被原厂软件占用）",
    -12: "取图超时（没有触发信号 / 曝光太长 / 线材松动）",
    -16: "没有找到相机（检查：相机上电、网线插好、网卡与相机同网段、驱动已装）",
    -20: "相机已被占用（关掉原厂软件/其它取图程序）",
    -25: "参数超出相机支持范围",
    -27: "不支持的命令（这份 SDK 版本太老）",
    32774: "相机被占用：本程序已经打开它（先点停止 / 或在相机对话框里换机位），"
           "或者原厂软件（2pin.exe / In-Vision）还开着、或另开了一个本程序实例",
}




def _lwf_hint() -> str:
    """查一下 GigE 过滤驱动 MvGigeLWF 是否在跑；没跑就是连不上的根因。"""
    import subprocess

    try:
        out = subprocess.run(["sc", "query", "MvGigeLWF"], capture_output=True, text=True, timeout=5)
        text = (out.stdout or "") + (out.stderr or "")
    except Exception:                                    # noqa: BLE001
        return ""
    if "RUNNING" in text:
        return ""
    if "STOPPED" in text:
        return "（相机驱动服务 MvGigeLWF 处于停止状态：请用管理员身份运行包里的『修复相机连接（管理员）.cmd』或重装 MV 相机平台）"
    if "FAILED 1060" in text or "不存在" in text:
        return "（相机驱动 MvGigeLWF 没装：请以管理员身份安装 MindVision 相机平台）"
    return ""


class MindVisionError(RuntimeError):
    """SDK 缺失 / 打开失败 / 设参被拒。"""


def explain_code(code: int) -> str:
    """把 MVSDK 的返回码翻成现场能看懂的一句话（没有映射就返回空串）。"""
    hint = SDK_ERROR_HINTS.get(int(code))
    text = f"（{hint}）" if hint else ""
    if int(code) == -36 and _non_ascii_path():
        text += ("\n注意：程序路径里有中文/非 ASCII 字符，迈德威视的 GigE 过滤驱动"
                 "在这种路径下会初始化失败（2026-11-17 实测）。请把整个程序文件夹"
                 "移到纯英文路径（例如 D:\\VisionApp\\）后再运行。")
    return text


def _non_ascii_path() -> bool:
    """程序所在目录是否含非 ASCII 字符（中文用户名 / 中文目录）。"""
    try:
        base = app_dir() if is_frozen() else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except Exception:                                     # noqa: BLE001
        return False
    try:
        base.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


# ---------------------------------------------------------------- SDK 定位
def candidate_sdk_dirs() -> list[str]:
    """按优先级返回 SDK 目录候选（放 MVCAMSDK_X64.dll / mvsdk.py / MvCameraControl_class.py 的地方）。"""
    dirs: list[str] = []
    env = os.environ.get(SDK_DIR_ENV, "").strip()
    if env and os.path.isdir(env):
        dirs.append(env)
    roots = []
    try:
        roots.append(app_dir())
    except Exception:                                   # noqa: BLE001
        pass
    try:
        if is_frozen():
            roots.append(bundle_dir())
    except Exception:                                   # noqa: BLE001
        pass
    for root in roots:
        dirs.append(os.path.join(root, "sdk", "mindvision"))
        dirs.append(os.path.join(root, "_deps", "mindvision"))
        dirs.append(os.path.join(root, "_internal", "sdk", "mindvision"))
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _has_c_lib(folder: str) -> str:
    for name in _C_LIB_NAMES:
        if os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
    return ""


def find_sdk_dir() -> str | None:
    """返回放 SDK 文件的目录（有 C 库、mvsdk.py 或 GenICam 绑定都算）。"""
    for d in candidate_sdk_dirs():
        if not os.path.isdir(d):
            continue
        if _has_c_lib(d) or os.path.isfile(os.path.join(d, "mvsdk.py")) \
                or os.path.isfile(os.path.join(d, _GENICAM_MARKER)):
            return d
    return None


def sdk_missing_help() -> str:
    cands = "\n".join(f"  - {d}" for d in candidate_sdk_dirs())
    return (
        "未找到迈德威视 MVSDK。\n"
        "需要以下任一组合，放到下列目录之一（或设环境变量 "
        f"{SDK_DIR_ENV}=<SDK 目录>）：\n"
        "  ① 经典 C 接口：MVCAMSDK_X64.dll（x64）\n"
        "  ② 官方 Python 绑定：mvsdk.py（SDK 例程里带的那个）\n"
        "  ③ 新版 GenICam：MvCameraControl_class.py + MvCameraControl.dll\n"
        f"{cands}\n"
        "提示：相机驱动（让设备管理器认出相机）要单独装；这里要的是 SDK 库文件。"
    )


# ---------------------------------------------------------------- 数据结构
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


def _text(buf) -> str:
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
        f"{d.get('model') or '?'} sn={d.get('serial') or '?'} {d.get('port') or ''}".strip()
        for d in devices
    )


def pick_device(devices: list[dict[str, Any]], serial: str = "",
                model: str = "", index: int = 0) -> dict[str, Any]:
    """按序列号 → 型号 → 序号选相机；对不上就报错，不打开旁边那台。"""
    if not devices:
        raise MindVisionError(
            "没有枚举到迈德威视相机。请确认：相机上电 / 网线与网卡同网段 / 已装 MVSDK / "
            "相机没被原厂软件占用。不会改用合成图或 UVC。"
        )
    if serial:
        hits = [d for d in devices if str(d.get("serial") or "") == serial]
        if not hits:
            raise MindVisionError(
                f"没有序列号 {serial} 的相机。枚举到：{_summary(devices)}。不会改用其它相机。")
        if model:
            named = [d for d in hits if model in str(d.get("model") or "")]
            if not named:
                raise MindVisionError(
                    f"序列号 {serial} 的型号是 {hits[0].get('model') or '未知'}，不是 {model}。")
            return named[0]
        return hits[0]
    if model:
        hits = [d for d in devices if model in str(d.get("model") or "")]
        if not hits:
            raise MindVisionError(f"没有型号 {model}。枚举到：{_summary(devices)}。")
        if len(hits) > 1:
            raise MindVisionError(f"有多台 {model}，请写序列号。枚举到：{_summary(hits)}。")
        return hits[0]
    if len(devices) == 1:
        return devices[0]
    if 0 <= index < len(devices):
        return devices[index]
    raise MindVisionError(f"有多台相机，请指定序列号（或序号）。枚举到：{_summary(devices)}。")


# ---------------------------------------------------------------- 后端：C 接口
class CtypesMindVision:
    """经典 C 接口（MVCAMSDK_X64.dll / libMVSDK.so），GigE 相机走这套。"""

    kind = "c-api"

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

    # -- 设备
    def enumerate(self) -> list[dict[str, Any]]:
        count = c_int(32)
        found = (tSdkCameraDevInfo * 32)()
        code = int(self.lib.CameraEnumerateDevice(found, byref(count)))
        if code == -16:                       # 没有相机：给带驱动状态的友好提示
            hint = _lwf_hint()
            if hint:
                raise MindVisionError(
                    "没有枚举到迈德威视相机。" + hint + " 其它检查：相机上电 / 网线 / 同网段 / 没被原厂软件占用。")
            return []
        devices = []
        for i in range(max(0, min(int(count.value), 32))):
            raw = tSdkCameraDevInfo()
            memmove(byref(raw), byref(found[i]), sizeof(raw))
            devices.append({
                "model": _text(raw.acProductName),
                "serial": _text(raw.acSn),
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

    # -- 参数
    def set_ae(self, handle: int, auto: int) -> None:
        self._ok(self.lib.CameraSetAeState(c_int(handle), c_int(auto)), "CameraSetAeState")

    def set_trigger(self, handle: int, mode: int) -> None:
        self._ok(self.lib.CameraSetTriggerMode(c_int(handle), c_int(mode)), "CameraSetTriggerMode")

    def set_exposure_us(self, handle: int, microseconds: float) -> None:
        self._ok(self.lib.CameraSetExposureTime(c_int(handle), c_double(float(microseconds))),
                 "CameraSetExposureTime")

    def set_gain(self, handle: int, gain: int) -> None:
        self._ok(self.lib.CameraSetAnalogGain(c_int(handle), c_int(int(gain))), "CameraSetAnalogGain")

    def set_frame_rate(self, handle: int, hz: int) -> None:
        fn = getattr(self.lib, "CameraSetFrameRate", None)
        if fn is None:
            raise MindVisionError("这份 SDK 没有导出 CameraSetFrameRate，帧率没有下发。")
        self._ok(fn(c_int(handle), c_int(int(hz))), "CameraSetFrameRate")

    def set_output(self, handle: int) -> None:
        if self.lib.CameraSetIspOutFormat(c_int(handle), c_uint(CAMERA_MEDIA_TYPE_BGR8)) == 0:
            self._channels = 3
            return
        if self.lib.CameraSetIspOutFormat(c_int(handle), c_uint(CAMERA_MEDIA_TYPE_MONO8)) == 0:
            self._channels = 1
            return
        raise MindVisionError("CameraSetIspOutFormat 失败（BGR8 与 Mono8 都被拒）。")

    def play(self, handle: int) -> None:
        self._ok(self.lib.CameraPlay(c_int(handle)), "CameraPlay")

    # -- 取图
    def grab(self, handle: int, timeout_ms: int) -> np.ndarray | None:
        head = tSdkFrameHead()
        raw = c_void_p()
        code = self.lib.CameraGetImageBuffer(c_int(handle), byref(head), byref(raw),
                                             c_uint(int(timeout_ms)))
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
            self._ok(self.lib.CameraImageProcess(c_int(handle), raw, ctypes.cast(out, c_void_p), byref(head)),
                     "CameraImageProcess")
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
            raise MindVisionError(f"{name} 失败，SDK 返回 {code} {message}{explain_code(code)}".strip())


# ---------------------------------------------------------------- 后端：官方 mvsdk.py
class MvsdkModule:
    """包装 SDK 自带 Python 绑定 mvsdk.py（函数同 C 接口）。"""

    kind = "mvsdk.py"

    def __init__(self, mod: Any):
        self.mod = mod
        self._channels = 3
        mod.CameraSdkInit(0)

    def enumerate(self) -> list[dict[str, Any]]:
        out = []
        for dev in self.mod.CameraEnumerateDevice():
            out.append({
                "model": dev.GetProductName(),
                "serial": dev.GetSn(),
                "port": dev.GetPortType(),
                "friendly": dev.GetFriendlyName(),
                "raw": dev,
            })
        return out

    def open(self, dev: dict[str, Any]) -> int:
        try:
            return int(self.mod.CameraInit(dev["raw"], -1, -1))
        except Exception as exc:                        # noqa: BLE001
            raise MindVisionError(f"CameraInit 失败：{exc}") from exc

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
        if self.mod.CameraSetIspOutFormat(handle, CAMERA_MEDIA_TYPE_BGR8) == 0:
            self._channels = 3
            return
        if self.mod.CameraSetIspOutFormat(handle, CAMERA_MEDIA_TYPE_MONO8) == 0:
            self._channels = 1
            return
        raise MindVisionError("CameraSetIspOutFormat 失败。")

    def play(self, handle: int) -> None:
        self._ok(self.mod.CameraPlay(handle), "CameraPlay")

    def grab(self, handle: int, timeout_ms: int) -> np.ndarray | None:
        try:
            raw, head = self.mod.CameraGetImageBuffer(handle, int(timeout_ms))
        except Exception as exc:                        # noqa: BLE001
            if getattr(exc, "error_code", None) == TIMEOUT:
                return None
            raise MindVisionError(f"CameraGetImageBuffer 失败：{exc}") from exc
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
            raise MindVisionError(f"{name} 失败，SDK 返回 {code} {message}{explain_code(code)}".strip())


# ---------------------------------------------------------------- SDK 加载
def _try_cdll(lib_path: str):
    """加载 DLL；架构不匹配（32 位 DLL 被 64 位程序加载）时给出可操作提示。"""
    # 打包成 exe 后 PyInstaller 会限制 DLL 搜索目录，厂商 SDK 的驱动客户端
    # 需要能从自己的目录加载依赖；这里显式把 SDK 目录加进搜索路径。
    folder = os.path.dirname(os.path.abspath(lib_path))
    try:
        os.add_dll_directory(folder)          # Python 3.8+，Windows
    except Exception:                         # noqa: BLE001
        pass
    try:
        ctypes.windll.kernel32.SetDllDirectoryW(folder)
    except Exception:                         # noqa: BLE001
        pass
    try:
        return CDLL(lib_path)
    except OSError as exc:
        text = str(exc)
        arch = "64 位" if ctypes.sizeof(ctypes.c_void_p) == 8 else "32 位"
        if "193" in text or "Win32" in text or "不是有效的 Win32" in text:
            raise MindVisionError(
                f"{os.path.basename(lib_path)} 的位数和程序不匹配（当前程序 {arch}）。\n"
                "现场这份是 32 位 MVCAMSDK.dll：请改用 32 位包（dist_x86/VisionApp-x86），"
                "或向厂商要 MVCAMSDK_X64.dll 放到 sdk/mindvision/。"
            ) from exc
        raise


_SDK_CACHE: dict[str, Any] = {}


def load_sdk(path: str | None = None):
    """加载 SDK（**进程内只初始化一次**：多机位共用同一个后端）。"""
    key = (path or os.environ.get(SDK_DIR_ENV) or "").strip() or "auto"
    cached = _SDK_CACHE.get(key)
    if cached is not None:
        return cached
    sdk = _load_sdk_uncached(path)
    _SDK_CACHE[key] = sdk
    return sdk


def _load_sdk_uncached(path: str | None = None):
    """加载迈德威视 SDK，返回后端对象（C 接口优先）。找不到抛 MindVisionError。"""
    chosen = (path or os.environ.get(SDK_DIR_ENV) or "").strip()

    def _from_dir(folder: str):
        # 目录里可能同时有 x86 和 x64 两份 SDK，按当前进程位数挑能加载的那份
        arch_errors: list[str] = []
        for name in _C_LIB_NAMES:
            lib_path = os.path.join(folder, name)
            if not os.path.isfile(lib_path):
                continue
            try:
                return CtypesMindVision(_try_cdll(lib_path))
            except MindVisionError as exc:              # 位数不匹配 → 换下一个
                arch_errors.append(str(exc))
            except OSError:
                continue
        if os.path.isfile(os.path.join(folder, "mvsdk.py")):
            if folder not in sys.path:
                sys.path.insert(0, folder)
            import mvsdk  # type: ignore  # noqa: PLC0415

            if hasattr(mvsdk, "CameraEnumerateDevice"):
                return MvsdkModule(mvsdk)
        if os.path.isfile(os.path.join(folder, _GENICAM_MARKER)):
            if folder not in sys.path:
                sys.path.insert(0, folder)
            return GenICamBackend.import_sdk()
        if arch_errors:
            raise MindVisionError(arch_errors[0])
        return None

    if chosen:
        if os.path.isdir(chosen):
            got = _from_dir(chosen)
            if got is not None:
                return got
            raise MindVisionError(f"{chosen} 里没有 MVCAMSDK_X64.dll / mvsdk.py / {_GENICAM_MARKER}。\n"
                                  + sdk_missing_help())
        if os.path.isfile(chosen) and chosen.lower().endswith(".py"):
            folder = os.path.dirname(os.path.abspath(chosen))
            got = _from_dir(folder)
            if got is not None:
                return got
            raise MindVisionError(f"{chosen} 不是可用的迈德威视 Python 绑定。\n" + sdk_missing_help())
        if os.path.isfile(chosen):
            return CtypesMindVision(_try_cdll(chosen))
        raise MindVisionError(f"{SDK_DIR_ENV} 指向的路径不存在：{chosen}\n" + sdk_missing_help())

    # 没有显式指定：先看我们的 sdk 目录，再看系统里的库 / 已安装模块
    for folder in candidate_sdk_dirs():
        if os.path.isdir(folder):
            try:
                got = _from_dir(folder)
            except MindVisionError:
                raise
            except Exception:                          # noqa: BLE001
                got = None
            if got is not None:
                return got
    try:
        import mvsdk  # type: ignore  # noqa: PLC0415

        if hasattr(mvsdk, "CameraEnumerateDevice"):
            return MvsdkModule(mvsdk)
    except ImportError:
        pass
    for name in _C_LIB_NAMES:
        try:
            return CtypesMindVision(CDLL(name))
        except OSError:
            continue
    try:
        return GenICamBackend.import_sdk()
    except MindVisionError:
        pass
    raise MindVisionError(sdk_missing_hint_wrap())


def sdk_missing_hint_wrap() -> str:
    return sdk_missing_help()


# ---------------------------------------------------------------- 后端：GenICam（兼容旧适配）
class GenICamBackend:
    """新版 GenICam 风格 SDK（MvCameraControl_class.py + MV_CC_* 函数）。"""

    kind = "genicam"

    def __init__(self, mv: Any):
        self.mv = mv
        self._handles: dict[int, Any] = {}
        self._next = 1

    @classmethod
    def import_sdk(cls) -> "GenICamBackend":
        for folder in candidate_sdk_dirs():
            if os.path.isfile(os.path.join(folder, _GENICAM_MARKER)) and folder not in sys.path:
                sys.path.insert(0, folder)
        try:
            import MvCameraControl_class as mv  # type: ignore  # noqa: PLC0415
        except Exception as exc:                        # noqa: BLE001
            raise MindVisionError(f"导入 GenICam 版 SDK 失败：{exc}") from exc
        if not hasattr(mv, "MvCamera"):
            raise MindVisionError("MvCameraControl_class 里没有 MvCamera。")
        return cls(mv)

    def enumerate(self) -> list[dict[str, Any]]:
        mv = self.mv
        dev_list = mv.MV_CC_DEVICE_INFO_LIST()
        gige = getattr(mv, "MV_GIGE_DEVICE", 1)
        usb = getattr(mv, "MV_USB_DEVICE", 2)
        ret = mv.MvCamera.MV_CC_EnumDevices(gige | usb, dev_list)
        if ret != 0:
            raise MindVisionError(f"枚举相机失败，错误码 0x{ret:08X}")
        out = []
        for i in range(int(dev_list.nDeviceNum)):
            info = ctypes.cast(dev_list.pDeviceInfo[i],
                               ctypes.POINTER(mv.MV_CC_DEVICE_INFO)).contents
            if info.nTLayerType == gige:
                g = info.SpecialInfo.stGigEInfo
                out.append({"model": _cstr(g.chModelName), "serial": _cstr(g.chSerialNumber),
                            "port": "GigE", "friendly": _cstr(g.chManufacturerName), "raw": i})
            else:
                u = info.SpecialInfo.stUsb3VInfo
                out.append({"model": _cstr(u.chModelName), "serial": _cstr(u.chSerialNumber),
                            "port": "USB", "friendly": _cstr(u.chManufacturerName), "raw": i})
        return out

    def open(self, dev: dict[str, Any]) -> int:
        mv = self.mv
        dev_list = mv.MV_CC_DEVICE_INFO_LIST()
        gige = getattr(mv, "MV_GIGE_DEVICE", 1)
        usb = getattr(mv, "MV_USB_DEVICE", 2)
        mv.MvCamera.MV_CC_EnumDevices(gige | usb, dev_list)
        idx = int(dev.get("raw") or 0)
        if idx >= int(dev_list.nDeviceNum):
            raise MindVisionError(f"相机序号 {idx} 超出范围")
        info = ctypes.cast(dev_list.pDeviceInfo[idx], ctypes.POINTER(mv.MV_CC_DEVICE_INFO)).contents
        cam = mv.MvCamera()
        ret = cam.MV_CC_CreateHandle(info)
        if ret != 0:
            raise MindVisionError(f"创建相机句柄失败，错误码 0x{ret:08X}")
        ret = cam.MV_CC_OpenDevice(getattr(mv, "MV_ACCESS_Exclusive", 1), 0)
        if ret != 0:
            cam.MV_CC_DestroyHandle()
            raise MindVisionError(f"打开相机失败，错误码 0x{ret:08X}（可能被别的软件占用）")
        handle = self._next
        self._next += 1
        self._handles[handle] = cam
        return handle

    def _cam(self, handle: int):
        cam = self._handles.get(handle)
        if cam is None:
            raise MindVisionError("相机句柄无效")
        return cam

    def close(self, handle: int) -> None:
        cam = self._handles.pop(handle, None)
        if cam is None:
            return
        try:
            cam.MV_CC_StopGrabbing()
        except Exception:                               # noqa: BLE001
            pass
        try:
            cam.MV_CC_CloseDevice()
        except Exception:                               # noqa: BLE001
            pass
        try:
            cam.MV_CC_DestroyHandle()
        except Exception:                               # noqa: BLE001
            pass

    def set_ae(self, handle: int, auto: int) -> None:
        self._cam(handle).MV_CC_SetEnumValue("ExposureAuto", 1 if auto else 0)

    def set_trigger(self, handle: int, mode: int) -> None:
        cam = self._cam(handle)
        ret = cam.MV_CC_SetEnumValue("TriggerMode", 0 if mode == CONTINUOUS else 1)
        if ret != 0:
            raise MindVisionError(f"设置触发模式失败，错误码 0x{ret:08X}")
        if mode != CONTINUOUS:
            cam.MV_CC_SetEnumValue("TriggerSource", 7 if mode == SOFT_TRIGGER else 0)

    def set_exposure_us(self, handle: int, microseconds: float) -> None:
        self._cam(handle).MV_CC_SetFloatValue("ExposureTime", float(microseconds))

    def set_gain(self, handle: int, gain: int) -> None:
        self._cam(handle).MV_CC_SetFloatValue("Gain", float(gain))

    def set_frame_rate(self, handle: int, hz: int) -> None:
        cam = self._cam(handle)
        cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
        cam.MV_CC_SetFloatValue("AcquisitionFrameRate", float(hz))

    def set_output(self, handle: int) -> None:
        self._cam(handle).MV_CC_SetEnumValue("PixelFormat", 0x02180015)   # BGR8

    def play(self, handle: int) -> None:
        cam = self._cam(handle)
        cam.MV_CC_SetImageNodeNum(3)
        ret = cam.MV_CC_StartGrabbing()
        if ret != 0:
            raise MindVisionError(f"开始采集失败，错误码 0x{ret:08X}")

    def grab(self, handle: int, timeout_ms: int) -> np.ndarray | None:
        cam = self._cam(handle)
        mv = self.mv
        st_param = mv.MVCC_INTVALUE() if hasattr(mv, "MVCC_INTVALUE") else mv.MVCC_INTVALUE_EX()
        cam.MV_CC_GetIntValue("PayloadSize", st_param)
        payload = int(getattr(st_param, "nCurValue", 0))
        if payload <= 0:
            return None
        buf = (c_ubyte * payload)()
        st_frame = mv.MV_FRAME_OUT_INFO_EX() if hasattr(mv, "MV_FRAME_OUT_INFO_EX") \
            else mv.MV_FRAME_OUT_INFO()
        ret = cam.MV_CC_GetOneFrameTimeout(byref(buf), payload, st_frame, int(timeout_ms))
        if ret != 0:
            return None
        w, h = int(st_frame.nWidth), int(st_frame.nHeight)
        fmt = int(st_frame.enPixelType)
        raw = bytes(buf[: int(st_frame.nFrameLen)])
        return to_bgr(raw, w, h, fmt)

    def _ok(self, code: int, name: str) -> None:
        if int(code) != 0:
            raise MindVisionError(f"{name} 失败，SDK 返回 {code}{explain_code(code)}")


def _cstr(arr) -> str:
    try:
        raw = bytes(arr)
    except Exception:                                   # noqa: BLE001
        return ""
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------- 像素转换（GenICam 路径用）
PIXEL_MONO8 = 0x01080001
PIXEL_BAYER_GR8 = 0x01080008
PIXEL_BAYER_RG8 = 0x01080009
PIXEL_BAYER_GB8 = 0x0108000A
PIXEL_BAYER_BG8 = 0x0108000B
PIXEL_RGB8 = 0x02180014
PIXEL_BGR8 = 0x02180015
PIXEL_YUV422_YUYV = 0x02100032
PIXEL_YUV422_PACKED = 0x0210001F

_BAYER_MAP = {
    PIXEL_BAYER_RG8: cv2.COLOR_BayerRG2BGR,
    PIXEL_BAYER_GR8: cv2.COLOR_BayerGR2BGR,
    PIXEL_BAYER_GB8: cv2.COLOR_BayerGB2BGR,
    PIXEL_BAYER_BG8: cv2.COLOR_BayerBG2BGR,
}


def to_bgr(raw, width: int, height: int, pixel_type: int) -> np.ndarray:
    """把相机原始数据转成 BGR（GenICam 路径用；C 接口由 CameraImageProcess 处理）。"""
    arr = np.frombuffer(raw, dtype=np.uint8)
    if pixel_type == PIXEL_MONO8:
        return cv2.cvtColor(arr[: width * height].reshape(height, width), cv2.COLOR_GRAY2BGR)
    if pixel_type in _BAYER_MAP:
        return cv2.cvtColor(arr[: width * height].reshape(height, width), _BAYER_MAP[pixel_type])
    if pixel_type == PIXEL_BGR8:
        return arr[: width * height * 3].reshape(height, width, 3).copy()
    if pixel_type == PIXEL_RGB8:
        return cv2.cvtColor(arr[: width * height * 3].reshape(height, width, 3), cv2.COLOR_RGB2BGR)
    if pixel_type in (PIXEL_YUV422_YUYV, PIXEL_YUV422_PACKED):
        return cv2.cvtColor(arr[: width * height * 2].reshape(height, width, 2),
                            cv2.COLOR_YUV2BGR_YUYV)
    raise ValueError(f"暂不支持该像素格式 0x{pixel_type:08X}；请把 PixelFormat 改成 BGR8 / Mono8")


# ---------------------------------------------------------------- 帧源
TRIGGER_LABELS = {
    "hardware": "硬件触发",
    "continuous": "连续采集",
    "software": "软触发",
}


class MindVisionSource(FrameSource):
    """迈德威视相机帧源（GigE / USB 都走同一套接口）。

    参数：
        serial: 序列号（默认现场铭牌 `058071510393`；传空串表示不校验）
        model:  型号（默认 `MV-GE500M-TPO`；传空串表示不校验）
        index:  序列号/型号都没给时，按枚举序号选
        trigger: "hardware"（默认，对应 SDK 的 2）/ "continuous"（0）/ "software"（1）
        exposure_ms / exposure_us: 曝光，毫秒或微秒（二者给一个即可）
        gain:   模拟增益（原样下发）
        fps:    帧率 Hz
        sdk_path: 指定 SDK 目录或库文件
        sdk:    测试注入的假后端
    """

    def __init__(
        self,
        index: int = 0,
        serial: str = DEFAULT_SERIAL,
        model: str = DEFAULT_MODEL,
        exposure_us: float | None = None,
        exposure_ms: float | None = None,
        gain: float | None = None,
        gain_db: float | None = None,
        fps: float = 0.0,
        trigger: str = "continuous",
        sdk_path: str = "",
        sdk: Any = None,
        name: str = "",
        **ignored: Any,
    ):
        self.index = int(index)
        self.serial = str(serial or "")
        self.model = str(model or "")
        if exposure_ms is not None:
            self.exposure_ms = float(exposure_ms)
        elif exposure_us:
            self.exposure_ms = float(exposure_us) / 1000.0
        else:
            self.exposure_ms = None
        gain_value = gain if gain is not None else gain_db
        self.gain = None if gain_value is None else float(gain_value)
        self.fps = float(fps or 0) or None
        self.trigger = (trigger or "hardware").lower()
        self._sdk = sdk
        self._sdk_path = sdk_path or ""
        self._handle: int | None = None
        self._pending = None
        self._backend_kind = getattr(sdk, "kind", "") if sdk is not None else ""
        self._name = name or f"mindvision:{self.model or 'camera'}"
        self._info = SourceInfo(name=self._name, width=0, height=0,
                                fps=float(self.fps or 0), serial=self.serial)
        self._frame_index = 0

    # ---------------------------------------------------------- 枚举
    @staticmethod
    def list_devices(sdk_path: str = "") -> list[dict[str, Any]]:
        sdk = load_sdk(sdk_path or None)
        out = []
        for i, d in enumerate(sdk.enumerate()):
            item = dict(d)
            item["index"] = i
            item.pop("raw", None)
            out.append(item)
        return out

    # ---------------------------------------------------------- 打开
    def open(self) -> None:
        if self._sdk is None:
            self._sdk = load_sdk(self._sdk_path or None)
        self._backend_kind = getattr(self._sdk, "kind", "")
        devices = self._sdk.enumerate()
        dev = pick_device(devices, self.serial, self.model, self.index)
        self._handle = int(self._sdk.open(dev))
        self.serial = str(dev.get("serial") or self.serial)
        self.model = str(dev.get("model") or self.model)

        self._sdk.set_ae(self._handle, 0)          # 关自动曝光
        self._sdk.set_output(self._handle)         # 输出 BGR8（退 Mono8）
        self._push()
        self._sdk.play(self._handle)
        self._info = SourceInfo(name=f"mindvision:{self.model}", width=0, height=0,
                                fps=float(self.fps or 0), serial=self.serial)
        # 打开后先抓一帧：① 把分辨率填进 info（界面上不用等第一帧）
        #                    ② 顺手验证真的能出图（曝光/触发错了这里就能发现）
        try:
            head = self._sdk.grab(self._handle, 1500)
        except Exception:                                     # noqa: BLE001
            head = None
        if head is not None:
            self._pending = head
            self._info.width = int(head.shape[1])
            self._info.height = int(head.shape[0])
        return None

    def _push(self) -> None:
        # 自动曝光开着的话，手动设曝光是无效的（现场反馈"改了没反应"就是这个）
        try:
            self._sdk.set_ae(self._handle, 0)
        except Exception:                                  # noqa: BLE001
            pass
        mode = {"hardware": EXTERNAL_TRIGGER, "software": SOFT_TRIGGER}.get(self.trigger, CONTINUOUS)
        self._sdk.set_trigger(self._handle, mode)
        if self.exposure_ms is not None:
            self._sdk.set_exposure_us(self._handle, self.exposure_ms * 1000.0)
        if self.gain is not None:
            self._sdk.set_gain(self._handle, int(round(self.gain)))
        if self.fps:
            try:
                self._sdk.set_frame_rate(self._handle, int(round(self.fps)))
            except MindVisionError:
                self.fps = None                    # 这份 SDK 没这个导出，忽略

    def apply_tune(self, exposure_ms: float | None = None, gain: float | None = None,
                   fps: float | None = None, trigger: str = "continuous") -> None:
        """界面调参用：相机已打开就真正下发，否则只记在对象上。"""
        if exposure_ms is not None:
            self.exposure_ms = float(exposure_ms)
        if gain is not None:
            self.gain = float(gain)
        if fps is not None:
            self.fps = float(fps) or None
        self.trigger = (trigger or self.trigger).lower()
        if self._handle is not None and self._sdk is not None:
            self._push()

    # ---------------------------------------------------------- 取图
    def read(self, timeout_ms: int = 2000) -> Frame | None:
        if self._handle is None or self._sdk is None:
            raise MindVisionError("相机还没打开")
        image = self._pending
        self._pending = None
        if image is None:
            image = self._sdk.grab(self._handle, int(timeout_ms))
        if image is None:
            return None
        self._info.width = int(image.shape[1])
        self._info.height = int(image.shape[0])
        idx = self._frame_index
        self._frame_index += 1
        return Frame.create(image, index=idx, source=self._name,
                            serial=self.serial, model=self.model, trigger=self.trigger,
                            backend=self._backend_kind)

    def grab_once(self, timeout_ms: int = 1500):
        """强制取一帧（硬件触发没有触发信号时，临时切连续取一张再切回来）。"""
        if self._handle is None or self._sdk is None:
            return None
        old = (self.trigger or "continuous").lower()
        mode = {"continuous": 0, "software": 1, "hardware": 2}.get(old, 2)
        img = None
        try:
            if mode != 0:
                self._sdk.set_trigger(self._handle, 0)      # 临时连续，保证能出图
            img = self._sdk.grab(self._handle, int(timeout_ms))
            if img is not None:
                self._pending = img
        finally:
            if mode != 0:
                try:
                    self._sdk.set_trigger(self._handle, mode)
                except Exception:                          # noqa: BLE001
                    pass
        return img

    def close(self) -> None:
        if self._handle is not None and self._sdk is not None:
            try:
                self._sdk.close(self._handle)
            finally:
                self._handle = None

    @property
    def info(self) -> SourceInfo:
        return self._info

    # ---------------------------------------------------------- 状态
    def describe(self) -> str:
        trig = TRIGGER_LABELS.get(self.trigger, self.trigger)
        return (f"{self.model or '迈德威视相机'}  SN={self.serial or '-'}  "
                f"后端={self._backend_kind or 'c-api'}  触发={trig}  "
                f"曝光={'' if self.exposure_ms is None else f'{self.exposure_ms:g}ms'}")


__all__ = [
    "DEFAULT_MODEL", "DEFAULT_SERIAL", "MindVisionError", "MindVisionSource",
    "CtypesMindVision", "MvsdkModule", "GenICamBackend",
    "candidate_sdk_dirs", "find_sdk_dir", "sdk_missing_help", "load_sdk",
    "pick_device", "to_bgr",
]
