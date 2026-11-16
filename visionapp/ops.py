"""检测算子（Item）与结果定义。

每个算子都是一个纯函数：输入 (Frame, ROI 内的图像, params) → 结果。
算子注册到 `REGISTRY`，配方文件里用 `type` 字段引用。

一期算子：
- find_blobs     : 阈值 + 连通域，返回所有斑块（中心/面积/外框）
- match_template : 模板匹配，返回最佳位置与分数
- distance_pp    : 取两个斑块中心算距离（可换算 mm）
- angle_ppp      : 三点夹角
- area_of_blob   : 取第 N 个斑块的面积（做"面积"判定）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import cv2
import numpy as np

from .core import Frame, Rect
from .imgio import imread as imread_safe
from .tuning import (
    POLARITIES, axis_reading, judge_from_nominal, pair_indices, sample_point,
    shielded_count, threshold_block_reason,
)


@dataclass
class Item:
    """一个检测项（配方里的一行）。"""
    id: str
    type: str
    roi: Rect | None = None
    params: dict[str, Any] = field(default_factory=dict)
    judge: dict[str, Any] | None = None
    name: str = ""
    station: int = 1

    def label(self) -> str:
        return self.name or self.id


@dataclass
class Blob:
    cx: float
    cy: float
    area: float
    bbox: tuple[int, int, int, int]   # x, y, w, h（整图坐标）
    pick: tuple[float, float] | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {"cx": self.cx, "cy": self.cy, "area": self.area, "bbox": list(self.bbox)}
        if self.pick is not None:
            d["pick"] = [round(self.pick[0], 2), round(self.pick[1], 2)]
        return d


@dataclass
class ItemResult:
    item_id: str
    type: str
    ok: bool | None = None
    value: float | None = None
    unit: str = "px"
    blobs: list[Blob] = field(default_factory=list)
    match: dict[str, Any] | None = None
    message: str = ""
    overlays: list[dict[str, Any]] = field(default_factory=list)   # 供界面画框
    datum: str = ""
    design: str = ""
    area: float | None = None
    ng_count: int = 0
    blocked: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)
    mask: np.ndarray | None = None      # 阈值屏蔽区，只用于画面，不进 JSON

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "type": self.type,
            "ok": self.ok,
            "value": None if self.value is None else round(float(self.value), 4),
            "unit": self.unit,
            "message": self.message,
            "blobs": [b.as_dict() for b in self.blobs],
            "match": self.match,
            "datum": self.datum,
            "design": self.design,
            "area": None if self.area is None else round(float(self.area), 3),
            "ng_count": int(self.ng_count),
            "blocked": self.blocked,
            "steps": self.steps,
        }


REGISTRY: dict[str, Callable[..., ItemResult]] = {}


def register(name: str) -> Callable[[Callable[..., ItemResult]], Callable[..., ItemResult]]:
    def deco(fn: Callable[..., ItemResult]) -> Callable[..., ItemResult]:
        REGISTRY[name] = fn
        return fn
    return deco


def _crop(frame: Frame, roi: Rect | None) -> tuple[np.ndarray, int, int]:
    img = frame.bgr
    if roi is None:
        return img, 0, 0
    r = roi.clamp(frame.width, frame.height)
    return img[r.y:r.y + r.h, r.x:r.x + r.w], r.x, r.y


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _design_text(params: dict[str, Any]) -> str:
    if "nominal" not in params:
        return ""
    return f"{float(params['nominal']):g} ± {abs(float(params.get('tol_upper', 0) or 0)):g}"


def _apply_judge(judge: dict[str, Any] | None, value: float | None) -> bool | None:
    if not judge or value is None:
        return None
    kind = judge.get("kind", "range")
    if kind == "range":
        lo, hi = judge.get("min"), judge.get("max")
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        return True
    if kind == "max":
        return value <= float(judge["value"])
    if kind == "min":
        return value >= float(judge["value"])
    if kind == "design":
        design = judge.get("design")
        if design is None:
            return None
        design = float(design)
        tu = judge.get("tol_upper")
        tl = judge.get("tol_lower")
        if tu is None and tl is None:
            tol = judge.get("tol")
            if tol is None:
                return None
            tu = tl = float(tol)
        tu = None if tu is None else float(tu)
        tl = None if tl is None else float(tl)
        if judge.get("abs"):
            span = max(abs(tu or 0.0), abs(tl or 0.0))
            if span <= 0:
                return None
            return abs(float(value) - design) <= span + 1e-9
        ok = True
        if tu is not None and value > design + tu + 1e-9:
            ok = False
        if tl is not None and value < design + tl - 1e-9:
            ok = False
        return ok
    return None


# --------------------------------------------------------------------------- #
# 算子实现
# --------------------------------------------------------------------------- #

@register("find_blobs")
def find_blobs(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    """阈值 + 形态学 + 连通域，找斑块。"""
    p = item.params
    img, ox, oy = _crop(frame, item.roi)
    gray = _to_gray(img)
    if p.get("blur", 0):
        k = int(p["blur"])
        gray = cv2.GaussianBlur(gray, (k, k), 0)

    mode = p.get("threshold_mode", "dark")     # dark: 亮斑块；light: 暗斑块
    low_raw = p.get("threshold_low", p.get("threshold", 128))
    thr = int(low_raw if low_raw is not None and low_raw != "" else 128)
    high = p.get("threshold_high", None)
    high_i = None if high is None or high == "" else int(high)
    blocked = threshold_block_reason(thr, high_i)
    if blocked:
        # 上限 255 不能当成「没有上限」继续出斑块。
        return ItemResult(
            item_id=item.id, type=item.type, ok=False, blocked=True,
            message=blocked, value=None, unit="个",
        )
    shielded = 0
    if high_i is not None:
        shielded = shielded_count(gray, thr, high_i)
        mask = ((gray >= thr) & (gray <= high_i)).astype(np.uint8) * 255
    elif mode == "dark":
        _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
    else:
        _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY_INV)

    ksize = int(p.get("morph_kernel", 3))
    if ksize > 0:
        kernel = np.ones((ksize, ksize), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = float(p.get("min_area", 20))
    max_area = float(p.get("max_area", 1e12))
    min_w = float(p.get("min_width", 0) or 0)
    min_h = float(p.get("min_height", 0) or 0)
    min_circ = float(p.get("min_circularity", 0) or 0)
    blobs: list[Blob] = []
    for c in contours:
        a = float(cv2.contourArea(c))
        if a < min_area or a > max_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if w < min_w or h < min_h:
            continue
        if min_circ > 0:
            per = float(cv2.arcLength(c, True))
            if per <= 0:
                continue
            circularity = 4.0 * np.pi * a / (per * per)
            if circularity < min_circ:
                continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cx = m["m10"] / m["m00"] + ox
        cy = m["m01"] / m["m00"] + oy
        bbox = (x + ox, y + oy, w, h)
        polarity = str(p.get("polarity", "") or "")
        if polarity in POLARITIES:
            cx, cy = sample_point(bbox, polarity)
        blobs.append(Blob(cx=float(cx), cy=float(cy), area=a, bbox=bbox))

    zone = str(p.get("pick_zone", "center") or "center").lower()
    if zone not in ("top", "upper", "center", "lower", "bottom"):
        zone = "center"
    for b in blobs:
        x, y, w, h = b.bbox
        if zone == "top":
            py = float(y)
        elif zone == "upper":
            py = y + h * 0.25
        elif zone == "lower":
            py = y + h * 0.75
        elif zone == "bottom":
            py = float(y + h)
        else:
            py = float(y + h / 2.0)
        b.pick = (float(x + w / 2.0), py)

    blobs.sort(key=lambda b: b.cx)
    res = ItemResult(item_id=item.id, type=item.type, blobs=blobs, value=float(len(blobs)), unit="个")
    res.ok = _apply_judge(item.judge, res.value)
    res.area = max((b.area for b in blobs), default=None)
    res.message = f"找到 {len(blobs)} 个斑块"
    if high_i is not None:
        res.message += f"  屏蔽 {shielded}"
    color = (0, 255, 0) if res.ok is not False else (0, 0, 255)
    res.overlays = [{"kind": "box", "rect": b.bbox, "color": color} for b in blobs]
    if str(p.get("show_pick", "off")).lower() in ("on", "1", "true", "yes"):
        for b in blobs:
            if b.pick is not None:
                res.overlays.append({"kind": "cross", "p": b.pick, "color": (255, 0, 255)})
    if str(p.get("show_mask", "off")).lower() in ("on", "1", "true", "yes"):
        excluded = cv2.bitwise_not(mask)
        full = np.zeros((frame.height, frame.width), np.uint8)
        eh, ew = excluded.shape[:2]
        full[oy:oy + eh, ox:ox + ew] = excluded
        res.mask = full
    return res


@register("distance_pp")
def distance_pp(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    """取两个斑块中心，算距离（可选换算 mm）。"""
    p = item.params
    src = ctx.get(p.get("src", ""))
    if src is not None and src.blocked:
        return ItemResult(
            item_id=item.id, type=item.type, ok=False, blocked=True,
            message="定位已被阈值拦截，本次不测量",
        )
    if src is None or len(src.blobs) < 2:
        return ItemResult(item_id=item.id, type=item.type, ok=False, message="依赖的斑块数量不足")

    ia = int(p.get("blob_a", 0))
    ib = int(p.get("blob_b", 1))
    axis = str(p.get("axis", "xy") or "xy")
    each_step = p.get("each_step") in (True, 1, "1", "是", "true", "True")
    use_pick = str(p.get("use_pick", "off")).lower() in ("on", "1", "true", "yes")
    scale = float(p.get("mm_per_px", 0) or 0)
    if scale <= 0:                       # 未显式给比例时，用配方里的标定
        scale = float(frame.meta.get("mm_per_px", 0) or 0)
    scale_x = float(frame.meta.get("mm_per_px_x", 0) or frame.meta.get("x_mm_per_px", 0) or 0)
    scale_y = float(frame.meta.get("mm_per_px_y", 0) or frame.meta.get("y_mm_per_px", 0) or 0)
    if axis in ("x", "y") and scale > 0:
        # 检测项上写了比例时，只作用在所选轴，避免另一轴串进来。
        if axis == "x":
            scale_x = scale_x or scale
        else:
            scale_y = scale_y or scale
    judge = item.judge or judge_from_nominal(p)
    pairs = pair_indices(len(src.blobs), ia, ib, each_step and axis in ("x", "y"))
    steps = []
    note = ""
    for i, j in pairs:
        a, b = src.blobs[i], src.blobs[j]
        ax, ay = (a.pick if use_pick and a.pick else (a.cx, a.cy))
        bx, by = (b.pick if use_pick and b.pick else (b.cx, b.cy))
        if axis in ("x", "y"):
            value_i, note = axis_reading(ax, ay, bx, by, axis, scale_x, scale_y, scale)
            used = scale_x if axis == "x" else scale_y
            if used <= 0:
                used = scale
            unit_i = "mm" if used > 0 else "px"
        else:
            explicit = float(p.get("mm_per_px", 0) or 0)
            dx = float(bx - ax)
            dy = float(by - ay)
            dist_px = float(np.hypot(dx, dy))
            if explicit > 0:
                value_i = dist_px * explicit
                unit_i = "mm"
            else:
                sx = scale_x or scale
                sy = scale_y or sx
                if sx > 0 or sy > 0:
                    value_i = float(np.hypot(dx * (sx or sy), dy * (sy or sx)))
                    unit_i = "mm"
                else:
                    value_i = dist_px
                    unit_i = "px"
            note = "距离"
        ok_i = _apply_judge(judge, value_i)
        steps.append({
            "datum": str(p.get("datum", "") or ""),
            "design": _design_text(p),
            "value": round(float(value_i), 4),
            "unit": unit_i,
            "ok": ok_i,
            "area": round(float(a.area + b.area), 3),
            "pair": [i, j],
        })
    a, b = src.blobs[pairs[0][0]], src.blobs[pairs[0][1]]
    value = float(steps[0]["value"])
    unit = steps[0]["unit"]
    res = ItemResult(item_id=item.id, type=item.type, value=value, unit=unit)
    res.ok = all(s["ok"] is not False for s in steps) if judge else None
    if any(s["ok"] is False for s in steps):
        res.ok = False
    res.datum = str(p.get("datum", "") or p.get("src", ""))
    res.design = _design_text(p)
    res.area = float(a.area + b.area)
    res.steps = steps if each_step and axis in ("x", "y") else []
    ax0, ay0 = (a.pick if use_pick and a.pick else (a.cx, a.cy))
    bx0, by0 = (b.pick if use_pick and b.pick else (b.cx, b.cy))
    res.message = f"{note}  {ax0:.1f},{ay0:.1f} → {bx0:.1f},{by0:.1f}"
    if len(steps) > 1:
        res.message += f"  共 {len(steps)} 段"
    res.overlays = [
        {"kind": "line", "p1": (ax0, ay0), "p2": (bx0, by0), "color": (0, 200, 255)},
        {"kind": "cross", "p": (ax0, ay0), "color": (255, 0, 255)},
        {"kind": "cross", "p": (bx0, by0), "color": (255, 0, 255)},
        {"kind": "text", "p": ((ax0 + bx0) / 2, (ay0 + by0) / 2 - 12),
         "text": f"{value:.3f} {unit}", "color": (0, 255, 255)},
    ]
    return res


@register("match_template")
def match_template(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    """模板匹配（灰度归一化相关）。"""
    p = item.params
    tpl = p.get("template")
    if isinstance(tpl, str):
        if not tpl.strip():
            return ItemResult(item_id=item.id, type=item.type, ok=False, message="模板路径为空")
        tpl = imread_safe(tpl, cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        return ItemResult(item_id=item.id, type=item.type, ok=False, message="模板未加载")
    if tpl.ndim == 3:
        tpl = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)

    img, ox, oy = _crop(frame, item.roi)
    gray = _to_gray(img)
    if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]:
        return ItemResult(item_id=item.id, type=item.type, ok=False, message="模板比搜索区域大")

    method = cv2.TM_CCOEFF_NORMED
    scores = cv2.matchTemplate(gray, tpl, method)
    _, max_val, _, max_loc = cv2.minMaxLoc(scores)
    x, y = max_loc[0] + ox, max_loc[1] + oy
    th, tw = tpl.shape[:2]
    min_score = float(p.get("min_score", 0.8))

    res = ItemResult(item_id=item.id, type=item.type, value=float(max_val), unit="score")
    res.ok = bool(max_val >= min_score)
    res.match = {"x": x, "y": y, "w": tw, "h": th, "score": float(max_val)}
    res.message = f"score={max_val:.3f} @({x},{y})"
    res.overlays = [{"kind": "box", "rect": (x, y, tw, th), "color": (0, 255, 0) if res.ok else (0, 0, 255)}]
    return res


@register("area_of_blob")
def area_of_blob(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    """取某个斑块的面积（"面积"判定）。"""
    p = item.params
    src = ctx.get(p.get("src", ""))
    if src is not None and src.blocked:
        return ItemResult(
            item_id=item.id, type=item.type, ok=False, blocked=True,
            message="定位已被阈值拦截，本次不测量",
        )
    if src is None or not src.blobs:
        return ItemResult(item_id=item.id, type=item.type, ok=False, message="依赖的斑块不存在")
    idx = int(p.get("index", 0))
    if idx >= len(src.blobs):
        return ItemResult(item_id=item.id, type=item.type, ok=False, message=f"斑块索引越界 {idx}")
    area = src.blobs[idx].area
    scale = float(p.get("mm2_per_px2", 0) or 0)
    if scale <= 0:
        sx = float(frame.meta.get("x_mm_per_px", 0) or frame.meta.get("mm_per_px_x", 0)
                   or frame.meta.get("mm_per_px", 0) or 0)
        sy = float(frame.meta.get("y_mm_per_px", 0) or frame.meta.get("mm_per_px_y", 0) or 0) or sx
        scale = sx * (sy or sx)
    value = area * scale if scale > 0 else area
    res = ItemResult(item_id=item.id, type=item.type, value=value, unit="mm2" if scale > 0 else "px2")
    res.ok = _apply_judge(item.judge, value)
    return res


@register("angle_ppp")
def angle_ppp(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    """三点夹角（顶点为 b）。参数：points = [[x1,y1],[x2,y2],[x3,y3]]。"""
    p = item.params
    pts = np.array(p.get("points", []), dtype=np.float64)
    if pts.shape != (3, 2):
        return ItemResult(item_id=item.id, type=item.type, ok=False, message="points 需为 3 点")
    v1 = pts[0] - pts[1]
    v2 = pts[2] - pts[1]
    cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
    ang = float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))
    res = ItemResult(item_id=item.id, type=item.type, value=ang, unit="deg")
    res.ok = _apply_judge(item.judge, ang)
    res.overlays = [{"kind": "line", "p1": tuple(pts[1]), "p2": tuple(pts[0]), "color": (255, 255, 0)},
                    {"kind": "line", "p1": tuple(pts[1]), "p2": tuple(pts[2]), "color": (255, 255, 0)},
                    {"kind": "text", "p": tuple(pts[1] + 10), "text": f"{ang:.2f}°", "color": (255, 255, 0)}]
    return res
