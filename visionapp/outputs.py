"""结果输出通道：串口 / TCP / 文件（调试用）。

产线用法：
  - 串口：RS232 或 USB 转串口，把每帧判定结果发给 PLC / 上位机 / 扫码器；
  - TCP ：网口设备（部分 PLC、贴标机、MES 网关）；
  - 文件：没有硬件时先跑通协议（写日志文件），便于联调。

协议模板支持占位符：
  {time} {frame} {verdict} {station} 以及任意检测项 id（如 {pitch} {pin_area}）

模板里可以用 \\r\\n 表示回车换行。
"""

from __future__ import annotations

import abc
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .recipe import PipelineResult

DEFAULT_TEMPLATE = "{time},{verdict},{frame}\r\n"


# --------------------------------------------------------------------------- #
# 通道
# --------------------------------------------------------------------------- #
class OutputChannel(abc.ABC):
    """输出通道：send() 发送一段文本。"""

    def open(self) -> None:
        raise NotImplementedError

    def send(self, text: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        return self.__class__.__name__


class FileChannel(OutputChannel):
    """写到文件（无硬件时联调用）。"""

    def __init__(self, path: str, encoding: str = "utf-8"):
        self.path = path
        self.encoding = encoding
        self._fh = None

    def open(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._fh = open(self.path, "a", encoding=self.encoding, newline="")

    def send(self, text: str) -> None:
        assert self._fh is not None
        self._fh.write(text)
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def describe(self) -> str:
        return f"文件 {self.path}"


class SerialChannel(OutputChannel):
    """串口通道（pyserial）。"""

    def __init__(self, port: str, baud: int = 115200, bytesize: int = 8,
                 parity: str = "N", stopbits: float = 1, encoding: str = "gbk"):
        self.port = port
        self.baud = baud
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.encoding = encoding
        self._ser = None

    def open(self) -> None:
        import serial  # noqa: PLC0415

        self._ser = serial.Serial(
            port=self.port, baudrate=self.baud, bytesize=self.bytesize,
            parity=self.parity, stopbits=self.stopbits, timeout=0.2,
        )

    def send(self, text: str) -> None:
        assert self._ser is not None
        self._ser.write(text.encode(self.encoding, errors="replace"))
        self._ser.flush()

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def describe(self) -> str:
        return f"串口 {self.port}@{self.baud}"


class TcpChannel(OutputChannel):
    """TCP 客户端通道。"""

    def __init__(self, host: str, port: int, encoding: str = "utf-8", timeout: float = 2.0):
        self.host = host
        self.port = int(port)
        self.encoding = encoding
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def open(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)

    def send(self, text: str) -> None:
        assert self._sock is not None
        self._sock.sendall(text.encode(self.encoding, errors="replace"))

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def describe(self) -> str:
        return f"TCP {self.host}:{self.port}"


class NullChannel(OutputChannel):
    """什么也不做的通道（关闭输出用）。"""

    def open(self) -> None:
        return None

    def send(self, text: str) -> None:
        return None

    def close(self) -> None:
        return None

    def describe(self) -> str:
        return "无输出"


# --------------------------------------------------------------------------- #
# 配置与协议
# --------------------------------------------------------------------------- #
@dataclass
class OutputConfig:
    enabled: bool = False
    channel: dict[str, Any] = field(default_factory=lambda: {"kind": "file", "path": "results/output.log"})
    template: str = DEFAULT_TEMPLATE
    encoding: str = "gbk"
    on_ok: bool = True
    on_ng: bool = True
    ok_text: str = "OK"
    ng_text: str = "NG"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutputConfig":
        return cls(
            enabled=bool(data.get("enabled", False)),
            channel=dict(data.get("channel", {"kind": "file", "path": "results/output.log"})),
            template=data.get("template", DEFAULT_TEMPLATE),
            encoding=data.get("encoding", "gbk"),
            on_ok=bool(data.get("on_ok", True)),
            on_ng=bool(data.get("on_ng", True)),
            ok_text=data.get("ok_text", "OK"),
            ng_text=data.get("ng_text", "NG"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "channel": self.channel,
            "template": self.template,
            "encoding": self.encoding,
            "on_ok": self.on_ok,
            "on_ng": self.on_ng,
            "ok_text": self.ok_text,
            "ng_text": self.ng_text,
        }


def build_channel(cfg: OutputConfig) -> OutputChannel:
    ch = cfg.channel or {}
    kind = ch.get("kind", "file")
    if kind == "serial":
        return SerialChannel(ch.get("port", "COM1"), int(ch.get("baud", 115200)),
                             encoding=cfg.encoding)
    if kind == "tcp":
        tcp_port = ch.get("tcp_port", ch.get("port", 5000))
        return TcpChannel(ch.get("host", "127.0.0.1"), int(tcp_port),
                          encoding=cfg.encoding)
    if kind == "file":
        return FileChannel(ch.get("path", "results/output.log"), cfg.encoding)
    return NullChannel()


def format_payload(cfg: OutputConfig, result: PipelineResult) -> str:
    """按模板格式化一帧结果。"""
    verdict = cfg.ok_text if result.ok else cfg.ng_text
    now = time.time()
    values: dict[str, str] = {
        "time": time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}",
        "date": time.strftime("%Y-%m-%d", time.localtime(now)),
        "frame": str(result.frame_index),
        "verdict": verdict,
        "result": "1" if result.ok else "0",
        "elapsed": f"{result.elapsed_ms:.1f}",
    }
    for item_id, res in result.results.items():
        values[item_id] = "" if res.value is None else f"{res.value:.3f}"

    text = cfg.template
    text = text.replace("\\r", "\r").replace("\\n", "\n").replace("\\t", "\t")

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    try:
        return text.format_map(_SafeDict(values))
    except Exception:                                  # noqa: BLE001
        return text + "\r\n"


class OutputManager:
    """组合：格式化 + 发送 + 统计。"""

    def __init__(self, cfg: OutputConfig):
        self.cfg = cfg
        self.channel: OutputChannel = build_channel(cfg)
        self.sent = 0
        self.failed = 0
        self.last_error = ""
        self._opened = False

    def open(self) -> None:
        self.close()
        self.channel = build_channel(self.cfg)
        self.channel.open()
        self._opened = True

    def send(self, result: PipelineResult) -> bool:
        if not self.cfg.enabled or not self._opened:
            return False
        if result.ok and not self.cfg.on_ok:
            return False
        if (not result.ok) and not self.cfg.on_ng:
            return False
        try:
            self.channel.send(format_payload(self.cfg, result))
            self.sent += 1
            return True
        except Exception as exc:                       # noqa: BLE001
            self.failed += 1
            self.last_error = f"输出失败: {exc}"
            return False

    def close(self) -> None:
        try:
            self.channel.close()
        except Exception:                              # noqa: BLE001
            pass
        self._opened = False

    def describe(self) -> str:
        state = "开" if self.cfg.enabled else "关"
        return f"{state} | {self.channel.describe()} | 已发 {self.sent}"
