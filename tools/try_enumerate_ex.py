"""试 `CameraEnumerateDeviceEx`：看能不能不走 MvGigeFilter 驱动也能枚举 GigE 相机。

背景：相机已确认是标准 GigE Vision（GVCP 广播有回包，IP 192.168.3.150）。
普通 `CameraEnumerateDevice` 返回 0 台，怀疑它依赖 `\\\\.\\MvGigeFilter` 驱动。
"Ex" 版本可能是纯 GVCP 实现，试一下（参数与普通版同构，结构体我们已有）。

用法：C:\\py38x86\\python.exe tools\\try_enumerate_ex.py
"""

from __future__ import annotations

import os
import sys
from ctypes import CDLL, POINTER, byref, c_int

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.sources_mindvision import find_sdk_dir, tSdkCameraDevInfo, _text  # noqa: E402

MAX = 32


def main() -> int:
    folder = find_sdk_dir() or ""
    if not folder:
        print("没找到 SDK 目录")
        return 2
    dll = os.path.join(folder, "MVCAMSDK.dll")
    if not os.path.isfile(dll):
        dll = os.path.join(folder, "MVCAMSDK_X64.dll")
    lib = CDLL(dll)
    lib.CameraSdkInit.argtypes = [c_int]
    lib.CameraSdkInit.restype = c_int
    print("CameraSdkInit ->", lib.CameraSdkInit(c_int(0)))

    for name in ("CameraEnumerateDevice", "CameraEnumerateDeviceEx"):
        fn = getattr(lib, name, None)
        if fn is None:
            print(f"{name}: 不存在")
            continue
        fn.argtypes = [POINTER(tSdkCameraDevInfo), POINTER(c_int)]
        fn.restype = c_int
        arr = (tSdkCameraDevInfo * MAX)()
        n = c_int(MAX)
        ret = fn(arr, byref(n))
        print(f"\n{name}: ret={ret}  n={n.value}")
        if ret == 0 and n.value > 0:
            for i in range(min(n.value, MAX)):
                d = arr[i]
                print(f"  #{i} model={_text(d.acProductName)!r} sn={_text(d.acSn)!r} "
                      f"port={_text(d.acPortType)!r} friendly={_text(d.acFriendlyName)!r}")
        else:
            print("  （没枚举到）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
