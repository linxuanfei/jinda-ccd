"""探测：这台相机认不认标准 GigE Vision（GVCP）发现协议。

背景：
    现场那份 MVCAMSDK.dll 做 GigE 发现时要用内核过滤驱动 `\\\\.\\MvGigeFilter`，
    这台笔记本没装 → SDK 枚举不到相机（但 ping 得通）。
    先确认相机本身是不是标准 GigE Vision 设备：
      是 → 可走标准 GenICam 方案（装 x64 的 MvCameraControl）
      否 → 必须装迈德威视官方 SDK（带 MvGigeFilter）

用法：
    python tools\\probe_gvcp.py
    python tools\\probe_gvcp.py --if-ip 192.168.3.100 --target 192.168.3.150
"""

from __future__ import annotations

import argparse
import socket
import time


GVCP_PORT = 3956


def build_discovery(req_id: int = 1) -> bytes:
    return bytes([0x42, 0x11, 0x00, 0x02]) + (0).to_bytes(2, "big") + req_id.to_bytes(2, "big")


def fields_from_ack(data: bytes) -> dict:
    """按标准 DISCOVERY_ACK 布局尽力解析（长度不够就跳过）。"""
    out: dict = {}
    if len(data) < 8:
        return out
    out["flags"] = data[1]
    out["cmd"] = int.from_bytes(data[2:4], "big")
    out["ack_len"] = int.from_bytes(data[4:6], "big")
    body = data[8:]

    def ip(off: int) -> str:
        return ".".join(str(b) for b in body[off:off + 4]) if len(body) >= off + 4 else ""

    def text(off: int, n: int) -> str:
        if len(body) < off + n:
            return ""
        raw = body[off:off + n].split(b"\x00")[0]
        try:
            return raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return raw.decode("gbk", "replace").strip()

    out["ip"] = ip(0)
    out["subnet"] = ip(4)
    out["gateway"] = ip(8)
    if len(body) >= 18:
        out["mac"] = "-".join(f"{b:02X}" for b in body[12:18])
    out["manufacturer"] = text(20, 32)
    out["model"] = text(52, 32)
    out["version"] = text(84, 32)
    out["serial"] = text(116, 16)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--if-ip", default="", help="从哪个本机 IP 发")
    ap.add_argument("--target", default="255.255.255.255", help="目标（默认广播）")
    ap.add_argument("--wait", type=float, default=1.5, help="听多少秒")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if args.if_ip:
        try:
            sock.bind((args.if_ip, 0))
        except OSError as exc:
            print("绑定 %s 失败：%s" % (args.if_ip, exc))
            return 2
        print("从 %s 发包" % args.if_ip)
    else:
        sock.bind(("", 0))
    sock.settimeout(0.4)

    pkt = build_discovery(1)
    print("向 %s:%d 发 GVCP DISCOVERY_CMD（%d 字节）" % (args.target, GVCP_PORT, len(pkt)))
    try:
        sock.sendto(pkt, (args.target, GVCP_PORT))
    except OSError as exc:
        print("发送失败：%s" % exc)
        return 2

    got = 0
    t0 = time.time()
    while time.time() - t0 < args.wait:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError as exc:
            print("接收出错：%s" % exc)
            break
        got += 1
        print("")
        print("收到 %d 字节，来自 %s:%d" % (len(data), addr[0], addr[1]))
        print("  hex: %s" % data[:80].hex(" "))
        try:
            info = fields_from_ack(data)
        except Exception as exc:                                  # noqa: BLE001
            print("  （解析失败：%s）" % exc)
            info = {}
        for k in ("flags", "cmd", "ip", "subnet", "gateway", "mac",
                  "manufacturer", "model", "version", "serial"):
            if info.get(k):
                print("  %-12s = %s" % (k, info[k]))

    print("")
    if got:
        print("结论：标准 GigE Vision 可用（收到 %d 个回复）" % got)
        return 0
    print("结论：没收到 GVCP 回复 —— 相机可能只认迈德威视私有协议，需要官方 SDK（含 MvGigeFilter 驱动）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
