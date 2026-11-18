"""配方（Recipe）：检测项集合的加载/保存与流水线执行。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from typing import Any

import cv2
import numpy as np

from .core import Frame, Rect
from .calibration import Calibration
from .ops import REGISTRY, Item, ItemResult
from .tracking import TrackConfig, Tracker, shift_rect


@dataclass
class Recipe:
    name: str = "untitled"
    station: int = 1
    camera: str = ""
    items: list[Item] = field(default_factory=list)
    calibration: Calibration = field(default_factory=Calibration)
    track: TrackConfig = field(default_factory=TrackConfig)
    detect_area: Rect | None = None            # 检测区域（黄色大框），检测框必须在其内
    locator: dict[str, Any] = field(default_factory=dict)  # 定位模板：{rect, path, score(1~99)}
    rules: list[dict] = field(default_factory=list)        # 规则设定

    # ---------------------------------------------------------------- 序列化
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recipe":
        items = []
        for raw in data.get("items", []):
            items.append(
                Item(
                    id=raw["id"],
                    type=raw["type"],
                    roi=Rect.from_any(raw["roi"]) if raw.get("roi") else None,
                    params=raw.get("params", {}),
                    judge=raw.get("judge"),
                    name=raw.get("name", ""),
                    station=int(raw.get("station", data.get("station", 1))),
                )
            )
        return cls(
            name=data.get("name", "untitled"),
            station=int(data.get("station", 1)),
            camera=data.get("camera", ""),
            items=items,
            track=TrackConfig.from_dict(data.get("track")),
            rules=list(data.get("rules") or []),
            detect_area=(Rect(*data["detect_area"]) if data.get("detect_area") else None),
            locator=dict(data.get("locator") or {}),
            calibration=Calibration.from_dict(data.get("calibration")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "station": self.station,
            "camera": self.camera,
            "calibration": self.calibration.to_dict(),
            "track": self.track.to_dict(),
            "detect_area": list(self.detect_area.as_tuple()) if self.detect_area else None,
            "locator": dict(self.locator or {}),
            "rules": list(self.rules or []),
            "items": [
                {
                    "id": it.id,
                    "name": it.name,
                    "type": it.type,
                    "station": it.station,
                    "roi": it.roi.as_dict() if it.roi else None,
                    "params": it.params,
                    "judge": it.judge,
                }
                for it in self.items
            ],
        }

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # 覆盖前留一份备份，防止误保存把配方冲掉
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = f.read()
                with open(path + ".bak", "w", encoding="utf-8") as f:
                    f.write(old)
            except Exception:                          # noqa: BLE001
                pass
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Recipe":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


@dataclass
class PipelineResult:
    frame_index: int
    results: dict[str, ItemResult]
    ok: bool
    elapsed_ms: float
    track: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame_index,
            "ok": self.ok,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "track": self.track,
            "items": {k: v.as_dict() for k, v in self.results.items()},
        }


class Pipeline:
    """按顺序执行配方里的检测项，后项可引用前项结果。"""

    def __init__(self, recipe: Recipe):
        self.recipe = recipe
        self._tracker: Tracker | None = None
        self._track_sig: str = ""

    # ---------------------------------------------------------------- 规则
    def _box_map(self, items: list) -> dict:
        mp = {}
        n = 0
        for it in items:
            if it.roi is None or it.roi.is_line():
                continue
            n += 1
            mp[n] = it
        return mp

    @staticmethod
    def _pick_xy(res):
        if res is None:
            return None
        blobs = getattr(res, "blobs", None) or []
        if not blobs:
            return None
        b = blobs[0]
        pick = getattr(b, "pick", None)
        if pick:
            return float(pick[0]), float(pick[1])
        return float(getattr(b, "cx", 0.0)), float(getattr(b, "cy", 0.0))

    def _apply_rules(self, items: list, ctx: dict) -> None:
        """按【规则设定】计算 X/Y 距离、参差度、面积并维护基准线。"""
        rules = list(getattr(self.recipe, "rules", []) or [])
        if not rules:
            return
        import math

        from .ops import Item as _Item

        bmap = self._box_map(items)
        for idx, rule in enumerate(rules, start=1):
            kind = str(rule.get("kind", "x"))
            fno = int(rule.get("from_no", 0) or 0)
            tno = int(rule.get("to_no", 0) or 0)
            it_a, it_b = bmap.get(fno), bmap.get(tno)
            rid = f"rule{idx}"
            name = str(rule.get("name") or f"规则{idx}")

            if kind == "baseline":
                if it_a is None or it_b is None or abs(tno - fno) != 1:
                    ctx[rid] = ItemResult(rid, "rule", ok=False,
                                          message="基准线为相邻的两个检测框")
                    continue
                pa = self._pick_xy(ctx.get(it_a.id))
                pb = self._pick_xy(ctx.get(it_b.id))
                if pa is None or pb is None:
                    ctx[rid] = ItemResult(rid, "rule", ok=False,
                                          message="基准线需要两个框都抓到斑块")
                    continue
                bid = str(rule.get("base_id") or f"baseline_{fno}_{tno}")
                rule["base_id"] = bid
                line = Rect.from_line(pa[0], pa[1], pb[0], pb[1])
                found = next((it for it in self.recipe.items if it.id == bid), None)
                if found is None:
                    self.recipe.items.append(_Item(id=bid, type="baseline", name=name,
                                                   roi=line, params={}))
                else:
                    found.roi = line
                ctx[rid] = ItemResult(rid, "rule", ok=True, value=0.0, unit="",
                                      message=f"基准线已生成（框{fno}-框{tno}）")
                continue

            if kind in ("x", "y"):
                if it_a is None or it_b is None:
                    ctx[rid] = ItemResult(rid, "rule", ok=False, message="起始/结束框未选")
                    continue
                pa = self._pick_xy(ctx.get(it_a.id))
                pb = self._pick_xy(ctx.get(it_b.id))
                if pa is None or pb is None:
                    ctx[rid] = ItemResult(rid, "rule", ok=False, message="框内没抓到斑块")
                    continue
                val = (pb[0] - pa[0]) if kind == "x" else (pb[1] - pa[1])
                ctx[rid] = ItemResult(rid, "rule", ok=True, value=float(abs(val)),
                                      unit="px", message=name)
                continue

            if kind == "area":
                total = 0.0
                ok = True
                for no in range(min(fno, tno), max(fno, tno) + 1):
                    it = bmap.get(no)
                    res = ctx.get(it.id) if it is not None else None
                    if res is None:
                        ok = False
                        break
                    total += float(getattr(res, "value", 0) or 0)
                ctx[rid] = ItemResult(rid, "rule", ok=ok, value=total, unit="px2",
                                      message=name if ok else "范围内有框没结果")
                continue

            if kind == "waviness":
                if it_a is None or it_b is None:
                    ctx[rid] = ItemResult(rid, "rule", ok=False, message="起始/结束框未选")
                    continue
                pa = self._pick_xy(ctx.get(it_a.id))
                pb = self._pick_xy(ctx.get(it_b.id))
                if pa is None or pb is None:
                    ctx[rid] = ItemResult(rid, "rule", ok=False, message="框内没抓到斑块")
                    continue
                ang = math.degrees(math.atan2(pb[1] - pa[1], pb[0] - pa[0]))
                ctx[rid] = ItemResult(rid, "rule", ok=True, value=float(abs(ang)),
                                      unit="deg", message=name)
                continue

    def run(self, frame: Frame, annotate: bool = True) -> tuple[PipelineResult, np.ndarray | None]:
        import time
        # 调试/界面用：记录本帧实际使用的 items（含跟踪平移后的 ROI）与偏移
        self.last_items = list(self.recipe.items)
        self.last_track = None

        # 把标定信息挂到帧上，供算子换算 mm（无标定时置 0，避免复用旧帧残留）
        cal = self.recipe.calibration
        if cal.valid:
            frame.meta["mm_per_px"] = cal.mm_per_px or cal.sx
            frame.meta["x_mm_per_px"] = cal.sx
            frame.meta["y_mm_per_px"] = cal.sy
        else:
            frame.meta["mm_per_px"] = 0.0
            frame.meta["x_mm_per_px"] = 0.0
            frame.meta["y_mm_per_px"] = 0.0

        # ---------------- 跟踪框：先定位产品偏移，再平移所有检测框 ----------------
        track_info: dict[str, Any] | None = None
        items = self.recipe.items
        cfg = self.recipe.track
        if cfg.valid:
            sig = repr(cfg.to_dict())
            if self._tracker is None or sig != self._track_sig:
                self._tracker = Tracker(cfg)
                self._track_sig = sig
            tr = self._tracker.locate(frame)
            track_info = tr.as_dict()
            if not tr.ok:
                # 跟踪失败：不允许按旧坐标硬算，直接判 NG
                bad = {"__track__": ItemResult("__track__", "tracking", ok=False,
                                               message=tr.message)}
                res = PipelineResult(frame.index, bad, False, 0.0, track=track_info)
                img = draw_overlays(frame, res) if annotate else None
                return res, img
            if abs(tr.dx) >= 0.5 or abs(tr.dy) >= 0.5:
                apply_to = set(cfg.apply_to or [])
                items = [
                    replace(it, roi=shift_rect(it.roi, tr.dx, tr.dy))
                    if (not apply_to or it.id in apply_to) else it
                    for it in self.recipe.items
                ]

        self.last_items = list(items)      # 本帧实际用的 ROI（含跟踪平移）
        self.last_track = track_info
        t0 = time.perf_counter()
        ctx: dict[str, ItemResult] = {}
        all_ok = True
        for item in items:
            if not item.type:
                continue                     # 空白检测项：还没选检测方式，跳过
            fn = REGISTRY.get(item.type)
            if fn is None:
                ctx[item.id] = ItemResult(item.id, item.type, ok=False, message=f"未知算子 {item.type}")
                all_ok = False
                continue
            try:
                res = fn(frame, item, ctx)
                res.roi = item.roi     # 结果带上框，NG 时画红框用
            except Exception as exc:                       # noqa: BLE001
                res = ItemResult(item.id, item.type, ok=False, message=f"异常: {exc}")
            ctx[item.id] = res
            if res.ok is False:
                all_ok = False

        # ---- 检测区域 + 定位模板校验（现场：模板/检测框都必须在检测区域内）----
        area = getattr(self.recipe, "detect_area", None)
        loc = dict(getattr(self.recipe, "locator", {}) or {})
        guard: list[str] = []
        if area is not None:
            for item in items:
                res = ctx.get(item.id)
                if res is None or item.roi is None:
                    continue
                if not _contains(area, item.roi.as_tuple()):
                    res.ok = False
                    res.message = "检测框超出检测区域"
                    all_ok = False
                    guard.append(f"{item.id} 超出检测区域")
        if loc.get("rect") and loc.get("path"):
            lr = loc["rect"]
            lr = lr if isinstance(lr, (list, tuple)) else lr.as_tuple()
            lx, ly, lw, lh = [int(v) for v in lr]
            ok_loc, msg_loc = True, ""
            if area is not None and not _contains(area, (lx, ly, lw, lh)):
                ok_loc, msg_loc = False, "定位模板超出检测区域 → 定位失败"
            else:
                lx2, ly2, score, ok_loc, msg_loc = _match_template(frame, loc, lx, ly, lw, lh)
                if area is not None and ok_loc and not _contains(area, (lx2, ly2, lw, lh)):
                    ok_loc, msg_loc = False, "匹配位置超出检测区域 → 定位失败"
            if not ok_loc:
                all_ok = False
            ctx["__locator"] = ItemResult(
                item_id="__locator", type="locator", ok=ok_loc,
                value=float(loc.get("score", 0) or 0), unit="",
                message=msg_loc or f"定位 OK（阈值 {loc.get('score')}）")
            guard.append(msg_loc or "定位 OK")

        elapsed = (time.perf_counter() - t0) * 1000.0
        # ---- 规则设定：按框号计算 X/Y/参差度/面积，或生成基准线 ----
        try:
            self._apply_rules(items, ctx)
        except Exception:                                # noqa: BLE001
            pass
        result = PipelineResult(frame.index, ctx, all_ok, elapsed, track=track_info)
        img = draw_overlays(frame, result) if annotate else None
        return result, img


MASK_ALPHA = 0.45      # 阈值屏蔽区红色蒙版的透明度


def _contains(area, rect) -> bool:
    """rect 是否完全落在 area 内（area/rect 都是 x,y,w,h）。"""
    ax, ay, aw, ah = [int(v) for v in area.as_tuple()]
    x, y, w, h = [int(v) for v in rect]
    return (x >= ax and y >= ay and x + w <= ax + aw and y + h <= ay + ah)


_TPL_CACHE: dict[str, "np.ndarray"] = {}


def _load_template(path: str):
    import os

    from .imgio import imread

    if path in _TPL_CACHE:
        return _TPL_CACHE[path]
    if not os.path.isfile(path):
        return None
    img = imread(path)
    if img is None:
        return None
    _TPL_CACHE[path] = img
    return img


def _match_template(frame: Frame, loc: dict, x: int, y: int, w: int, h: int):
    """用保存的模板图在当前帧搜索；返回 (x, y, score, ok, msg)。"""
    tpl = _load_template(str(loc.get("path", "")))
    if tpl is None:
        return x, y, 0.0, False, "找不到定位模板图片，请重新截取模板"
    gray = frame.bgr if frame.bgr.ndim == 2 else cv2.cvtColor(frame.bgr, cv2.COLOR_BGR2GRAY)
    t = tpl if tpl.ndim == 2 else cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    if t.shape[0] > gray.shape[0] or t.shape[1] > gray.shape[1]:
        return x, y, 0.0, False, "定位模板比画面还大，请重新截取"
    res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
    _min, maxv, _minloc, maxloc = cv2.minMaxLoc(res)
    need = float(loc.get("score", 80) or 80) / 100.0
    nx, ny = int(maxloc[0]), int(maxloc[1])
    if float(maxv) < need:
        return x, y, float(maxv), False, f"定位失败：相似度 {maxv * 100:.0f} < 阈值 {need * 100:.0f}"
    return nx, ny, float(maxv), True, ""


def draw_overlays(frame: Frame, result: PipelineResult) -> np.ndarray:
    """把结果画到画面上（供界面/截图使用）。"""
    img = frame.bgr.copy()
    # 阈值屏蔽区：被排除的像素叠成红色（现场调阈值时看"屏蔽掉哪一块"）
    for res in result.results.values():
        mask = getattr(res, "mask", None)
        if mask is None or mask.shape[:2] != img.shape[:2]:
            continue
        sel = mask > 0
        if not sel.any():
            continue
        tint = np.zeros_like(img)
        tint[..., 2] = 255
        img[sel] = (img[sel] * (1.0 - MASK_ALPHA) + tint[sel] * MASK_ALPHA).astype(np.uint8)
    for res in result.results.values():
        for ov in res.overlays:
            kind = ov.get("kind")
            color = ov.get("color", (0, 255, 0))
            if kind == "box":
                x, y, w, h = ov["rect"]
                cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), color, 2)
            elif kind == "line":
                p1 = (int(ov["p1"][0]), int(ov["p1"][1]))
                p2 = (int(ov["p2"][0]), int(ov["p2"][1]))
                cv2.line(img, p1, p2, color, 2)
            elif kind == "cross":
                x, y = int(ov["p"][0]), int(ov["p"][1])
                cv2.line(img, (x - 8, y), (x + 8, y), color, 2)
                cv2.line(img, (x, y - 8), (x, y + 8), color, 2)
            elif kind == "text":
                x, y = int(ov["p"][0]), int(ov["p"][1])
                cv2.putText(img, ov.get("text", ""), (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, color, 2, cv2.LINE_AA)


    # 每个检测项自己的框：OK 绿、NG 红（现场要"哪个框 NG 一眼看到"）
    for res in result.results.values():
        roi = getattr(res, "roi", None)
        if roi is None:
            continue
        if res.ok is None:
            continue          # 没有判定结果的项（未设规则）不画框、不变红
        col = (0, 200, 0) if res.ok else (0, 0, 255)
        thick = 2 if res.ok else 3
        if getattr(roi, "is_line", lambda: False)() and getattr(roi, "line", None):
            x1, y1, x2, y2 = roi.line
            cv2.line(img, (int(x1), int(y1)), (int(x2), int(y2)), col, thick)
        else:
            cv2.rectangle(img, (int(roi.x), int(roi.y)),
                          (int(roi.x + roi.w), int(roi.y + roi.h)), col, thick)

    status = "OK" if result.ok else "NG"
    color = (0, 200, 0) if result.ok else (0, 0, 255)
    cv2.rectangle(img, (0, 0), (img.shape[1] - 1, 40), (30, 30, 30), -1)
    cv2.putText(img, f"{status}  frame={result.frame_index}  {result.elapsed_ms:.1f}ms",
                (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
    # 对象编号：每个检测项在它的第一个标注旁写序号（现场靠编号认框）
    # 放在统计条之后画，避免被顶部状态条盖住
    for idx, res in enumerate(result.results.values(), start=1):
        if not res.overlays or res.item_id.startswith("__"):
            continue
        first = res.overlays[0]
        if first.get("kind") == "box":
            x, y, _w, _h = first["rect"]
            pos = (int(x), max(52, int(y) - 4))
        elif first.get("kind") == "line":
            pos = (int(first["p1"][0]), max(52, int(first["p1"][1]) - 6))
        else:
            continue
        cv2.putText(img, f"{idx:02d}", pos, cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 255), 2, cv2.LINE_AA)

    return img
