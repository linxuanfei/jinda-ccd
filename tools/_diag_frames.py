"""临时：单机位模式下，相机到底有没有在跑（排查"0 帧"）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from visionapp.qtcompat import QApplication, QTimer  # noqa: E402
from visionapp.settings import AppSettings  # noqa: E402
from visionapp.sources import open_source  # noqa: E402
from visionapp.recipe import Recipe  # noqa: E402
from visionapp.ui import theme  # noqa: E402
from visionapp.ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
st = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
theme.set_scale(st.ui_scale)
theme.apply(app)

src = open_source("mindvision", index=0, trigger="continuous", exposure_ms=40)
src.open()
print("源已打开:", src.info.width, "x", src.info.height, "handle=", getattr(src, "_handle", None))

win = MainWindow(recipe=Recipe(name="t"), source=src, settings=st)
station = win.station
print("station.source is src:", station.source is src, " handle:", getattr(station.source, "_handle", None))
print("runner 里的源 is src:", getattr(station.runner, "_source", None) is src)
win.show()
win.start_run()


def check():
    snap = win.station.runner.snapshot()
    print("运行:", snap.running, " 总数:", snap.total, " 亮度:", round(snap.mean_brightness, 1),
          " 等待:", getattr(snap, "waiting_frames", -1), " 错误:", snap.last_error[:120])
    app.quit()


QTimer.singleShot(2500, check)
app.exec()
win.close()
