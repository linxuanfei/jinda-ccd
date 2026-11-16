"""应用级设置（记录路径、上次用的配方/相机等）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SETTINGS = {
    "results_root": "results",
    "csv_enabled": True,
    "save_ng_images": True,
    "save_ok_images": False,
    "last_recipe": "",
    "last_source": {"kind": "synthetic", "path": "", "url": "", "index": 0},
    "output": {
        "enabled": False,
        "channel": {"kind": "file", "path": "results/output.log", "port": "COM3",
                    "baud": 115200, "host": "127.0.0.1", "tcp_port": 5000},
        "template": "{time},{verdict},{frame},{pitch}\r\n",
        "encoding": "gbk",
        "on_ok": True,
        "on_ng": True,
        "ok_text": "OK",
        "ng_text": "NG",
    },
    "alarm": {"enabled": True, "beep_on_ng": True, "sound_path": "", "min_interval_s": 1.0,
              "on_merged": True},
    "security": {"require_login": False, "engineer_password_hash": ""},
}


@dataclass
class AppSettings:
    path: str = ""
    data: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_SETTINGS)))

    @classmethod
    def load(cls, path: str) -> "AppSettings":
        s = cls(path=path)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                s.data.update(raw or {})
            except Exception:                          # noqa: BLE001
                pass
        return s

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ 便捷
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    @property
    def recorder_cfg(self) -> dict[str, Any]:
        return {
            "root": self.data.get("results_root", "results"),
            "csv_enabled": bool(self.data.get("csv_enabled", True)),
            "save_ng_images": bool(self.data.get("save_ng_images", True)),
            "save_ok_images": bool(self.data.get("save_ok_images", False)),
        }

    @property
    def output_cfg(self) -> dict[str, Any]:
        return dict(self.data.get("output", DEFAULT_SETTINGS["output"]))

    @property
    def alarm_cfg(self) -> dict[str, Any]:
        return dict(self.data.get("alarm", DEFAULT_SETTINGS["alarm"]))

    @property
    def security_cfg(self) -> dict[str, Any]:
        return dict(self.data.get("security", DEFAULT_SETTINGS["security"]))
