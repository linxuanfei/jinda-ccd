"""跟踪框（Tracking）：补偿产品放料位置偏差。

现场原话：「机台运行的时候不会百分百把产品都放在同一个位置，增加跟踪框是为了保证检测框都在正确范围」。

做法（不改任何检测算子）：
1. 第一帧：把跟踪框内的图像存成**参考图**；
2. 之后每帧：在跟踪框**四周扩一圈**的搜索区里做模板匹配，得到 (dx, dy)；
3. Pipeline 把**所有检测框 ROI 平移 (dx, dy)** 后再跑原有算子；
4. 匹配分数低于 `min_score` → 视为跟踪失败，返回 ok=False，由 Pipeline 直接判 NG（不允许按旧坐标硬算）。

参考图默认只在第一帧取一次（`relearn=False`），也可以设置 `relearn=True` 在跟踪失败时用当前帧重新学习。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .core import Frame, Rect


@dataclass
class TrackConfig:
    """跟踪框配置（存在配方里）。"""
    roi: Rect | None = None
    min_score: float = 0.7          # 低于这个相似度视为跟踪失败
    search_margin: int = 60         # 搜索区在跟踪框外扩的像素
    apply_to: list[str] = field(default_factory=list)   # 为空 = 全部检测框
    enabled: bool = True
    relearn: bool = False           # 失败时用当前帧重新学参考图

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
        data = data or {}
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
    learned: bool = False           # 本帧是否刚学了参考图

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "dx": round(self.dx, 2), "dy": round(self.dy, 2),
                "score": round(self.score, 4), "message": self.message}


class Tracker:
    """跟踪框定位器（每个 Pipeline 持有自己的状态）。"""

    def __init__(self, cfg: TrackConfig):
        self.cfg = cfg
        self._ref_gray: np.ndarray | None = None

    def reset(self) -> None:
        self._ref_gray = None

    # ------------------------------------------------------------------ 参考图
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

    # ------------------------------------------------------------------ 定位
    def locate(self, frame: Frame) -> TrackResult:
        """返回本帧相对参考位置的偏移 (dx, dy)。"""
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
    """把 ROI 平移 (dx, dy)（用于把检测框跟着产品走）。线形 ROI 两端一起平移。"""
    if rect is None:
        return None
    line = rect.line
    if line is not None:
        return Rect(
            int(round(rect.x + dx)), int(round(rect.y + dy)), rect.w, rect.h,
            shape=rect.shape,
            line=(int(round(line[0] + dx)), int(round(line[1] + dy)),
                  int(round(line[2] + dx)), int(round(line[3] + dy))),
        )
    return Rect(int(round(rect.x + dx)), int(round(rect.y + dy)), rect.w, rect.h)


__all__ = ["TrackConfig", "TrackResult", "Tracker", "shift_rect"]
