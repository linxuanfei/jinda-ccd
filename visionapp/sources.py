"""帧源（相机来源）抽象与实现。

一期提供：
- FolderSource     : 从图片目录回放（先用原程序样图 0121.bmp 做开发）
- SyntheticSource  : 合成画面（带真值，用于算法自测与回归，不需要任何相机）
- RtspSource       : MaixCam Pro / 网络相机 RTSP（生产联调用，需相机联网）
- UvcSource        : 本地 UVC 摄像头（相机切到 UVC 模式时可用）
- MindVisionSource : 迈德威视 GigE（官方 MVSDK，库不在则失败）

新增工业相机时，只需实现 FrameSource 并在这里注册（工厂函数 `open_source`）。
"""

from __future__ import annotations

import abc
import glob
import os
import time
from typing import Any

import cv2
import numpy as np

from .core import Frame, SourceInfo
from .imgio import imread as imread_safe


class FrameSource(abc.ABC):
    """所有帧源实现的统一接口。"""

    def open(self) -> None:
        raise NotImplementedError

    def read(self, timeout_ms: int = 2000) -> Frame | None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def info(self) -> SourceInfo:
        raise NotImplementedError

    def __enter__(self) -> "FrameSource":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FolderSource(FrameSource):
    """图片目录回放源。

    用于：拿原程序保存的图片做离线开发/黄金样张回归。
    支持 `--loop`（循环）和 `--fps`（按帧率节流，0 表示不等待）。
    """

    EXTS = (".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff")

    def __init__(self, path: str, loop: bool = True, fps: float = 0.0, name: str = "folder"):
        self.path = path
        self.loop = loop
        self.fps = fps
        self._name = name
        self._files: list[str] = []
        self._idx = 0
        self._info = SourceInfo(name=name, width=0, height=0, fps=fps)
        self._last_ts = 0.0

    def open(self) -> None:
        if os.path.isfile(self.path):
            self._files = [self.path]
        else:
            files: list[str] = []
            for ext in self.EXTS:
                files.extend(glob.glob(os.path.join(self.path, f"*{ext}")))
                files.extend(glob.glob(os.path.join(self.path, f"*{ext.upper()}")))
            self._files = sorted(set(files))
        if not self._files:
            raise FileNotFoundError(f"目录里没有图片: {self.path}")
        img = imread_safe(self._files[0], cv2.IMREAD_COLOR)
        if img is None:
            raise IOError(f"读不出图片: {self._files[0]}")
        h, w = img.shape[:2]
        self._info = SourceInfo(name=self._name, width=w, height=h, fps=self.fps)

    def read(self, timeout_ms: int = 2000) -> Frame | None:
        if self._idx >= len(self._files):
            if not self.loop:
                return None
            self._idx = 0
        path = self._files[self._idx]
        img = imread_safe(path, cv2.IMREAD_COLOR)
        idx = self._idx
        self._idx += 1
        if img is None:
            return None
        self._throttle()
        return Frame.create(img, index=idx, source=path)

    def _throttle(self) -> None:
        if self.fps <= 0:
            return
        period = 1.0 / self.fps
        now = time.perf_counter()
        wait = period - (now - self._last_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_ts = time.perf_counter()

    def close(self) -> None:
        return None

    @property
    def info(self) -> SourceInfo:
        return self._info


class SyntheticSource(FrameSource):
    """合成画面源（模拟相机）。

    在深色背景上画两个"PIN"（亮矩形）+ 一个基准标记，模拟产线画面。
    画面里附带真值（真实间距/数量），便于自动验证检测结果是否正确。
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 960,
        fps: float = 20.0,
        noise: float = 3.0,
        jitter_px: float = 0.0,
        seed: int = 42,
    ):
        self.width = width
        self.height = height
        self.fps = fps
        self.noise = noise
        self.jitter_px = jitter_px
        self._rng = np.random.default_rng(seed)
        self._idx = 0
        self._last_ts = 0.0
        self._info = SourceInfo(name="synthetic", width=width, height=height, fps=fps)
        # 真值：两个 PIN 的中心
        self._pin_a_x = int(width * 0.32)
        self._pin_b_x = int(width * 0.68)
        self._pin_y = int(height * 0.5)
        self._pin_w = 24
        self._pin_h = int(height * 0.34)

    def open(self) -> None:
        return None

    def read(self, timeout_ms: int = 2000) -> Frame | None:
        idx = self._idx
        self._idx += 1
        img = np.full((self.height, self.width, 3), 24, dtype=np.uint8)

        # 基准标记（左下角方块），用于定位/对齐
        cv2.rectangle(img, (40, self.height - 80), (100, self.height - 20), (60, 60, 200), -1)

        shift = 0.0
        if self.jitter_px > 0:
            shift = float(self._rng.normal(0, self.jitter_px))

        ax = int(self._pin_a_x + shift * 0.5)
        bx = int(self._pin_b_x + shift)
        for cx, color in ((ax, (230, 230, 230)), (bx, (230, 230, 230))):
            x0 = cx - self._pin_w // 2
            y0 = self._pin_y - self._pin_h // 2
            cv2.rectangle(img, (x0, y0), (x0 + self._pin_w, y0 + self._pin_h), color, -1)

        if self.noise > 0:
            img = np.clip(
                img.astype(np.float32) + self._rng.normal(0, self.noise, img.shape), 0, 255
            ).astype(np.uint8)

        self._throttle()
        truth = {
            "pin_a_center": (float(ax), float(self._pin_y)),
            "pin_b_center": (float(bx), float(self._pin_y)),
            "distance_px": float(abs(bx - ax)),
            "pin_count": 2,
        }
        return Frame.create(img, index=idx, source="synthetic", truth=truth)

    def _throttle(self) -> None:
        if self.fps <= 0:
            return
        period = 1.0 / self.fps
        now = time.perf_counter()
        wait = period - (now - self._last_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_ts = time.perf_counter()

    def close(self) -> None:
        return None

    @property
    def info(self) -> SourceInfo:
        return self._info


class RtspSource(FrameSource):
    """网络相机 RTSP 源（MaixCam Pro 开 RTSP 推流后可用）。"""

    def __init__(self, url: str, name: str = "rtsp"):
        self.url = url
        self._name = name
        self._cap: cv2.VideoCapture | None = None
        self._info = SourceInfo(name=name, width=0, height=0)

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self.url)
        if not self._cap.isOpened():
            raise IOError(f"打不开 RTSP: {self.url}")
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        fps = float(self._cap.get(cv2.CAP_PROP_FPS)) or 0.0
        self._info = SourceInfo(name=self._name, width=w, height=h, fps=fps)

    def read(self, timeout_ms: int = 2000) -> Frame | None:
        assert self._cap is not None
        ok, img = self._cap.read()
        if not ok or img is None:
            return None
        return Frame.create(img, index=int(self._cap.get(cv2.CAP_PROP_POS_FRAMES)))

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def info(self) -> SourceInfo:
        return self._info


class UvcSource(FrameSource):
    """本地 USB 摄像头（UVC）。MaixCam Pro 切到 UVC 模式下可用。"""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 960, name: str = "uvc"):
        self.index = index
        self.width = width
        self.height = height
        self._name = name
        self._cap: cv2.VideoCapture | None = None
        self._info = SourceInfo(name=name, width=width, height=height)

    def open(self) -> None:
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        self._cap = cv2.VideoCapture(self.index, backend)
        if not self._cap.isOpened():
            raise IOError(f"打不开摄像头 index={self.index}")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height
        self._info = SourceInfo(name=self._name, width=w, height=h)

    def read(self, timeout_ms: int = 2000) -> Frame | None:
        assert self._cap is not None
        ok, img = self._cap.read()
        if not ok or img is None:
            return None
        return Frame.create(img)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def info(self) -> SourceInfo:
        return self._info


def open_source(kind: str, **kwargs: Any) -> FrameSource:
    """工厂：按类型创建帧源。"""
    kind = kind.lower()
    if kind == "synthetic":
        return SyntheticSource(**kwargs)
    if kind == "folder":
        return FolderSource(**kwargs)
    if kind == "rtsp":
        return RtspSource(**kwargs)
    if kind == "uvc":
        return UvcSource(**kwargs)
    if kind in ("mindvision", "mvsdk"):
        from .mindvision import MindVisionSource
        return MindVisionSource(**kwargs)
    raise ValueError(f"未知帧源类型: {kind}")


def source_from_config(cfg: dict[str, Any]) -> FrameSource:
    """按配置字典创建帧源（不打开）。

    cfg 形如 {"kind": "uvc", "index": 0} / {"kind": "rtsp", "url": "..."} /
             {"kind": "folder", "path": "..."} / {"kind": "synthetic"}
    """
    kind = (cfg or {}).get("kind", "synthetic")
    if kind == "synthetic":
        return open_source("synthetic", fps=0)
    if kind == "folder":
        path = cfg.get("path") or ""
        if not path:
            raise ValueError("图片目录未配置（folder 源需要 path）")
        return open_source("folder", path=path, loop=True, fps=0)
    if kind == "uvc":
        return open_source("uvc", index=int(cfg.get("index", 0) or 0))
    if kind == "rtsp":
        url = cfg.get("url") or ""
        if not url:
            raise ValueError("RTSP 地址不能为空")
        return open_source("rtsp", url=url)
    if kind in ("mindvision", "mvsdk"):
        from .mindvision import MODEL, SERIAL
        return open_source(
            "mindvision",
            serial=str(cfg.get("serial") or SERIAL),
            model=str(cfg.get("model") or MODEL),
            trigger=str(cfg.get("trigger") or "hardware"),
            exposure_ms=cfg.get("exposure"),
            gain=cfg.get("gain"),
            fps=cfg.get("fps"),
            sdk_path=str(cfg.get("sdk_path") or ""),
        )
    raise ValueError(f"未知帧源: {kind}")
