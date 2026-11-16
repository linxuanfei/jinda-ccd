"""跟踪框：模板匹配补偿放料偏差。

参考图只留在内存里。taught_xy / base_rois 一类运行时状态不进配方。
相似度低于阈值时 ok=False，调用方必须判 NG，不能按旧框继续测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .core import Frame, Rect

# 这些键是运行时状态，读进来就丢掉，保存时也不写。
_RUNTIME_KEYS = ("taught_xy", "base_rois", "ref_gray", "last_dx", "last_dy")


@dataclass
class TrackConfig:
    """跟踪框配置（存在配方里的只有这些）。"""
    roi: Rect | None = None
    min_score: float = 0.7
    search_margin: int = 60
    apply_to: list[str] = field(default_factory=list)
    enabled: bool = True
    relearn: bool = False

    @property
    def valid(self) -> bool:
        return bool(self.enabled and self.roi is not None and self.roi.w >= 8 and self.roi.h >= 8)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "roi": list(self.roi.as_tuple()) if self.roi else None,
            "min_score": float(self.min_score),
            "search_margin": int(self.search_margin),
            "apply_to": list(self.apply_to),
            "relearn": bool(self.relearn),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TrackConfig":
        data = dict(data or {})
        for key in _RUNTIME_KEYS:
            data.pop(key, None)
        roi = None
        if data.get("roi"):
            try:
                roi = Rect.from_any(data["roi"])
            except Exception:                          # noqa: BLE001
                roi = None
        return cls(
            roi=roi,
            min_score=float(data.get("min_score", 0.7) or 0.7),
            search_margin=int(data.get("search_margin", 60) or 60),
            apply_to=list(data.get("apply_to") or []),
            enabled=bool(data.get("enabled", True)),
            relearn=bool(data.get("relearn", False)),
        )


@dataclass
class TrackResult:
    ok: bool = True
    dx: float = 0.0
    dy: float = 0.0
    score: float = 0.0
    message: str = ""
    learned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "dx": round(self.dx, 2), "dy": round(self.dy, 2),
                "score": round(self.score, 4), "message": self.message}


class Tracker:
    """跟踪框定位器。参考图只放在这个对象里，不写进配方。"""

    def __init__(self, cfg: TrackConfig):
        self.cfg = cfg
        self._ref_gray: np.ndarray | None = None

    def reset(self) -> None:
        self._ref_gray = None

    def _learn(self, gray: np.ndarray) -> bool:
        roi = self.cfg.roi
        if roi is None:
            return False
        r = roi.clamp(gray.shape[1], gray.shape[0])
        patch = gray[r.y:r.y + r.h, r.x:r.x + r.w]
        if patch.size == 0 or patch.shape[0] < 8 or patch.shape[1] < 8:
            return False
        self._ref_gray = patch.copy()
        return True

    def locate(self, frame: Frame) -> TrackResult:
        if not self.cfg.valid:
            return TrackResult(ok=True, message="未启用跟踪")
        gray = frame.bgr if frame.bgr.ndim == 2 else cv2.cvtColor(frame.bgr, cv2.COLOR_BGR2GRAY)
        learned = False
        if self._ref_gray is None:
            if not self._learn(gray):
                return TrackResult(ok=False, message="跟踪框太小，无法学习参考图")
            learned = True

        roi = self.cfg.roi
        assert roi is not None
        m = max(0, int(self.cfg.search_margin))
        sx = max(0, roi.x - m)
        sy = max(0, roi.y - m)
        ex = min(gray.shape[1], roi.x + roi.w + m)
        ey = min(gray.shape[0], roi.y + roi.h + m)
        search = gray[sy:ey, sx:ex]
        ref = self._ref_gray
        if search.shape[0] < ref.shape[0] or search.shape[1] < ref.shape[1]:
            return TrackResult(ok=False, message="搜索区小于跟踪框（检查跟踪框是否越界）")

        res = cv2.matchTemplate(search, ref, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        dx = float(max_loc[0] + sx - roi.x)
        dy = float(max_loc[1] + sy - roi.y)
        score = float(max_val)

        if score < self.cfg.min_score:
            msg = f"跟踪失败：相似度 {score:.3f} < {self.cfg.min_score:.2f}"
            if self.cfg.relearn and self._learn(gray):
                return TrackResult(ok=True, dx=0.0, dy=0.0, score=score,
                                   message=msg + "（已重新学习参考图）", learned=True)
            return TrackResult(ok=False, dx=0.0, dy=0.0, score=score, message=msg)

        return TrackResult(ok=True, dx=dx, dy=dy, score=score,
                           message=f"偏移 {dx:+.1f},{dy:+.1f}px 相似度 {score:.3f}",
                           learned=learned)


def shift_rect(rect: Rect | None, dx: float, dy: float) -> Rect | None:
    """把 ROI 平移 (dx, dy)。线形 ROI 两端一起平移。"""
    if rect is None:
        return None
    line = rect.line
    if line is not None and rect.is_line():
        return Rect(
            int(round(rect.x + dx)), int(round(rect.y + dy)), rect.w, rect.h,
            shape=rect.shape,
            line=(int(round(line[0] + dx)), int(round(line[1] + dy)),
                  int(round(line[2] + dx)), int(round(line[3] + dy))),
        )
    return Rect(int(round(rect.x + dx)), int(round(rect.y + dy)), rect.w, rect.h)


def coerce_track(value: Any) -> TrackConfig:
    """配方上的 track 可能是 TrackConfig，也可能是旧 dict。运行时键一律丢掉。"""
    if isinstance(value, TrackConfig):
        return value
    if isinstance(value, dict):
        return TrackConfig.from_dict(value)
    return TrackConfig()


__all__ = ["TrackConfig", "TrackResult", "Tracker", "shift_rect", "coerce_track"]
