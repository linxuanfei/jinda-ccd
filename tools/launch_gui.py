"""图形界面启动包装器（供 .cmd / .lnk 调用）。

pythonw.exe 没有控制台，异常会静默消失；这个包装器把 stdout/stderr 与异常
写进 logs/gui.log，方便排查。参数原样转发给 visionapp_gui.py。
"""

from __future__ import annotations

import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "gui.log")

sys.path.insert(0, ROOT)


def _open_log():
    os.makedirs(LOG_DIR, exist_ok=True)
    f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f
    print("\n" + "=" * 60)
    import datetime
    print("启动:", datetime.datetime.now().isoformat(timespec="seconds"))
    return f


def main() -> int:
    f = _open_log()
    try:
        sys.argv = [os.path.join(HERE, "visionapp_gui.py")] + sys.argv[1:]
        runpy.run_path(os.path.join(HERE, "visionapp_gui.py"), run_name="__main__")
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        print("（异常已记录到 logs/gui.log）")
        f.flush()
        raise
    finally:
        f.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
