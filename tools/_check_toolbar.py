"""临时：检查工具条溢出按钮是否存在/可见，以及各工具条按钮数量。用完即删。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(BASE))
sys.path.insert(0, BASE)

from visionapp.qtcompat import QApplication, QToolButton  # noqa: E402
from visionapp.settings import AppSettings  # noqa: E402
from visionapp.sources import open_source  # noqa: E402
from visionapp.recipe import Recipe  # noqa: E402
from visionapp.ui import theme  # noqa: E402
from visionapp.ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
st = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
theme.set_scale(st.ui_scale)
theme.apply(app)
win = MainWindow(recipe=Recipe(name="t"), source=open_source("synthetic", fps=0), settings=st)
win.resize(1920, 1080)
win.show()
app.processEvents()

for bar in win.findChildren(type(win.findChild(type(win).__mro__[0]) or object)):
    break

from visionapp.qtcompat import QToolBar  # noqa: E402

for i, bar in enumerate(win.findChildren(QToolBar)):
    ext = bar.findChild(QToolButton, "qt_toolbar_ext_button")
    print(f"工具条{i}: name={bar.objectName()} 高度={bar.height()} "
          f"动作数={len(bar.actions())} 溢出按钮={'有' if ext else '无'} "
          f"可见={ext.isVisible() if ext else '-'} 宽度={bar.width()}")
win.close()
