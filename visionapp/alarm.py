"""NG 报警：声音 / 蜂鸣 / 回调，带最小间隔防吵。

Windows 上用 winsound 放 wav 或系统提示音；其它平台退化为终端响铃。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class AlarmConfig:
    enabled: bool = True
    beep_on_ng: bool = True
    sound_path: str = ""          # 可选 wav 文件
    min_interval_s: float = 1.0   # 两次报警最小间隔

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlarmConfig":
        return cls(
            enabled=bool(data.get("enabled", True)),
            beep_on_ng=bool(data.get("beep_on_ng", True)),
            sound_path=data.get("sound_path", ""),
            min_interval_s=float(data.get("min_interval_s", 1.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "beep_on_ng": self.beep_on_ng,
            "sound_path": self.sound_path,
            "min_interval_s": self.min_interval_s,
        }


def _default_beep(sound_path: str) -> None:
    """默认发声实现（可被替换，便于测试）。"""
    try:
        import winsound  # noqa: PLC0415 — 仅 Windows
        if sound_path and os.path.exists(sound_path):
            winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.MessageBeep(winsound.MB_ICONHAND)
        return
    except Exception:                                  # noqa: BLE001
        pass
    try:
        print("\a", end="", flush=True)                # 终端响铃
    except Exception:                                  # noqa: BLE001
        pass


class Alarm:
    """NG 报警器。"""

    def __init__(self, cfg: AlarmConfig, beep_fn: Callable[[str], None] | None = None):
        self.cfg = cfg
        self._beep = beep_fn or _default_beep
        self._last_fire = 0.0
        self.fired = 0
        self.skipped = 0

    def trigger(self, ok: bool) -> bool:
        """每帧调用；NG 且超过最小间隔时报警，返回是否真的响了。"""
        if not self.cfg.enabled or ok or not self.cfg.beep_on_ng:
            return False
        now = time.time()
        if now - self._last_fire < self.cfg.min_interval_s:
            self.skipped += 1
            return False
        self._last_fire = now
        self.fired += 1
        try:
            self._beep(self.cfg.sound_path)
        except Exception:                              # noqa: BLE001
            pass
        return True

    def reset(self) -> None:
        self._last_fire = 0.0
