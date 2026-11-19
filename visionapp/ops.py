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
from .tuning import resolve_pick_zone, sample_point
from .imgio import imread as imread_safe


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
    pick: tuple[float, float] | None = None    # 抓取位置取出来的测量点（整图坐标）

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
    overlays: list[dict[str, Any]] = field(default_factory=list)
    roi: Any = None                  # 这个检测项的框（用于 NG 时画红框）   # 供界面画框
    mask: np.ndarray | None = None      # 阈值屏蔽区（0/255），仅用于画面叠加，不进 JSON

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


def _clamp_threshold(v: Any, default: int = 128) -> int:
    """阈值限幅到 1~254。

    现场规则（In-Vision）：「阈值最小 0、最大 255，**最大可用阈值 254**；
    调到 0 或 255 会抓取错误」。这里统一钳到 1~254，并保持整数。
    """
    try:
        t = int(round(float(v)))
    except (TypeError, ValueError):
        t = default
    return max(1, min(254, t))


def _apply_judge(judge: dict[str, Any] | None, value: float | None) -> bool | None:
    """判定。支持四种口径：

    - ``range``  : min ~ max 区间
    - ``max``    : 上限（value ≤ value）
    - ``min``    : 下限（value ≥ value）
    - ``design`` : **设计值 ± 容差**（对齐现场 In-Vision 的用法）
      ``{"kind": "design", "design": 1.0, "tol_upper": 0.08, "tol_lower": -0.08, "abs": false}``
      容差是**相对设计值的偏差**：合格区间 = [design+tol_lower, design+tol_upper]。
      ``abs: true``（界面"结果取绝对值"）时改为 |value-design| ≤ max(|tol_upper|, |tol_lower|)。
      也兼容对称写法 ``"tol": 0.08``（等价于上下各 0.08）。
    """
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

def _cells_find(frame: Frame, item: Item, p: dict, cells: list) -> ItemResult:
    """按用户保存的小框逐个检测（每个小框独立判定，可任意个数）。"""
    from .core import Rect as _Rect

    min_area = float(p.get("min_area", 20))
    max_area = float(p.get("max_area", 1e12))
    overlays: list[dict] = []
    bad: list[str] = []
    areas: list[float] = []
    total = 0
    for i, c in enumerate(cells):
        x, y, w, h = [int(v) for v in c]
        if w < 2 or h < 2:
            continue
        sub = Item(id=item.id, type=item.type, roi=_Rect(x, y, w, h),
                   params={**p, "cells": None, "grid_cols": 1, "grid_rows": 1,
                           "inset_l": 0, "inset_r": 0, "inset_t": 0, "inset_b": 0})
        r = find_blobs(frame, sub, {})
        cnt = len(r.blobs)
        total += cnt
        good = cnt >= 1 and all(min_area <= b.area <= max_area for b in r.blobs)
        if cnt:
            areas.append(min(b.area for b in r.blobs))
        if not good:
            bad.append(f"{i + 1}号框")
        overlays.append({"kind": "box", "rect": (x, y, w, h),
                         "color": (0, 200, 0) if good else (0, 0, 255)})
    res = ItemResult(item_id=item.id, type=item.type,
                     value=(min(areas) if areas else 0.0), unit="px²", ok=not bad)
    res.overlays = overlays
    res.message = (f"小框 {len(cells)} 个：{len(cells) - len(bad)}/{len(cells)} 合格，共 {total} 个斑块"
                   + (f"；不合格：{','.join(bad[:6])}" if bad else ""))
    res.roi = None
    return res


def _grid_find(frame: Frame, item: Item, p: dict, x0: int, y0: int,
               cw: float, ch: float, cols: int, rows: int) -> ItemResult:
    """把大框切成 cols×rows 格，每格独立找斑块；有任何一格不合格就判 NG。"""
    from .core import Rect as _Rect

    min_area = float(p.get("min_area", 20))
    max_area = float(p.get("max_area", 1e12))
    overlays: list[dict] = []
    fail_cells: list[str] = []
    areas: list[float] = []
    total = 0
    for gy in range(rows):
        for gx in range(cols):
            cell = _Rect(int(x0 + gx * cw), int(y0 + gy * ch), max(1, int(cw)), max(1, int(ch)))
            sub = Item(id=item.id, type=item.type, roi=cell, params={**p, "grid_cols": 1, "grid_rows": 1,
                                                                     "inset_l": 0, "inset_r": 0,
                                                                     "inset_t": 0, "inset_b": 0})
            r = find_blobs(frame, sub, {})
            cnt = len(r.blobs)
            total += cnt
            good = cnt >= 1 and all(min_area <= b.area <= max_area for b in r.blobs)
            if cnt:
                areas.append(min(b.area for b in r.blobs))
            if not good:
                fail_cells.append(f"{gy * cols + gx + 1}格")
            overlays.append({
                "kind": "box",
                "rect": (cell.x, cell.y, cell.w, cell.h),
                "color": (0, 200, 0) if good else (0, 0, 255),
            })
    ok = not fail_cells
    res = ItemResult(item_id=item.id, type=item.type, value=(min(areas) if areas else 0.0),
                     unit="px²", ok=ok)
    res.overlays = overlays
    res.message = (f"{rows}行×{cols}列：{rows * cols - len(fail_cells)}/{rows * cols} 格合格，共 {total} 个斑块"
                   + (f"；不合格：{','.join(fail_cells[:6])}" if fail_cells else ""))
    res.roi = None          # 用逐格框代替大框
    return res


