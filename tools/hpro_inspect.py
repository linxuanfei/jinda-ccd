"""检查 .HPro 模板结构并导出 JSON。

用法：
  python tools\\hpro_inspect.py --file "E:\\lin\\mv\\2pin.HPro"
  python tools\\hpro_inspect.py --file ... --json out\\2pin.hpro.json
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.hpro import parse_file      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    tpl = parse_file(args.file)
    print(tpl.summary())

    # 交叉校验：声明数量 vs 解析数量
    for st in tpl.stations:
        if st.count != len(st.items):
            print(f"[WARN] 站 {st.index} 声明 {st.count} 项，实际解析 {len(st.items)} 项")

    out = args.json or os.path.splitext(args.file)[0] + ".hpro.json"
    tpl.dump(out)
    print(f"\nJSON 已导出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
