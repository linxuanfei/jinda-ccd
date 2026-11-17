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
            rules = it.judges or ([it.judge] if it.judge else [])
            judge = "-"
            if rules:
                judge = " ; ".join(f"{j.get('min')} ~ {j.get('max')}" for j in rules)
            roi = "-" if it.roi is None else f"{it.roi.x},{it.roi.y},{it.roi.w},{it.roi.h}"
            lines.append(f"| {it.station} | {it.id.split('_i')[-1].split('_')[0]} | "
                         f"{it.params.get('_hpro_type', '-')} | {it.name} | {it.type} | {roi} | {judge} |")
        if self.recipe.tail_rules:
            lines += ["", "## 文件尾类型4", ""]
            for rule in self.recipe.tail_rules:
                lines.append(
                    f"- {rule.get('name')} {rule.get('min')} ~ {rule.get('max')} "
                    f"refs={rule.get('refs')} raw=`{rule.get('raw')}`"
                )
        if self.recipe.scales:
            lines += ["", "## 比例（原始值，单位未在文件中写明）", ""]
            for sc in self.recipe.scales:
                lines.append(
                    f"- 站{sc.get('station')}: scale_x={sc.get('scale_x')} "
                    f"scale_y={sc.get('scale_y')} scale={sc.get('scale')} "
                    f"unit={sc.get('unit')}"
                )
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


def _eval_bound(txt: str, low: float, high: float, tol: float) -> float:
    txt = txt.replace("low", str(low)).replace("high", str(high)).replace("tol", str(abs(tol)))
    return round(float(eval(txt, {"__builtins__": {}}, {})), 4)     # noqa: S307 —— 仅用于配置表达式


