"""规则判定自测：设定值/上下公差 → OK/NG；基准线要求相邻框。"""
import os
import sys

sys.path.insert(0, os.getcwd())

import numpy as np

from visionapp.core import Frame, Rect
from visionapp.ops import Item
from visionapp.recipe import Pipeline, Recipe

FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


P = {
    "threshold_low": 100, "threshold_high": 255, "threshold_mode": "dark",
    "min_area": 50, "max_area": 999999, "min_width": 2, "min_height": 2,
    "pick_zone": "白色中下", "use_pick": "on", "show_pick": "off", "show_mask": "off",
    "blur": 0, "morph": 0,
}


def build():
    img = np.full((400, 700, 3), 20, np.uint8)
    img[100:180, 100:200] = 240
    img[100:180, 300:400] = 240
    rec = Recipe(name="t")
    rec.items = [
        Item(id="a", type="find_blobs", roi=Rect(80, 80, 160, 140), params=dict(P)),
        Item(id="b", type="find_blobs", roi=Rect(280, 80, 160, 140), params=dict(P)),
    ]
    return rec, Frame.create(img)


def main() -> None:
    rec, frame = build()
    rec.rules = [{"kind": "x", "from_no": 1, "to_no": 2, "base": "1", "abs": True,
                  "target": 200, "tol_up": 20, "tol_lo": -20}]
    res, _ = Pipeline(rec).run(frame, annotate=False)
    check("X 规则在公差内为 OK", res.results["rule1"].ok is True, str(res.results["rule1"]))
    check("整体判定 OK", res.ok is True, str(res.ok))
    check("数值 = 200", abs(float(res.results["rule1"].value) - 200) < 1, str(res.results["rule1"].value))

    rec.rules[0]["target"] = 300
    res2, _ = Pipeline(rec).run(frame, annotate=False)
    check("超差判 NG", res2.results["rule1"].ok is False, str(res2.results["rule1"]))
    check("整体判 NG", res2.ok is False)

    rec.rules = [{"kind": "baseline", "from_no": 1, "to_no": 3, "abs": True}]
    res3, _ = Pipeline(rec).run(frame, annotate=False)
    check("基准线非相邻 → 报错", res3.results["rule1"].ok is False,
          str(res3.results["rule1"].message))

    rec.rules = [{"kind": "baseline", "from_no": 1, "to_no": 2, "abs": True}]
    res4, _ = Pipeline(rec).run(frame, annotate=False)
    check("基准线相邻 → 生成", res4.results["rule1"].ok is None and
          any(it.type == "baseline" for it in rec.items),
          str([it.id for it in rec.items]))

    print("\nRESULT:", "PASS" if not FAIL else "FAIL")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
