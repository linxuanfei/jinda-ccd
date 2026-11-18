"""自测：.HPro 导入（现场 2pin.HPro 逐段核对版）。

现场文件：`C:\\Users\\津达\\Downloads\\2pin.HPro`，与 `samples/2pin.HPro` **字节级相同**
（SHA256 7766392C097EF911…），所以样例就可以当现场样本用。

原程序参数行（第 7 段）：
    2 2 2 2 999999 25 0 0 0 -1 0.000 255.000 0.000
    └最小宽┘└最小高┘└最大面积┘└最小面积┘        └下限┘└上限┘
原程序判定行：
    1 1 0 -1 -0.100 0.080 0.080 100 99999 0 0 0   → 标称 -0.100 ± 0.080 → [-0.18, -0.02]
    2 0 0 -1 1.000 0.080 0.080 100 99999 0 0 0    → 标称  1.000 ± 0.080 → [ 0.92,  1.08]
评分行：
    0 200 0 0.85                                   → 阈值 200、模板相关 0.85
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp import hpro_import as hi  # noqa: E402
from visionapp.hpro import HProTemplate  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HRO = os.path.join(BASE, "samples", "2pin.HPro")
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        fails.append(name)


def main() -> int:
    print("[1] 解析结构")
    tpl = HProTemplate.parse(HRO)
    check("机位数=2", tpl.station_count == 2, str(tpl.station_count))
    check("站1 声明 6 项", tpl.stations[0].count == 6, str(tpl.stations[0].count))
    check("站2 声明 5 项", tpl.stations[1].count == 5, str(tpl.stations[1].count))
    check("共 11 项", tpl.item_count() == 11, str(tpl.item_count()))

    print("[2] 参数段（最小宽高/面积/阈值）")
    it0 = tpl.stations[0].items[0]
    check("第 7 段是斑块参数", it0.tokens[6].startswith("2 2 2 2 999999 25"),
          it0.tokens[6] if len(it0.tokens) > 6 else "(段数不足)")
    mapping = hi.load_mapping(os.path.join(BASE, "config", "hpro_types.json"))
    bp = hi._parse_blob_params(it0, mapping["blob_params"])
    check("最小宽=2", bp.get("min_width") == 2.0, str(bp))
    check("最小高=2", bp.get("min_height") == 2.0, str(bp))
    check("最小面积=25", bp.get("min_area") == 25.0, str(bp))
    check("最大面积=999999", bp.get("max_area") == 999999.0, str(bp))
    check("下限 0 / 上限 255 原样读到", bp.get("_hpro_threshold_low") == 0.0
          and bp.get("_hpro_threshold_high") == 255.0, str(bp))

    print("[3] 评分行（阈值 200 / 相关 0.85）")
    sc = hi._parse_score(it0, mapping["score_line"])
    check("评分行阈值=200", sc.get("_hpro_score_threshold") == 200, str(sc))
    check("模板相关=0.85", abs(float(sc.get("_hpro_correlation", 0)) - 0.85) < 1e-9, str(sc))

    print("[4] 判定行（标称 ± 容差）")
    j1 = hi._parse_judge(tpl.stations[0].items[1].extra, mapping["judge"])
    check("模式1: -0.100±0.080 → [-0.18,-0.02]",
          j1 is not None and abs(j1["min"] + 0.18) < 1e-6 and abs(j1["max"] + 0.02) < 1e-6, str(j1))
    j2 = hi._parse_judge(tpl.stations[0].items[2].extra, mapping["judge"])
    check("模式2: 1.000±0.080 → [0.92,1.08]",
          j2 is not None and abs(j2["min"] - 0.92) < 1e-6 and abs(j2["max"] - 1.08) < 1e-6, str(j2))
    check("不是「下限~下限+公差」", not (j2 is not None and abs(j2["min"] - 1.0) < 1e-6), str(j2))

    print("[5] 整包导入")
    res = hi.import_template(tpl, mapping)
    check("导入 11 项", res.mapped == 11, str(res.mapped))
    by_id = {it.id: it for it in res.recipe.items}
    check("s0_i2 判定 = [0.92,1.08]",
          by_id["s0_i2"].judge["min"] == 0.92 and by_id["s0_i2"].judge["max"] == 1.08,
          str(by_id["s0_i2"].judge))
    p = by_id["s0_i0"].params
    check("s0_i0 最小面积=25", p.get("min_area") == 25, str(p))
    check("s0_i0 最大面积=999999", p.get("max_area") == 999999, str(p))
    check("s0_i0 最小宽高=2/2", p.get("min_width") == 2 and p.get("min_height") == 2, str(p))
    check("自动阈值标记", p.get("_auto_threshold") is True, str(p))
    mt = by_id["s0_i3"].params
    check("match_template 用相关 0.85", abs(float(mt.get("min_score", 0)) - 0.85) < 1e-9, str(mt))
    # ROI 换算：原文件 (1060,1079)-(1126,1104) → x,y,w,h
    roi = by_id["s0_i1"].roi
    check("ROI 换算 (1060,1079,66,25)",
          roi is not None and (roi.x, roi.y, roi.w, roi.h) == (1060, 1079, 66, 25), str(roi))

    print()
    if fails:
        for f in fails:
            print("  [FAIL]", f)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
