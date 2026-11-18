import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from visionapp.qtcompat import QApplication  # noqa: E402

app = QApplication([])

from visionapp.core import Rect  # noqa: E402
from visionapp.imgio import imwrite  # noqa: E402
from visionapp.recipe import Recipe  # noqa: E402
from visionapp.settings import AppSettings  # noqa: E402
from visionapp.sources import open_source  # noqa: E402
from visionapp.ui import theme  # noqa: E402
from visionapp.ui.main_window import MainWindow  # noqa: E402

st = AppSettings.load(os.path.join("config", "app_settings.json"))
theme.set_scale(st.ui_scale)
theme.apply(app)

w = MainWindow(recipe=Recipe(name="t"), source=open_source("synthetic", fps=0), settings=st)
w.showNormal()
w.resize(1920, 1020)
w.new_recipe()
# 造一张图并打开（模拟用户流程）
d = os.path.join("out", "preview17")
os.makedirs(d, exist_ok=True)
img = np.zeros((700, 1200, 3), np.uint8)
img[300:340, 100:1100] = 200
img[420:470, 200:1000] = 240
p = os.path.join(d, "样张.png")
imwrite(p, img)
w._load_image_path(p)

# 给第 2 项一个大框，分 10 个小框（5x2）
w.recipe.items[1].roi = Rect(150, 280, 900, 220)
w.list_items.setCurrentRow(1)
w._box_dlg_item = w.recipe.items[1]
w.sp_nudge.setValue(1)
w.recipe.items[1].params["grid_cols"] = 5
w.recipe.items[1].params["grid_rows"] = 2
w._gen_cells()
w._sync_view()
w._refresh_still()
app.processEvents()

print("小框数量:", len(w.recipe.items[1].params.get("cells") or []))
print("cells[0..2]:", (w.recipe.items[1].params.get("cells") or [])[:3])
print("画面尺寸:", w.view.frame_size())
w.grab().save(os.path.join("out", "ui_preview_v17.png"))
print("截图 ok:", os.path.exists(os.path.join("out", "ui_preview_v17.png")))
w.close()
