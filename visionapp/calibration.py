"""标定：像素 ↔ 毫米换算。

保留旧字段 ``mm_per_px`` / ``mm_per_px_x`` / ``mm_per_px_y``。
分轴 ``set_axis`` 只写对应轴时，``valid`` 仍为真。
另外支持直接填 X/Y，以及「已知真实值 + 当前实测值」反算
（现场：设定 1，实测 1.433）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

AXIS_X = "x"
AXIS_Y = "y"


@dataclass
class Calibration:
    """一套标定参数（跟着配方走）。"""
    name: str = "default"
    method: str = "two_points"          # two_points | manual | value_fix | checkerboard（预留）
    mm_per_px: float = 0.0              # 像素 → 毫米（两点斜距，兼容旧配方）
    mm_per_px_x: float = 0.0            # 水平轴，间距
    mm_per_px_y: float = 0.0            # 垂直轴，贴板
    reference_mm: float = 0.0           # 标定时输入的实际长度
    points_px: list[list[float]] = field(default_factory=list)   # 两个标定点（图像坐标）
    note: str = ""

    # ------------------------------------------------------------------ 计算
    @property
    def valid(self) -> bool:
        """任一轴或旧的单一比例有效，都算已经标定。"""
        return self.mm_per_px > 0 or self.mm_per_px_x > 0 or self.mm_per_px_y > 0

    @property
    def sx(self) -> float:
        """水平 mm/px。分轴字段优先，否则退回旧字段。"""
        if self.mm_per_px_x > 0:
            return self.mm_per_px_x
        return self.mm_per_px

    @property
    def sy(self) -> float:
        """垂直 mm/px。没单独给就退回水平比例。"""
        if self.mm_per_px_y > 0:
            return self.mm_per_px_y
        return self.sx

    @property
    def px_per_mm(self) -> float:
        scale = self.mm_per_px if self.mm_per_px > 0 else self.sx
        return 1.0 / scale if scale > 0 else 0.0

    @property
    def line_px(self) -> float:
        if len(self.points_px) < 2:
            return 0.0
        (x1, y1), (x2, y2) = self.points_px[0], self.points_px[1]
        return math.hypot(x2 - x1, y2 - y1)

    # ------------------------------------------------------------------ 设置
    def set_xy(self, x_scale: float, y_scale: float, method: str = "manual") -> None:
        """直接设置 X / Y 两个比例（mm/px）。"""
        if x_scale <= 0 and y_scale <= 0:
            raise ValueError("X/Y 比例必须至少有一个大于 0")
        self.mm_per_px_x = float(max(x_scale, 0.0))
        self.mm_per_px_y = float(max(y_scale, 0.0))
        self.method = method
        self.mm_per_px = self.mm_per_px_x or self.mm_per_px_y

    def set_by_two_points(self, p1: tuple[float, float], p2: tuple[float, float],
                          known_mm: float, axis: str | None = None) -> float:
        """用两点 + 实际长度计算换算关系，返回 mm/px。

        ``axis`` 省略时按斜距写入旧字段，并在还没分轴时同步 X/Y（兼容原来的两点标定）。
        ``x`` / ``y`` / ``auto`` 按该方向的像素跨度写入对应轴。
        """
        dx = abs(float(p2[0]) - float(p1[0]))
        dy = abs(float(p2[1]) - float(p1[1]))
        if axis is None:
            dist = math.hypot(dx, dy)
            if dist <= 0 or known_mm <= 0:
                raise ValueError("标定点重合或长度无效")
            scale = float(known_mm) / dist
            self.method = "two_points"
            self.points_px = [[float(p1[0]), float(p1[1])], [float(p2[0]), float(p2[1])]]
            self.reference_mm = float(known_mm)
            self.mm_per_px = scale
            if self.mm_per_px_x <= 0:
                self.mm_per_px_x = scale
            if self.mm_per_px_y <= 0:
                self.mm_per_px_y = scale
            return scale

        which = axis
        if which == "auto":
            which = AXIS_X if dx >= dy else AXIS_Y
        span_px = dx if which == AXIS_X else dy
        if span_px <= 0:
            span_px = math.hypot(dx, dy)
        if span_px <= 0 or known_mm <= 0:
            raise ValueError("标定点重合或长度无效")
        scale = float(known_mm) / span_px
        self.method = "two_points"
        self.points_px = [[float(p1[0]), float(p1[1])], [float(p2[0]), float(p2[1])]]
        self.reference_mm = float(known_mm)
        if which == AXIS_X:
            self.mm_per_px_x = scale
            if self.mm_per_px_y <= 0:
                self.mm_per_px_y = scale
        else:
            self.mm_per_px_y = scale
            if self.mm_per_px_x <= 0:
                self.mm_per_px_x = scale
        self.mm_per_px = self.mm_per_px_x or self.mm_per_px_y
        return scale

    def set_axis(self, pixel_span: float, real_mm: float, axis: str) -> float:
        """只标定一条轴。X 不看垂直像素，Y 不看水平像素。"""
        from .tuning import scale_for_reading

        scale = scale_for_reading(pixel_span, real_mm)
        if axis == "x":
            self.mm_per_px_x = scale
        elif axis == "y":
            self.mm_per_px_y = scale
        else:
            raise ValueError("轴向只能是 x 或 y")
        self.method = "two_points"
        return scale

    def set_by_known_value(self, known_mm: float, measured_mm: float,
                           axis: str = AXIS_X) -> float:
        """用「真实值 vs 当前实测值」反算比例。

        现场例子：设定 1，实测 1.433。
        新比例 = 旧比例 × (真实值 / 实测值)。X、Y 按同一系数缩放，避免只改一轴把画面拉歪。
        """
        if known_mm <= 0 or measured_mm <= 0:
            raise ValueError("真实值/实测值必须大于 0")
        k = float(known_mm) / float(measured_mm)
        base_x = self.sx if self.sx > 0 else 1.0
        base_y = self.sy if self.sy > 0 else base_x
        self.set_xy(base_x * k, base_y * k, method="value_fix")
        return k

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "mm_per_px": round(self.mm_per_px, 8),
            "mm_per_px_x": round(self.mm_per_px_x, 8),
            "mm_per_px_y": round(self.mm_per_px_y, 8),
            "x_mm_per_px": round(self.sx, 8),
            "y_mm_per_px": round(self.sy, 8),
            "reference_mm": self.reference_mm,
            "points_px": self.points_px,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Calibration":
        data = data or {}
        old = float(data.get("mm_per_px", 0) or 0)
        x = float(data.get("mm_per_px_x", 0) or data.get("x_mm_per_px", 0) or 0)
        y = float(data.get("mm_per_px_y", 0) or data.get("y_mm_per_px", 0) or 0)
        if x <= 0 and y <= 0 and old > 0:
            x = y = old
        return cls(
            name=data.get("name", "default"),
            method=data.get("method", "two_points"),
            mm_per_px=old or x or y,
            mm_per_px_x=x,
            mm_per_px_y=y,
            reference_mm=float(data.get("reference_mm", 0) or 0),
            points_px=[list(map(float, p)) for p in data.get("points_px", [])],
            note=data.get("note", ""),
        )

    def describe(self) -> str:
        if not self.valid:
            return "未标定"
        if self.sx > 0 and self.sy > 0 and abs(self.sx - self.sy) >= 1e-9:
            return (f"X {self.sx:.6f} / Y {self.sy:.6f} mm/px"
                    f"（参考 {self.reference_mm:g}mm）")
        scale = self.sx or self.mm_per_px
        px = (1.0 / scale) if scale > 0 else 0.0
        return f"{scale:.6f} mm/px（{px:.2f} px/mm，参考 {self.reference_mm:g}mm）"