def _parse_judges(extra: list[str], judge_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """读出一条检测项上的全部判定行。模式 0/1/2 仍是标称 ± 公差。"""
    mode_idx = int(judge_cfg.get("mode_index", 1))
    val_idx = int(judge_cfg.get("value_index", 4))
    val2_idx = int(judge_cfg.get("value2_index", 5))
    tol_idx = int(judge_cfg.get("tol_index", 6))
    modes = {k: v for k, v in judge_cfg.get("modes", {}).items() if not str(k).startswith("_")}
    found: list[dict[str, Any]] = []

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
            # 值1 ~ 值2。第三个数不进区间，含义未证实。
            found.append({
                "kind": "range",
                "min": low,
                "max": high,
                "mode": mode,
                "unproven_third": tol,
            })
            continue
        lo, hi = (p.strip() for p in expr.split("~"))
        found.append({
            "kind": "range",
            "min": _eval_bound(lo, low, high, tol),
            "max": _eval_bound(hi, low, high, tol),
        })
    return found


def _parse_judge(extra: list[str], judge_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """兼容旧调用：只返回第一条。新导入走 `_parse_judges`。"""
    judges = _parse_judges(extra, judge_cfg)
    return judges[0] if judges else None


_LIVE_THRESHOLD_KEYS = ("threshold", "threshold_low", "threshold_high")


def _unmapped_item_tail(params_line: str) -> list[float] | None:
    """项参数行末尾常见 `0.000 255.000 0.000`。记下来，不要当成阈值。"""
    parts = params_line.split()
    if len(parts) < 3:
        return None
    try:
        tail = [float(parts[-3]), float(parts[-2]), float(parts[-1])]
    except ValueError:
        return None
    if tail[0] == 0.0 and tail[1] == 255.0:
        return tail
    return None


def _drop_blocked_thresholds(params: dict[str, Any], station: int, idx: int,
                             issues: list[ImportIssue]) -> None:
    """0 和 255 继续硬拦截。这里直接不写入，也不改成 1 或 254。"""
    for key in _LIVE_THRESHOLD_KEYS:
        if key not in params:
            continue
        raw = params[key]
        try:
            val = int(float(raw))
        except (TypeError, ValueError):
            continue
        if val in (0, 255):
            params.pop(key, None)
            issues.append(ImportIssue(
                "warn", station, idx,
                f"{key}={raw} 未写入（0/255 不作为阈值，也不钳位）",
            ))


def _import_tail_rules(rules: list[Any]) -> list[dict[str, Any]]:
    """文件尾类型 4：标称 ± 公差。下标与项内判定行不同，不能复用那张表。"""
    out: list[dict[str, Any]] = []
    for rule in rules:
        nominal = float(rule.nominal)
        lo = round(nominal - abs(float(rule.tol_a)), 4)
        hi = round(nominal + abs(float(rule.tol_b)), 4)
        out.append({
            "type": 4,
            "name": "类型4",
            "kind": "range",
            "nominal": nominal,
            "tol": [float(rule.tol_a), float(rule.tol_b)],
            "min": lo,
            "max": hi,
            "refs": [int(rule.refs[0]), int(rule.refs[1])],
            "raw": rule.raw,
            "note": "类型4中文含义未确认；末尾整数原样保存，含义未确认",
        })
    return out


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace")


_CAM_SCALE_RE = re.compile(
    r"^相机(\d+)(X|Y)?比例\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)


def load_raw_scales(path: str) -> list[dict[str, Any]]:
    """读 2pin 工程系统 ini 里的相机比例，按原值保存。

    相机 1 → station 0。文件没有写单位，所以不换算 mm_per_px，
    也不把「比例」当成毫米。二次元 ini 的 HorzScale/VertScale 对不上这些键，不会被读进来。
    """
    by_cam: dict[int, dict[str, Any]] = {}
    for line in _read_text(path).splitlines():
        m = _CAM_SCALE_RE.match(line.strip())
        if not m:
            continue
        cam = int(m.group(1))
        axis = m.group(2)
        value = float(m.group(3))
        rec = by_cam.setdefault(cam, {
            "station": cam - 1,
            "scale_x": None,
            "scale_y": None,
            "scale": None,
            "unit": None,
            "note": "单位未在文件中写明",
        })
        if axis == "X":
            rec["scale_x"] = value
        elif axis == "Y":
            rec["scale_y"] = value
        else:
            rec["scale"] = value
    return [by_cam[k] for k in sorted(by_cam)]


def import_template(tpl: HProTemplate, mapping: dict[str, Any],
                    scales: list[dict[str, Any]] | None = None) -> ImportResult:
    types_cfg: dict[str, Any] = mapping.get("types", {})
    judge_cfg: dict[str, Any] = mapping.get("judge", {})
    thickness = int(mapping.get("roi", {}).get("line_roi_thickness", 24))
    unmapped_action = mapping.get("unmapped", {}).get("action", "skip")

    recipe = Recipe(name=os.path.splitext(os.path.basename(tpl.path))[0], station=1,
                    camera="")
    result = ImportResult(recipe=recipe)
    saw_unmapped_tail = False
    if scales:
        # 原值挂在配方上，不写进 calibration 的 mm_per_px。
        recipe.scales = [dict(sc) for sc in scales]
        result.issues.append(ImportIssue(
            "info", -1, -1,
            "相机比例按文件原值写入 scale_x/scale_y/scale，单位未在文件中写明，未换算 mm_per_px",
        ))
    if tpl.tail_rules:
        recipe.tail_rules = _import_tail_rules(tpl.tail_rules)
        result.issues.append(ImportIssue(
            "info", -1, -1,
            f"文件尾类型4 已导入 {len(recipe.tail_rules)} 条（中文含义未确认）",
        ))

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
            if hp.roi is not None:
                params["_hpro_roi_xyxy"] = [int(v) for v in hp.roi]
            tail = _unmapped_item_tail(hp.params_line)
            if tail is not None:
                params["_hpro_unmapped_tail"] = tail
                saw_unmapped_tail = True
            _drop_blocked_thresholds(params, station, idx, result.issues)
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
                judges = _parse_judges(hp.extra, judge_cfg)
                item.judges = judges
                item.judge = judges[0] if judges else None
                if not judges:
                    result.issues.append(ImportIssue("info", station, idx, "未解析到判定规则，判定留空"))
            if thick and hp.roi is not None:
                x1, y1, x2, y2 = hp.roi
                result.issues.append(ImportIssue(
                    "info", station, idx,
                    f"线形 ROI 已加厚至 {thickness}px；原端点 {x1},{y1}–{x2},{y2}",
                ))
            recipe.items.append(item)
            result.mapped += 1

    if saw_unmapped_tail:
        result.issues.append(ImportIssue(
            "info", -1, -1,
            "参数行末尾 0.000 255.000 未映射为阈值（不钳位，0/255 仍硬拦截）",
        ))
    return result
