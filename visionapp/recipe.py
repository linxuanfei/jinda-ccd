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
from .tracking import Tracker, coerce_track, shift_rect


@dataclass
class Recipe:
    name: str = "untitled"
    station: int = 1
    camera: str = ""
    items: list[Item] = field(default_factory=list)
    calibration: Calibration = field(default_factory=Calibration)
    track: Any = field(default_factory=lambda: coerce_track(None))

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
            calibration=Calibration.from_dict(data.get("calibration")),
            track=coerce_track(data.get("track")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "station": self.station,
            "camera": self.camera,
            "calibration": self.calibration.to_dict(),
            "track": coerce_track(self.track).to_dict(),
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
        self.ng_counts: dict[str, int] = {}
        self._tracker: Tracker | None = None
        self._track_sig: str = ""
        self.last_items: list[Item] = []
        self.last_track: dict[str, Any] | None = None

    def run(self, frame: Frame, annotate: bool = True) -> tuple[PipelineResult, np.ndarray | None]:
        import time

        self.last_items = list(self.recipe.items)
        self.last_track = None

        # 把标定信息挂到帧上，供算子换算 mm（无标定时置 0，避免复用旧帧残留）
        cal = self.recipe.calibration
        frame.meta["mm_per_px"] = cal.mm_per_px if cal.mm_per_px > 0 else 0.0
        frame.meta["mm_per_px_x"] = cal.mm_per_px_x if cal.mm_per_px_x > 0 else frame.meta["mm_per_px"]
        frame.meta["mm_per_px_y"] = cal.mm_per_px_y if cal.mm_per_px_y > 0 else frame.meta["mm_per_px"]
        frame.meta["x_mm_per_px"] = frame.meta["mm_per_px_x"]
        frame.meta["y_mm_per_px"] = frame.meta["mm_per_px_y"]

        cfg = coerce_track(self.recipe.track)
        self.recipe.track = cfg
        track_info: dict[str, Any] | None = None
        items = self.recipe.items
        if cfg.valid:
            sig = repr(cfg.to_dict())
            if self._tracker is None or sig != self._track_sig:
                self._tracker = Tracker(cfg)
                self._track_sig = sig
            tr = self._tracker.locate(frame)
            track_info = tr.as_dict()
            frame.meta["track_note"] = tr.message
            self.last_track = track_info
            if not tr.ok:
                bad = ItemResult("__track__", "tracking", ok=False, message=tr.message)
                bad.ng_count = self.ng_counts.get("__track__", 0) + 1
                self.ng_counts["__track__"] = bad.ng_count
                res = PipelineResult(frame.index, {"__track__": bad}, False, 0.0, track=track_info)
                img = draw_overlays(frame, res) if annotate else None
                return res, img
            if abs(tr.dx) >= 0.5 or abs(tr.dy) >= 0.5:
                apply_to = set(cfg.apply_to or [])
                items = [
                    replace(it, roi=shift_rect(it.roi, tr.dx, tr.dy))
                    if (not apply_to or it.id in apply_to) else it
                    for it in self.recipe.items
                ]

        self.last_items = list(items)
        t0 = time.perf_counter()
        ctx: dict[str, ItemResult] = {}
        all_ok = True
        for item in items:
            fn = REGISTRY.get(item.type)
            if fn is None:
                ctx[item.id] = ItemResult(item.id, item.type, ok=False, message=f"未知算子 {item.type}")
                all_ok = False
                continue
            try:
                res = fn(frame, item, ctx)
            except Exception as exc:                       # noqa: BLE001
                res = ItemResult(item.id, item.type, ok=False, message=f"异常: {exc}")
            if res.ok is False:
                self.ng_counts[item.id] = self.ng_counts.get(item.id, 0) + 1
                all_ok = False
            res.ng_count = self.ng_counts.get(item.id, 0)
            ctx[item.id] = res

        elapsed = (time.perf_counter() - t0) * 1000.0
        result = PipelineResult(frame.index, ctx, all_ok, elapsed, track=track_info)
        img = draw_overlays(frame, result) if annotate else None
        return result, img


MASK_ALPHA = 0.45


def draw_overlays(frame: Frame, result: PipelineResult) -> np.ndarray:
    """把结果画到画面上（供界面/截图使用）。"""
    img = frame.bgr.copy()
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

    status = "OK" if result.ok else "NG"
    color = (0, 200, 0) if result.ok else (0, 0, 255)
    cv2.rectangle(img, (0, 0), (img.shape[1] - 1, 64), (30, 30, 30), -1)
    cv2.putText(img, f"{status}  frame={result.frame_index}  {result.elapsed_ms:.1f}ms",
                (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    ids = " ".join(result.results.keys())
    cv2.putText(img, f"对象 {ids}   复检品 0",
                (12, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
    return img


def stamp_totals(img: np.ndarray | None, ok_n: int, ng_n: int) -> np.ndarray | None:
    """在画面底部写本机位累计：不良品 / 良品 / 良品率 / 复检品。"""
    if img is None:
        return None
    total = int(ok_n) + int(ng_n)
    rate = (ok_n / total * 100.0) if total else 0.0
    h, w = img.shape[:2]
    y0 = max(0, h - 28)
    cv2.rectangle(img, (0, y0), (w - 1, h - 1), (30, 30, 30), -1)
    text = f"不良品 {ng_n}   良品 {ok_n}   良品率 {rate:.1f}%   复检品 0"
    cv2.putText(img, text, (12, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1, cv2.LINE_AA)
    return img
