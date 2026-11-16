"""核心数据结构：帧、帧源信息、几何辅助。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class SourceInfo:
    """帧源元信息（用于配方绑定与标定）。"""
    name: str                      # 如 "sim" / "folder" / "maixcam-rtsp"
    width: int
    height: int
    fps: float = 0.0
    is_color: bool = True
    serial: str = ""


@dataclass
class Frame:
    """统一帧对象。"""
    bgr: np.ndarray                # HxWx3 uint8
    index: int = 0
    ts_ns: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, img: np.ndarray, index: int = 0, **meta: Any) -> "Frame":
        if img.ndim == 2:
            img = np.dstack([img] * 3)
        return cls(bgr=img, index=index, ts_ns=time.time_ns(), meta=dict(meta))

    @property
    def height(self) -> int:
        return int(self.bgr.shape[0])

    @property
    def width(self) -> int:
        return int(self.bgr.shape[1])


@dataclass
class Rect:
    """检测区域。

    - ``rect``：矩形，用 x/y/w/h；
    - ``line``：任意角度的线形 ROI，端点在 ``line=(x1,y1,x2,y2)``，
      x/y/w/h 是外接矩形，按矩形裁图的算子不用改。
    水平基准线（只向右、锁高度）仍由界面的 hline 工具生成普通矩形。
    """
    x: int
    y: int
    w: int
    h: int
    shape: str = "rect"
    line: tuple[int, int, int, int] | None = None

    def is_line(self) -> bool:
        return self.shape == "line" and self.line is not None

    @classmethod
    def from_line(cls, x1: float, y1: float, x2: float, y2: float, pad: int = 1) -> "Rect":
        xi1, yi1, xi2, yi2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
        x = min(xi1, xi2)
        y = min(yi1, yi2)
        w = max(pad, abs(xi2 - xi1) + pad)
        h = max(pad, abs(yi2 - yi1) + pad)
        return cls(x, y, w, h, shape="line", line=(xi1, yi1, xi2, yi2))

    def clamp(self, width: int, height: int) -> "Rect":
        x = max(0, min(int(self.x), max(0, width - 1)))
        y = max(0, min(int(self.y), max(0, height - 1)))
        w = max(1, min(int(self.w), width - x))
        h = max(1, min(int(self.h), height - y))
        line = self.line
        if line is not None:
            x1 = max(0, min(int(line[0]), max(0, width - 1)))
            y1 = max(0, min(int(line[1]), max(0, height - 1)))
            x2 = max(0, min(int(line[2]), max(0, width - 1)))
            y2 = max(0, min(int(line[3]), max(0, height - 1)))
            line = (x1, y1, x2, y2)
        return Rect(x, y, w, h, shape=self.shape, line=line)

    def bbox(self) -> "Rect":
        return Rect(self.x, self.y, self.w, self.h)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    def as_dict(self) -> list[int] | dict:
        if self.is_line() and self.line is not None:
            return {"shape": "line", "line": list(self.line)}
        return [self.x, self.y, self.w, self.h]

    @classmethod
    def from_any(cls, value: Any) -> "Rect":
        if isinstance(value, Rect):
            return value
        if isinstance(value, dict):
            shape = str(value.get("shape", "rect"))
            ln = value.get("line") or value.get("points")
            if shape == "line" and ln and len(ln) == 4:
                return cls.from_line(*ln)
            if "x" in value:
                return cls(int(value["x"]), int(value["y"]), int(value["w"]), int(value["h"]))
            raise ValueError(f"无法解析 ROI: {value!r}")
        if isinstance(value, (list, tuple)):
            if len(value) == 4:
                return cls(*(int(v) for v in value))
            if len(value) == 8:
                return cls.from_line(*value[:4])
        raise ValueError(f"无法解析 ROI: {value!r}")


@dataclass
class Point:
    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)
