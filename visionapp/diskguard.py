"""磁盘守护：监控结果目录占用 / 硬盘剩余空间，超阈值就清旧图并提醒。

现场担心的事：「30 天不一定放得下，怕塞满卡机」。
所以这里做三层保护：
1. 定时看硬盘剩余空间（默认低于 5 GB 提醒，低于 1 GB 报警并自动清理旧日期目录）；
2. 结果目录总占用可设上限（GB），超了从最旧的日期目录开始删；
3. 存图前再查一次剩余空间，太小就不存图（只写 CSV），避免写满把机器拖卡。
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
from dataclasses import dataclass, field

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_CRIT = "crit"


def free_gb(path: str) -> float:
    """某个目录所在磁盘的剩余空间（GB）。"""
    try:
        usage = shutil.disk_usage(path if os.path.isdir(path) else os.path.dirname(path) or ".")
        return usage.free / (1024 ** 3)
    except OSError:
        return -1.0


def dir_size_gb(path: str) -> float:
    """目录总大小（GB）。"""
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                continue
    return total / (1024 ** 3)


def iter_date_dirs(root: str) -> list[tuple[_dt.date, str]]:
    """找出 NG/OK 下面的 <日期> 目录，按日期从旧到新。"""
    found: list[tuple[_dt.date, str]] = []
    if not os.path.isdir(root):
        return found
    for dirpath, dirs, _files in os.walk(root):
        for d in list(dirs):
            if d.upper() not in ("NG", "OK"):
                continue
            kind_dir = os.path.join(dirpath, d)
            for name in os.listdir(kind_dir):
                p = os.path.join(kind_dir, name)
                if not os.path.isdir(p):
                    continue
                try:
                    day = _dt.datetime.strptime(name, "%Y-%m-%d").date()
                except ValueError:
                    continue
                found.append((day, p))
    found.sort(key=lambda t: t[0])
    return found


def prune_days(root: str, keep_days: int) -> int:
    """按天数清理（0 = 不清理），返回删除的目录数。"""
    if keep_days <= 0:
        return 0
    cutoff = _dt.date.today() - _dt.timedelta(days=int(keep_days))
    removed = 0
    for day, path in iter_date_dirs(root):
        if day < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def prune_until_size(root: str, max_gb: float) -> int:
    """占用超上限时，从最旧的日期目录开始删，直到不超（0 = 不限制）。"""
    if max_gb <= 0 or not os.path.isdir(root):
        return 0
    removed = 0
    dirs = iter_date_dirs(root)
    size = dir_size_gb(root)
    for _day, path in dirs:
        if size <= max_gb:
            break
        try:
            part = dir_size_gb(path)
        except OSError:
            part = 0
        shutil.rmtree(path, ignore_errors=True)
        size -= part
        removed += 1
    return removed


@dataclass
class DiskGuard:
    """设定阈值 + 做检查。"""

    root: str
    min_free_gb: float = 5.0          # 剩余低于这个就提醒
    critical_free_gb: float = 1.0     # 低于这个就报警并自动清理
    max_results_gb: float = 0.0       # 结果目录上限（0=不限制）
    keep_days: int = 30
    last_level: str = ""
    last_text: str = ""
    removed_total: int = 0

    def check(self) -> tuple[str, str]:
        """返回 (级别, 给人看的文字)；必要时自动清理。"""
        free = free_gb(self.root) if os.path.isdir(self.root) else free_gb(os.getcwd())
        used = dir_size_gb(self.root) if os.path.isdir(self.root) else 0.0
        removed = 0

        # 结果目录超上限 → 删最旧的
        if self.max_results_gb > 0 and used > self.max_results_gb:
            removed += prune_until_size(self.root, self.max_results_gb * 0.9)

        level = LEVEL_OK
        if free >= 0 and free < self.critical_free_gb:
            level = LEVEL_CRIT
            # 报警时再激进清一轮：只留最近 1 天
            removed += prune_days(self.root, 1)
            free = free_gb(self.root)
        elif free >= 0 and free < self.min_free_gb:
            level = LEVEL_WARN

        self.removed_total += removed
        free_txt = f"剩余 {free:.1f} GB" if free >= 0 else "剩余空间未知"
        if level == LEVEL_CRIT:
            text = f"硬盘快满了！{free_txt}，已自动清理旧图 {removed} 个目录"
        elif level == LEVEL_WARN:
            text = f"硬盘空间不足：{free_txt}（建议清理或调小保留天数）"
        else:
            text = f"硬盘 {free_txt}｜结果目录 {used:.1f} GB"
        self.last_level, self.last_text = level, text
        return level, text
