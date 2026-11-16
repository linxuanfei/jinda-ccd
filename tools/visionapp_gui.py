"""启动 VisionApp 图形界面（支持单机位 / 多机位产线）。

用法：
  python tools\\visionapp_gui.py                                  # 按 config/line.json 起 6 机位
  python tools\\visionapp_gui.py --line config\\line.json --run 1
  python tools\\visionapp_gui.py --source folder --path E:\\lin\\mv\\Bin   # 单机位回放
  python tools\\visionapp_gui.py --source uvc --index 0
  python tools\\visionapp_gui.py --source rtsp --url rtsp://10.239.136.1:8554/live

打包成 exe 后同样支持上面的参数，另外多两个实用开关：
  VisionApp.exe --frames 3 --run 1        # 跑 3 帧自动退出（产线/客户现场自检）
  VisionApp.exe --shot D:\\ui.png         # 截一张界面图后退出（无显示器也能验界面）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.qtcompat import QTimer                     # noqa: E402
from visionapp.qtcompat import QApplication          # noqa: E402

from visionapp.paths import app_dir, ensure_data_dir  # noqa: E402
from visionapp.recipe import Recipe                  # noqa: E402
from visionapp.settings import AppSettings           # noqa: E402
from visionapp.stations import LineConfig            # noqa: E402
from visionapp.ui import theme                       # noqa: E402
from visionapp.ui.dialogs import build_source        # noqa: E402
from visionapp.ui.main_window import MainWindow      # noqa: E402

BASE = ensure_data_dir()


def _setup_frozen_output() -> None:
    """exe（无控制台）模式下把 stdout/stderr/异常都写进 logs/gui.log。"""
    if not getattr(sys, "frozen", False):
        return
    log_dir = os.path.join(app_dir(), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        fd = open(os.path.join(log_dir, "gui.log"), "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = fd
    if sys.stderr is None:
        sys.stderr = fd

    def _hook(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb, file=fd)
        fd.flush()

    sys.excepthook = _hook
    print(f"\n===== VisionApp 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")


def _attach_shot(app: QApplication, win: MainWindow, path: str) -> None:
    """延时截图并退出（自检/无显示器环境用）。"""

    def grab() -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            ok = win.grab().save(path, "PNG")
            print(f"[shot] {path} ok={ok}")
        except Exception:                              # noqa: BLE001
            traceback.print_exc()
        finally:
            win.close()
            app.quit()

    QTimer.singleShot(2000, grab)


def main() -> int:
    _setup_frozen_output()

    ap = argparse.ArgumentParser()
    ap.add_argument("--line", default="", help="产线配置文件（默认 config/line.json）")
    ap.add_argument("--single", action="store_true", help="强制单机位模式")
    ap.add_argument("--source", default="", choices=["", "synthetic", "folder", "uvc", "rtsp", "mindvision"])
    ap.add_argument("--path", default="")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--url", default="")
    ap.add_argument("--recipe", default="")
    ap.add_argument("--frames", type=int, default=0, help="跑够 N 帧自动退出（0=一直跑）")
    ap.add_argument("--run", type=int, default=0, help="启动后自动开始运行（1=是）")
    ap.add_argument("--shot", default="", help="截一张界面图到该路径后退出（自检用）")
    args = ap.parse_args()

    # --shot 是"截图自检"模式：先跑够 2 秒再截图，所以忽略 --frames 的自动退出，
    # 避免帧数限制先到、还没截图窗口就关了。
    max_frames = 0 if args.shot else args.frames

    settings = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
    app = QApplication(sys.argv)
    theme.apply(app)

    use_line = not args.single and not args.source and not args.recipe
    if use_line:
        line_path = args.line or os.path.join(BASE, "config", "line.json")
        if not os.path.isabs(line_path):
            line_path = os.path.join(BASE, line_path)
        if not os.path.exists(line_path):
            print(f"[ERR] 找不到产线配置: {line_path}", file=sys.stderr)
            return 2
        line = LineConfig.load(line_path)
        win = MainWindow(line=line, max_frames=max_frames, settings=settings)
        errors = win.manager.open_all()
        for e in errors:
            print(f"[WARN] {e}", file=sys.stderr)
        win._refresh_station_tabs()
        win._refresh_item_list()
        win._sync_view()
        win._refresh_calib_label()
        win.show()
        if args.run:
            win.start_run()
        if args.shot:
            _attach_shot(app, win, args.shot)
        return app.exec()

    # ---------------- 单机位模式 ----------------
    recipe_path = args.recipe
    if not recipe_path:
        recipe_path = settings.get("last_recipe", "") or os.path.join(BASE, "recipes", "sim_demo.json")
    if recipe_path and not os.path.isabs(recipe_path):
        recipe_path = os.path.join(BASE, recipe_path)
    if recipe_path and os.path.exists(recipe_path):
        recipe = Recipe.load(recipe_path)
        path = recipe_path
    else:
        recipe = Recipe(name="untitled")
        path = ""

    src_cfg = dict(settings.get("last_source", {}))
    if args.source:
        src_cfg = {"kind": args.source,
                   "path": args.path or src_cfg.get("path", ""),
                   "index": args.index,
                   "url": args.url or src_cfg.get("url", "")}
    if src_cfg.get("kind") == "folder" and not src_cfg.get("path"):
        print("[ERR] folder 源需要 --path <图片目录>", file=sys.stderr)
        return 2
    src = build_source(src_cfg)
    try:
        src.open()
    except Exception as exc:                            # noqa: BLE001
        print(f"[ERR] 帧源打开失败: {exc}", file=sys.stderr)
        return 2

    win = MainWindow(recipe, src, path=path, max_frames=max_frames, settings=settings)
    win.show()
    if args.run:
        win.start_run()
    if args.shot:
        _attach_shot(app, win, args.shot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
