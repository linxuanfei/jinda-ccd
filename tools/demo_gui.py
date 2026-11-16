"""PySide6 界面演示：模拟相机实时预览 + 检测结果叠加。

用法：
  python tools\\demo_gui.py
  python tools\\demo_gui.py --source folder --path "E:\\lin\\mv\\Bin"

无显示器环境自测：
  set QT_QPA_PLATFORM=offscreen && python tools\\demo_gui.py --frames 5
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2                                   # noqa: E402
from visionapp.qtcompat import QTimer, Qt        # noqa: E402
from visionapp.qtcompat import QImage, QPixmap     # noqa: E402
from visionapp.qtcompat import (               # noqa: E402
    QApplication, QHBoxLayout, QLabel, QMainWindow, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from visionapp.recipe import Pipeline, Recipe   # noqa: E402
from visionapp.sources import open_source        # noqa: E402
from visionapp.ui import theme                   # noqa: E402


class MainWindow(QMainWindow):
    def __init__(self, source, pipeline: Pipeline, max_frames: int = 0):
        super().__init__()
        self.setWindowTitle("VisionApp - 模拟相机演示")
        self.source = source
        self.pipeline = pipeline
        self.max_frames = max_frames
        self.frames = 0

        central = QWidget()
        root = QHBoxLayout(central)

        self.view = QLabel("等待画面...")
        self.view.setMinimumSize(960, 720)
        self.view.setAlignment(Qt.AlignCenter)
        theme.set_class(self.view, "hint")
        root.addWidget(self.view, 3)

        right = QVBoxLayout()
        self.status = QLabel("--")
        theme.set_class(self.status, "statValue")
        right.addWidget(self.status)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["检测项", "值", "判定"])
        self.table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.table, 1)
        root.addLayout(right, 1)

        self.setCentralWidget(central)
        self.resize(1400, 900)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)

    def tick(self) -> None:
        frame = self.source.read()
        if frame is None:
            self.timer.stop()
            return
        result, annotated = self.pipeline.run(frame, annotate=True)
        self.frames += 1

        if annotated is not None:
            rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
            self.view.setPixmap(QPixmap.fromImage(img).scaled(
                self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.status.setText(("OK" if result.ok else "NG") + f"   frame={result.frame_index}   {result.elapsed_ms:.1f}ms")
        theme.set_state(self.status, "ok" if result.ok else "ng")

        items = list(result.results.values())
        self.table.setRowCount(len(items))
        for r, it in enumerate(items):
            self.table.setItem(r, 0, QTableWidgetItem(it.item_id))
            val = "-" if it.value is None else f"{it.value:.3f} {it.unit}"
            self.table.setItem(r, 1, QTableWidgetItem(val))
            self.table.setItem(r, 2, QTableWidgetItem({True: "OK", False: "NG", None: "-"}[it.ok]))

        if self.max_frames and self.frames >= self.max_frames:
            self.timer.stop()
            self.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="synthetic", choices=["synthetic", "folder"])
    ap.add_argument("--path", default="")
    ap.add_argument("--recipe", default="recipes/sim_demo.json")
    ap.add_argument("--frames", type=int, default=0, help="跑够 N 帧自动退出（0=一直跑）")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    recipe_path = args.recipe if os.path.isabs(args.recipe) else os.path.join(base, args.recipe)
    recipe = Recipe.load(recipe_path)
    pipeline = Pipeline(recipe)

    if args.source == "synthetic":
        src = open_source("synthetic", fps=0)
    else:
        src = open_source("folder", path=args.path, loop=True, fps=0)
    src.open()

    app = QApplication(sys.argv)
    theme.apply(app)
    win = MainWindow(src, pipeline, max_frames=args.frames)
    win.show()
    code = app.exec()
    src.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