@register("find_blobs")
def find_blobs(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    """阈值 + 形态学 + 连通域，找斑块。

    阈值两种用法（对齐现场）：
    - **区间**（推荐）：``threshold_low`` ~ ``threshold_high``（如 111~255），只取区间内的像素；
    - 单阈值（兼容旧配方）：只给 ``threshold``，按 ``threshold_mode`` 取亮/暗。
    阈值一律限幅到 **1~254**（0/255 在 In-Vision 里会抓取错误）。
    过滤：``min_area`` / ``max_area`` / ``min_width`` / ``min_height`` / ``min_circularity``。
    """
    p = item.params
    # 大框分割：把 ROI 先按"抓取范围"内缩，再切成 cols×rows 个小框分别检测
    # 有手动画/改过的小框就按小框来（一个框里可以随便分几个，且能单独挪）
    cells = p.get("cells")
    if cells:
        return _cells_find(frame, item, p, cells)
    cols = max(1, int(p.get("grid_cols", 1) or 1))
    rows = max(1, int(p.get("grid_rows", 1) or 1))
    _roi = item.roi
    if _roi is not None and (cols > 1 or rows > 1 or any(
            float(p.get(k, 0) or 0) for k in ("inset_l", "inset_r", "inset_t", "inset_b"))):
        from .core import Rect as _Rect

        l = int(float(p.get("inset_l", 0) or 0))
        r = int(float(p.get("inset_r", 0) or 0))
        t = int(float(p.get("inset_t", 0) or 0))
        b = int(float(p.get("inset_b", 0) or 0))
        x0 = int(_roi.x) + l
        y0 = int(_roi.y) + t
        x1 = int(_roi.x + _roi.w) - r
        y1 = int(_roi.y + _roi.h) - b
        if x1 - x0 < 2 or y1 - y0 < 2:
            x0, y0 = int(_roi.x), int(_roi.y)
            x1, y1 = int(_roi.x + _roi.w), int(_roi.y + _roi.h)
        cw = (x1 - x0) / float(cols)
        ch = (y1 - y0) / float(rows)
        item.roi = _Rect(0, 0, 1, 1)          # 占位，下面逐格处理
        return _grid_find(frame, item, p, x0, y0, cw, ch, cols, rows)

    img, ox, oy = _crop(frame, item.roi)
    gray = _to_gray(img)
    if p.get("blur", 0):
        k = int(p["blur"])
        gray = cv2.GaussianBlur(gray, (k, k), 0)

    mode = p.get("threshold_mode", "dark")     # dark: 亮斑块；light: 暗斑块
    lo_raw, hi_raw = p.get("threshold_low"), p.get("threshold_high")
    if lo_raw is None and hi_raw is None:
        thr = _clamp_threshold(p.get("threshold", 128))
        if mode == "dark":
            _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        else:
            _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY_INV)
    else:
        lo = _clamp_threshold(lo_raw if lo_raw is not None else 1, default=1)
        # 上阈值 255 表示"一直到最亮"（纯白 255 的斑块也要能抓），不能钳成 254
        hi = 255 if (hi_raw is None or float(hi_raw) >= 255) else _clamp_threshold(hi_raw, default=254)
        if lo > hi:
            lo, hi = hi, lo
        mask = cv2.inRange(gray, lo, hi)
        if mode == "light":                    # 暗斑块 = 区间之外
            mask = cv2.bitwise_not(mask)

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
        blobs.append(Blob(cx=cx, cy=cy, area=a, bbox=(x + ox, y + oy, w, h)))

    # ---- 抓取位置：按"黑白×区位"从斑块边界取测量点 ----
    zone = p.get("pick_zone", "center")
    # "白色中上/黑色中下" 这种说法自带黑白语义：白色=找亮块，黑色=找暗块
    _z = str(zone)
    if _z.startswith("白色"):
        p["threshold_mode"] = "light"
    elif _z.startswith("黑色"):
        p["threshold_mode"] = "dark"
    pick_offset = float(p.get("pick_offset", 0) or 0)
    for b in blobs:
        b.pick = sample_point(b.bbox, zone, pick_offset)

    # 框内一个轮廓都没有（阈值把像素全滤掉了）：退回"按抓取方向找极值点"，
    # 保证规则还能出数值；若轮廓存在、只是被圆度/面积条件筛掉，则保持 0 个不动。
    if not blobs and not contours:
        gray_roi = gray if gray is not None else None
        if gray_roi is not None and gray_roi.size:
            if str(zone).startswith("白色"):
                iy, ix = np.unravel_index(int(np.argmax(gray_roi)), gray_roi.shape)
            else:
                iy, ix = np.unravel_index(int(np.argmin(gray_roi)), gray_roi.shape)
            px, py = float(ix + ox), float(iy + oy)
            blob = Blob(cx=px, cy=py, area=1.0, bbox=(int(px), int(py), 1, 1))
            blob.pick = (px, py)
            blobs.append(blob)
            _fallback = True
        else:
            _fallback = False
    else:
        _fallback = False

    blobs.sort(key=lambda b: b.cx)
    res = ItemResult(item_id=item.id, type=item.type, blobs=blobs, value=float(len(blobs)), unit="个")
    res.ok = _apply_judge(item.judge, res.value)
    _is_fb = _fallback or (len(blobs) == 1
                           and float(getattr(blobs[0], "area", 0) or 0) <= 1.0)
    res.message = (f"没找到斑块，按{zone}取极值点" if _is_fb
                   else f"找到 {len(blobs)} 个斑块")
    color = (0, 255, 0) if res.ok is not False else (0, 0, 255)
    res.overlays = [{"kind": "box", "rect": b.bbox, "color": color} for b in blobs]
    # 画出抓取点（现场调"抓取位置"时靠它看对不对）
    if str(p.get("show_pick", "off")).lower() in ("on", "1", "true", "yes"):
        for b in blobs:
            if b.pick is not None:
                res.overlays.append({"kind": "cross", "p": b.pick, "color": (255, 0, 255)})
    # ---- 阈值屏蔽区：把被排除的像素做成红色蒙版（现场调阈值时用）----
    if str(p.get("show_mask", "off")).lower() in ("on", "1", "true", "yes"):
        excluded = cv2.bitwise_not(mask)
        res.mask = excluded
    return res


