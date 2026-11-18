"""机位（Station）与产线（Line）：多相机检测的骨架。

一个机位 = 一路相机 + 一套配方 + 自己的流水线/记录/输出；
一条产线 = 多个机位 + 合并判定规则。

合并规则：
  all_ok   所有启用机位都 OK 才算 OK（默认）
  any_ok   任一机位 OK 就算 OK
  per_station  不合并，各机位独立判定
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .alarm import Alarm, AlarmConfig
from .core import Frame
from .outputs import OutputConfig, OutputManager
from .recipe import Pipeline, Recipe
from .recorder import Recorder, RecorderConfig
from .runner import MODE_CONTINUOUS, Runner
from .sources import FrameSource, source_from_config

MERGE_RULES = ("all_ok", "any_ok", "per_station")
MERGE_LABELS = {"all_ok": "全部 OK 才 OK", "any_ok": "任一 OK 即 OK", "per_station": "各机位独立"}


@dataclass
class StationConfig:
    id: int = 1
    name: str = ""
    enabled: bool = True
    source: dict[str, Any] = field(default_factory=lambda: {"kind": "synthetic"})
    recipe: str = ""
    output: dict[str, Any] | None = None      # None = 用全局输出配置
    alarm: dict[str, Any] | None = None       # None = 用全局报警配置

    def display_name(self) -> str:
        return self.name or f"机位{self.id}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StationConfig":
        return cls(
            id=int(data.get("id", 1)),
            name=data.get("name", ""),
            enabled=bool(data.get("enabled", True)),
            source=dict(data.get("source", {"kind": "synthetic"})),
            recipe=data.get("recipe", ""),
            output=dict(data["output"]) if data.get("output") is not None else None,
            alarm=dict(data["alarm"]) if data.get("alarm") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "name": self.name, "enabled": self.enabled,
             "source": self.source, "recipe": self.recipe}
        if self.output is not None:
            d["output"] = self.output
        if self.alarm is not None:
            d["alarm"] = self.alarm
        return d


@dataclass
class LineConfig:
    name: str = "默认产线"
    merge_rule: str = "all_ok"
    stations: list[StationConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LineConfig":
        return cls(
            name=data.get("name", "默认产线"),
            merge_rule=data.get("merge_rule", "all_ok"),
            stations=[StationConfig.from_dict(s) for s in data.get("stations", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "merge_rule": self.merge_rule,
                "stations": [s.to_dict() for s in self.stations]}

    @classmethod
    def load(cls, path: str) -> "LineConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


class Station:
    """一路相机的完整运行单元。"""

    def __init__(self, cfg: StationConfig, base_dir: str, settings_getter):
        self.cfg = cfg
        self.base_dir = base_dir
        self._settings_getter = settings_getter
        self.source: FrameSource | None = None
        self.recipe = Recipe(name=cfg.display_name())
        self.recipe_path = ""
        self.pipeline = Pipeline(self.recipe)
        self.recorder = Recorder(self._recorder_cfg(), self.recipe.name)
        self.outputs = OutputManager(self._output_cfg())
        self.alarm = Alarm(self._alarm_cfg())
        self._load_recipe(cfg.recipe)
        self.runner = Runner(None, lambda: self.pipeline, self.recorder,
                             self.outputs, self.alarm)

    # ------------------------------------------------------------------ 配方
    def _load_recipe(self, path: str) -> None:
        p = path
        if p and not os.path.isabs(p):
            p = os.path.join(self.base_dir, p)
        if p and os.path.exists(p):
            try:
                self.recipe = Recipe.load(p)
                self.recipe_path = p
            except Exception:                          # noqa: BLE001
                pass
        self.pipeline = Pipeline(self.recipe)

    def set_recipe(self, recipe: Recipe, path: str = "") -> None:
        self.recipe = recipe
        self.pipeline = Pipeline(recipe)
        self.recipe_path = path

    # ------------------------------------------------------------------ 相机
    def open_source(self) -> None:
        src = source_from_config(self.cfg.source)
        src.open()
        self.source = src
        self.runner.set_source(src)

    def close_source(self) -> None:
        if self.source is not None:
            try:
                self.source.close()
            except Exception:                          # noqa: BLE001
                pass
            self.source = None
            self.runner.set_source(None)

    # ------------------------------------------------------------------ 记录
    def _recorder_cfg(self) -> RecorderConfig:
        data = self._settings_getter("recorder_cfg", {}) or {}
        cfg = RecorderConfig.from_dict({
            "root": data.get("root", "results"),
            "csv_enabled": data.get("csv_enabled", True),
            "save_ng_images": data.get("save_ng_images", True),
            "save_ok_images": data.get("save_ok_images", False),
        })
        if not os.path.isabs(cfg.root):
            cfg.root = os.path.join(self.base_dir, cfg.root)
        return cfg

    def open_recorder(self) -> None:
        cfg = self._recorder_cfg()
        cfg.root = os.path.join(cfg.root, f"相机{self.cfg.id}")   # 按相机号分文件夹
        self.recorder.cfg = cfg
        self.recorder.open(self.recipe.name, [it.id for it in self.recipe.items])

    def open_outputs(self) -> None:
        self.outputs.cfg = self._output_cfg()
        if self.outputs.cfg.enabled:
            self.outputs.open()
        else:
            self.outputs.close()

    def _output_cfg(self) -> OutputConfig:
        raw = self.cfg.output if self.cfg.output is not None else (
            self._settings_getter("output_cfg", {}) or {})
        cfg = OutputConfig.from_dict(raw)
        ch = cfg.channel
        if ch.get("kind") == "file" and ch.get("path") and not os.path.isabs(ch["path"]):
            ch["path"] = os.path.join(self.base_dir, ch["path"])
        return cfg

    def _alarm_cfg(self) -> AlarmConfig:
        raw = self.cfg.alarm if self.cfg.alarm is not None else (
            self._settings_getter("alarm_cfg", {}) or {})
        return AlarmConfig.from_dict(raw)

    def refresh_alarm(self) -> None:
        self.alarm.cfg = self._alarm_cfg()

    def close_outputs(self) -> None:
        self.outputs.close()

    # ------------------------------------------------------------------ 运行
    def start(self, mode: str = MODE_CONTINUOUS) -> bool:
        if self.source is None:
            return False
        return self.runner.start(mode)

    def stop(self) -> None:
        self.runner.stop()
        self.recorder.close()
        self.outputs.close()

    def snapshot(self):
        return self.runner.snapshot()

    def describe(self) -> str:
        return f"{self.cfg.display_name()}｜{self.recipe.name}｜{'启用' if self.cfg.enabled else '停用'}"


class StationManager:
    """管理一条产线上的所有机位。"""

    def __init__(self, line: LineConfig, base_dir: str, settings_getter):
        self.line = line
        self.base_dir = base_dir
        self.stations: list[Station] = [
            Station(cfg, base_dir, settings_getter) for cfg in line.stations
        ]

    # ------------------------------------------------------------------ 运行
    def open_all(self) -> list[str]:
        errors = []
        for st in self.stations:
            if not st.cfg.enabled:
                continue
            try:
                st.open_source()
            except Exception as exc:                   # noqa: BLE001
                errors.append(f"{st.cfg.display_name()}: {exc}")
        return errors

    def start_all(self, mode: str = MODE_CONTINUOUS) -> None:
        for st in self.stations:
            if not st.cfg.enabled:
                continue
            st.open_recorder()
            st.open_outputs()
            st.refresh_alarm()
            st.start(mode)

    def stop_all(self) -> None:
        for st in self.stations:
            st.stop()

    def close_all(self) -> None:
        for st in self.stations:
            st.close_source()
            st.close_outputs()

    # ------------------------------------------------------------------ 判定
    def snapshots(self) -> list:
        return [st.snapshot() for st in self.stations]

    def merged_ok(self) -> bool | None:
        """按合并规则给出整线判定；无数据返回 None。"""
        snaps = [s for s in self.snapshots() if s.total > 0]
        if not snaps:
            return None
        if self.line.merge_rule == "any_ok":
            return any(s.last_result and s.last_result.get("ok") for s in snaps)
        if self.line.merge_rule == "per_station":
            return None
        # all_ok
        return all(s.last_result and s.last_result.get("ok") for s in snaps)

    def totals(self) -> dict[str, int]:
        ok = sum(s.snapshot().ok for s in self.stations)
        ng = sum(s.snapshot().ng for s in self.stations)
        total = sum(s.snapshot().total for s in self.stations)
        return {"ok": ok, "ng": ng, "total": total}

    def running(self) -> bool:
        return any(st.runner.running for st in self.stations)
