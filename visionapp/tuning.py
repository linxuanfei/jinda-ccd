"""调机用的纯函数（界面和算子共用，避免两处各算一套）。

现场话术与内部命名的对应：
- **抓取位置**（定位位置）用「黑/白 + 区位」描述，例如"黑色底部""白色中下""白色左侧"；
  颜色（找亮还是找暗）由 `threshold_mode` 决定，区位由本模块映射。
- **规则预览**要把「设计 1 ± 0.08」直接翻译成 `[0.920, 1.080]`，现场好对表。
"""

from __future__ import annotations

from typing import Any, Sequence

# 区位：从上到下 + 左右
VALID_ZONES = ("top", "upper", "center", "lower", "bottom", "left", "right")

# 现场中文话术 → 内部区位
_PICK_ZONE_ALIAS = {
    "黑色底部": "bottom", "白色底部": "bottom", "底部": "bottom",
    "黑色中下": "lower", "白色中下": "lower", "中下": "lower",
    "黑色中上": "upper", "白色中上": "upper", "中上": "upper",
    "黑色顶部": "top", "白色顶部": "top", "黑色上": "top", "白色上": "top", "顶部": "top",
    "黑色左侧": "left", "白色左侧": "left", "左侧": "left",
    "黑色右侧": "right", "白色右侧": "right", "右侧": "right",
    "黑色中部": "center", "白色中部": "center", "居中": "center", "中心": "center",
    "白色右侧": "right", "黑色右侧": "right", "白色上": "upper", "白色下": "lower",
}

ZONE_LABELS = {
    "top": "顶部",
    "upper": "中上",
    "center": "中心",
    "lower": "中下",
    "bottom": "底部",
    "left": "左侧",
    "right": "右侧",
}


def resolve_pick_zone(value: Any) -> str:
    """把界面/配方里的区位（可能是现场中文）解析成内部区位名。"""
    text = str(value or "center").strip()
    if text in VALID_ZONES:
        return text
    return _PICK_ZONE_ALIAS.get(text, "center")


def sample_point(bbox: Sequence[float], zone: str, offset: float = 0.0) -> tuple[float, float]:
    """按区位在斑块外框上取测量点。

    bbox = (x, y, w, h)；offset 用于「黑色底部」这类"再往下偏一点"的现场习惯。
    """
    x, y, w, h = (float(v) for v in bbox)
    z = resolve_pick_zone(zone)
    cx, cy = x + w / 2.0, y + h / 2.0
    if z == "top":
        return cx, y + offset
    if z == "upper":
        return cx, y + 0.25 * h
    if z == "lower":
        return cx, y + 0.75 * h
    if z == "bottom":
        return cx, y + h + offset
    if z == "left":
        return x - offset, cy
    if z == "right":
        return x + w + offset, cy
    return cx, cy


def threshold_warnings(low: Any, high: Any) -> str:
    """阈值 0 / 255 的提示（In-Vision 里这两个值会抓取错误）。

    我们现在的策略是**限幅到 1~254**（不中断检测），但界面要告诉人"你填的值被夹了"。
    """
    msgs: list[str] = []
    for name, v in (("下阈值", low), ("上阈值", high)):
        if v is None or v == "":
            continue
        try:
            iv = int(round(float(v)))
        except (TypeError, ValueError):
            continue
        if iv <= 0 or iv >= 255:
            msgs.append(f"{name} {iv} 已按 {'1' if iv <= 0 else '254'} 处理（0/255 会抓取错误）")
    return "；".join(msgs)


def bilateral_window(nominal: float, tol_lower: float, tol_upper: float) -> tuple[float, float]:
    """设计值 + 双边容差 → 合格区间。

    正数容差表示幅度：1 与 ±0.08 → [0.920, 1.080]；
    若有负数容差，则按带符号偏差处理：nominal+tol_lower .. nominal+tol_upper。
    """
    nominal = float(nominal)
    lo_t = float(tol_lower)
    hi_t = float(tol_upper)
    if lo_t < 0 or hi_t < 0:
        return nominal + lo_t, nominal + hi_t
    return nominal - abs(lo_t), nominal + abs(hi_t)


def rule_preview_text(judge: dict[str, Any] | None) -> str:
    """判定规则的一行预览（给现场对表用）。"""
    if not judge:
        return "规则：（未设置）"
    kind = judge.get("kind", "range")
    if kind == "design":
        design = judge.get("design")
        if design is None:
            return "规则：（设计值为空）"
        design = float(design)
        tu = judge.get("tol_upper")
        tl = judge.get("tol_lower")
        if judge.get("abs"):
            span = max(abs(float(tu or 0)), abs(float(tl or 0)))
            return f"规则：|实测 − {design:g}| ≤ {span:g}"
        span = max(abs(float(tu or 0)), abs(float(tl or 0)))
        lo, hi = bilateral_window(design, float(tl or 0), float(tu or 0))
        return f"规则：设计 {design:g} ± {span:g} → [{lo:.3f}, {hi:.3f}]"
    if kind == "range":
        lo, hi = judge.get("min"), judge.get("max")
        left = "-∞" if lo is None else f"{float(lo):g}"
        right = "+∞" if hi is None else f"{float(hi):g}"
        return f"规则：区间 [{left}, {right}]"
    if kind == "max":
        return f"规则：≤ {float(judge.get('value', 0)):g}"
    if kind == "min":
        return f"规则：≥ {float(judge.get('value', 0)):g}"
    return "规则：（未知类型）"


__all__ = [
    "VALID_ZONES", "ZONE_LABELS", "resolve_pick_zone", "sample_point",
    "threshold_warnings", "bilateral_window", "rule_preview_text",
]