@register("distance_pp")
def distance_pp(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    """取两个斑块中心，算距离（可选换算 mm）。"""
    p = item.params

    def _first_point(ref_id: str):
        """取某个检测项第一个斑块的测量点（优先抓取位置）。"""
        r = ctx.get(str(ref_id or ""))
        if r is None or not r.blobs:
            return None, None
        bl = r.blobs[0]
        use_pick = str(p.get("use_pick", "off")).lower() in ("on", "1", "true", "yes")
        if use_pick and bl.pick:
            return float(bl.pick[0]), float(bl.pick[1]), r
        return float(bl.cx), float(bl.cy), r

    from_item = str(p.get("from_item", "") or "")
    to_item = str(p.get("to_item", "") or "")
    ax = ay = bx = by = None
    pa = pb = None
    if from_item and to_item:
        ax, ay, ra = _first_point(from_item)
        bx, by, rb = _first_point(to_item)
        pa, pb = ra, rb
        if ax is None or bx is None:
            return ItemResult(item_id=item.id, type=item.type, ok=False,
                              message=f"所选检测框没有可用斑块（{from_item} / {to_item}）")

    src = ctx.get(p.get("src", ""))
    if ax is None:
        if src is None or len(src.blobs) < 2:
            return ItemResult(item_id=item.id, type=item.type, ok=False,
                              message="依赖的斑块数量不足（请选择起始/终止检测框）")
        ia = int(p.get("blob_a", 0))
        ib = int(p.get("blob_b", 1))
        a, b = src.blobs[ia], src.blobs[ib]
    # 用抓取位置取出的测量点（现场"抓取位置"就是决定量哪里）；没有就退回中心
    use_pick = str(p.get("use_pick", "off")).lower() in ("on", "1", "true", "yes")
    if ax is None:
        ax, ay = (a.pick if use_pick and a.pick else (a.cx, a.cy))
        bx, by = (b.pick if use_pick and b.pick else (b.cx, b.cy))
    dx = float(bx - ax)
    dy = float(by - ay)
    dist_px = float(np.hypot(dx, dy))
    # 显式 mm_per_px 优先（X/Y 同比例）；否则用配方里的 X/Y 分别标定
    scale = float(p.get("mm_per_px", 0) or 0)
    if scale > 0:
        sx = sy = scale
    else:
        sx = float(frame.meta.get("x_mm_per_px", 0) or frame.meta.get("mm_per_px", 0) or 0)
        sy = float(frame.meta.get("y_mm_per_px", 0) or 0) or sx
    axis = str(p.get("axis", "dist") or "dist")
    if axis == "x":
        dist_use, name = abs(dx), "X方向距离"
    elif axis == "y":
        dist_use, name = abs(dy), "Y方向距离"
    else:
        dist_use, name = dist_px, "直线距离"
    if sx > 0 or sy > 0:
        if axis == "x":
            value = abs(dx) * (sx or sy)
        elif axis == "y":
            value = abs(dy) * (sy or sx)
        else:
            value = float(np.hypot(dx * (sx or sy), dy * (sy or sx)))
        unit = "mm"
    else:
        value = dist_use
        unit = "px"

    res = ItemResult(item_id=item.id, type=item.type, value=value, unit=unit)
    res.ok = _apply_judge(item.judge, value)
    res.message = f"{name}  {ax:.1f},{ay:.1f} → {bx:.1f},{by:.1f}"
    res.overlays = [
        {"kind": "line", "p1": (ax, ay), "p2": (bx, by), "color": (0, 200, 255)},
        {"kind": "cross", "p": (ax, ay), "color": (255, 0, 255)},
        {"kind": "cross", "p": (bx, by), "color": (255, 0, 255)},
        {"kind": "text", "p": ((ax + bx) / 2, (ay + by) / 2 - 12),
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
    if src is None or not src.blobs:
        return ItemResult(item_id=item.id, type=item.type, ok=False, message="依赖的斑块不存在")
    idx = int(p.get("index", 0))
    if idx >= len(src.blobs):
        return ItemResult(item_id=item.id, type=item.type, ok=False, message=f"斑块索引越界 {idx}")
    area = src.blobs[idx].area
    scale = float(p.get("mm2_per_px2", 0) or 0)
    if scale <= 0:
        sx = float(frame.meta.get("x_mm_per_px", 0) or frame.meta.get("mm_per_px", 0) or 0)
        sy = float(frame.meta.get("y_mm_per_px", 0) or 0) or sx
        scale = sx * (sy or sx)
    value = area * scale if scale > 0 else area
    res = ItemResult(item_id=item.id, type=item.type, value=value, unit="mm2" if scale > 0 else "px2")
    res.ok = _apply_judge(item.judge, value)
    return res


@register("angle_ppp")
def angle_ppp(frame: Frame, item: Item, ctx: dict[str, ItemResult]) -> ItemResult:
    p = item.params
    ids = [str(p.get(k, "") or "") for k in ("p1_item", "p2_item", "p3_item")]
    if all(ids):
        pts = []
        for rid in ids:
            r = ctx.get(rid)
            if r is None or not r.blobs:
                return ItemResult(item_id=item.id, type=item.type, ok=False,
                                  message=f"检测框 {rid} 没有可用斑点")
            b = r.blobs[0]
            use_pick = str(p.get("use_pick", "off")).lower() in ("on", "1", "true", "yes")
            pts.append(b.pick if (use_pick and b.pick) else (b.cx, b.cy))
        (x1, y1), (x2, y2), (x3, y3) = pts
        v1 = (x2 - x1, y2 - y1)
        v2 = (x3 - x2, y3 - y2)
        import math as _m
        ang = abs(_m.degrees(_m.atan2(v1[0] * v2[1] - v1[1] * v2[0],
                                    v1[0] * v2[0] + v1[1] * v2[1])))
        res = ItemResult(item_id=item.id, type=item.type, value=ang, unit="°")
        res.ok = _apply_judge(item.judge, ang)
        res.message = f"三点角度 {ang:.2f}°"
        res.overlays = [
            {"kind": "line", "p1": (x1, y1), "p2": (x2, y2), "color": (0, 200, 255)},
            {"kind": "line", "p1": (x2, y2), "p2": (x3, y3), "color": (0, 200, 255)},
            {"kind": "text", "p": (x2, y2 - 14), "text": f"{ang:.2f}°", "color": (0, 255, 255)},
        ]
        return res
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
