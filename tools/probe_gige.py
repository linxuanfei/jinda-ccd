"""探测：用 SDK 的 GigE 专用接口找相机（跨网段也能找到），必要时读/改相机 IP。

背景：相机可能不在我们网卡所在的网段，普通 `CameraEnumerateDevice` 看不到。
MindVision SDK 另外导出了：
    CameraGigeEnumerateDevice   枚举 GigE 相机（返回 IP 列表）
    CameraGigeGetIp / GetMac    读某台相机的 IP / MAC
    CameraGigeSetIp             改相机 IP

用法（必须 32 位 Python）：
    C:\\py38x86\\python.exe tools\\probe_gige.py
    C:\\py38x86\\python.exe tools\\probe_gige.py --set-ip 192.168.0.10   # 改相机 IP（谨慎）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from ctypes import CDLL, POINTER, byref, c_char_p, c_int, c_uint, create_string_buffer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.sources_mindvision import find_sdk_dir  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_DEV = 32
STR_LEN = 32


def ascii_strings(buf: bytes, minlen: int = 4) -> list[str]:
    out, cur = [], bytearray()
    for b in buf:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= minlen:
                out.append(cur.decode("ascii"))
            cur.clear()
    if len(cur) >= minlen:
        out.append(cur.decode("ascii"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-ip", default="", help="把第一台相机 IP 改成这个")
    ap.add_argument("--sdk", default="")
    args = ap.parse_args()

    folder = args.sdk or find_sdk_dir() or ""
    if not folder:
        print("没找到 SDK 目录")
        return 2
    dll_path = os.path.join(folder, "MVCAMSDK.dll")
    if not os.path.isfile(dll_path):
        dll_path = os.path.join(folder, "MVCAMSDK_X64.dll")
    print(f"DLL: {dll_path}")
    lib = CDLL(dll_path)

    lib.CameraSdkInit.argtypes = [c_int]
    lib.CameraSdkInit.restype = c_int
    code = lib.CameraSdkInit(c_int(0))
    print(f"CameraSdkInit -> {code}")

    # ---- CameraGigeEnumerateDevice(char* pIpList, int* pNum, int bufSize)
    fn = getattr(lib, "CameraGigeEnumerateDevice", None)
    if fn is None:
        print("这份 SDK 没有 CameraGigeEnumerateDevice")
        return 3
    fn.argtypes = [c_char_p, POINTER(c_int), c_int]
    fn.restype = c_int

    for variant, prep in (
        ("count=容量", lambda: c_int(MAX_DEV)),
        ("count=0", lambda: c_int(0)),
    ):
        buf = create_string_buffer(MAX_DEV * STR_LEN)
        cnt = prep()
        try:
            ret = fn(buf, byref(cnt), c_int(MAX_DEV * STR_LEN))
        except Exception as exc:                                    # noqa: BLE001
            print(f"[{variant}] 调用异常: {exc}")
            continue
        raw = bytes(buf)
        strs = ascii_strings(raw)
        print(f"[{variant}] ret={ret} count={cnt.value}")
        if strs:
            print("  缓冲里的字符串:")
            for s in strs[:40]:
                print(f"    {s}")
        else:
            print("  缓冲里没有可读字符串")

    # ---- 用 CameraEnumerateDeviceEx 对比（可能带 IP 字段）
    ex = getattr(lib, "CameraEnumerateDeviceEx", None)
    if ex is not None:
        print("\nCameraEnumerateDeviceEx 存在（结构体未知，先不调用，避免踩内存）")

    # ---- 改 IP（可选）
    if args.set_ip:
        setfn = getattr(lib, "CameraGigeSetIp", None)
        if setfn is None:
            print("\n没有 CameraGigeSetIp")
            return 4
        ips = [s for s in ascii_strings(bytes(create_string_buffer(0))) ]  # 占位
        print(f"\n准备把相机 IP 改成 {args.set_ip}（需要先知道当前 IP，暂不自动执行）")
        print("  这个函数需要 MAC/IP 参数，签名未在本脚本确认；先看上面的枚举结果。")
        del setfn, ips
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
