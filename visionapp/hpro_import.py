""".HPro → 配方 导入器（映射表可配置）。

用法见 tools/hpro_import.py。核心流程：

    .HPro AST  →  按 config/hpro_types.json 的映射  →  Recipe（json）

映射表里每个 type 配置：
    meaning         人类可读含义（会写进检测项名称）
    op              目标算子（本系统的算子名）
    params          该算子的默认参数
    judge_from_rule 是否从原模板的判定规则行生成判定
    role            "baseline" 标记该类型作为"基准项"，供 $baseline 引用

未配置的类型按 `unmapped.action` 处理（skip / placeholder）。
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .core import Rect
from .hpro import HProItem, HProTemplate
from .ops import Item
from .recipe import Recipe


@dataclass
class ImportIssue:
    level: str          # info / warn / error
    station: int
    item: int
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "station": self.station, "item": self.item, "message": self.message}


@dataclass
class ImportResult:
    recipe: Recipe
    issues: list[ImportIssue] = field(default_factory=list)
    mapped: int = 0
    skipped: int = 0

    def report_markdown(self, src_path: str) -> str:
        lines = [f"# HPro 导入报告", "", f"源文件: `{src_path}`",
                 f"导入检测项: **{self.mapped}**，跳过: **{self.skipped}**", "",
                 "| 站 | # | 类型 | 含义 | 算子 | ROI (x,y,w,h) | 判定 |",
                 "| --- | --- | --- | --- | --- | --- | --- |"]
        for it in self.recipe.items:
            judge = "-"
            if it.judge:
                judge = f"{it.judge.get('min')} ~ {it.judge.get('max')}"
            roi = "-" if it.roi is None else f"{it.roi.x},{it.roi.y},{it.roi.w},{it.roi.h}"
            lines.append(f"| {it.station} | {it.id.split('_i')[-1].split('_')[0]} | "
                         f"{it.params.get('_hpro_type', '-')} | {it.name} | {it.type} | {roi} | {judge} |")
        if self.issues:
            lines += ["", "## 提示", ""]
            for iss in self.issues:
                lines.append(f"- [{iss.level}] 站{iss.station} 项{iss.item}: {iss.message}")
        return "\n".join(lines) + "\n"


def load_mapping(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _line_rect(roi: tuple[int, int, int, int] | None, thickness: int) -> tuple[Rect | None, bool]:
    """把原模板 ROI (x1,y1,x2,y2) 换成矩形；线形 ROI 加厚。"""
    if roi is None:
        return None, False
    x1, y1, x2, y2 = roi
    x, y = min(x1, x2), min(y1, y2)
    w, h = abs(x2 - x1), abs(y2 - y1)
    thick = False
    if h == 0:
        y, h, thick = y - thickness // 2, thickness, True
    if w == 0:
        x, w, thick = x - thickness // 2, thickness, True
    return Rect(x, y, max(1, w), max(1, h)), thick


def _parse_judge(extra: list[str], judge_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """从附加行里找判定规则行并换算成区间。"""
    mode_idx = int(judge_cfg.get("mode_index", 1))
    val_idx = int(judge_cfg.get("value_index", 4))
    val2_idx = int(judge_cfg.get("value2_index", 5))
    tol_idx = int(judge_cfg.get("tol_index", 6))
    modes = {k: v for k, v in judge_cfg.get("modes", {}).items() if not k.startswith("_")}

    for line in extra:
        fields = line.split()
        if len(fields) < 6:
            continue
        try:
            mode = int(fields[mode_idx])
            low = float(fields[val_idx])
            high = float(fields[val2_idx]) if len(fields) > val2_idx else low
            tol = float(fields[tol_idx]) if len(fields) > tol_idx else 0.0
        except (ValueError, IndexError):
            continue
        spec = modes.get(str(mode))
        if not spec:
            continue
        expr = str(spec.get("expr", "low - tol ~ low + tol"))
        if expr.strip() == "value2":
            return {"kind": "range", "min": low, "max": high}
        lo, hi = (p.strip() for p in expr.split("~"))
        env = {"low": low, "high": high, "tol": tol}

        def ev(txt: str) -> float:
            txt = txt.replace("low", str(low)).replace("high", str(high)).replace("tol", str(abs(tol)))
            return round(float(eval(txt, {"__builtins__": {}}, {})), 4)     # noqa: S307 —— 仅用于配置表达式

        return {"kind": "range", "min": ev(lo), "max": ev(hi)}
    return None


def import_template(tpl: HProTemplate, mapping: dict[str, Any]) -> ImportResult:
    types_cfg: dict[str, Any] = mapping.get("types", {})
    judge_cfg: dict[str, Any] = mapping.get("judge", {})
    thickness = int(mapping.get("roi", {}).get("line_roi_thickness", 24))
    unmapped_action = mapping.get("unmapped", {}).get("action", "skip")

    recipe = Recipe(name=os.path.splitext(os.path.basename(tpl.path))[0], station=1,
                    camera="")
    result = ImportResult(recipe=recipe)

    for st in tpl.stations:
        station = st.index
        baseline_ids: list[str] = []
        for idx, hp in enumerate(st.items):
            t = hp.type_hint
            cfg = types_cfg.get(str(t)) if t is not None else None
            if cfg is None:
                result.skipped += 1
                result.issues.append(ImportIssue("warn", station, idx, f"类型 {t} 未配置映射，已跳过"))
                if unmapped_action == "placeholder":
                    item = Item(
                        id=f"s{station}_i{idx}_type{t}",
                        type="find_blobs",
                        name=f"未映射类型{t}",
                        roi=_line_rect(hp.roi, thickness)[0],
                        params={"_hpro_type": t, "threshold": 128, "min_area": 200},
                        judge=None,
                        station=station,
                    )
                    recipe.items.append(item)
                continue

            roi, thick = _line_rect(hp.roi, thickness)
            params: dict[str, Any] = copy.deepcopy(cfg.get("params", {}))
            params["_hpro_type"] = t
            item_id = f"s{station}_i{idx}"
            item = Item(id=item_id, type=cfg.get("op", "find_blobs"),
                        name=cfg.get("meaning", f"类型{t}"), roi=roi, params=params,
                        judge=None, station=station)

            # $baseline 引用解析
            if params.get("src") == "$baseline":
                params["src"] = baseline_ids[0] if baseline_ids else ""
                if not baseline_ids:
                    result.issues.append(ImportIssue("warn", station, idx,
                                                     "需要基准项但没有前置基线类型，src 留空"))
            if cfg.get("role") == "baseline":
                baseline_ids.append(item_id)

            if cfg.get("judge_from_rule"):
                judge = _parse_judge(hp.extra, judge_cfg)
                if judge:
                    item.judge = judge
                else:
                    result.issues.append(ImportIssue("info", station, idx, "未解析到判定规则，判定留空"))
            if thick:
                result.issues.append(ImportIssue("info", station, idx,
                                                 f"线形 ROI 已加厚至 {thickness}px"))
            recipe.items.append(item)
            result.mapped += 1

    return result
