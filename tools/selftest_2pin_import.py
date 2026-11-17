"""用老主机原文件核对 2pin 导入。

不读阅读纪要代替原文件。二次元测量参数.ini 不参与 2pin 标定。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.hpro import parse_file
from visionapp.hpro_import import _parse_judge, import_template, load_mapping, load_raw_scales
from visionapp.recipe import Recipe

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(BASE, "tests", "fixtures", "old-host-2pin")
HPRO = os.path.join(FIX, "2pin.HPro")
INI = os.path.join(FIX, "2pin-工程系统.ini")
DIM = os.path.join(FIX, "二次元测量参数.ini")


def main() -> int:
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
        if not ok:
            fails.append(name if not detail else f"{name}: {detail}")

    def near(got: float, exp: float, tol: float = 1e-6) -> bool:
        return abs(float(got) - float(exp)) <= tol

    def pair(judge: dict, lo: float, hi: float) -> bool:
        return near(judge["min"], lo) and near(judge["max"], hi)

    mapping = load_mapping(os.path.join(BASE, "config", "hpro_types.json"))
    one = _parse_judge(["0 1 0 0 1.0 1.0 0.08"], mapping["judge"])
    check("single rule dict unchanged", one == {"kind": "range", "min": 0.92, "max": 1.08}, str(one))
    for mode in ("0", "1", "2"):
        expr = str(mapping["judge"]["modes"][mode]["expr"])
        check(f"mode {mode} still nominal ± tol", expr == "low - tol ~ low + tol", expr)

    tpl = parse_file(HPRO)
    summary = tpl.summary()
    check("two stations", tpl.station_count == 2 and len(tpl.stations) == 2)
    check("station 0 has 6 items", len(tpl.stations[0].items) == 6, str(len(tpl.stations[0].items)))
    check("station 1 has 5 items", len(tpl.stations[1].items) == 5, str(len(tpl.stations[1].items)))
    check("tail type 4 count", len(tpl.tail_rules) == 2, str(len(tpl.tail_rules)))
    check("inspect shows type 4 raw", "4 0 0.000 0.120 0.120 1 1" in summary)
    check("tail is not only ok", tpl.tail != ["ok"], str(tpl.tail))
    check("type 4 left the station trailer", all("4 0 0.000" not in line for line in tpl.stations[-1].trailer))
    check("tail refs", [list(r.refs) for r in tpl.tail_rules] == [[1, 1], [2, 1]], str([r.refs for r in tpl.tail_rules]))

    # 同一行若走项内下标，公差会被读成 1，区间不再是 ±0.120。
    mis = _parse_judge(["4 0 0.000 0.120 0.120 1 1"], mapping["judge"])
    check(
        "in-item index map does not fit the tail line",
        mis is not None and not near(mis["min"], -0.12),
        str(mis),
    )

    dim_scales = load_raw_scales(DIM)
    check("二次元 ini is not 2pin scales", dim_scales == [], str(dim_scales))
    scales = load_raw_scales(INI)
    check("two cameras", len(scales) == 2, str(scales))
    result = import_template(tpl, mapping, scales=scales)
    recipe = result.recipe
    check("mapped 11", result.mapped == 11 and result.skipped == 0, f"{result.mapped}/{result.skipped}")

    cam1 = [it for it in recipe.items if it.station == 0]
    cam2 = [it for it in recipe.items if it.station == 1]
    check("camera 1 keeps 6", [it.params.get("_hpro_type") for it in cam1] == [1, 2, 3, 12, 13, 14],
          str([it.params.get("_hpro_type") for it in cam1]))
    check("camera 2 keeps 5", [it.params.get("_hpro_type") for it in cam2] == [1, 2, 11, 12, 13],
          str([it.params.get("_hpro_type") for it in cam2]))

    base = cam1[0]
    check("baseline original ends", base.params.get("_hpro_roi_xyxy") == [241, 1082, 2440, 1082],
          str(base.params.get("_hpro_roi_xyxy")))
    check("baseline thickened", base.roi is not None and base.roi.h > 1 and base.roi.y != 1082,
          str(None if base.roi is None else (base.roi.x, base.roi.y, base.roi.w, base.roi.h)))
    documented = any("241,1082" in iss.message and "2440,1082" in iss.message for iss in result.issues)
    check("baseline ends documented", documented)

    check("cam1 type2", len(cam1[1].judges) == 1 and pair(cam1[1].judges[0], -0.18, -0.02), str(cam1[1].judges))
    check("cam1 type3 both", len(cam1[2].judges) == 2, str(cam1[2].judges))
    check("cam1 type3 first", pair(cam1[2].judges[0], 0.92, 1.08), str(cam1[2].judges[0]))
    check("cam1 type3 second", pair(cam1[2].judges[1], -0.18, -0.02), str(cam1[2].judges[1]))
    check("cam1 type12 no invented rule", cam1[3].judge is None and cam1[3].judges == [])
    for item, label in ((cam1[4], "类型13"), (cam1[5], "类型14")):
        check(f"{label} name", item.name == label, item.name)
        check(f"{label} guess stays out of the name", "猜测" not in item.name and "面积" not in item.name)
        rule = item.judges[0]
        check(f"{label} interval", pair(rule, 300, 1000), str(rule))
        check(f"{label} third stays unproven", near(rule.get("unproven_third"), 100) and rule.get("mode") == 5, str(rule))
        check(f"{label} third is not a bound", not near(rule["min"], 100) and not near(rule["max"], 100))

    check("cam2 type2", len(cam2[1].judges) == 1 and pair(cam2[1].judges[0], 0.92, 1.08), str(cam2[1].judges))
    check("cam2 type12", len(cam2[3].judges) == 1 and pair(cam2[3].judges[0], 3.65, 3.95), str(cam2[3].judges))
    check("cam2 type13 unnamed guess", cam2[4].name == "类型13", cam2[4].name)

    check("tail rules stored apart from items", len(recipe.tail_rules) == 2 and len(recipe.items) == 11)
    for rule, refs in zip(recipe.tail_rules, ([1, 1], [2, 1])):
        check(
            f"tail {rule.get('raw')}",
            rule.get("type") == 4 and rule.get("name") == "类型4" and pair(rule, -0.12, 0.12)
            and rule.get("refs") == refs and "未确认" in str(rule.get("note")),
            str(rule),
        )

    check("scales not written as mm",
          recipe.calibration.mm_per_px == 0 and recipe.calibration.mm_per_px_x == 0
          and recipe.calibration.mm_per_px_y == 0)
    expect = (
        (0, 12.6, 4.5, 10.0),
        (1, 9.4, 22.0, 10.0),
    )
    for sc, (station, sx, sy, scale) in zip(recipe.scales, expect):
        check(
            f"station {station} raw scale",
            sc.get("station") == station and sc.get("unit") is None
            and "未在文件中写明" in str(sc.get("note"))
            and near(sc.get("scale_x"), sx) and near(sc.get("scale_y"), sy) and near(sc.get("scale"), scale)
            and "mm_per_px" not in sc,
            str(sc),
        )
    check("scale 10 is not millimeters", all(sc.get("scale") != recipe.calibration.mm_per_px for sc in recipe.scales))
    check("not the 二次元 2/6", all(not near(sc["scale_x"], 2) and not near(sc["scale_y"], 6) for sc in recipe.scales))

    for it in recipe.items:
        for key in ("threshold", "threshold_low", "threshold_high"):
            val = it.params.get(key)
            blocked = val in (0, 255, 0.0, 255.0)
            check(f"{it.id} {key} not live 0/255", not blocked, str(val))
        check(f"{it.id} tail reported", it.params.get("_hpro_unmapped_tail") == [0.0, 255.0, 0.0],
              str(it.params.get("_hpro_unmapped_tail")))

    blob = recipe.to_dict()
    again = Recipe.from_dict(blob)
    check("scales round-trip", len(again.scales) == 2 and near(again.scales[0]["scale_x"], 12.6)
          and near(again.scales[1]["scale_y"], 22.0))
    check("tail round-trip", len(again.tail_rules) == 2 and near(again.tail_rules[0]["min"], -0.12))
    re_cam1 = [it for it in again.items if it.station == 0]
    check("both rules round-trip", len(re_cam1[2].judges) == 2 and pair(re_cam1[2].judges[1], -0.18, -0.02))
    check("calibration still empty after reload", again.calibration.mm_per_px == 0 and again.calibration.mm_per_px_x == 0)

    print()
    if fails:
        for item in fails:
            print("  [FAIL]", item)
        print(f"RESULT: FAIL ({len(fails)})")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
