"""调机纯函数：极性取点、阈值拦截、双边容差、对齐、水平基准线。

界面和算子都调用这里，避免两套几何。
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

# 只保留这六种极性名，不另起别名。
POLARITIES = (
    "黑色底部",
    "白色底部",
    "白色中下",
    "黑色中下",
    "白色中上",
    "白色左侧",
)

AXIS_X = "x"   # 间距
AXIS_Y = "y"   # 贴板


def sample_point(bbox: Sequence[float], polarity: str) -> tuple[float, float]:
    """按极性在斑块外框上取测量点。bbox = (x, y, w, h)。"""
    x, y, w, h = (float(v) for v in bbox)
    cx = x + w / 2.0
    cy = y + h / 2.0
    if polarity == "黑色底部":
        # 相对框高伸出底边四分之一，不再写死 10 像素。
        return cx, y + h + 0.25 * h
    if polarity == "白色底部":
        return cx, y + h
    if polarity == "白色中下":
        return cx, y + 0.75 * h
    if polarity == "黑色中下":
        return cx, y + 0.875 * h
    if polarity == "白色中上":
        return cx, y + 0.25 * h
    if polarity == "白色左侧":
        return x, cy
    return cx, cy


def threshold_block_reason(low: int, high: int | None) -> str:
    """0 或 255 不能当「没有上限」。返回拦截说明；空字符串表示可以测。"""
    if int(low) in (0, 255):
        return "阈值 0 或 255 已拦截，本次不定位、不测量"
    if high is not None and int(high) in (0, 255):
        return "阈值 0 或 255 已拦截，本次不定位、不测量"
    return ""


def shielded_count(gray: np.ndarray, low: int, high: int) -> int:
    """红色掩膜对应的像素数：落在 [low, high] 之外的点。"""
    band = (gray >= int(low)) & (gray <= int(high))
    return int((~band).sum())


def bilateral_window(nominal: float, lower: float, upper: float) -> tuple[float, float]:
    """设计值 ± 双边容差。

    正数容差是幅度：1 与 0.08/0.08 → [0.92, 1.08]。
    任一容差为负时，按名义值加带符号偏差：nominal+lower .. nominal+upper。
    禁止用「下限 ~ 下限+公差」。
    """
    nominal = float(nominal)
    lower = float(lower)
    upper = float(upper)
    if lower < 0 or upper < 0:
        return nominal + lower, nominal + upper
    return nominal - abs(lower), nominal + abs(upper)


def rule_preview(nominal: float, lower: float, upper: float, axis: str) -> str:
    lo, hi = bilateral_window(nominal, lower, upper)
    kind = {AXIS_X: "间距", AXIS_Y: "贴板"}.get(axis, "距离")
    return f"{kind}  设计 {float(nominal):g} ± {abs(float(upper)):g}  → [{lo:.3f}, {hi:.3f}]"


def scale_for_reading(pixel_span: float, real_mm: float) -> float:
    """把某轴上的像素跨度标定成实物长度，得到该轴的 mm/px。"""
    if pixel_span <= 0 or real_mm <= 0:
        raise ValueError("像素跨度或实物长度无效")
    return float(real_mm) / float(pixel_span)


def axis_reading(
    ax: float, ay: float, bx: float, by: float,
    axis: str, scale_x: float, scale_y: float, scale_xy: float,
) -> tuple[float, str]:
    """X 只用水平像素，Y 只用垂直像素。另一轴的比例不得混进来。"""
    if axis == AXIS_X:
        span = abs(bx - ax)
        scale = scale_x if scale_x > 0 else scale_xy
        return (span * scale if scale > 0 else span), "间距"
    if axis == AXIS_Y:
        span = abs(by - ay)
        scale = scale_y if scale_y > 0 else scale_xy
        return (span * scale if scale > 0 else span), "贴板"
    scale = scale_xy if scale_xy > 0 else 0.0
    dist = float(np.hypot(bx - ax, by - ay))
    return (dist * scale if scale > 0 else dist), "距离"


def pair_indices(count: int, start: int, end: int, each_step: bool) -> list[tuple[int, int]]:
    """each_step 为假时只保留起始→终止一对，为真时取中间每一段。"""
    if count <= 0:
        return []
    start = max(0, min(int(start), count - 1))
    end = max(0, min(int(end), count - 1))
    if end < start:
        start, end = end, start
    if not each_step or end == start:
        return [(start, end)]
    return [(i, i + 1) for i in range(start, end)]


def clamp_horizontal_line(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """水平基准线只留在起始高度，并且只向右延伸。"""
    return (float(x0), float(y0), float(max(x0, x1)), float(y0))


def moved_rect(
    x: float, y: float, w: float, h: float,
    x_from: float, y_from: float, x_to: float, y_to: float,
) -> tuple[float, float, float, float]:
    return (x + (x_to - x_from), y + (y_to - y_from), w, h)


def selection_after_draw(selected: Sequence[Any], new_id: Any, ctrl: bool) -> list[Any]:
    """Ctrl 把新框并进当前选择，否则只留新框。"""
    if ctrl:
        out = list(selected)
        if new_id not in out:
            out.append(new_id)
        return out
    return [new_id]


def align_rois(rects: Sequence[Sequence[float]], mode: str) -> list[tuple[float, float, float, float]]:
    """对齐到第一个框。水平/垂直等间距保持首尾，中间均匀。"""
    items = [tuple(float(v) for v in r) for r in rects]
    if len(items) < 2 or mode not in {"left", "right", "top", "bottom", "equal", "hspace", "vspace"}:
        return [(x, y, w, h) for x, y, w, h in items]
    x0, y0, w0, h0 = items[0]
    if mode == "hspace":
        start, end = items[0][0], items[-1][0]
        step = (end - start) / (len(items) - 1)
        return [(start + step * i, y, w, h) for i, (x, y, w, h) in enumerate(items)]
    if mode == "vspace":
        start, end = items[0][1], items[-1][1]
        step = (end - start) / (len(items) - 1)
        return [(x, start + step * i, w, h) for i, (x, y, w, h) in enumerate(items)]
    out = []
    for x, y, w, h in items:
        if mode == "left":
            x = x0
        elif mode == "right":
            x = x0 + w0 - w
        elif mode == "top":
            y = y0
        elif mode == "bottom":
            y = y0 + h0 - h
        elif mode == "equal":
            w, h = w0, h0
        out.append((x, y, w, h))
    return out


def judge_from_nominal(params: dict[str, Any]) -> dict[str, Any] | None:
    if "nominal" not in params:
        return None
    lo, hi = bilateral_window(
        float(params["nominal"]),
        float(params.get("tol_lower", 0) or 0),
        float(params.get("tol_upper", 0) or 0),
    )
    return {"kind": "range", "min": lo, "max": hi}
