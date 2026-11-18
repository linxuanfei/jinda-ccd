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

import cv2

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
    keep_days: int = 30                 # NG/OK 图保留天数，0 = 永不清理
    min_free_gb: float = 0.5            # 剩余空间低于这个就不存图（只写 CSV），防写满卡机
    save_mode: str = "ng"               # ng / ok / all（现场"通用设置"里的三选一）

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecorderConfig":
        return cls(
            root=data.get("root", "results"),
            csv_enabled=bool(data.get("csv_enabled", True)),
            save_ng_images=bool(data.get("save_ng_images", True)),
            save_ok_images=bool(data.get("save_ok_images", False)),
            image_ext=data.get("image_ext", ".png"),
            keep_days=int(data.get("keep_days", data.get("ng_keep_days", 30)) or 0),
            min_free_gb=float(data.get("min_free_gb", 0.5) or 0),
            save_mode=str(data.get("save_mode", "ng") or "ng"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "csv_enabled": self.csv_enabled,
            "save_ng_images": self.save_ng_images,
            "save_ok_images": self.save_ok_images,
            "image_ext": self.image_ext,
            "keep_days": self.keep_days,
            "save_mode": self.save_mode,
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
        self.camera_tag = ""

    # ------------------------------------------------------------------ 打开
    def open(self, recipe_name: str, item_ids: list[str], camera_tag: str = "") -> None:
        self.close()
        self.recipe_name = recipe_name or "untitled"
        self.camera_tag = camera_tag or getattr(self, "camera_tag", "") or ""
        self._item_ids = list(item_ids)
        # 现场要求：保存路径 = 日期 / 时间_相机号
        #   results\2026-11-17\18-12-30_相机1\NG\xxx.png
        tag = self.camera_tag or "相机1"
        day = time.strftime("%Y-%m-%d")
        stamp = time.strftime("%H-%M-%S")
        self._dir = os.path.join(self.cfg.root, day, f"{stamp}_{tag}")
        os.makedirs(self._dir, exist_ok=True)
        mode = str(getattr(self.cfg, "save_mode", "") or "").lower()
        if mode in ("ng", "all") or self.cfg.save_ng_images:
            os.makedirs(os.path.join(self._dir, "NG"), exist_ok=True)
        if mode in ("ok", "all") or self.cfg.save_ok_images:
            os.makedirs(os.path.join(self._dir, "OK"), exist_ok=True)
        self._prune_old_dirs()
        self._open_csv_for_today()

    def prune_old(self) -> int:
        """删除超过 keep_days 天的 NG/OK 日期目录，返回删除的目录数。"""
        return self._prune_old_dirs()

    def _prune_old_dirs(self) -> int:
        if not getattr(self.cfg, "keep_days", 0) or self.cfg.keep_days <= 0:
            return 0
        import datetime as _dt
        import shutil

        cutoff = _dt.date.today() - _dt.timedelta(days=int(self.cfg.keep_days))
        removed = 0
        base = self._dir or os.path.join(self.cfg.root, self.recipe_name)
        if not os.path.isdir(base):
            return 0
        for kind in ("NG", "OK"):
            kind_dir = os.path.join(base, kind)
            if not os.path.isdir(kind_dir):
                continue
            for name in os.listdir(kind_dir):
                p = os.path.join(kind_dir, name)
                if not os.path.isdir(p):
                    continue
                try:
                    d = _dt.datetime.strptime(name, "%Y-%m-%d").date()
                except ValueError:
                    continue                    # 不是日期目录，不动
                if d < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
                    removed += 1
        if removed:
            self.stats.last_error = f"已清理 {removed} 个过期目录（保留 {self.cfg.keep_days} 天）"
        return removed

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
            header = ["时间", "帧号", "模式", "判定", "耗时ms"] + self._item_ids
            self._writer.writerow(header)
            self._csv_file.flush()

    # ------------------------------------------------------------------ 记录
    def record(self, result: PipelineResult, frame: Frame, annotated, elapsed_ms: float,
               recheck: bool = False) -> None:
        ts = time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if day != self._day:
            self._prune_old_dirs()
        self._open_csv_for_today()

        verdict = "OK" if result.ok else "NG"
        if self._writer is not None:
            row = [
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                result.frame_index,
                "复检" if recheck else "首检",
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

        mode = str(getattr(self.cfg, "save_mode", "") or "").lower()
        if mode == "ng":
            want = verdict == "NG"
        elif mode == "ok":
            want = verdict == "OK"
        elif mode == "all":
            want = True
        else:
            want = (verdict == "NG" and self.cfg.save_ng_images) or (
                verdict == "OK" and self.cfg.save_ok_images)
        if want and float(getattr(self.cfg, "min_free_gb", 0) or 0) > 0:
            from .diskguard import free_gb

            if 0 <= free_gb(self._dir) < float(self.cfg.min_free_gb):
                self.stats.last_error = "磁盘剩余空间不足，已跳过存图（仅记录 CSV）"
                want = False
        if want:
            img = annotated if annotated is not None else frame.bgr
            sub = os.path.join(self._dir, verdict)      # 日期已在上层目录里
            os.makedirs(sub, exist_ok=True)
            name = f"{time.strftime('%H%M%S', time.localtime(ts))}_frame{result.frame_index:06d}{self.cfg.image_ext}"
            path = os.path.join(sub, name)
            try:
                if not imwrite_safe(path, img):
                    raise RuntimeError(f"无法编码图像: {path}")
                self.stats.images += 1
                # 同目录写一份结果快照：图库"重新调试"时能看到当时测了什么
                side = os.path.splitext(path)[0] + ".json"
                try:
                    import json as _json

                    with open(side, "w", encoding="utf-8") as fp:
                        _json.dump({
                            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                            "recipe": self.recipe_name,
                            "camera": self.camera_tag,
                            "frame_index": result.frame_index,
                            "verdict": verdict,
                            "result": result.as_dict(),
                        }, fp, ensure_ascii=False, indent=2)
                except Exception:                      # noqa: BLE001
                    pass
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
