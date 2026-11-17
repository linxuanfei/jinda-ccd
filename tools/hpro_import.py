"""把 .HPro 模板导入成 VisionApp 配方。

用法：
  python tools\\hpro_import.py --hpro "E:\\lin\\mv\\2pin.HPro"
  python tools\\hpro_import.py --hpro ... --map config\\hpro_types.json --out recipes\\2pin_imported.json
  python tools\\hpro_import.py --hpro ... --report out\\2pin_import.md

映射表 config\\hpro_types.json 可以直接编辑，改完重跑即可，不需要改代码。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.hpro import parse_file                 # noqa: E402
from visionapp.hpro_import import import_template, load_mapping, load_raw_scales   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hpro", required=True, help="原模板路径")
    ap.add_argument("--map", dest="map_path", default="config/hpro_types.json")
    ap.add_argument("--ini", default="", help="2pin 工程系统 ini。比例按原值保存，单位未写明，不换算 mm/px")
    ap.add_argument("--out", default="", help="输出配方 JSON（默认 recipes/<名字>_imported.json）")
    ap.add_argument("--report", default="", help="输出导入报告 Markdown")
    args = ap.parse_args()

    map_path = args.map_path if os.path.isabs(args.map_path) else os.path.join(BASE, args.map_path)
    if not os.path.exists(map_path):
        print(f"[ERR] 找不到映射表: {map_path}", file=sys.stderr)
        return 2

    tpl = parse_file(args.hpro)
    mapping = load_mapping(map_path)
    scales = load_raw_scales(args.ini) if args.ini else None
    result = import_template(tpl, mapping, scales=scales)

    name = os.path.splitext(os.path.basename(args.hpro))[0]
    out = args.out or os.path.join(BASE, "recipes", f"{name}_imported.json")
    if not os.path.isabs(out):
        out = os.path.join(BASE, out)
    result.recipe.save(out)

    print(f"源模板 : {args.hpro}")
    print(f"机位数 : {tpl.station_count}   检测项: {tpl.item_count()}")
    print(f"映射表 : {map_path}")
    print(f"导入   : {result.mapped} 项   跳过: {result.skipped} 项")
    print("\n检测项:")
    for it in result.recipe.items:
        rules = it.judges or ([it.judge] if it.judge else [])
        judge = "-" if not rules else " ; ".join(f"[{j['min']} , {j['max']}]" for j in rules)
        roi = "-" if it.roi is None else f"{it.roi.x},{it.roi.y},{it.roi.w}x{it.roi.h}"
        print(f"  站{it.station}  {it.id:10s} {it.type:15s} {it.name:22s} roi={roi:22s} 判定={judge}")
    if result.recipe.tail_rules:
        print("\n文件尾类型4:")
        for rule in result.recipe.tail_rules:
            print(f"  {rule['name']} [{rule['min']} , {rule['max']}] refs={rule['refs']} raw={rule['raw']}")
    if result.recipe.scales:
        print("\n比例（原始值，单位未在文件中写明）:")
        for sc in result.recipe.scales:
            print(f"  站{sc['station']} scale_x={sc['scale_x']} scale_y={sc['scale_y']} scale={sc['scale']}")

    if result.issues:
        print("\n提示:")
        for iss in result.issues:
            print(f"  [{iss.level}] 站{iss.station} 项{iss.item}: {iss.message}")

    print(f"\n配方已保存: {out}")

    if args.report:
        rep = args.report if os.path.isabs(args.report) else os.path.join(BASE, args.report)
        os.makedirs(os.path.dirname(rep) or ".", exist_ok=True)
        with open(rep, "w", encoding="utf-8") as f:
            f.write(result.report_markdown(args.hpro))
        print(f"报告已保存: {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
