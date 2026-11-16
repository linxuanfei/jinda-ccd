"""输出通道 / 报警 自测。"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.alarm import Alarm, AlarmConfig                    # noqa: E402
from visionapp.ops import ItemResult                              # noqa: E402
from visionapp.outputs import (                                    # noqa: E402
    FileChannel, OutputConfig, OutputManager, SerialChannel, format_payload,
)
from visionapp.recipe import PipelineResult                        # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out", "selftest_out")


def make_result(ok: bool = True) -> PipelineResult:
    results = {
        "pins": ItemResult("pins", "find_blobs", value=2.0, unit="个", ok=True),
        "pitch": ItemResult("pitch", "distance_pp", value=23.05, unit="mm", ok=ok),
    }
    return PipelineResult(frame_index=7, results=results, ok=ok, elapsed_ms=3.2)


def main() -> int:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    fails: list[str] = []

    # ---------------- 1) 模板格式化 ----------------
    cfg = OutputConfig(enabled=True, template="S,{verdict},{frame},{pitch},{pins}\\r\\n")
    ok_txt = format_payload(cfg, make_result(True))
    ng_txt = format_payload(cfg, make_result(False))
    print(f"[1] 模板: OK={ok_txt!r}")
    print(f"           NG={ng_txt!r}")
    if ok_txt != "S,OK,7,23.050,2.000\r\n":
        fails.append(f"OK 模板输出不符: {ok_txt!r}")
    if not ng_txt.startswith("S,NG,7,"):
        fails.append(f"NG 模板输出不符: {ng_txt!r}")

    # ---------------- 2) 文件通道 ----------------
    path = os.path.join(OUT, "out.log")
    mgr = OutputManager(OutputConfig(enabled=True, template="{verdict}\\n",
                                     channel={"kind": "file", "path": path}))
    mgr.open()
    mgr.send(make_result(True))
    mgr.send(make_result(False))
    mgr.close()
    content = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    print(f"[2] 文件通道: {content!r}  sent={mgr.sent}")
    if content != "OK\nNG\n":
        fails.append(f"文件内容不符: {content!r}")

    # ---------------- 3) 串口通道（假串口） ----------------
    import serial  # noqa: PLC0415

    class FakeSerial:
        def __init__(self, **kw):
            self.kw = kw
            self.data = b""
            FakeSerial.last = self

        def write(self, b):
            self.data += b

        def flush(self):
            pass

        def close(self):
            pass

    real_serial = serial.Serial
    serial.Serial = FakeSerial
    try:
        ch = SerialChannel("COM9", 115200, encoding="gbk")
        with_cfg = OutputConfig(enabled=True, template="T,{verdict}\\r\\n", encoding="gbk",
                                channel={"kind": "serial", "port": "COM9", "baud": 115200})
        m = OutputManager(with_cfg)
        m.open()
        m.send(make_result(False))
        m.close()
        data = FakeSerial.last.data.decode("gbk")
        print(f"[3] 串口通道: 写入 {FakeSerial.last.data!r} -> {data!r}  参数={FakeSerial.last.kw}")
        if data != "T,NG\r\n":
            fails.append(f"串口内容不符: {data!r}")
        if FakeSerial.last.kw.get("baudrate") != 115200:
            fails.append("串口参数未透传")
    finally:
        serial.Serial = real_serial

    # ---------------- 4) TCP 通道 ----------------
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received: list[bytes] = []

    def server():
        conn, _ = srv.accept()
        received.append(conn.recv(256))
        conn.close()
        srv.close()

    t = threading.Thread(target=server, daemon=True)
    t.start()
    tcp_cfg = OutputConfig(enabled=True, template="N,{result}\\n", encoding="utf-8",
                           channel={"kind": "tcp", "host": "127.0.0.1", "tcp_port": port})
    m2 = OutputManager(tcp_cfg)
    m2.open()
    m2.send(make_result(True))
    m2.close()
    t.join(timeout=2)
    got = received[0].decode() if received else ""
    print(f"[4] TCP 通道: 服务端收到 {got!r}")
    if got != "N,1\n":
        fails.append(f"TCP 内容不符: {got!r}")

    # ---------------- 5) 报警 ----------------
    hits: list[str] = []
    alarm = Alarm(AlarmConfig(enabled=True, beep_on_ng=True, min_interval_s=0.5),
                  beep_fn=lambda p: hits.append("beep"))
    alarm.trigger(True)
    alarm.trigger(False)
    alarm.trigger(False)          # 间隔内，应被抑制
    time.sleep(0.55)
    alarm.trigger(False)
    print(f"[5] 报警: 响 {len(hits)} 次（fired={alarm.fired} skipped={alarm.skipped}）")
    if len(hits) != 2 or alarm.skipped != 1:
        fails.append(f"报警逻辑不符: hits={len(hits)} skipped={alarm.skipped}")

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
