"""标定：像素 ↔ 毫米换算。

支持两种来源：
1. **两点标定**：在图像上点两个点并输入实际长度（X 水平 / Y 垂直分别设）；
2. **直接给数值**（现场习惯）：直接填 `X mm/px`、`Y mm/px`，或用
   「已知真实值 + 当前实测值」反算（现场例子：设定 1，实测 1.433 → 比例 × 1/1.433）。

X/Y 分开是必须的：相机像素不一定是正方形，且现场就是「X 为水平、Y 为垂直」分别标。
为了兼容旧配方，保留 `mm_per_px` 字段；新字段是 `x_mm_per_px` / `y_mm_per_px`。
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
    mm_per_px: float = 0.0              # 兼容旧字段：X/Y 相同时的单一比例
    x_mm_per_px: float = 0.0            # 水平方向 mm/px
    y_mm_per_px: float = 0.0            # 垂直方向 mm/px
    reference_mm: float = 0.0           # 标定时输入的实际长度
    points_px: list[list[float]] = field(default_factory=list)   # 两个标定点（图像坐标）
    note: str = ""

    # ------------------------------------------------------------------ 计算
    @property
    def valid(self) -> bool:
        return self.sx > 0 or self.sy > 0

    @property
    def sx(self) -> float:
        """水平 mm/px（新字段优先，其次旧字段）。"""
        return self.x_mm_per_px if self.x_mm_per_px > 0 else self.mm_per_px

    @property
    def sy(self) -> float:
        """垂直 mm/px（没单独给就退回 X 的比例）。"""
        if self.y_mm_per_px > 0:
            return self.y_mm_per_px
        return self.sx

    @property
    def px_per_mm(self) -> float:
        return 1.0 / self.mm_per_px if self.mm_per_px > 0 else 0.0

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
        self.x_mm_per_px = float(max(x_scale, 0.0))
        self.y_mm_per_px = float(max(y_scale, 0.0))
        self.method = method
        # 同步旧字段，保证老代码/老配方读得到
        self.mm_per_px = self.x_mm_per_px or self.y_mm_per_px

    def set_by_two_points(self, p1: tuple[float, float], p2: tuple[float, float],
                          known_mm: float, axis: str = AXIS_X) -> float:
        """用两点 + 实际长度标定，返回设置的比例（mm/px）。

        axis：
        - ``x``（默认）：按水平像素距离算，写入 X 比例；
        - ``y``：按垂直像素距离算，写入 Y 比例；
        - ``auto``：按两点里较大的那个分量判断方向。
        """
        dx = abs(float(p2[0]) - float(p1[0]))
        dy = abs(float(p2[1]) - float(p1[1]))
        if axis == "auto":
            axis = AXIS_X if dx >= dy else AXIS_Y
        span_px = dx if axis == AXIS_X else dy
        if span_px <= 0:
            # 该方向没有位移（点画在同一条水平/垂直线上），退回欧氏距离
            span_px = math.hypot(dx, dy)
        if span_px <= 0 or known_mm <= 0:
            raise ValueError("标定点重合或长度无效")
        scale = float(known_mm) / span_px
        self.method = "two_points"
        self.points_px = [[float(p1[0]), float(p1[1])], [float(p2[0]), float(p2[1])]]
        self.reference_mm = float(known_mm)
        if axis == AXIS_X:
            self.x_mm_per_px = scale
            if self.y_mm_per_px <= 0:
                self.y_mm_per_px = scale        # Y 还没标就先用同一个，避免半标定
        else:
            self.y_mm_per_px = scale
            if self.x_mm_per_px <= 0:
                self.x_mm_per_px = scale
        self.mm_per_px = self.x_mm_per_px or self.y_mm_per_px
        return scale

    def set_by_known_value(self, known_mm: float, measured_mm: float,
                           axis: str = AXIS_X) -> float:
        """用「真实值 vs 当前实测值」反算比例（现场最常用的一招）。

        现场原话：「视频中设定值为 1，现检测结果为 1.433 → 点相机标定改标定值」。
        新比例 = 旧比例 × (真实值 / 实测值)；X 时同时按 sx/sy 比例缩放 Y，
        保证只有一个方向可测时也不会把画面拉歪。
        """
        if known_mm <= 0 or measured_mm <= 0:
            raise ValueError("真实值/实测值必须大于 0")
        k = float(known_mm) / float(measured_mm)
        if axis == AXIS_X:
            base_x = self.sx if self.sx > 0 else 1.0
            base_y = self.sy if self.sy > 0 else base_x
            self.set_xy(base_x * k, base_y * k, method="value_fix")
        else:
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
            "x_mm_per_px": round(self.x_mm_per_px, 8),
            "y_mm_per_px": round(self.y_mm_per_px, 8),
            "reference_mm": self.reference_mm,
            "points_px": self.points_px,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Calibration":
        data = data or {}
        old = float(data.get("mm_per_px", 0) or 0)
        x = float(data.get("x_mm_per_px", 0) or 0)
        y = float(data.get("y_mm_per_px", 0) or 0)
        if x <= 0 and y <= 0 and old > 0:      # 旧配方只有 mm_per_px
            x = y = old
        return cls(
            name=data.get("name", "default"),
            method=data.get("method", "two_points"),
            mm_per_px=old or x or y,
            x_mm_per_px=x,
            y_mm_per_px=y,
            reference_mm=float(data.get("reference_mm", 0) or 0),
            points_px=[list(map(float, p)) for p in data.get("points_px", [])],
            note=data.get("note", ""),
        )

    def describe(self) -> str:
        if not self.valid:
            return "未标定"
        if abs(self.sx - self.sy) < 1e-9:
            return f"X/Y {self.sx:.6f} mm/px（{1.0 / self.sx:.2f} px/mm，参考 {self.reference_mm:g}mm）"
        return (f"X {self.sx:.6f} / Y {self.sy:.6f} mm/px"
                f"（参考 {self.reference_mm:g}mm）")
