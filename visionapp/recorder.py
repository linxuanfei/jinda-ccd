"""结果记录：CSV 落盘 + OK/NG 图像保存。

目录结构（默认在 visionapp/results 下）：

    results/
      <配方名>/
        2026-11-14.csv                # 当天结果
        NG/2026-11-14/HHMMSS_frame000123.png
        OK/...                        # 可选（默认不存 OK 图）
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .core import Frame
from .imgio import imwrite as imwrite_safe
from .recipe import PipelineResult


@dataclass
class RecorderConfig:
    root: str
    csv_enabled: bool = True
    save_ng_images: bool = True
    save_ok_images: bool = False
    image_ext: str = ".png"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecorderConfig":
        return cls(
            root=data.get("root", "results"),
            csv_enabled=bool(data.get("csv_enabled", True)),
            save_ng_images=bool(data.get("save_ng_images", True)),
            save_ok_images=bool(data.get("save_ok_images", False)),
            image_ext=data.get("image_ext", ".png"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "csv_enabled": self.csv_enabled,
            "save_ng_images": self.save_ng_images,
            "save_ok_images": self.save_ok_images,
            "image_ext": self.image_ext,
        }


@dataclass
class RecorderStats:
    rows: int = 0
    images: int = 0
    last_error: str = ""


class Recorder:
    """把检测结果写入 CSV，并保存 NG/OK 图像。"""

    def __init__(self, cfg: RecorderConfig, recipe_name: str = "untitled"):
        self.cfg = cfg
        self.recipe_name = recipe_name
        self.stats = RecorderStats()
        self._dir = ""
        self._csv_path = ""
        self._csv_file = None
        self._writer = None
        self._item_ids: list[str] = []
        self._day = ""

    # ------------------------------------------------------------------ 打开
    def open(self, recipe_name: str, item_ids: list[str]) -> None:
        self.close()
        self.recipe_name = recipe_name or "untitled"
        self._item_ids = list(item_ids)
        self._dir = os.path.join(self.cfg.root, self.recipe_name)
        os.makedirs(self._dir, exist_ok=True)
        if self.cfg.save_ng_images:
            os.makedirs(os.path.join(self._dir, "NG"), exist_ok=True)
        if self.cfg.save_ok_images:
            os.makedirs(os.path.join(self._dir, "OK"), exist_ok=True)
        self._open_csv_for_today()

    def _open_csv_for_today(self) -> None:
        if not self.cfg.csv_enabled:
            return
        day = time.strftime("%Y-%m-%d")
        if day == self._day and self._writer is not None:
            return
        self._close_csv()
        self._day = day
        self._csv_path = os.path.join(self._dir, f"{day}.csv")
        new_file = not os.path.exists(self._csv_path)
        self._csv_file = open(self._csv_path, "a", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._csv_file)
        if new_file:
            header = ["时间", "帧号", "判定", "耗时ms"] + self._item_ids
            self._writer.writerow(header)
            self._csv_file.flush()

    # ------------------------------------------------------------------ 记录
    def record(self, result: PipelineResult, frame: Frame, annotated, elapsed_ms: float) -> None:
        ts = time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if day != self._day:
            self._open_csv_for_today()

        verdict = "OK" if result.ok else "NG"
        if self._writer is not None:
            row = [
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                result.frame_index,
                verdict,
                f"{elapsed_ms:.2f}",
            ]
            for item_id in self._item_ids:
                r = result.results.get(item_id)
                if r is None or r.value is None:
                    row.append("")
                else:
                    row.append(f"{r.value:.3f}")
            try:
                self._writer.writerow(row)
                self._csv_file.flush()
                self.stats.rows += 1
            except Exception as exc:                   # noqa: BLE001
                self.stats.last_error = f"CSV 写入失败: {exc}"

        want = (verdict == "NG" and self.cfg.save_ng_images) or (
            verdict == "OK" and self.cfg.save_ok_images)
        if want:
            img = annotated if annotated is not None else frame.bgr
            sub = os.path.join(self._dir, verdict, day)
            os.makedirs(sub, exist_ok=True)
            name = f"{time.strftime('%H%M%S', time.localtime(ts))}_frame{result.frame_index:06d}{self.cfg.image_ext}"
            path = os.path.join(sub, name)
            try:
                if not imwrite_safe(path, img):
                    raise RuntimeError(f"无法编码图像: {path}")
                self.stats.images += 1
            except Exception as exc:                   # noqa: BLE001
                self.stats.last_error = f"图像保存失败: {exc}"

    # ------------------------------------------------------------------ 关闭
    def _close_csv(self) -> None:
        if self._csv_file is not None:
            try:
                self._csv_file.close()
            finally:
                self._csv_file = None
                self._writer = None

    def close(self) -> None:
        self._close_csv()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
