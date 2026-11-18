"""自测：磁盘守护（剩余空间/占用统计/按天数清理/超限清理/报警级别）。"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from visionapp.diskguard import (  # noqa: E402
    LEVEL_CRIT, LEVEL_OK, LEVEL_WARN, DiskGuard, dir_size_gb, free_gb, iter_date_dirs,
    prune_days, prune_until_size,
)

FAILED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILED
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED += 1


def make_tree(root: str, days_list: list[int], size: int = 1024) -> str:
    base = os.path.join(root, "相机1", "2pin", "NG")
    for days in days_list:
        d = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        p = os.path.join(base, d)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "a.png"), "wb") as f:
            f.write(b"x" * size)
    return base


def main() -> int:
    root = os.path.join(BASE, "out", "disktest_selftest")
    shutil.rmtree(root, ignore_errors=True)
    base = make_tree(root, [40, 20, 2])

    check("剩余空间能读到", free_gb(root) > 0, f"{free_gb(root):.1f} GB")
    check("目录大小能算", dir_size_gb(root) > 0, f"{dir_size_gb(root):.4f} GB")
    check("日期目录按旧到新", [d.isoformat() for d, _p in iter_date_dirs(root)] ==
          sorted((dt.date.today() - dt.timedelta(days=n)).isoformat() for n in (40, 20, 2)))

    check("按天数清理 30 天：删 1 个", prune_days(root, 30) == 1)
    check("清理后只剩 2 个日期目录", len(iter_date_dirs(root)) == 2)
    check("保留最近的那天", os.path.isdir(os.path.join(
        base, (dt.date.today() - dt.timedelta(days=2)).isoformat())))

    make_tree(root, [15, 5])
    check("keep_days=0 不清理", prune_days(root, 0) == 0)

    removed = prune_until_size(root, 0.000001)
    check("超上限会清理", removed >= 1, f"删了 {removed} 个")

    make_tree(root, [10, 1])
    g = DiskGuard(root=root, min_free_gb=1e9, critical_free_gb=1e8, max_results_gb=0)
    level, text = g.check()
    check("空间很紧时报 crit", level == LEVEL_CRIT, text)
    check("crit 时自动清到只留最近 1 天", len(iter_date_dirs(root)) <= 1)

    g2 = DiskGuard(root=root, min_free_gb=1e9, critical_free_gb=0, max_results_gb=0)
    check("低于提醒阈值报 warn", g2.check()[0] == LEVEL_WARN)

    g3 = DiskGuard(root=root, min_free_gb=0.001, critical_free_gb=0.0001, max_results_gb=0)
    check("空间充足报 ok", g3.check()[0] == LEVEL_OK)

    shutil.rmtree(root, ignore_errors=True)
    print(f"\nRESULT: {'PASS' if FAILED == 0 else 'FAIL'}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
