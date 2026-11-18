import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.getcwd())

import numpy as np

from visionapp.qtcompat import QApplication, BINDING

app = QApplication([])
print("绑定:", BINDING)

from visionapp.core import Rect
from visionapp.recipe import Recipe
from visionapp.settings import AppSettings
from visionapp.sources import open_source
from visionapp.ui.main_window import MainWindow

st = AppSettings.load(os.path.join("config", "app_settings.json"))
w = MainWindow(recipe=Recipe(name="t"), source=open_source("synthetic", fps=0), settings=st)
w.showNormal(); w.resize(1200, 800)
w.new_recipe()
w.act_select.setChecked(False)
w.act_multi_draw.setChecked(True)
w._on_roi_created(Rect(150, 200, 250, 160))
w._on_roi_created(Rect(600, 200, 250, 160))
w._on_line_created(Rect.from_line(120, 700, 1200, 700))
w._refresh_item_list(); w._sync_view()
w.tiles[0].view.set_frame(np.full((900, 1400, 3), 70, np.uint8))
app.processEvents()
print("标签:", [it.label for it in list(w.view._rois.values()) + list(w.view._lines.values())])
w.grab().save(os.path.join("out", "py2_labels.png"))
print("截图完成")
w.close()
