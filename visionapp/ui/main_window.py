"""VisionApp 主界面（多机位版）：6 路画面同屏 + 单机位编辑 + 整线判定。"""

from __future__ import annotations

import math
import time
import os
from typing import Any

import cv2
import numpy as np
from ..qtcompat import Qt, QTimer
from ..qtcompat import QApplication
from ..qtcompat import QAction, QColor
from ..qtcompat import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QMenu, QToolButton,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..align import ACTION_LABELS, align_rects
from ..alarm import Alarm, AlarmConfig
from ..calibration import Calibration
from ..core import Frame, Rect
from ..security import ROLE_ENGINEER, ROLE_LABELS, ROLE_OPERATOR, SecurityConfig
from ..ops import REGISTRY, Item
from ..paths import ensure_data_dir
from ..outputs import OutputConfig, OutputManager
from ..recipe import Pipeline, Recipe
from ..recorder import Recorder, RecorderConfig
from ..runner import MODE_CONTINUOUS, MODE_LABELS, MODE_SINGLE, Runner
from ..settings import AppSettings
from ..tuning import rule_preview_text, threshold_warnings
from ..sources import FrameSource, open_source
from ..stations import (
    MERGE_LABELS, MERGE_RULES, LineConfig, Station, StationConfig, StationManager,
)
from . import theme
from .dialogs import (
    CalibrationDialog, OutputAlarmDialog, RecordSettingsDialog, SourceDialog, build_source,
)
from .image_view import ImageView
from .line_dialog import LineConfigDialog
from .recipe_manager import RecipeManagerDialog
from .security_dialog import LoginDialog, PasswordDialog
from .station_tile import StationTile

BASE_DIR = ensure_data_dir()   # 源码运行=项目根；exe 运行=exe 所在目录（含配置/配方/结果）

OP_SCHEMA: dict[str, list[tuple[str, str, Any, str]]] = {
    "find_blobs": [
        ("threshold_mode", "choice:dark,light", "dark", "阈值模式（黑色=找黑块，白色=找白块）"),
        ("threshold_low", "int", 111, "下阈值（区间，1~254；与上阈值一起用）"),
        ("threshold_high", "int", 255, "上阈值（区间，1~254）"),
        ("threshold", "int", 128, "单阈值（仅当上下阈值都留空时生效）"),
        ("min_width", "int", 2, "最小宽度（像素）"),
        ("min_height", "int", 2, "最小高度（像素）"),
            ("pick_zone",
         "choice:白色中上,黑色中上,白色中下,黑色中下,白色左侧,白色右侧,黑色左侧,黑色右侧,白色顶部,黑色顶部,白色底部,黑色底部,中心",
         "白色中上",
         "抓取位置（黑/白 + 区位：底部/中下/中上/左侧…）"),
        ("pick_offset", "float", 0.0, "抓取位置·额外偏移像素（0=正好在边界）"),
        ("show_pick", "choice:off,on", "off", "画出抓取点（调抓取位置时开）"),
        ("show_mask", "choice:off,on", "off", "显示阈值屏蔽区（红色=被排除的像素）"),
        ("blur", "int", 5, "高斯模糊核（0=不做）"),
        ("morph_kernel", "int", 5, "形态学核（0=不做）"),
        ("min_area", "float", 500.0, "最小面积"),
        ("max_area", "float", 50000.0, "最大面积"),
    ],
    "distance_pp": [
        ("from_item", "item_choice", "", "起始检测框（从第几号框量）"),
        ("to_item", "item_choice", "", "终止检测框（量到第几号框）"),
        ("axis", "choice:x,y,dist", "x", "检测项目（x=X方向距离 y=Y方向距离 dist=直线距离）"),
        ("src", "str", "pins", "依赖的斑块项 id"),
        ("blob_a", "int", 0, "起始斑块序号"),
        ("blob_b", "int", 1, "目标斑块序号"),
        ("mm_per_px", "float", 0.0, "像素→毫米（0=用配方标定）"),
        ("use_pick", "choice:off,on", "off", "用抓取位置的点测距（on=不用中心）"),
    ],
    "match_template": [
        ("template", "file", "", "模板图片路径"),
        ("min_score", "float", 0.8, "最低相似度"),
    ],
    "area_of_blob": [
        ("src", "str", "pins", "依赖的斑块项 id"),
        ("index", "int", 0, "斑块序号"),
        ("mm2_per_px2", "float", 0.0, "面积换算系数（0=用配方标定）"),
    ],
    "angle_ppp": [
        ("p1_item", "item_choice", "", "第 1 个检测框（顶点）"),
        ("p2_item", "item_choice", "", "第 2 个检测框（角点）"),
        ("p3_item", "item_choice", "", "第 3 个检测框"),
        ("use_pick", "choice:off,on", "on", "用抓取位置的点算角度"),
        ("points", "str", "", "三点坐标 x1,y1;x2,y2;x3,y3"),
    ],
}

JUDGE_KINDS = [("range", "区间"), ("design", "设计值±容差"), ("max", "上限"), ("min", "下限")]


# 算子中文名（现场看得懂）
_OP_CN = {
    "find_blobs": "找斑块",
    "distance_pp": "两点距离",
    "match_template": "模板匹配",
    "area_of_blob": "斑块面积",
    "angle_ppp": "三点角度",
}


def _op_label(item) -> str:
    """检测项的中文名：优先用配方里起的名字，其次算子中文名。"""
    name = (getattr(item, "name", "") or "").strip()
    if name:
        return name
    return _OP_CN.get(getattr(item, "type", ""), getattr(item, "type", "检测项"))


def _param_summary(item) -> str:
    """把关键参数压成一句话（阈值/面积/设计值/基准）。"""
    p = getattr(item, "params", {}) or {}
    j = getattr(item, "judge", None) or {}
    bits: list[str] = []
    if "threshold_low" in p and "threshold_high" in p:
        bits.append(f"阈值{p['threshold_low']}~{p['threshold_high']}")
    elif "threshold" in p:
        bits.append(f"阈值{p['threshold']}")
    if p.get("min_area") is not None and p.get("max_area") is not None:
        bits.append(f"面积{p['min_area']}~{p['max_area']}")
    if j.get("kind") == "design" and j.get("design") is not None:
        tol = max(abs(float(j.get("tol_upper") or 0)), abs(float(j.get("tol_lower") or 0)))
        bits.append(f"设计{j['design']:g}±{tol:g}")
    elif j.get("kind") == "range" and j.get("min") is not None and j.get("max") is not None:
        bits.append(f"{j['min']:g}~{j['max']:g}")
    if p.get("serial"):
        bits.append(str(p["serial"]))
    return "  ".join(bits)


def _clamp_rect(rect, fw: int, fh: int):
    """把 ROI 夹进画面内（超出画面的部分被裁掉；完全在外就搬回画面内）。"""
    if rect is None or rect.is_line():
        return rect
    x = max(0, min(int(rect.x), max(0, fw - 2)))
    y = max(0, min(int(rect.y), max(0, fh - 2)))
    w = max(2, min(int(rect.w), fw - x))
    h = max(2, min(int(rect.h), fh - y))
    if (x, y, w, h) == (rect.x, rect.y, rect.w, rect.h):
        return rect
    return Rect(x, y, w, h)


def _clamp_cell(cell, fw: int, fh: int):
    x, y, w, h = [int(v) for v in cell]
    x = max(0, min(x, max(0, fw - 2)))
    y = max(0, min(y, max(0, fh - 2)))
    w = max(2, min(w, fw - x))
    h = max(2, min(h, fh - y))
    return [x, y, w, h]


class MainWindow(QMainWindow):
    # ------------------------------------------------------------------ 构造
    def __init__(self, recipe: Recipe | None = None, source: FrameSource | None = None,
                 path: str | None = None, max_frames: int = 0,
                 settings: AppSettings | None = None, line: LineConfig | None = None,
                 line_path: str | None = None):
        super().__init__()
        self.settings = settings or AppSettings.load(
            os.path.join(BASE_DIR, "config", "app_settings.json"))
        self.max_frames = max_frames
        self.recipes_dir = os.path.join(BASE_DIR, "recipes")
        self.line_path = line_path or os.path.join(BASE_DIR, "config", "line.json")
        self._building = False
        self.active = 0
        self.tiles: list[StationTile] = []
        self._active_path: str = path or ""
        self._calib_points: list[tuple[float, float]] = []
        self._edit_actions: list[QAction] = []
        self._edit_widgets: list[Any] = []
        self._undo_stack: list[dict] = []      # 撤销栈：每步存一份配方快照
        self._cur_id: str | None = None        # 当前检测项（按 id，列表可能隐藏该项）
        self._user_cleared = False             # 用户刚点空白取消选中

        self.security = SecurityConfig.from_dict(self.settings.security_cfg)
        self.role = ROLE_ENGINEER
        self.merged_alarm = Alarm(AlarmConfig.from_dict(self.settings.alarm_cfg))

        self.manager = self._make_manager(line, recipe, source, path)
        self.setWindowTitle("JD.vision   v72  (2026-11-19)")
        self.resize(1720, 1000)
        self.showMaximized()          # 现场要求：打开就最大化

        self._build_ui()
        self._refresh_station_tabs()
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()
        self.role = self._initial_role()
        self._apply_role()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(33)

        # 磁盘守护：状态栏常驻显示剩余空间，低于阈值提醒（怕塞满卡机）
        from ..diskguard import DiskGuard

        self.lbl_disk = QLabel("硬盘 --")
        theme.set_class(self.lbl_disk, "mono")
        self.lbl_disk.setToolTip("结果目录的硬盘剩余空间；低于阈值会提醒并自动清理旧图")
        self.statusBar().addPermanentWidget(self.lbl_disk)
        self.disk_guard = DiskGuard(
            root=self._results_dir(),
            min_free_gb=float(self.settings.get("disk_min_free_gb", 5.0) or 5.0),
            critical_free_gb=float(self.settings.get("disk_critical_free_gb", 1.0) or 1.0),
            max_results_gb=float(self.settings.get("results_max_gb", 0.0) or 0.0),
            keep_days=int(self.settings.get("ng_keep_days", 30) or 0),
        )
        self._disk_alert_ts = 0.0
        self.disk_timer = QTimer(self)
        self.disk_timer.timeout.connect(self._check_disk)
        self.disk_timer.start(60_000)
        QTimer.singleShot(1500, self._check_disk)
        QTimer.singleShot(1200, self._ensure_preview)

    def _ensure_preview(self) -> None:
        """相机就绪后自动开"连续采集预览"：不用点开始也能看到实时画面。"""
        st = self.station
        # 图片模式（打开的是本地图片）不跑连续预览，否则图片会一直循环切换
        if getattr(self, "_image_mode", False):
            return
        if str((getattr(st.cfg, "source", {}) or {}).get("kind", "")) == "folder":
            return
        if st.source is None or st.runner.running:
            return
        try:
            st.runner.set_auto_detect(False)   # 预览：只出画面，不检测不计数
            st.runner.start("continuous")
            self.statusBar().showMessage("相机已连接，实时画面已开始（不用点开始）", 6000)
        except Exception as exc:                       # noqa: BLE001
            self.statusBar().showMessage(f"预览启动失败：{exc}", 6000)

    def _results_dir(self) -> str:
        root = self.settings.get("results_root", "results")
        return root if os.path.isabs(root) else os.path.join(BASE_DIR, root)

    def _check_disk(self) -> None:
        """看硬盘：快满就提醒 + 自动清旧图（避免写满把机器拖卡）。"""
        try:
            g = self.disk_guard
            g.keep_days = int(self.settings.get("ng_keep_days", 30) or 0)
            g.min_free_gb = float(self.settings.get("disk_min_free_gb", 5.0) or 0)
            g.critical_free_gb = float(self.settings.get("disk_critical_free_gb", 1.0) or 0)
            g.max_results_gb = float(self.settings.get("results_max_gb", 0.0) or 0.0)
            g.root = self._results_dir()
            level, text = g.check()
        except Exception as exc:                            # noqa: BLE001
            self.lbl_disk.setText("硬盘检查失败")
            return
        self.lbl_disk.setText(text)
        theme.set_state(self.lbl_disk, "ng" if level == "crit" else ("warn" if level == "warn" else "idle"))
        now = time.time()
        if level in ("warn", "crit") and now - getattr(self, "_disk_alert_ts", 0) > 600:
            self._disk_alert_ts = now
            if level == "crit":
                QMessageBox.warning(
                    self, "硬盘空间不足",
                    text + "\n\n建议：管理 ▾ → 记录设置… 把【NG 图保留】改小（比如 7 天），"
                            "或换一个更大的盘做结果目录。\n"
                            "空间继续不足时程序会自动停止存图（只写 CSV），不会卡死。")
            else:
                self.statusBar().showMessage(text, 10000)

    def _initial_role(self) -> str:
        if self.security.require_login and self.security.has_password:
            return ROLE_OPERATOR
        return ROLE_ENGINEER

    def _make_manager(self, line, recipe, source, path) -> StationManager:
        if line is None:
            src_cfg = dict(self.settings.get("last_source", {"kind": "synthetic"}))
            if source is not None:
                src_cfg = self._source_cfg_of(source) or src_cfg
            line = LineConfig(
                name="单机位",
                merge_rule="all_ok",
                stations=[StationConfig(id=1, name="机位1", enabled=True,
                                        source=src_cfg,
                                        recipe=path or "")],
            )
        manager = StationManager(line, BASE_DIR, self._settings_getter)
        if source is not None and manager.stations:
            st = manager.stations[0]
            st.source = source
            st.runner.set_source(source)
            if recipe is not None:
                st.set_recipe(recipe, path or "")
        return manager

    def _settings_getter(self, key: str, default: Any = None) -> Any:
        mapping = {
            "recorder_cfg": self.settings.recorder_cfg,
            "output_cfg": self.settings.output_cfg,
            "alarm_cfg": self.settings.alarm_cfg,
        }
        return mapping.get(key, default)

    @staticmethod
    def _source_cfg_of(source: FrameSource) -> dict[str, Any] | None:
        cls = source.__class__.__name__
        if cls == "SyntheticSource":
            return {"kind": "synthetic"}
        if cls == "FolderSource":
            return {"kind": "folder", "path": getattr(source, "path", "")}
        if cls == "UvcSource":
            return {"kind": "uvc", "index": getattr(source, "index", 0)}
        if cls == "RtspSource":
            return {"kind": "rtsp", "url": getattr(source, "url", "")}
        if cls == "MindVisionSource":
            return {"kind": "mindvision", "index": getattr(source, "index", 0),
                    "serial": getattr(source, "serial", "")}
        return None

    # ------------------------------------------------------------------ 属性
    @property
    def station(self) -> Station:
        return self.manager.stations[self.active]

    @property
    def recipe(self) -> Recipe:
        return self.station.recipe

    @recipe.setter
    def recipe(self, value: Recipe) -> None:
        self.station.set_recipe(value, self.station.recipe_path)

    @property
    def pipeline(self) -> Pipeline:
        return self.station.pipeline

    @pipeline.setter
    def pipeline(self, value: Pipeline) -> None:
        self.station.pipeline = value

    @property
    def runner(self) -> Runner:
        return self.station.runner

    @property
    def recorder(self) -> Recorder:
        return self.station.recorder

    @property
    def outputs(self) -> OutputManager:
        return self.station.outputs

    @property
    def merged_alarm_cfg(self) -> AlarmConfig:
        return AlarmConfig.from_dict(self.settings.alarm_cfg)

    @property
    def source(self):
        return self.station.source

    @source.setter
    def source(self, value) -> None:
        self.station.source = value
        self.station.runner.set_source(value)

    @property
    def view(self) -> ImageView:
        return self.tiles[self.active].view

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        tb = self.addToolBar("main")
        tb.setMovable(False)

        def act(text: str, slot) -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        # 配方组
        a_new = act("新建模板", self.new_recipe)
        act_open = act("打开模板", self.open_recipe)
        a_save = act("保存模板", self.save_recipe)
        a_saveas = act("模板另存为", self.save_recipe_as)
        self._edit_actions += [a_new, act_open, a_save, a_saveas]   # 模板操作要管理员
        tb.addSeparator()

        # 配置组
        tb.addWidget(QLabel(" 当前机位 "))
        self.cb_station = QComboBox()
        self.cb_station.setMinimumWidth(110)
        self.cb_station.currentIndexChanged.connect(self._on_station_changed)
        tb.addWidget(self.cb_station)

        # 管理组收进下拉菜单：字放大后工具条要省地方（配方管理/相机/记录/输出/产线/权限）
        btn_admin = QToolButton()
        btn_admin.setText(" 管理 ▾ ")
        btn_admin.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn_admin.setToolTip("配方管理 / 相机 / 通用设置 / 记录设置 / 输出报警")

        # NG 图库 / 登录：放到工具条上，一步就能点
        self.act_gallery = QAction("NG图库", self)
        self.act_gallery.setToolTip("查看历史 NG 图（任意目录都能打开），可载入复现")
        self.act_gallery.triggered.connect(self.open_ng_gallery)
        tb.addAction(self.act_gallery)

        self.act_login = QAction("登录", self)
        self.act_login.setToolTip("工程师登录 / 修改密码（密码默认 8888）")
        self.act_login.triggered.connect(self.open_security)
        tb.addAction(self.act_login)
        menu_admin = QMenu(btn_admin)
        a_mgr = menu_admin.addAction("配方管理…", self.open_recipe_manager)
        self._edit_actions.append(a_mgr)   # 配方管理也要管理员
        a_cam = menu_admin.addAction("相机…", self.open_source_dialog)
        a_rec = menu_admin.addAction("记录设置…", self.open_record_settings)
        a_out = menu_admin.addAction("输出/报警…", self.open_output_settings)
        menu_admin.addSeparator()
        a_box = menu_admin.addAction("检测框设置…", self.open_box_settings)
        a_trk = menu_admin.addAction("跟踪设置…", self.open_track_settings)
        a_gen = menu_admin.addAction("通用设置…", self.open_general_settings)
        a_ov = menu_admin.addAction("画面显示…（字号/位置）", self.open_overlay_settings)
        a_img = menu_admin.addAction("打开图片…（文件/文件夹）", self.open_image_file)


        btn_admin.setMenu(menu_admin)
        tb.addWidget(btn_admin)

        # 第二行：画框 / 跟踪 / 标定（放大字号后第一行放不下，单独一行）
        self.addToolBarBreak()
        tb2 = self.addToolBar("edit")
        tb2.setMovable(False)
        tb2.setObjectName("toolbarEdit")
        self._edit_actions += [a_cam, a_rec, a_out]

        # 批量对齐：全部平铺在工具条上（点一下就对齐，不用先开下拉）
        self._align_actions = []
        for _act_name, _label in ACTION_LABELS.items():
            _a = QAction(_label, self)
            _a.setToolTip(f"多选后点这里：{_label}")
            _a.triggered.connect(lambda _=False, name=_act_name: self.batch_align(name))
            tb2.addAction(_a)
            self._align_actions.append(_a)
        self.act_undo_align = act(" 撤销 ", self.undo_last)
        tb2.addAction(self.act_undo_align)
        self._edit_actions += list(self._align_actions) + [self.act_undo_align]
        # 微调：上下左右箭头（步长可选 1~20）
        self.sp_nudge = QSpinBox()
        self.sp_nudge.setRange(1, 20)
        self.sp_nudge.setValue(1)
        self.sp_nudge.setSuffix(" px")
        self.sp_nudge.setToolTip("每次微调移动多少像素（1~20）")
        self.sp_nudge.setFixedWidth(90)
        tb2.addWidget(self.sp_nudge)
        for _label, _dx, _dy, _name in (("←", -1, 0, "nudge_left"), ("→", 1, 0, "nudge_right"),
                                        ("↑", 0, -1, "nudge_up"), ("↓", 0, 1, "nudge_down")):
            _a = QAction(_label, self)
            _a.setToolTip("把选中的框按步长移动")
            _a.triggered.connect(lambda _=False, dx=_dx, dy=_dy: self.nudge_selected(dx, dy))
            setattr(self, "act_" + _name, _a)
            tb2.addAction(_a)
            self._edit_actions.append(_a)

        self.act_del_box = QAction("删除框", self)
        self.act_del_box.setToolTip("删除选中的检测框（检测项还在）")
        self.act_del_box.triggered.connect(self.delete_selected_boxes)
        tb2.addAction(self.act_del_box)

        self.act_sel_all = QAction("全部选中", self)
        self.act_sel_all.setToolTip("选中所有检测框")
        self.act_sel_all.triggered.connect(self.select_all_boxes)
        tb2.addAction(self.act_sel_all)

        self.act_del_all = QAction("全部删除", self)
        self.act_del_all.setToolTip("删除所有检测框（检测项还在）")
        self.act_del_all.triggered.connect(self.delete_all_boxes)
        tb2.addAction(self.act_del_all)

        self.act_rule_settings = QAction("规则设置", self)
        self.act_rule_settings.setToolTip("添加/修改检测规则：间距、Y方向距离、平整度、面积")
        self.act_rule_settings.triggered.connect(self.open_rule_settings)
        tb2.addAction(self.act_rule_settings)
        self._edit_actions.append(self.act_rule_settings)

        self.act_show_no = QAction("显示编号", self, checkable=True)
        self.act_show_no.setChecked(True)
        self.act_show_no.setToolTip("关掉后画面上不显示 1/L1 这些编号（图片自带编号时可关掉，避免看混）")
        self.act_show_no.toggled.connect(self._on_show_number_toggled)
        tb2.addAction(self.act_show_no)

        self.act_box_settings = QAction("检测框设置", self)
        self.act_box_settings.setToolTip("分割小框 / 抓取范围 / 检测区域 / 定位模板")
        self.act_box_settings.triggered.connect(self.open_box_settings)
        tb2.addAction(self.act_box_settings)
        self._edit_actions.append(self.act_box_settings)
        # 删除框 / 全部选中 / 全部删除 也要登录后才能用
        self._edit_actions.extend([self.act_del_box, self.act_del_all])   # 全选不限权限

        self.act_select = QAction("选择/移动", self, checkable=True)
        self.act_select.setChecked(True)
        self.act_select.setToolTip("勾上：点框选中、框内拖动移动、空白拖=框选；取消：空白拖=画新框")
        self.act_select.toggled.connect(self._on_select_mode_toggled)
        tb2.addAction(self.act_select)

        self.act_multi_draw = QAction("矩形", self, checkable=True)
        self.act_multi_draw.setToolTip("勾上后：连续画矩形框，每拖一个就新建一个检测项")
        self.act_multi_draw.toggled.connect(self._on_multi_draw_toggled)
        tb2.addAction(self.act_multi_draw)
        self._edit_actions.append(self.act_multi_draw)
        self.act_line = act(" 直线 ", self.draw_line_roi)
        self._edit_actions.append(self.act_line)
        tb2.addAction(self.act_line)
        # 界面缩放（A-/A+/A0）已按现场要求移除；字号统一在主题里调大
        self.act_zoom_in = None
        self.act_zoom_out = None
        self.act_zoom_reset = None

        self.act_recheck = None      # 复检模式已按现场要求从界面移除（功能仍在）
        tb2.addSeparator()
        tb2.addSeparator()
        self.act_calib_value = act(" 标定(数值) ", self.calibrate_by_value)
        self._edit_actions.append(self.act_calib_value)
        tb2.addAction(self.act_calib_value)
        self.act_calib = QAction("标定(两点)", self, checkable=True)
        self.act_calib.toggled.connect(self._toggle_calib)
        tb2.addAction(self.act_calib)
        tb2.addSeparator()

        # 运行组单独一行（工具条第一行放不下时，开始/停止也不会被挤掉）
        self.addToolBarBreak()
        tb3 = self.addToolBar("run")
        tb3.setMovable(False)
        tb3.setObjectName("toolbarRun")

        def act2(text: str, slot) -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb3.addAction(a)
            return a

        tb3.addWidget(QLabel(" 模式 "))
        self.cb_mode = QComboBox()
        self.cb_mode.addItem("连续", MODE_CONTINUOUS)
        self.cb_mode.addItem("单次", MODE_SINGLE)
        tb3.addWidget(self.cb_mode)

        # 触发模式（相机硬件怎么出图）：随时切换，不用每次进管理菜单
        tb3.addWidget(QLabel(" 触发 "))
        self.cb_trigger = QComboBox()
        self.cb_trigger.addItem("连续采集", "continuous")
        self.cb_trigger.addItem("硬件触发", "hardware")
        self.cb_trigger.addItem("软触发", "software")
        self.cb_trigger.setToolTip(
            "相机出图方式：\n连续采集 = 一直出图（调机用）\n"
            "硬件触发 = 等外部信号才出一帧（产线用，没信号就不出图）")
        self.cb_trigger.currentIndexChanged.connect(self._on_trigger_changed)
        tb3.addWidget(self.cb_trigger)

        self.act_auto = QAction("自动检测", self, checkable=True)
        self.act_auto.setChecked(True)
        self.act_auto.setToolTip(
            "勾上：每帧都检测并计数\n取消：只显示画面不检测（调机看画面用），要看结果点【单帧】")
        self.act_auto.toggled.connect(self._on_auto_detect_toggled)
        tb3.addAction(self.act_auto)
        self.lbl_run_state = QLabel("○ 已停止")
        self.lbl_run_state.setStyleSheet("color:#8fa3b8;font-weight:bold;padding:0 8px;")
        tb3.addWidget(self.lbl_run_state)
        self.act_start_btn = act2(" ▶ 开始 ", self.start_run)
        self.act_start_btn.setShortcut("F5")
        self.act_start_btn.setToolTip("开始 (F5)")
        tb3.widgetForAction(self.act_start_btn).setObjectName("btnStart")
        self.act_stop_btn = act2(" ■ 停止 ", self.stop_run)
        self.act_stop_btn.setShortcut("F6")
        self.act_stop_btn.setToolTip("停止 (F6)")
        tb3.widgetForAction(self.act_stop_btn).setObjectName("btnStop")
        a_step = act2(" 检测一次 ", self.step_once)
        a_step.setShortcut("F7")
        a_step.setToolTip("检测一次 (F7)：不管有没有开自动检测，都立刻取一帧并检测一次（硬件触发没信号也能用）")
        act2(" 清空计数 ", self.reset_stats)

        # 去重：act() 默认把动作加到第一行，本意属于第二行的要从第一行摘掉
        _secondary = set(tb2.actions()) | set(tb3.actions())
        for _a in list(tb.actions()):
            if _a in _secondary:
                tb.removeAction(_a)

        # 工具条溢出按钮给个可见的文字（字放大后部分按钮会进"更多"里）
        for _bar in (tb, tb2, tb3):
            _ext = _bar.findChild(QToolButton, "qt_toolbar_ext_button")
            if _ext is not None:
                _ext.setText(" » ")
                _ext.setToolTip("更多（放不下时：跟踪框 / 标定 等在这里）")
                _ext.setVisible(True)

        split = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：机位画面网格
        grid_host = QWidget()
        self.grid = QGridLayout(grid_host)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        split.addWidget(scroll)

        # 右侧：编辑面板
        right = self._build_editor_panel()
        self.editor_panel = right
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setWidget(right)
        split.addWidget(right_scroll)
        split.setSizes([1180, 500])
        self.setCentralWidget(split)
        self.statusBar().showMessage("就绪")

        self.lbl_role = QLabel("")
        self.lbl_role.setObjectName("lblRole")
        self.statusBar().addPermanentWidget(self.lbl_role)

        # 整线判定条
        self.lbl_merged = QLabel("待机中")
        self.lbl_merged.setObjectName("lblMerged")
        theme.set_class(self.lbl_merged, "mono")
        theme.set_state(self.lbl_merged, "idle")
        self.statusBar().addWidget(self.lbl_merged)      # 左下角

    def _build_editor_panel(self) -> QWidget:
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 6, 6, 6)

        box_stats = QGroupBox("当前机位统计")
        grid = QGridLayout(box_stats)
        self.lbl_verdict = QLabel("待机")
        self.lbl_verdict.setObjectName("lblVerdict")
        self.lbl_ok = QLabel("0")
        self.lbl_ng = QLabel("0")
        self.lbl_yield = QLabel("--")
        self.lbl_total = QLabel("0")
        for lab in (self.lbl_verdict, self.lbl_ok, self.lbl_ng, self.lbl_yield, self.lbl_total):
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.set_state(self.lbl_verdict, "idle")
        for lab in (self.lbl_ok, self.lbl_ng, self.lbl_yield, self.lbl_total):
            theme.set_class(lab, "statValue")
        def stat_key(text: str) -> QLabel:
            lab = QLabel(text)
            theme.set_class(lab, "statKey")
            return lab

        grid.addWidget(self.lbl_verdict, 0, 0, 1, 3)
        grid.addWidget(stat_key("OK"), 1, 0); grid.addWidget(stat_key("NG"), 1, 1); grid.addWidget(stat_key("良率"), 1, 2)
        grid.addWidget(self.lbl_ok, 2, 0); grid.addWidget(self.lbl_ng, 2, 1); grid.addWidget(self.lbl_yield, 2, 2)
        grid.addWidget(stat_key("总数"), 3, 0)
        grid.addWidget(self.lbl_total, 4, 0)
        self.lbl_bright = QLabel("亮度 --")
        theme.set_class(self.lbl_bright, "mono")
        grid.addWidget(self.lbl_bright, 6, 0, 1, 3)
        self.lbl_calib = QLabel("标定：未标定")
        self.lbl_calib.setObjectName("lblCalib")
        theme.set_class(self.lbl_calib, "mono")
        # 统计已移到画面左上角：右侧不再显示（先挂到布局再隐藏，避免被回收）
        rv.addWidget(box_stats)
        box_stats.hide()

        box_items = QGroupBox("检测项")
        il = QVBoxLayout(box_items)
        self.list_items = QListWidget()
        self.list_items.currentRowChanged.connect(self._on_item_selected)
        il.addWidget(self.list_items, 1)
        row = QHBoxLayout()
        for text, slot in (("新增", self.add_item), ("复制", self.dup_item), ("删除", self.del_item),
                           ("↑", lambda: self.move_item(-1)), ("↓", lambda: self.move_item(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        il.addLayout(row)
        self.box_items = box_items
        rv.addWidget(box_items, 1)
        box_items.hide()            # 现场要求：检测项面板去掉（选中靠画面点框）

        # 跟踪（补偿放料偏差）面板已移除：现在用 管理 ▾ → 跟踪设置… 选一个检测框做基准

        box_base = QGroupBox("基本")
        fb = QFormLayout(box_base)
        self.ed_id = QLineEdit()
        self.ed_name = QLineEdit()
        self.cb_type = QComboBox()
        self.cb_type.addItem("（未设置）", "")
        for _key in sorted(REGISTRY.keys()):
            self.cb_type.addItem(_OP_CN.get(_key, _key), _key)
        self.cb_type.currentIndexChanged.connect(
            lambda _i: self._on_type_changed(self.cb_type.currentData()))
        fb.addRow("名称", self.ed_name)
        fb.addRow("检测方式", self.cb_type)
        self.box_base = box_base
        rv.addWidget(box_base)
        box_base.hide()             # 现场要求：基本（名称/检测方式）也去掉

        # ---- 检测设置（现场要的固定几项）----
        self.box_detect = QGroupBox("检测设置")
        dg = QGridLayout(self.box_detect)
        dg.addWidget(QLabel("起始框"), 0, 0)
        self.cb_det_from = QComboBox()
        dg.addWidget(self.cb_det_from, 0, 1)
        dg.addWidget(QLabel("结束框"), 0, 2)
        self.cb_det_to = QComboBox()
        dg.addWidget(self.cb_det_to, 0, 3)

        dg.addWidget(QLabel("下阈值"), 1, 0)
        self.sl_det_lo = QSlider(Qt.Orientation.Horizontal)
        self.sl_det_lo.setRange(0, 255); self.sl_det_lo.setValue(111)
        dg.addWidget(self.sl_det_lo, 1, 1, 1, 2)
        self.sp_det_lo = QSpinBox(); self.sp_det_lo.setRange(0, 255); self.sp_det_lo.setValue(111)
        dg.addWidget(self.sp_det_lo, 1, 3)

        dg.addWidget(QLabel("上阈值"), 2, 0)
        self.sl_det_hi = QSlider(Qt.Orientation.Horizontal)
        self.sl_det_hi.setRange(0, 255); self.sl_det_hi.setValue(255)
        dg.addWidget(self.sl_det_hi, 2, 1, 1, 2)
        self.sp_det_hi = QSpinBox(); self.sp_det_hi.setRange(0, 255); self.sp_det_hi.setValue(255)
        dg.addWidget(self.sp_det_hi, 2, 3)

        # 滑条与数字联动 + 实时预览
        self.sl_det_lo.valueChanged.connect(self.sp_det_lo.setValue)
        self.sp_det_lo.valueChanged.connect(self.sl_det_lo.setValue)
        self.sl_det_hi.valueChanged.connect(self.sp_det_hi.setValue)
        self.sp_det_hi.valueChanged.connect(self.sl_det_hi.setValue)
        self.sl_det_lo.valueChanged.connect(lambda _v: self._on_detect_threshold_live())
        self.sl_det_hi.valueChanged.connect(lambda _v: self._on_detect_threshold_live())

        dg.addWidget(QLabel("抓取方向"), 3, 0)
        self.cb_pick_dir = QComboBox()
        for _t in ("白色中上", "白色中下", "黑色中上", "黑色中下",
                   "白色顶部", "白色底部", "黑色顶部", "黑色底部",
                   "白色左侧", "白色右侧", "黑色左侧", "黑色右侧"):
            self.cb_pick_dir.addItem(_t)
        dg.addWidget(self.cb_pick_dir, 3, 1, 1, 3)

        self.ck_det_force_h = QCheckBox("强制水平"); self.ck_det_force_h.setChecked(True)
        self.ck_det_force_v = QCheckBox("强制垂直"); self.ck_det_force_v.setChecked(True)
        self.ck_det_max_area = QCheckBox("最大区域"); self.ck_det_max_area.setChecked(True)
        dg.addWidget(self.ck_det_force_h, 4, 0)
        dg.addWidget(self.ck_det_force_v, 4, 1)
        dg.addWidget(self.ck_det_max_area, 4, 2)
        for _ck, _key in ((self.ck_det_force_h, "force_h"), (self.ck_det_force_v, "force_v"),
                          (self.ck_det_max_area, "max_area_on")):
            _ck.toggled.connect(lambda on, k=_key: self._on_detect_flag(k, on))

        btn_clear_th = QPushButton("清屏（恢复原阈值）")
        btn_clear_th.clicked.connect(self._clear_detect_threshold)
        dg.addWidget(btn_clear_th, 5, 0, 1, 2)
        btn_set_th = QPushButton("设定阈值")
        btn_set_th.clicked.connect(self._apply_detect_threshold)
        dg.addWidget(btn_set_th, 5, 2, 1, 2)
        rv.addWidget(self.box_detect)
        rv.removeWidget(self.box_detect)
        rv.insertWidget(0, self.box_detect)          # 放到最上面（补上删掉的面板位置）

        self.box_rule = QGroupBox("规则设定")
        rg = QGridLayout(self.box_rule)
        rg.addWidget(QLabel("起始框"), 0, 0)
        self.cb_rule_from = QComboBox()
        rg.addWidget(self.cb_rule_from, 0, 1)
        rg.addWidget(QLabel("结束框"), 0, 2)
        self.cb_rule_to = QComboBox()
        rg.addWidget(self.cb_rule_to, 0, 3)
        self.ck_rule_inc = QCheckBox("自增")
        self.ck_rule_inc.setChecked(False)              # 默认不勾，需要时手动勾
        self.ck_rule_inc.setToolTip("勾上后：每添加一条规则，起始框/结束框各 +1（X 方向时基准也 +1）")
        rg.addWidget(self.ck_rule_inc, 0, 4)

        # 检测方式单独一行（在基准框上面，不与基准框并排）
        rg.addWidget(QLabel("检测方式"), 1, 0)
        self.cb_rule_kind = QComboBox()
        for _k, _t in (("x", "X/水平方向"), ("y", "Y/垂直方向"), ("waviness", "参差度"),
                       ("area", "面积"), ("baseline", "生成基准线")):
            self.cb_rule_kind.addItem(_t, _k)
        rg.addWidget(self.cb_rule_kind, 1, 1, 1, 3)

        rg.addWidget(QLabel("基准框"), 2, 0)
        self.cb_rule_base = QComboBox()
        self.cb_rule_base.addItem("无", "")
        rg.addWidget(self.cb_rule_base, 2, 1, 1, 3)

        rg.addWidget(QLabel("设定值"), 3, 0)
        self.sp_rule_target = QDoubleSpinBox(); self.sp_rule_target.setRange(-1e9, 1e9); self.sp_rule_target.setDecimals(3)
        rg.addWidget(self.sp_rule_target, 3, 1, 1, 3)
        rg.addWidget(QLabel("上公差"), 4, 0)
        self.sp_rule_tol_up = QDoubleSpinBox(); self.sp_rule_tol_up.setRange(-1e9, 1e9); self.sp_rule_tol_up.setDecimals(3)
        self.sp_rule_tol_up.setValue(0.08)
        rg.addWidget(self.sp_rule_tol_up, 4, 1)
        rg.addWidget(QLabel("下公差"), 4, 2)
        self.sp_rule_tol_lo = QDoubleSpinBox(); self.sp_rule_tol_lo.setRange(-1e9, 1e9); self.sp_rule_tol_lo.setDecimals(3)
        self.sp_rule_tol_lo.setValue(-0.08)
        rg.addWidget(self.sp_rule_tol_lo, 4, 3)

        self.ck_rule_abs = QCheckBox("结果取绝对值")
        self.ck_rule_abs.setChecked(True)
        rg.addWidget(self.ck_rule_abs, 5, 0, 1, 4)

        rg.addWidget(QLabel("备注"), 6, 0)
        self.ed_rule_note = QLineEdit()
        self.ed_rule_note.setPlaceholderText("可填备注，随规则保存，方便排查")
        rg.addWidget(self.ed_rule_note, 6, 1, 1, 3)

        btn_add_rule = QPushButton("添加规则")
        btn_add_rule.clicked.connect(self._add_rule)
        rg.addWidget(btn_add_rule, 7, 0, 1, 2)
        btn_del_rule = QPushButton("删除规则")
        btn_del_rule.clicked.connect(self._del_rule)
        rg.addWidget(btn_del_rule, 7, 2, 1, 2)
        self.lbl_rules = QLabel("规则：（空）")
        theme.set_class(self.lbl_rules, "hint")
        _f = self.lbl_rules.font()
        _f.setPixelSize(16)
        self.lbl_rules.setStyleSheet("font-size:16px;")
        self.lbl_rules.setFont(_f)
        self.lbl_rules.setMinimumHeight(26)
        self.lbl_rules.setWordWrap(True)
        rg.addWidget(self.lbl_rules, 8, 0, 1, 4)
        self.cb_rule_kind.currentIndexChanged.connect(lambda _i: self._sync_rule_base_enabled())
        self.cb_pick_dir.currentTextChanged.connect(self._on_pick_dir_changed)
        rv.addWidget(self.box_rule)
        rv.removeWidget(self.box_rule)
        rv.insertWidget(1, self.box_rule)          # 紧跟【检测设置】

        box_params = QGroupBox("其它参数")
        self.form_params = QFormLayout(box_params)
        self.box_params = box_params
        rv.addWidget(box_params)

        box_judge = QGroupBox("判定")
        fj = QFormLayout(box_judge)
        self.cb_judge = QComboBox()
        for kind, label in JUDGE_KINDS:
            self.cb_judge.addItem(label, kind)
        self.sp_lo = QDoubleSpinBox()
        self.sp_hi = QDoubleSpinBox()
        self.sp_design = QDoubleSpinBox()
        self.sp_tol_up = QDoubleSpinBox()
        self.sp_tol_lo = QDoubleSpinBox()
        for sp in (self.sp_lo, self.sp_hi, self.sp_design, self.sp_tol_up, self.sp_tol_lo):
            sp.setRange(-1e9, 1e9)
            sp.setDecimals(4)
        self.sp_tol_up.setValue(0.08)
        self.sp_tol_lo.setValue(-0.08)
        self.ck_abs = QCheckBox("结果取绝对值")
        self.cb_judge.currentIndexChanged.connect(self._on_judge_changed)
        for _w in (self.sp_lo, self.sp_hi, self.sp_design, self.sp_tol_up, self.sp_tol_lo):
            _w.valueChanged.connect(self._on_judge_changed)
        self.ck_abs.toggled.connect(self._on_judge_changed)
        fj.addRow("类型", self.cb_judge)
        fj.addRow("下限", self.sp_lo)
        fj.addRow("上限", self.sp_hi)
        fj.addRow("设计值", self.sp_design)
        fj.addRow("容差上限", self.sp_tol_up)
        fj.addRow("容差下限", self.sp_tol_lo)
        fj.addRow("", self.ck_abs)
        self.lbl_rule = QLabel("规则：（未设置）")
        theme.set_class(self.lbl_rule, "hint")
        fj.addRow(self.lbl_rule)
        self._judge_form = fj
        self._sync_judge_rows("range")
        # ---- 批量阈值（右侧直接设，不弹小窗）----
        box_batch = QGroupBox("批量阈值（按检测框号）")
        bg = QGridLayout(box_batch)
        bg.addWidget(QLabel("起始框"), 0, 0)
        self.sp_th_from = QSpinBox()
        self.sp_th_from.setRange(1, 999)
        self.sp_th_from.setValue(1)
        bg.addWidget(self.sp_th_from, 0, 1)
        bg.addWidget(QLabel("结束框"), 0, 2)
        self.sp_th_to = QSpinBox()
        self.sp_th_to.setRange(1, 999)
        self.sp_th_to.setValue(1)
        bg.addWidget(self.sp_th_to, 0, 3)
        bg.addWidget(QLabel("下阈值"), 1, 0)
        self.sl_th_lo = QSlider(Qt.Orientation.Horizontal)
        self.sl_th_lo.setRange(1, 255)
        self.sl_th_lo.setValue(111)
        bg.addWidget(self.sl_th_lo, 1, 1, 1, 2)
        self.sp_th_lo = QSpinBox()
        self.sp_th_lo.setRange(1, 255)
        self.sp_th_lo.setValue(111)
        bg.addWidget(self.sp_th_lo, 1, 3)
        bg.addWidget(QLabel("上阈值"), 2, 0)
        self.sl_th_hi = QSlider(Qt.Orientation.Horizontal)
        self.sl_th_hi.setRange(1, 255)
        self.sl_th_hi.setValue(255)
        bg.addWidget(self.sl_th_hi, 2, 1, 1, 2)
        self.sp_th_hi = QSpinBox()
        self.sp_th_hi.setRange(1, 255)
        self.sp_th_hi.setValue(255)
        bg.addWidget(self.sp_th_hi, 2, 3)
        btn_apply_th = QPushButton("应用阈值")
        btn_apply_th.clicked.connect(self.apply_batch_threshold)
        bg.addWidget(btn_apply_th, 3, 0, 1, 4)
        self.sl_th_lo.valueChanged.connect(self.sp_th_lo.setValue)
        self.sp_th_lo.valueChanged.connect(self.sl_th_lo.setValue)
        self.sl_th_hi.valueChanged.connect(self.sp_th_hi.setValue)
        self.sp_th_hi.valueChanged.connect(self.sl_th_hi.setValue)
        self.box_batch = box_batch
        rv.addWidget(box_batch)
        box_batch.hide()            # 与【检测设置】重复，隐藏

        self.box_judge = box_judge
        rv.addWidget(box_judge)
        box_judge.hide()          # 判定模块先隐藏（现场要求稍后重做）

        box_res = QGroupBox("检测结果")
        rl = QVBoxLayout(box_res)
        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(
            ["备注", "对象", "基准", "设计值", "实测", "判定结果"])
        self.tbl.setWordWrap(False)
        _f = self.tbl.font()
        _f.setPixelSize(13)
        self.tbl.setFont(_f)
        self.tbl.horizontalHeader().setDefaultSectionSize(96)
        for _c, _w in enumerate((110, 64, 64, 168, 84, 74)):
            self.tbl.setColumnWidth(_c, _w)
        self.tbl.verticalHeader().setDefaultSectionSize(30)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.tbl)
        rv.addWidget(box_res, 1)
        self.box_params.hide()      # 其它参数（旧参数表）不显示
        return right

    # ------------------------------------------------------------------ 机位
    def prune_unopened_stations(self) -> int:
        """把"没打开成功"的机位裁掉：只插 1 台相机就只显示 1 个画面。"""
        try:
            total = len(self.manager.stations)
            good = [st for st in self.manager.stations if getattr(st, "source", None) is not None]
            if not good or len(good) == total:
                return total
            for i, st in enumerate(good, start=1):
                try:
                    st.cfg.id = i
                except Exception:                       # noqa: BLE001
                    pass
            self.manager.stations = good
            self.active = 0
            self._refresh_station_tabs()
            self._refresh_item_list()
            self._sync_view()
            self._refresh_calib_label()
            self.statusBar().showMessage(
                f"检测到 {len(good)} 台可用相机，已只显示 {len(good)} 个画面", 6000)
            return len(good)
        except Exception:                               # noqa: BLE001
            return 0

    def _refresh_station_tabs(self) -> None:
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w is not None:
                w.setParent(None)
        self.tiles = []
        cols = 3 if len(self.manager.stations) > 2 else 1
        for idx, st in enumerate(self.manager.stations):
            tile = StationTile(idx, f"相机{idx + 1}")
            # 把该机位画面的交互信号接到主窗口（只对当前机位生效）
            tile.view.roiCreated.connect(lambda rect, i=idx: self._on_roi_created(rect) if i == self.active else None)
            tile.view.roiEdited.connect(lambda key, rect, i=idx: self._on_roi_edited(key, rect) if i == self.active else None)
            tile.view.roiSelected.connect(lambda key, i=idx: self._on_roi_selected(key) if i == self.active else None)
            tile.view.calibPointClicked.connect(
                lambda x, y, i=idx: self._on_calib_point(x, y) if i == self.active else None)
            tile.view.lineCreated.connect(
                lambda rect, i=idx: self._on_line_created(rect) if i == self.active else None)
            tile.view.cellsEdited.connect(
                lambda iid, rects, i=idx: self._on_cells_edited(iid, rects) if i == self.active else None)
            tile.view.areaCreated.connect(
                lambda rect, i=idx: self._on_area_created(rect) if i == self.active else None)
            tile.view.locatorCreated.connect(
                lambda rect, i=idx: self._on_locator_created(rect) if i == self.active else None)
            tile.view.itemMenuRequested.connect(
                lambda key, i=idx: self._defer_item_menu(key, i))
            tile.view.trackCreated.connect(
                lambda rect, i=idx: self._on_track_created(rect) if i == self.active else None)
            self.grid.addWidget(tile, idx // cols, idx % cols)
            self.tiles.append(tile)
        self._apply_overlay()

        self._building = True
        self.cb_station.clear()
        for i, st in enumerate(self.manager.stations):
            self.cb_station.addItem(f"相机{i + 1}")
        self.cb_station.setCurrentIndex(self.active)
        self._building = False

    def _on_station_changed(self, idx: int) -> None:
        self._sync_trigger_combo()
        if self._building or not (0 <= idx < len(self.tiles)):
            return
        self.active = idx
        for i, tile in enumerate(self.tiles):
            tile.set_active(i == idx)
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()

    def _on_merge_changed(self) -> None:
        self.manager.line.merge_rule = getattr(self, "cb_merge", None).currentData() if getattr(self, "cb_merge", None) else "all_ok"

    # ------------------------------------------------------------------ 权限
    def _apply_role(self) -> None:
        eng = self.role == ROLE_ENGINEER
        self.editor_panel.setEnabled(eng)
        for a in self._edit_actions:
            a.setEnabled(eng)
        for box in (getattr(self, "box_detect", None), getattr(self, "box_rule", None)):
            if box is not None:
                box.setEnabled(eng)
        self.act_calib.setEnabled(eng)
        self.lbl_role.setText(f"权限：{ROLE_LABELS[self.role]}")
        theme.set_state(self.lbl_role, "warn" if eng else "idle")

    def open_security(self) -> None:
        if self.role == ROLE_ENGINEER:
            dlg = PasswordDialog(self.security, self)
            dlg.setWindowTitle("权限设置（工程师模式）")
            if dlg.exec():
                self.settings.set("security", self.security.to_dict())
                self.settings.save()
                self.role = ROLE_ENGINEER if not self.security.require_login else ROLE_ENGINEER
                self._apply_role()
                self.statusBar().showMessage("权限设置已保存", 4000)
            return
        dlg = LoginDialog(self.security, self)
        if dlg.exec():
            self.role = ROLE_ENGINEER
            self._apply_role()
            self.statusBar().showMessage("已进入工程师模式", 4000)

    # ------------------------------------------------------------------ 产线配置
    def open_line_config(self) -> None:
        dlg = LineConfigDialog(self.manager.line, self.recipes_dir,
                               self.settings.output_cfg, self.settings.alarm_cfg, self)
        if not dlg.exec():
            return
        try:
            dlg.line.save(self.line_path)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.apply_line(dlg.line)
        self.statusBar().showMessage(f"产线配置已保存：{self.line_path}", 5000)

    def apply_line(self, line: LineConfig) -> None:
        """按新产线配置重建所有机位。"""
        try:
            self.manager.stop_all()
        except Exception:                              # noqa: BLE001
            pass
        try:
            self.manager.close_all()
        except Exception:                              # noqa: BLE001
            pass
        self.manager = StationManager(line, BASE_DIR, self._settings_getter)
        self.active = 0
        self._refresh_station_tabs()
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()

        self.setWindowTitle("JD.vision   v72  (2026-11-19)")
        errors = self.manager.open_all()
        for e in errors:
            self.statusBar().showMessage(f"机位打开失败：{e}", 8000)

    # ------------------------------------------------------------------ 运行
    def _update_run_buttons(self) -> None:
        """开始/停止按钮 + 状态灯：一眼看出当前是运行还是停止。"""
        running = bool(self.manager.running())
        try:
            self.act_start_btn.setEnabled(not running)
            self.act_stop_btn.setEnabled(running)
            self.lbl_run_state.setText("● 运行中" if running else "○ 已停止")
            self.lbl_run_state.setStyleSheet(
                "color:%s;font-weight:bold;padding:0 8px;"
                % ("#22c55e" if running else "#8fa3b8"))
        except Exception:                                # noqa: BLE001
            pass

    def start_run(self) -> None:
        for _st in self.manager.stations:
            _st.runner.set_auto_detect(bool(getattr(self, "act_auto", None) and self.act_auto.isChecked()))
        if self.manager.running():
            return
        if not any(st.source for st in self.manager.stations if st.cfg.enabled):
            QMessageBox.warning(self, "无法运行", "没有可用的相机/帧源，请先点“相机”连接")
            return
        self.merged_alarm.cfg = AlarmConfig.from_dict(self.settings.alarm_cfg)
        self.manager.start_all(self.cb_mode.currentData())
        self._update_run_buttons()
        self.statusBar().showMessage("● 已开始运行（整线）")

    def stop_run(self) -> None:
        self.manager.stop_all()
        self._update_run_buttons()
        self.statusBar().showMessage("已停止（实时画面仍保留为预览）", 4000)
        QTimer.singleShot(200, self._ensure_preview)
        self.statusBar().showMessage("已停止")

    def step_once(self) -> None:
        """检测一次：同步跑一遍流水线，结果直接进表并显示标注图（不依赖采集线程）。"""
        _was_running = bool(self.manager.running())
        if _was_running:
            try:
                self.manager.stop_all()          # 先暂停采集，避免和采集线程抢流水线
                self.statusBar().showMessage("已暂停采集，正在做单次检测…", 3000)
            except Exception:                    # noqa: BLE001
                pass
        shown = 0
        for st in self.manager.stations:
            if not (st.cfg.enabled and st.source is not None):
                continue
            st.open_recorder()
            st.open_outputs()
            # 硬件/软触发模式下可能一直等不到触发：先强制抓一帧
            src = st.source
            trig = str(getattr(src, "trigger", "") or "").lower()
            if trig in ("hardware", "software") and hasattr(src, "grab_once"):
                try:
                    src.grab_once(1500)
                except Exception as exc:                # noqa: BLE001
                    self.statusBar().showMessage(f"强制取图失败：{exc}", 6000)
            frame = getattr(self, "_raw_frame", None)
            if frame is None:
                frame = getattr(st.snapshot(), "last_frame", None)
            if frame is None:
                frame = getattr(self, "_last_view_frame", None)
            if frame is None:
                self.statusBar().showMessage("没有画面可检测：请先连相机出图", 5000)
                continue
            base = frame.bgr if hasattr(frame, "bgr") else frame
            pipe = st.pipeline
            if pipe is None:
                self.statusBar().showMessage("流水线未就绪", 4000)
                continue
            try:
                result, annotated = pipe.run(Frame.create(base), annotate=True)
            except Exception as exc:                    # noqa: BLE001
                self._log_crash("检测一次", exc)
                continue
            st.runner.publish(result, annotated, base)
            self._raw_frame = base
            try:
                self._diag(f"检测一次：frame={base.shape[1]}x{base.shape[0]} "
                           f"结果项={len(result.results)} 规则={len(self.recipe.rules or [])}")
            except Exception:                        # noqa: BLE001
                pass
            self._last_view_frame = base
            self.tiles[self.active].view.set_frame(annotated)
            self._fill_result_table(result.as_dict())
            self._show_pick_points(result)
            self._preview_until = time.time() + 3.0     # 3 秒内别被原始帧盖掉标注
            shown += 1
        if shown:
            self.statusBar().showMessage(f"检测一次完成：{shown} 个机位出结果", 4000)
        self._poll()

    def _show_pick_points(self, result) -> None:
        """把本次检测的抓取中心点画到画面上（品红十字）。"""
        pts = []
        try:
            for res in (result.results or {}).values():
                for b in getattr(res, "blobs", []) or []:
                    pick = getattr(b, "pick", None)
                    if pick is None:
                        pick = (getattr(b, "cx", None), getattr(b, "cy", None))
                    if pick[0] is None or pick[1] is None:
                        continue
                    pts.append((float(pick[0]), float(pick[1])))
            self.tiles[self.active].view.set_pick_points(pts)
            if pts:
                self.statusBar().showMessage(f"抓取中心 {len(pts)} 个（品红十字）", 4000)
        except Exception:                                # noqa: BLE001
            pass

    def reset_stats(self) -> None:
        for st in self.manager.stations:
            st.runner.reset_stats()

    def _poll(self) -> None:
        merged_frames = 0
        # 只有"取到新帧"才重绘，且整屏每秒最多 ~15 次；否则 5MP 图每 33ms 转一次会把界面拖死
        _now = time.time()
        _seqs = getattr(self, "_seq_cache", None)
        if _seqs is None:
            _seqs = self._seq_cache = {}
        draw_budget = _now - getattr(self, "_draw_ts", 0) > 0.14
        for idx, st in enumerate(self.manager.stations):
            snap = st.snapshot()
            tile = self.tiles[idx]
            seq = int(getattr(snap, "frame_seq", 0) or 0)
            changed = _seqs.get(idx) != seq
            _locked = time.time() < float(getattr(self, "_preview_until", 0) or 0)
            if snap.last_annotated is not None and changed and draw_budget and not (idx == self.active and _locked):
                _seqs[idx] = seq
                raw = getattr(snap, "last_frame", None)
                if raw is not None:
                    # 没在跑（硬件触发/停止）：显示原图，避免上一帧的红框被当成"野框"
                    if idx == self.active:
                        self._last_view_frame = raw
                        self._raw_frame = raw            # 检测/预览永远用这张原图
                        _fw = int(getattr(raw, "shape", [0, 0])[1] or 0)
                        _rw = int(self.recipe.frame_width or 0)
                        _max_r = 0
                        for _it in self.recipe.items:
                            if _it.roi is not None and not _it.roi.is_line():
                                _max_r = max(_max_r, int(_it.roi.x + _it.roi.w))
                        if _max_r and _max_r > _fw * 1.05:
                            # 框画在更大的画面上（例如 2592 宽模板拿到 1600 帧）：
                            # 按"最大框右边界"当原始宽度，整体缩到当前画面
                            self.recipe.frame_width = _max_r
                        _big = _rw and abs(_fw - _rw) / float(_rw) > 0.2
                        if _fw and (_big or (_max_r and _max_r > _fw * 1.05)):
                            try:
                                self.recipe.scale_to(_fw)
                                self._diag(f"缩放配方：{self.recipe.frame_width} ← frame {_fw}")
                                self._sync_view()
                                self.statusBar().showMessage(
                                    f"画面宽度 {self.recipe.frame_width} → {_fw}，检测框已按比例缩放", 5000)
                            except Exception:            # noqa: BLE001
                                pass
                        if _fw and not self.recipe.frame_width:
                            self.recipe.frame_width = _fw
                    img = raw
                else:
                    img = snap.last_annotated
                limit = 0 if idx == self.active else 1000   # 活动机位用原图，避免框错位
                if limit and img.shape[1] > limit:
                    sc = float(limit) / img.shape[1]
                    img = cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
                tile.view.set_frame(img)
            if not st.runner.running and st.source is not None:
                try:
                    _latest = getattr(st.source, "latest", lambda: None)()
                except Exception:                        # noqa: BLE001
                    _latest = None
                if _latest is not None and draw_budget:
                    _ls = int(getattr(st.source, "latest_seq", lambda: 0)() or 0)
                    if _seqs.get(("idle", idx)) != _ls:
                        _seqs[("idle", idx)] = _ls
                        self._raw_frame = _latest
                        if idx == self.active:
                            self._last_view_frame = _latest
                        tile.view.set_frame(_latest)
            _res = snap.last_result
            if _res and st.runner.running:
                try:
                    states = {k: bool(v.get("ok")) for k, v in (_res.get("items") or {}).items()}
                    tile.view.set_item_states(states)
                except Exception:                       # noqa: BLE001
                    pass
            elif not st.runner.running:
                # 静止/编辑状态：框一律绿色，避免被判 NG 的红框被当成"影子"
                tile.view.set_item_states({})
            tile.update_stats(snap, st.cfg.enabled)
            merged_frames = max(merged_frames, snap.total)
        if draw_budget:
            self._draw_ts = _now

        for i, tile in enumerate(self.tiles):
            tile.set_active(i == self.active)

        snap = self.station.snapshot()
        if snap.last_result:
            self._fill_result_table(snap.last_result)     # 非运行状态也把结果显示出来
        self._update_stats(snap)
        # 结果表最多 5 次/秒，且只在有结果时刷新（原来每 33ms 刷一遍，界面会卡）
        _now = time.time()
        if snap.last_result and _now - getattr(self, "_tbl_ts", 0) > 0.2:
            _items = (snap.last_result or {}).get("items") or {}
            if _items or self.tbl.rowCount() == 0:
                self._tbl_ts = _now
                self._fill_result_table(snap.last_result)

        merged = self.manager.merged_ok()
        totals = self.manager.totals()
        if merged is None:
            text = "整线：待机" if totals["total"] == 0 else "整线：各机位独立"
            theme.set_state(self.lbl_merged, "idle")
        else:
            text = f"整线：{'OK' if merged else 'NG'}   OK {totals['ok']} / NG {totals['ng']}"
            theme.set_state(self.lbl_merged, "ok" if merged else "ng")
        self.lbl_merged.setText(text)

        if merged is not None:
            cfg = self.merged_alarm_cfg
            if cfg.enabled and cfg.to_dict().get("on_merged", True):
                self.merged_alarm.cfg = cfg
                self.merged_alarm.trigger(merged)

        if self.max_frames and merged_frames >= self.max_frames:
            self.manager.stop_all()
            self.timer.stop()
            self.close()

    def _change_ui_scale(self, delta: float, absolute: float | None = None) -> None:
        """调整界面缩放并立刻重绘 + 记住（现场嫌字小时用）。"""
        cur = theme.scale()
        new = float(absolute) if absolute is not None else cur + delta
        val = theme.set_scale(new)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_qss())
            app.setFont(theme.ui_font(max(11, int(round(13 * val)))))
        if getattr(self, "settings", None) is not None:
            self.settings.set("ui_scale", val)
            try:
                self.settings.save()
            except OSError:
                pass
        self.statusBar().showMessage(f"界面缩放 {int(round(val * 100))}%（Ctrl+= 放大 / Ctrl+- 缩小 / Ctrl+0 复位）", 5000)

    def _reopen_camera(self) -> bool:
        """从"图片模式"切回真实相机（打开相机并开始预览）。"""
        from ..sources import build_source

        cfg = dict(self.settings.get("last_source", {}) or {})
        if cfg.get("kind") != "mindvision":
            cfg = {"kind": "mindvision", "serial": "056082520586",
                   "model": "MV-GE507GC", "exposure_ms": 100.0, "gain": 0,
                   "trigger": "continuous"}
        st = self.station
        try:
            st.runner.stop()
            st.close_source()
            src = build_source(cfg)
            src.open()
        except Exception as exc:                        # noqa: BLE001
            self.statusBar().showMessage(f"切回相机失败：{exc}", 8000)
            return False
        st.source = src
        st.runner.set_source(src)
        st.cfg.source = {k: v for k, v in cfg.items()}
        self._image_mode = False
        self._sync_trigger_combo()
        self._ensure_preview()
        self.statusBar().showMessage("已切回相机，开始实时采集", 6000)
        return True

    def _on_trigger_changed(self) -> None:
        """切换当前机位相机的触发模式（立即下发给相机）。"""
        val = self.cb_trigger.currentData()
        st = self.station
        src = st.source
        cfg = dict(getattr(st.cfg, "source", {}) or {})
        cfg["trigger"] = val
        st.cfg.source = cfg
        self.settings.set("last_source", cfg)
        applied = False
        if src is not None and hasattr(src, "apply_tune"):
            try:
                src.apply_tune(trigger=val)
                applied = True
            except Exception as exc:                       # noqa: BLE001
                self.statusBar().showMessage(f"触发切换失败：{exc}", 6000)
                return
        if getattr(self, "_image_mode", False):
            # 当前是图片模式：切触发等于要回到相机
            if self._reopen_camera():
                self.statusBar().showMessage("已从图片模式切回相机（连续采集）", 6000)
                return
            else:
                self.statusBar().showMessage(
                    "切回相机失败（相机没连上？），当前仍在图片模式；修好相机后点【管理▾→相机…】连接", 10000)
        label = {"continuous": "连续采集", "hardware": "硬件触发", "software": "软触发"}.get(val, val)
        note = "已下发到相机" if applied else "已记录（下次连接生效）"
        # 现场规则：连续模式只用来调机/取原图，不触发检测；硬件触发才拍照检测
        try:
            st.runner.set_auto_detect(val != "continuous")
            if getattr(self, "act_auto", None) is not None:
                self.act_auto.blockSignals(True)
                self.act_auto.setChecked(val != "continuous")
                self.act_auto.blockSignals(False)
        except Exception:                                # noqa: BLE001
            pass
        extra = "（只取图、不检测）" if val == "continuous" else "（每来一张触发图就检测）"
        self.statusBar().showMessage(f"触发模式 → {label}（{note}）{extra}", 6000)

    def _on_auto_detect_toggled(self, on: bool) -> None:
        """自动检测开关：关掉后连续取图只预览，不计数。"""
        for st in self.manager.stations:
            st.runner.set_auto_detect(on)
        self.statusBar().showMessage(
            "自动检测：开（每帧都判）" if on else
            "自动检测：关（只显示画面，要看结果点【单帧】）", 6000)

    def _sync_trigger_combo(self) -> None:
        """把当前机位的触发模式同步到工具条下拉（切机位时用）。"""
        cfg = getattr(self.station.cfg, "source", {}) or {}
        val = cfg.get("trigger") or "continuous"
        idx = self.cb_trigger.findData(val)
        if idx >= 0 and idx != self.cb_trigger.currentIndex():
            self.cb_trigger.blockSignals(True)
            self.cb_trigger.setCurrentIndex(idx)
            self.cb_trigger.blockSignals(False)
        try:
            # 连续模式只取图不检测；硬件/软触发才检测
            self.station.runner.set_auto_detect(val != "continuous")
        except Exception:                                # noqa: BLE001
            pass
        is_mv = cfg.get("kind") == "mindvision"
        # 图片/文件夹模式下也允许选（先记下来，接回相机时生效）
        self.cb_trigger.setEnabled(is_mv or cfg.get("kind") == "folder")
        self.cb_trigger.setToolTip(
            "相机出图方式：连续采集 = 一直出图（调机）；硬件触发 = 等外部信号（产线）"
            if is_mv else "当前机位不是工业相机，触发模式不适用")

    def _on_recheck_toggled(self, on: bool) -> None:
        """首检 / 复检切换：只影响计数口径，不改配方。"""
        for st in self.manager.stations:
            st.runner.set_recheck(on)
        if on:
            self.statusBar().showMessage(
                "已切到复检模式：接下来的检测计入「复检品」，首检良率不变，另算复检合格率", 8000)
        else:
            self.statusBar().showMessage("已回到首检模式", 5000)

    def _update_stats(self, snap) -> None:
        # 长时间取不到图：多半是硬件触发模式没给触发信号（现场最容易误判成"相机坏了"）
        _waiting = int(getattr(snap, "waiting_frames", 0) or 0)
        if _waiting > 20 and getattr(snap, "running", False):
            _now = time.time()
            if _now - getattr(self, "_wait_hint_ts", 0) > 8:
                self._wait_hint_ts = _now
                self.statusBar().showMessage(
                    "相机没出图：硬件触发模式下需要外部触发信号 —— 调机请在【管理▾→相机…】把触发改成「连续采集」", 8000)
        _mean = float(getattr(snap, "mean_brightness", 0.0) or 0.0)
        self.lbl_ok.setText(str(snap.ok))
        self.lbl_ng.setText(str(snap.ng))
        self.lbl_total.setText(str(snap.total))
        self.lbl_yield.setText(f"{snap.yield_rate * 100:.1f}%" if snap.total else "--")
        verdict = ""
        state = "idle"
        if snap.last_error:
            verdict = "异常"
            state = "warn"
            self.statusBar().showMessage(snap.last_error, 8000)
        elif snap.last_result is not None:
            ok = bool(snap.last_result.get("ok"))
            verdict = "OK" if ok else "NG"
            state = "ok" if ok else "ng"
        self.lbl_verdict.setText(verdict or ("运行中" if snap.running else "待机"))
        theme.set_state(self.lbl_verdict, state)
        # 左下角：只显示 待机中 / 运行中
        _running = bool(getattr(snap, "running", False))
        self.lbl_merged.setText("运行中" if _running else "待机中")
        theme.set_state(self.lbl_merged, "ok" if _running else "idle")
        # 画面左上角叠加统计
        try:
            self.view.set_stats(
                running=bool(snap.running),
                verdict=verdict,
                ok=int(snap.ok), ng=int(snap.ng), total=int(snap.total),
                yield_text=(f"{snap.yield_rate * 100:.1f}%" if snap.total else "--"))
        except Exception:                                # noqa: BLE001
            pass

    def _fill_result_table(self, data: dict[str, Any]) -> None:
        """检测结果：备注 | 对象 | 基准 | 设计值 | 实测 | 判定结果，按 X/Y 分组。"""
        items = (data or {}).get("items") or {}
        rules = list(self.recipe.rules or [])
        known = ("x", "y", "waviness", "area", "baseline")
        groups = [("x", "X 方向距离"), ("y", "Y 方向距离"),
                  ("waviness", "参差度"), ("area", "面积"), ("baseline", "基准线"),
                  (None, "其它")]
        self.tbl.setRowCount(0)
        self.tbl.setVerticalHeaderLabels([])

        def add_header(title: str) -> None:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            cell = QTableWidgetItem(title)
            f = cell.font()
            f.setBold(True)
            f.setPixelSize(13)
            cell.setFont(f)
            cell.setForeground(QColor(theme.color("accent.cyan")))
            self.tbl.setItem(r, 0, cell)
            try:
                self.tbl.setVerticalHeaderItem(r, QTableWidgetItem(""))
                self.tbl.setSpan(r, 0, 1, 6)
            except Exception:                            # noqa: BLE001
                pass

        def add_row(note: str, obj: str, base: str, design: str,
                    value: str, verdict: str, ok, seq: str = "", tip: str = "") -> None:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            for c, txt in enumerate((note, obj, base, design, value, verdict)):
                cell = QTableWidgetItem(txt)
                if c in (4, 5):
                    _ff = theme.mono_font(14 if c == 5 else 13)
                    _ff.setBold(c == 5)
                    cell.setFont(_ff)
                if c == 5:
                    cell.setForeground(QColor(
                        theme.color("state.ok") if ok is True else
                        (theme.color("state.ng") if ok is False else theme.color("text.muted"))))
                self.tbl.setItem(r, c, cell)
            if seq:
                hi = self.tbl.verticalHeaderItem(r)
                if hi is None:
                    self.tbl.setVerticalHeaderItem(r, QTableWidgetItem(seq))
                else:
                    hi.setText(seq)
            if tip:
                for c in range(6):
                    _it = self.tbl.item(r, c)
                    if _it is not None:
                        _it.setToolTip(tip)

        if not rules:
            return                                       # 新建模板：结果表保持空白

        seq = 0
        for kind, title in groups:
            if kind is None:
                idxs = [i for i, r in enumerate(rules, 1) if r.get("kind") not in known]
            else:
                idxs = [i for i, r in enumerate(rules, 1) if r.get("kind") == kind]
            if not idxs:
                continue
            add_header(title)
            for i in idxs:
                rule = rules[i - 1]
                base = str(rule.get("base") or "-")
                note = str(rule.get("note") or "")
                try:
                    design = (f"{float(rule.get('target', 0) or 0):g}"
                              f" (+{float(rule.get('tol_up', 0) or 0):g}/"
                              f"{float(rule.get('tol_lo', 0) or 0):g})")
                except Exception:                        # noqa: BLE001
                    design = "-"
                fno = int(rule.get("from_no", 0) or 0)
                tno = int(rule.get("to_no", 0) or 0)
                k = str(rule.get("kind") or "")
                if k in ("x", "y") and base.upper().startswith("L"):
                    for no in range(min(fno, tno), max(fno, tno) + 1):
                        res = items.get(f"rule{i}_{no}") or {}
                        seq += 1
                        val = res.get("value")
                        add_row(note, f"{no}", base, design,
                                "-" if val is None else f"{float(val):.3f}",
                                {True: "OK", False: "NG", None: "-"}.get(res.get("ok"), "-"),
                                res.get("ok"), str(seq), str(res.get("message") or ""))
                else:
                    res = items.get(f"rule{i}") or {}
                    seq += 1
                    val = res.get("value")
                    _obj = f"{fno}" if fno == tno else f"{fno}→{tno}"
                    add_row(note, _obj, base, design,
                            "-" if val is None else f"{float(val):.3f}",
                            {True: "OK", False: "NG", None: "-"}.get(res.get("ok"), "-"),
                            res.get("ok"), str(seq), str(res.get("message") or ""))

    def open_source_dialog(self) -> None:
        """换相机。

        注意顺序：**先把本机位的相机关掉**再弹对话框。
        因为对话框里"连接"会真的去开一次相机，原相机还开着的话 SDK 会报
        32774（No access, Camera has been opened by other programs）。
        """
        st = self.station
        old_cfg = dict(st.cfg.source)

        def _live(**kw) -> None:
            """对话框里改曝光/增益：直接下发到正在用的相机，画面立即变化。"""
            src = st.source
            if src is not None and hasattr(src, "apply_tune"):
                src.apply_tune(**kw)

        dlg = SourceDialog(old_cfg, self, live_tune=_live)
        accepted = bool(dlg.exec() and dlg.result_cfg)
        cfg = dict(dlg.result_cfg) if accepted else old_cfg
        if accepted:
            st.cfg.source = dict(cfg)
            self.settings.set("last_source", cfg)

        if not accepted:
            # 没改设置：相机一直开着，直接保持（不再重连，避免打断采集）
            if st.source is not None:
                self.statusBar().showMessage("相机设置未改动（当前相机保持连接）", 5000)
                return

        # 改了设置才重新打开
        was_running = st.runner.running
        try:
            if st.runner.running:
                st.stop()
            st.close_source()
            src = build_source(cfg)
            src.open()
        except Exception as exc:                  # noqa: BLE001
            QMessageBox.critical(self, "连接失败", str(exc))
            return
        self._image_mode = False        # 接回真实相机：退出图片模式
        st.source = src
        st.runner.set_source(src)
        self._ensure_preview()
        info = src.info
        note = "" if accepted else "（设置未改动，已重新连接）"
        self.statusBar().showMessage(
            f"{st.cfg.display_name()} 已连接 {info.name} {info.width}x{info.height}{note}", 6000)

    def open_image_file(self) -> None:
        """直接打开一张图片或一个图片文件夹来查看/调试。"""
        from ..sources import open_source

        path, _ = QFileDialog.getOpenFileName(
            self, "打开图片（也可先选到目录再点打开）", self.results_dir if hasattr(self, "results_dir") else BASE_DIR,
            "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;所有文件 (*.*)")
        if not path:
            return
        self._load_image_path(path)

    def _load_image_path(self, path: str) -> None:
        from ..sources import open_source

        st = self.station
        try:
            st.runner.stop()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            st.close_source()
            src = open_source("folder", path=path)
            try:
                src.loop = False        # 单张图不循环，避免画面来回切换
            except Exception:           # noqa: BLE001
                pass
            src.open()
        except Exception as exc:                           # noqa: BLE001
            QMessageBox.critical(self, "打不开这张图", f"{path}\n\n{exc}")
            return
        st.source = src
        st.runner.set_source(src)
        low = path.replace("/", "\\").lower()
        if ("\\ng\\" in low or "\\ok\\" in low) and "\\results\\" in low:
            QMessageBox.information(
                self, "这是保存过的检测图",
                "你打开的是保存过的检测结果图（图片本身已经画了红框和编号）。\n\n"
                "如果要重新调框/调阈值，建议打开【原始图片】（相机采集的原图），\n"
                "否则画面上的红框是图片自带的内容，不是软件新画的。")
        st.cfg.source = {"kind": "folder", "path": path}
        self._image_mode = True        # 图片模式：不跑连续预览
        self._sync_trigger_combo()
        self._show_one_frame()

    def _show_one_frame(self) -> None:
        """读一帧、跑一次检测、把结果显示出来（不写记录）。"""
        st = self.station
        src = st.source
        if src is None:
            return
        try:
            frame = src.read(timeout_ms=1500)
        except Exception as exc:                           # noqa: BLE001
            QMessageBox.critical(self, "读图失败", str(exc))
            return
        if frame is None:
            QMessageBox.warning(self, "读不到图", "这个文件读不出图像（格式不支持？）")
            return
        try:
            result, annotated = st.pipeline.run(frame, annotate=True)
        except Exception as exc:                           # noqa: BLE001
            QMessageBox.critical(self, "检测失败", str(exc))
            return
        self._last_view_frame = frame.bgr.copy()
        self.tiles[self.active].view.set_item_states({})
        self.tiles[self.active].view.set_frame(annotated)
        self._fill_result_table(result.as_dict())
        self.statusBar().showMessage(
            f"已打开图片：{getattr(src, 'path', '')}（{frame.bgr.shape[1]}x{frame.bgr.shape[0]}）", 6000)

    def open_ng_gallery(self) -> None:
        """NG 图库：停机也能看历史 NG 图；选一张可载入当前机位重新调试。"""
        from .ng_gallery import NgGalleryDialog

        root = self.settings.get("results_root", "results")
        if not os.path.isabs(root):
            root = os.path.join(BASE_DIR, root)
        dlg = NgGalleryDialog(root, self)
        if not dlg.exec() or not dlg.selected:
            return
        img_dir = dlg.selected_dir
        st = self.station
        from ..sources import open_source

        try:
            if st.runner.running:
                st.stop()
            st.close_source()
            src = open_source("folder", path=img_dir)
            src.open()
        except Exception as exc:                        # noqa: BLE001
            QMessageBox.critical(self, "载入失败", str(exc))
            return
        st.source = src
        st.runner.set_source(src)
        st.cfg.source = {"kind": "folder", "path": img_dir}
        self._sync_trigger_combo()
        # 载入图片后自动出图（不用再点开始/单帧）
        QTimer.singleShot(300, self._show_one_frame)
        self.statusBar().showMessage(
            f"已载入 NG 图回放：{os.path.basename(img_dir)}（改参数后点【单帧】复现）", 8000)

    def open_box_settings(self) -> None:
        """检测框设置：分割小框 / 抓取范围 / 检测区域 / 定位模板 / 匹配阈值。"""
        from ..qtcompat import (
            QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
            QSpinBox, QVBoxLayout,
        )

        item = self._current_item()
        dlg = QDialog(self)
        dlg.setWindowTitle("检测框设置")
        dlg.resize(520, 560)
        outer = QVBoxLayout(dlg)
        form = QFormLayout()

        cb_item = QComboBox()
        cb_item.addItem("（选择一个检测框）", "")
        boxed = [it for it in self.recipe.items if it.roi is not None]
        for no, it in enumerate(boxed, start=1):
            cb_item.addItem(f"{no}", it.id)
        if item is not None:
            cb_item.setCurrentIndex(max(0, cb_item.findData(item.id)))
        form.addRow("选择检测框", cb_item)
        lbl_cur = QLabel(f"当前：{_op_label(item) if item else '（未选中）'}")
        form.addRow(lbl_cur)

        def _pick_item() -> None:
            iid = cb_item.currentData()
            it = next((x for x in self.recipe.items if x.id == iid), None)
            self.list_items.setCurrentRow(self.recipe.items.index(it) if it else -1)
            lbl_cur.setText(f"当前：{_op_label(it) if it else '（未选中）'}")
            sp_cols.setValue(int(it.params.get("grid_cols", 1) or 1) if it else 1)
            sp_rows.setValue(int(it.params.get("grid_rows", 1) or 1) if it else 1)
            sp_lr.setValue(int(float(it.params.get("inset_l", 0) or 0)) if it else 0)
            sp_tb.setValue(int(float(it.params.get("inset_t", 0) or 0)) if it else 0)

        cb_item.currentIndexChanged.connect(lambda _i: _pick_item())

        sp_cols = QSpinBox()
        sp_cols.setRange(1, 999)
        sp_cols.setValue(int(item.params.get("grid_cols", 1) or 1) if item else 1)
        form.addRow("列数（横向，1 行 10 列 = 一排 10 个）", sp_cols)

        sp_rows = QSpinBox()
        sp_rows.setRange(1, 999)
        sp_rows.setValue(int(item.params.get("grid_rows", 1) or 1) if item else 1)
        form.addRow("行数（纵向）", sp_rows)

        sp_lr = QSpinBox()
        sp_lr.setRange(0, 5000)
        sp_lr.setSuffix(" px")
        sp_lr.setToolTip("左右同时向内缩多少像素（屏蔽框里左右多余部分）")
        sp_lr.setValue(int(float(item.params.get("inset_l", 0) or 0)) if item else 0)
        form.addRow("抓取范围·左右各缩", sp_lr)

        sp_tb = QSpinBox()
        sp_tb.setRange(0, 5000)
        sp_tb.setSuffix(" px")
        sp_tb.setToolTip("上下同时向内缩多少像素（屏蔽框里上下多余部分）")
        sp_tb.setValue(int(float(item.params.get("inset_t", 0) or 0)) if item else 0)
        form.addRow("抓取范围·上下各缩", sp_tb)

        # ---- 小框（分割结果，可单独拖）----
        row_cells = QHBoxLayout()
        self._lbl_cells = QLabel(self._cells_text())
        row_cells.addWidget(self._lbl_cells, 1)
        btn_split = QPushButton("生成小框")
        btn_split.setToolTip("按上面的列数/行数，把大框切成小框（生成后可以在画面上一个个拖）")
        btn_split.clicked.connect(self._gen_cells)
        row_cells.addWidget(btn_split)
        btn_cells_clear = QPushButton("清除小框")
        btn_cells_clear.clicked.connect(self._clear_cells)
        row_cells.addWidget(btn_cells_clear)
        form.addRow("小框（可单独移动）", row_cells)

        # ---- 检测区域（黄色大框）----
        row_area = QHBoxLayout()
        self._lbl_area = QLabel(self._area_text())
        row_area.addWidget(self._lbl_area, 1)
        btn_area = QPushButton("画检测区域")
        btn_area.clicked.connect(lambda: (dlg.accept(), self._start_area_draw()))
        row_area.addWidget(btn_area)
        btn_area_clear = QPushButton("清除")
        btn_area_clear.clicked.connect(self._clear_area)
        row_area.addWidget(btn_area_clear)
        form.addRow("检测区域（黄色）", row_area)

        # ---- 定位模板（青色）----
        row_loc = QHBoxLayout()
        self._lbl_loc = QLabel(self._locator_text())
        row_loc.addWidget(self._lbl_loc, 1)
        btn_loc = QPushButton("画定位框")
        btn_loc.clicked.connect(lambda: (dlg.accept(), self._start_locator_draw()))
        row_loc.addWidget(btn_loc)
        btn_grab = QPushButton("截取模板")
        btn_grab.clicked.connect(self._capture_template)
        row_loc.addWidget(btn_grab)
        btn_loc_clear = QPushButton("清除")
        btn_loc_clear.clicked.connect(self._clear_locator)
        row_loc.addWidget(btn_loc_clear)
        form.addRow("定位模板（青色）", row_loc)

        sp_score = QSpinBox()
        sp_score.setRange(1, 99)
        sp_score.setValue(int(self.recipe.locator.get("score", 80) or 80))
        sp_score.setToolTip("匹配阈值：1 = 最容易匹配（灵敏度最高），99 = 最严格")
        form.addRow("匹配阈值（1 最低 ~ 99 最高）", sp_score)

        outer.addLayout(form)
        outer.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        outer.addWidget(bb)

        if not dlg.exec():
            return
        self._push_undo()
        chosen = next((x for x in self.recipe.items if x.id == cb_item.currentData()), None)
        if chosen is not None:
            chosen.params["grid_cols"] = int(sp_cols.value())
            chosen.params["grid_rows"] = int(sp_rows.value())
            chosen.params["inset_l"] = int(sp_lr.value())
            chosen.params["inset_r"] = int(sp_lr.value())
            chosen.params["inset_t"] = int(sp_tb.value())
            chosen.params["inset_b"] = int(sp_tb.value())
            try:
                self.list_items.setCurrentRow(self.recipe.items.index(chosen))
            except ValueError:
                pass
        self._box_dlg_item = chosen
        item = chosen
        # 列/行改过就自动重算小框（避免出现"改成 1 行 10 列，画面还是旧的 2 行 5 列"）
        if chosen is not None and chosen.roi is not None and not chosen.roi.is_line():
            self._gen_cells()
        loc = dict(self.recipe.locator or {})
        loc["score"] = int(sp_score.value())
        self.recipe.locator = loc
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        if item is not None:
            n = int(sp_cols.value()) * int(sp_rows.value())
            self.statusBar().showMessage(
                f"已设为 {sp_rows.value()} 行 × {sp_cols.value()} 列（共 {n} 个小框），点【单帧】看效果", 6000)
        self._schedule_preview()

    # ---------------------------------------------------------------- 检测区域
    def _last_frame_bgr(self):
        """取最近一帧原图（截取定位模板用）：优先 runner 缓存的原始帧，其次直接读一次。"""
        st = self.station
        snap = st.runner.snapshot()
        frame = getattr(snap, "last_frame", None)
        if frame is not None:
            return frame
        if st.source is not None:
            try:
                f = st.source.read(timeout_ms=800)
                return None if f is None else f.bgr
            except Exception:                          # noqa: BLE001
                return None
        return None

    def _ordered_with_roi(self) -> list:
        """所有"有框/有线"的检测项，按画的先后顺序（配方里的顺序）。"""
        return [it for it in self.recipe.items if it.roi is not None]

    def _box_no(self, item) -> str:
        """矩形框单独编号 1、2、3…；直线单独编号 L1、L2…（互不占号）。"""
        rect_no = 0
        line_no = 0
        for it in self.recipe.items:
            if it.roi is None:
                continue
            if it.roi.is_line():
                line_no += 1
                if it is item:
                    return f"L{line_no}"
            else:
                rect_no += 1
                if it is item:
                    return str(rect_no)
        return "—"

    def _cells_text(self) -> str:
        cur = self._current_item()
        n = len((cur.params.get("cells") or [])) if cur else 0
        return f"已生成 {n} 个小框（画面上可单独拖动）" if n else "还没有小框（点【生成小框】）"

    def _gen_cells(self) -> None:
        """按检测框设置里选中的那一项 + 列/行 + 抓取范围生成小框。"""
        item = getattr(self, "_box_dlg_item", None) or self._current_item()
        if item is None or item.roi is None:
            QMessageBox.information(self, "先选检测框", "请先在右侧选中一个矩形检测框。")
            return
        if item.roi.is_line():
            QMessageBox.information(self, "线框不能分割", "直线检测框不能分割，请选矩形框。")
            return
        cols = max(1, int(item.params.get("grid_cols", 1) or 1))
        rows = max(1, int(item.params.get("grid_rows", 1) or 1))
        l = int(float(item.params.get("inset_l", 0) or 0))
        r_ = int(float(item.params.get("inset_r", l) or 0))
        t_ = int(float(item.params.get("inset_t", 0) or 0))
        b_ = int(float(item.params.get("inset_b", t_) or 0))
        roi = item.roi
        x0, y0 = int(roi.x) + l, int(roi.y) + t_
        x1, y1 = int(roi.x + roi.w) - r_, int(roi.y + roi.h) - b_
        if x1 - x0 < 4 or y1 - y0 < 4:
            x0, y0, x1, y1 = int(roi.x), int(roi.y), int(roi.x + roi.w), int(roi.y + roi.h)
        cw = (x1 - x0) / float(cols)          # 列 = 横向切几刀
        ch = (y1 - y0) / float(rows)          # 行 = 纵向切几刀
        cells = []
        for gy in range(rows):
            for gx in range(cols):
                cells.append([int(x0 + gx * cw), int(y0 + gy * ch), max(2, int(cw)), max(2, int(ch))])
        self._push_undo()
        item.params["cells"] = cells
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self.statusBar().showMessage(f"已生成 {len(cells)} 个小框，可在画面上单独拖动调整", 6000)

    def _clear_cells(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self._push_undo()
        item.params["cells"] = []
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self.statusBar().showMessage("已清除小框（回到整块检测）", 4000)

    def _area_text(self) -> str:
        a = self.recipe.detect_area
        return f"已设置 {a.w}x{a.h} @ {a.x},{a.y}" if a else "未设置"

    def _locator_text(self) -> str:
        loc = dict(self.recipe.locator or {})
        r = loc.get("rect")
        if not r:
            return "未设置"
        path = loc.get("path") or ""
        flag = "已截取模板" if path else "还没截取模板"
        return f"{r[2]}x{r[3]} @ {r[0]},{r[1]}（{flag}）"

    def _start_area_draw(self) -> None:
        if getattr(self, "act_select", None) is not None:
            self.act_select.setChecked(False)
        self.view.set_area_mode(True)
        self.statusBar().showMessage("请在画面上拖出一个【黄色检测区域】，检测框和定位模板都必须在里面", 10000)

    def _start_locator_draw(self) -> None:
        if getattr(self, "act_select", None) is not None:
            self.act_select.setChecked(False)
        self.view.set_locator_mode(True)
        self.statusBar().showMessage("请在【检测区域内】拖出定位模板框（青色），画好后点【截取模板】", 10000)

    def _clear_area(self) -> None:
        self._push_undo()
        self.recipe.detect_area = None
        self._sync_view()
        self.statusBar().showMessage("检测区域已清除", 4000)

    def _clear_locator(self) -> None:
        self._push_undo()
        self.recipe.locator = {}
        self._sync_view()
        self.statusBar().showMessage("定位模板已清除", 4000)

    def _capture_template(self) -> None:
        """把定位框里的当前画面存成模板图（之后按它匹配定位）。"""
        loc = dict(self.recipe.locator or {})
        rect = loc.get("rect")
        if not rect:
            QMessageBox.information(self, "还没画定位框", "请先点【画定位框】，在检测区域内画一个定位框。")
            return
        frame = self._last_frame_bgr()
        if frame is None:
            QMessageBox.information(self, "没有画面", "当前还没有取到画面，请先让相机出图。")
            return
        x, y, w, h = [int(v) for v in rect]
        ih, iw = frame.shape[:2]
        x, y = max(0, x), max(0, y)
        w, h = min(w, iw - x), min(h, ih - y)
        if w < 4 or h < 4:
            QMessageBox.warning(self, "定位框太小", "定位框太小了，请重新画大一点。")
            return
        crop = frame[y:y + h, x:x + w].copy()
        d = os.path.join(BASE_DIR, "recipes", "templates")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{self.recipe.name}_{x}_{y}_{w}x{h}.png")
        from ..imgio import imwrite

        if not imwrite(path, crop):
            QMessageBox.warning(self, "保存失败", f"模板图写不进去：{path}")
            return
        loc["path"] = path
        self.recipe.locator = loc
        self.statusBar().showMessage(f"定位模板已截取：{os.path.basename(path)}（以后按这张图定位）", 7000)

    def apply_batch_threshold(self) -> None:
        """右侧面板：给"起始框~结束框"范围内的找斑块检测框统一设阈值。"""
        lo = int(self.sp_th_lo.value())
        hi = int(self.sp_th_hi.value())
        if lo > hi:
            lo, hi = hi, lo
        a = min(self.sp_th_from.value(), self.sp_th_to.value())
        b = max(self.sp_th_from.value(), self.sp_th_to.value())
        self._push_undo()
        changed = 0
        for i, it in enumerate(self.recipe.items, start=1):
            if a <= i <= b and it.type == "find_blobs":
                it.params["threshold_low"] = lo
                it.params["threshold_high"] = hi
                changed += 1
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self._schedule_preview()
        self.statusBar().showMessage(f"第 {a}~{b} 号中 {changed} 个检测框已设阈值 {lo}~{hi}", 6000)

    def open_rule_settings(self) -> None:
        """规则设置：选几个框 + 规则类型 + 判定上下限，生成一条检测规则。"""
        from ..ops import Item as _Item
        from ..qtcompat import (
            QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLineEdit,
            QSpinBox, QVBoxLayout,
        )

        boxes = [(i + 1, it) for i, it in enumerate(self.recipe.items) if it.roi is not None]
        if len(boxes) < 1:
            QMessageBox.information(self, "还没有检测框", "请先画检测框（框必须存在），再添加规则。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("规则设置")
        dlg.resize(520, 420)
        v = QVBoxLayout(dlg)
        form = QFormLayout()

        cb_kind = QComboBox()
        for key, label in (("gap_x", "间距测量（两框 X 方向）"),
                           ("gap_y", "Y方向距离（两框 Y 方向）"),
                           ("flat", "平整度测量（三个框）"),
                           ("area", "面积测量（单个框）")):
            cb_kind.addItem(label, key)
        form.addRow("规则类型", cb_kind)

        def _box_combo() -> QComboBox:
            cb = QComboBox()
            for no, it in boxes:
                cb.addItem(f"{no}  {_op_label(it)}", it.id)
            return cb

        cb_a = _box_combo()
        form.addRow("第 1 个框", cb_a)
        cb_b = _box_combo()
        cb_b.setCurrentIndex(min(1, cb_b.count() - 1))
        form.addRow("第 2 个框", cb_b)
        cb_c = _box_combo()
        cb_c.setCurrentIndex(min(2, cb_c.count() - 1))
        form.addRow("第 3 个框（平整度用）", cb_c)

        ed_name = QLineEdit("新规则")
        form.addRow("规则名称", ed_name)

        sp_lo = QDoubleSpinBox()
        sp_lo.setRange(-1e9, 1e9)
        sp_lo.setDecimals(3)
        sp_lo.setValue(0.0)
        form.addRow("判定下限", sp_lo)

        sp_hi = QDoubleSpinBox()
        sp_hi.setRange(-1e9, 1e9)
        sp_hi.setDecimals(3)
        sp_hi.setValue(999999.0)
        form.addRow("判定上限", sp_hi)

        v.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if not dlg.exec():
            return

        kind = cb_kind.currentData()
        self._push_undo()
        idx = len(self.recipe.items) + 1
        name = (ed_name.text() or "新规则").strip()
        judge = {"kind": "range", "min": float(sp_lo.value()), "max": float(sp_hi.value())}
        if kind in ("gap_x", "gap_y"):
            params = {"from_item": cb_a.currentData(), "to_item": cb_b.currentData(),
                      "axis": "x" if kind == "gap_x" else "y", "use_pick": "on"}
            it = _Item(id=f"rule{idx}", type="distance_pp", name=name, roi=None, params=params)
        elif kind == "flat":
            params = {"p1_item": cb_a.currentData(), "p2_item": cb_b.currentData(),
                      "p3_item": cb_c.currentData(), "use_pick": "on"}
            it = _Item(id=f"rule{idx}", type="angle_ppp", name=name, roi=None, params=params)
        else:
            params = {"src": cb_a.currentData(), "index": 0}
            it = _Item(id=f"rule{idx}", type="area_of_blob", name=name, roi=None, params=params)
        it.judge = judge
        self.recipe.items.append(it)
        self._refresh_item_list(select=len(self.recipe.items) - 1)
        self._sync_view()
        self._schedule_preview()
        self.statusBar().showMessage(f"已添加规则：{name}（判定 {judge['min']}~{judge['max']}）", 6000)

    def open_batch_threshold(self) -> None:
        """批量阈值：起始框 ~ 结束框，一起设下/上阈值（拖滑块，画面实时变）。"""
        from ..qtcompat import (
            QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QSlider,
            QSpinBox, QVBoxLayout,
        )

        n = len(self.recipe.items)
        if n == 0:
            QMessageBox.information(self, "没有检测框", "当前模板还没有检测框，请先画框。")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("批量阈值")
        dlg.resize(460, 320)
        v = QVBoxLayout(dlg)
        form = QFormLayout()

        sp_from = QSpinBox()
        sp_from.setRange(1, n)
        sp_from.setValue(1)
        form.addRow("起始框（第几号）", sp_from)

        sp_to = QSpinBox()
        sp_to.setRange(1, n)
        sp_to.setValue(n)
        form.addRow("结束框（第几号）", sp_to)

        row_lo = QHBoxLayout()
        sl_lo = QSlider(Qt.Orientation.Horizontal)
        sl_lo.setRange(1, 255)
        sl_lo.setValue(111)
        sp_lo = QSpinBox()
        sp_lo.setRange(1, 255)
        sp_lo.setValue(111)
        sp_lo.setFixedWidth(70)
        row_lo.addWidget(sl_lo, 1)
        row_lo.addWidget(sp_lo)
        form.addRow("下阈值", row_lo)

        row_hi = QHBoxLayout()
        sl_hi = QSlider(Qt.Orientation.Horizontal)
        sl_hi.setRange(1, 255)
        sl_hi.setValue(255)
        sp_hi = QSpinBox()
        sp_hi.setRange(1, 255)
        sp_hi.setValue(255)
        sp_hi.setFixedWidth(70)
        row_hi.addWidget(sl_hi, 1)
        row_hi.addWidget(sp_hi)
        form.addRow("上阈值", row_hi)

        v.addLayout(form)
        v.addWidget(QLabel("说明：只改【找斑块】类的检测框；其它类型会跳过。"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        sl_lo.valueChanged.connect(sp_lo.setValue)
        sp_lo.valueChanged.connect(sl_lo.setValue)
        sl_hi.valueChanged.connect(sp_hi.setValue)
        sp_hi.valueChanged.connect(sl_hi.setValue)

        if not dlg.exec():
            return
        a = min(sp_from.value(), sp_to.value())
        b = max(sp_from.value(), sp_to.value())
        lo, hi = int(sp_lo.value()), int(sp_hi.value())
        if lo > hi:
            lo, hi = hi, lo
        self._push_undo()
        changed = 0
        for i, it in enumerate(self.recipe.items, start=1):
            if a <= i <= b and it.type == "find_blobs":
                it.params["threshold_low"] = lo
                it.params["threshold_high"] = hi
                changed += 1
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self._schedule_preview()
        self.statusBar().showMessage(f"已给第 {a}~{b} 号中的 {changed} 个检测框设阈值 {lo}~{hi}", 6000)

    def open_track_settings(self) -> None:
        """跟踪设置：直接选一个已有检测框当基准（补偿放料偏差），不用再画跟踪框。"""
        from ..qtcompat import QDialog, QDialogButtonBox, QDoubleSpinBox, QSpinBox

        dlg = QDialog(self)
        dlg.setWindowTitle("跟踪设置（补偿放料偏差）")
        dlg.resize(520, 260)
        from ..qtcompat import QFormLayout

        v = QFormLayout(dlg)
        cb = QComboBox()
        cb.addItem("（不跟踪）", "")
        for i, it in enumerate(self.recipe.items):
            if it.roi is not None:
                cb.addItem(f"{i + 1:02d}  {_op_label(it)}", it.id)
        cur = ""
        tr = self.recipe.track
        if tr.roi is not None:
            for it in self.recipe.items:
                if it.roi == tr.roi:
                    cur = it.id
                    break
        cb.setCurrentIndex(max(0, cb.findData(cur)))
        v.addRow("基准检测框", cb)

        sp_score = QDoubleSpinBox()
        sp_score.setRange(0.05, 0.99)
        sp_score.setSingleStep(0.05)
        sp_score.setValue(float(getattr(tr, "min_score", 0.7) or 0.7))
        v.addRow("最低相似度", sp_score)

        sp_margin = QSpinBox()
        sp_margin.setRange(0, 500)
        sp_margin.setValue(int(getattr(tr, "search_margin", 60) or 60))
        v.addRow("搜索范围(像素)", sp_margin)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addRow(bb)

        if not dlg.exec():
            return
        self._push_undo()
        item_id = cb.currentData()
        tr.min_score = float(sp_score.value())
        tr.search_margin = int(sp_margin.value())
        if item_id:
            it = next((x for x in self.recipe.items if x.id == item_id), None)
            tr.roi = it.roi if it else None
            tr.enabled = True
            self.statusBar().showMessage(f"跟踪已启用：用 {_op_label(it)} 做基准（相似度 {tr.min_score:.2f}）", 6000)
        else:
            tr.roi = None
            tr.enabled = False
            self.statusBar().showMessage("已关闭跟踪", 4000)
        self._sync_view()

    def _overlay_cfg(self) -> dict:
        cfg = dict(self.settings.get("overlay", {}) or {})
        cfg.setdefault("size_stat", 20)
        cfg.setdefault("size_okng", 46)
        cfg.setdefault("x", 14)
        cfg.setdefault("y", 10)
        return cfg

    def _apply_overlay(self, cfg: dict | None = None) -> None:
        cfg = cfg or self._overlay_cfg()
        for tile in getattr(self, "tiles", []) or []:
            try:
                tile.view.set_overlay(cfg)
            except Exception:                            # noqa: BLE001
                pass

    def open_overlay_settings(self) -> None:
        from .overlay_dialog import OverlayDialog

        dlg = OverlayDialog(self._overlay_cfg(), self)
        dlg.bind_apply(lambda c: self._apply_overlay(c))   # 实时预览
        if dlg.exec():
            cfg = dlg.values()
            self.settings.set("overlay", cfg)
            self.settings.save()
            self._apply_overlay(cfg)
            self.statusBar().showMessage("画面显示设置已保存", 4000)
        else:
            self._apply_overlay()                          # 取消 → 还原

    def open_general_settings(self) -> None:
        """通用设置：图片保存路径 / 保存内容（NG、OK、全部）/ 清空图片。"""
        from .general_settings import GeneralSettingsDialog

        dlg = GeneralSettingsDialog(self.settings, self)
        if not dlg.exec():
            return
        res = dlg.result()
        for k, v in res.items():
            self.settings.set(k, v)
        self._check_disk()
        mode_txt = {"ng": "只存 NG", "ok": "只存 OK", "all": "全部保存"}.get(res["save_mode"], res["save_mode"])
        self.statusBar().showMessage(
            f"通用设置已保存：保存内容={mode_txt}，目录={res['results_root']}（下次开始运行生效）", 6000)

    def open_record_settings(self) -> None:
        cfg = RecorderConfig.from_dict(self.settings.recorder_cfg)
        dlg = RecordSettingsDialog(cfg, self)
        if dlg.exec():
            new_cfg = dlg.result_cfg()
            self.settings.set("results_root", new_cfg.root)
            self.settings.set("csv_enabled", new_cfg.csv_enabled)
            self.settings.set("save_ng_images", new_cfg.save_ng_images)
            self.settings.set("save_ok_images", new_cfg.save_ok_images)
            self.settings.set("ng_keep_days", int(getattr(new_cfg, "keep_days", 30) or 0))
            self.settings.set("disk_min_free_gb", float(dlg.sp_free.value()))
            self.settings.set("results_max_gb", float(dlg.sp_maxsize.value()))
            self._check_disk()
            self.statusBar().showMessage("记录设置已更新（下次开始运行生效）", 5000)

    def open_output_settings(self) -> None:
        dlg = OutputAlarmDialog(self.settings.output_cfg, self.settings.alarm_cfg, self)
        if dlg.exec():
            self.settings.set("output", dlg.result_output_cfg())
            self.settings.set("alarm", dlg.result_alarm_cfg())
            self.statusBar().showMessage("输出/报警设置已更新（下次开始运行生效）", 5000)

    # ------------------------------------------------------------------ 配方
    def _refresh_recipe_combo(self) -> None:
        pass

    def _load_recipe(self, path: str) -> None:
        try:
            recipe = Recipe.load(path)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, "加载失败", str(exc))
            return
        self.station.set_recipe(recipe, path)
        self._active_path = path
        self.setWindowTitle(f"VisionApp - {self.manager.line.name} / {os.path.basename(path)}")
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()
        self.settings.set("last_recipe", path)
        if self.station.runner.running:
            self.station.open_recorder()
        self.statusBar().showMessage(
            f"{self.station.cfg.display_name()} 已切换配方 {os.path.basename(path)}", 4000)

    def open_recipe_manager(self) -> None:
        dlg = RecipeManagerDialog(self.recipes_dir, self.station.recipe_path or "", self)
        if dlg.exec() and dlg.selected:
            self._load_recipe(dlg.selected)

    # ------------------------------------------------------------------ 编辑
    def _current_index(self) -> int:
        return self.list_items.currentRow()

    def _current_item(self) -> Item | None:
        # 先按"记住的 id"取（自动建的框项在列表里是隐藏的，用行号会取错）
        cid = getattr(self, "_cur_id", None)
        if cid:
            for it in self.recipe.items:
                if it.id == cid:
                    return it
        i = self._current_index()
        if 0 <= i < len(self.recipe.items):
            return self.recipe.items[i]
        return None

    # ------------------------------------------------------------------ 撤销
    def _snapshot(self) -> dict:
        """把当前配方压成可还原的快照（检测项 + 跟踪设置）。"""
        import copy

        return {
            "items": copy.deepcopy([it.to_dict() if hasattr(it, "to_dict") else it for it in self.recipe.items]),
            "track": copy.deepcopy(self.recipe.track.to_dict()),
            "index": self._current_index(),
        }

    def _push_undo(self) -> None:
        """在做修改之前调用：记住"改之前"的样子。"""
        try:
            snap = self._snapshot()
        except Exception:                                   # noqa: BLE001
            return
        self._undo_stack.append(snap)
        if len(self._undo_stack) > 50:
            self._undo_stack = self._undo_stack[-50:]

    def undo_last(self) -> None:
        """撤销上一个动作：删除框、移动框、对齐、新建框都算。"""
        from ..recipe import Item

        if not self._undo_stack:
            self.statusBar().showMessage("没有可撤销的操作", 3000)
            return
        snap = self._undo_stack.pop()
        try:
            items = []
            for d in snap["items"]:
                items.append(Item.from_dict(d) if isinstance(d, dict) else d)
            self.recipe.items = items
            from ..tracking import TrackConfig

            self.recipe.track = TrackConfig.from_dict(snap.get("track") or {})
            idx = int(snap.get("index", 0) or 0)
            self._refresh_item_list(select=min(idx, max(0, len(items) - 1)))
            self._sync_view()
            self.statusBar().showMessage(f"已撤销（剩余可撤销 {len(self._undo_stack)} 步）", 4000)
        except Exception as exc:                            # noqa: BLE001
            self.statusBar().showMessage(f"撤销失败：{exc}", 5000)

    def _on_show_number_toggled(self, on: bool) -> None:
        self.view.set_numbers_visible(bool(on))
        self.statusBar().showMessage("画面编号：" + ("显示" if on else "隐藏"), 3000)

    def _refresh_item_list(self, select: int | None = None) -> None:
        self._building = True
        self.list_items.clear()
        show_auto = bool(getattr(self, "_show_auto_items", False))
        for it in self.recipe.items:
            # 空白项也要显示：操作员要在这里选检测方式
            if it.roi is None:
                roi = "-"
            elif it.roi.is_line():
                ln = it.roi.line or (0, 0, 0, 0)
                roi = f"LINE({ln[0]},{ln[1]})→({ln[2]},{ln[3]})"
            else:
                roi = f"{it.roi.x},{it.roi.y} {it.roi.w}x{it.roi.h}"
            _txt = f"{self._box_no(it)}  {_op_label(it)}"
            self.list_items.addItem(_txt)
            _it = self.list_items.item(self.list_items.count() - 1)
            if _it is not None:
                _it.setToolTip(f"{it.id}   {_op_label(it)}   ROI {roi}")
        if select is not None and 0 <= select < self.list_items.count():
            self.list_items.setCurrentRow(select)
        elif self.list_items.count():
            # 没指定时，尽量保住"当前项"的选中
            row = 0
            cid = getattr(self, "_cur_id", None)
            if cid:
                for i, it in enumerate(self.recipe.items):
                    if it.id == cid:
                        row = i
                        break
            self.list_items.setCurrentRow(max(0, min(row, self.list_items.count() - 1)))
        self._building = False
        self._refresh_rule_preview()
        self._fill_editor()
        _it = self._current_item()
        if _it is not None:
            self._fill_detect_settings(_it)          # 保证检测设置与当前项一致

    def _fill_editor(self) -> None:
        item = self._current_item()
        enabled = item is not None
        for w in (self.ed_id, self.ed_name, self.cb_type):
            w.setEnabled(enabled)
        if not enabled:
            self.ed_id.setText("")
            self.ed_name.setText("")
            self._rebuild_param_form(None)
            return
        self.ed_id.setText(item.id)
        self.ed_name.setText(item.name)
        self.cb_type.blockSignals(True)
        self.cb_type.setCurrentIndex(max(0, self.cb_type.findData(getattr(item, "type", "") or "")))
        self.cb_type.blockSignals(False)
        self._rebuild_param_form(item)
        judge = item.judge or {}
        idx = max(0, self.cb_judge.findData(judge.get("kind", "range")))
        self.cb_judge.setCurrentIndex(idx)
        self.sp_lo.setValue(float(judge.get("min", 0) or 0))
        self.sp_hi.setValue(float(judge.get("max", 0) or 0))
        self.sp_design.setValue(float(judge.get("design", 0) or 0))
        self.sp_tol_up.setValue(float(judge.get("tol_upper", 0.08) or 0.08))
        self.sp_tol_lo.setValue(float(judge.get("tol_lower", -0.08) or -0.08))
        self.ck_abs.setChecked(bool(judge.get("abs", False)))
        self._sync_judge_rows(judge.get("kind", "range"))
        self._refresh_rule_preview()

    def _schedule_preview(self) -> None:
        """参数一改就 200ms 后重算一帧（防抖），拖动阈值时画面实时变。"""
        t = getattr(self, "_preview_timer", None)
        if t is None:
            t = self._preview_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._preview_once)
        t.start(200)

    def _schedule_still(self) -> None:
        """编辑后 500ms 统一重算一次（不要每个对象各算一次，否则拖动很卡）。"""
        t = getattr(self, "_still_timer", None)
        if t is None:
            t = self._still_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._refresh_still)
        t.start(500)

    def _refresh_still(self) -> None:
        """用最近一帧原图重跑一次检测并显示（编辑框后清掉旧的红框残影）。"""
        st = self.station
        snap = st.runner.snapshot()
        frame = getattr(self, "_raw_frame", None)
        if frame is None:
            frame = getattr(snap, "last_frame", None)
        if frame is None:
            frame = getattr(self, "_last_view_frame", None)
        if frame is None:
            if getattr(self, "_image_mode", False):
                self._show_one_frame()
            return
        try:
            from ..core import Frame as _Frame

            f = _Frame.create(frame) if not hasattr(frame, "bgr") else frame
            result, annotated = st.pipeline.run(f, annotate=True)
        except Exception:                                   # noqa: BLE001
            return
        self.tiles[self.active].view.set_item_states({})
        self.tiles[self.active].view.set_frame(annotated)
        self._fill_result_table(result.as_dict())

    def _preview_once(self) -> None:
        st = self.station
        if st.runner.running:
            return                      # 正在跑的话本来就是实时，交给它
        src = st.source
        if src is None:
            return
        try:
            frame = src.read(timeout_ms=800)
        except Exception:               # noqa: BLE001
            frame = None
        if frame is None:
            return
        try:
            result, annotated = st.pipeline.run(frame, annotate=True)
        except Exception as exc:        # noqa: BLE001
            st.runner._last_error = str(exc)
            self.statusBar().showMessage(f"预览失败：{exc}", 4000)
            return
        self.tiles[self.active].view.set_item_states({})
        self.tiles[self.active].view.set_frame(annotated)
        self._fill_result_table(result.as_dict())

    def _rebuild_param_form(self, item: Item | None) -> None:
        while self.form_params.rowCount():
            self.form_params.removeRow(0)
        self._param_widgets: dict[str, Any] = {}
        if item is None:
            return
        for name, kind, default, desc in OP_SCHEMA.get(item.type, []):
            value = item.params.get(name, default)
            w = self._make_widget(kind, value, name)
            w.setToolTip(desc)
            self.form_params.addRow(desc or name, w)
            self._param_widgets[name] = w

    def _make_widget(self, kind: str, value: Any, key: str):
        from ..qtcompat import QSpinBox, QSlider
        # 阈值类参数用"滑块 + 数字"，拖的时候画面立刻能看到斑块变化
        if kind == "int" and key in ("threshold", "threshold_low", "threshold_high"):
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, 254)
            slider.setValue(max(1, min(254, int(value or 128))))
            spin = QSpinBox()
            spin.setRange(1, 254)
            spin.setValue(slider.value())
            spin.setFixedWidth(64)
            h.addWidget(slider, 1)
            h.addWidget(spin)

            def _apply(v, k=key):
                v = max(1, min(254, int(v)))
                if slider.value() != v:
                    slider.setValue(v)
                if spin.value() != v:
                    spin.setValue(v)
                self._set_param(k, int(v))

            slider.valueChanged.connect(lambda v: _apply(v))
            spin.valueChanged.connect(lambda v: _apply(v))
            box._slider = slider
            box._spin = spin
            return box
        if kind == "int":
            w = QSpinBox()
            w.setRange(-1_000_000, 1_000_000)
            w.setValue(int(value or 0))
            w.valueChanged.connect(lambda v, k=key: self._set_param(k, int(v)))
            return w
        # 选择"哪个检测框"（动态列出当前配方里的框）
        if kind == "item_choice":
            w = QComboBox()
            w.addItem("（未选择）", "")
            for i, it in enumerate(self.recipe.items):
                label = f"{i + 1:02d}  {_op_label(it)}"
                w.addItem(label, it.id)
            idx = w.findData(str(value or ""))
            w.setCurrentIndex(max(0, idx))
            w.currentIndexChanged.connect(lambda _i, k=key, ww=w: self._set_param(k, ww.currentData() or ""))
            return w
        if kind == "float":
            w = QDoubleSpinBox()
            w.setRange(-1e9, 1e9)
            w.setDecimals(4)
            w.setValue(float(value or 0))
            w.valueChanged.connect(lambda v, k=key: self._set_param(k, float(v)))
            return w
        if kind.startswith("choice:"):
            w = QComboBox()
            opts = kind.split(":", 1)[1].split(",")
            labels = [{"dark": "黑色", "light": "白色", "off": "关", "on": "开"}.get(x, x) for x in opts]
            w.addItems(labels)
            w.setCurrentIndex(max(0, w.findText(str(value))))
            if w.currentText() != str(value):
                try:
                    w.setCurrentIndex(opts.index(str(value)))
                except ValueError:
                    pass
            w.currentTextChanged.connect(
                lambda txt, k=key, o=opts, ls=labels: self._set_param(k, o[ls.index(txt)] if txt in ls else txt))
            return w
        if kind == "str":
            ed = QLineEdit(str(value or ""))
            ed.textChanged.connect(lambda v, k=key: self._set_param(k, v))
            return ed

        if kind == "file":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            ed = QLineEdit(str(value or ""))
            btn = QPushButton("...")
            btn.setFixedWidth(30)

            def pick():
                p, _ = QFileDialog.getOpenFileName(self, "选择文件", ed.text())
                if p:
                    ed.setText(p)
                    self._set_param(key, p)

            btn.clicked.connect(pick)
            ed.textChanged.connect(lambda v, k=key: self._set_param(k, v))
            h.addWidget(ed, 1)
            h.addWidget(btn)
            return w
        w = QLineEdit(str(value or ""))
        w.textChanged.connect(lambda v, k=key: self._set_param(k, v))
        return w

    def _set_param(self, key: str, value: Any) -> None:
        _needs_preview = key in ("threshold", "threshold_low", "threshold_high",
                                 "pick_zone", "pick_offset", "min_area", "max_area",
                                 "threshold_mode", "show_mask", "show_pick",
                                 "from_item", "to_item", "axis", "use_pick",
                                 "min_width", "min_height")
        if key == "pick_zone":
            z = str(value)
            mode = "light" if z.startswith("白色") else ("dark" if z.startswith("黑色") else None)
            if mode:
                self._set_param("threshold_mode", mode)

        if _needs_preview:
            self._schedule_preview()
        if self._building:
            return
        item = self._current_item()
        if item is not None:
            item.params[key] = value

    # ------------------------------------------------------------------ 检测项操作
    def add_item(self, auto: bool = False) -> None:
        self._push_undo()
        n = 1
        existing = {it.id for it in self.recipe.items}
        while f"item{n}" in existing:
            n += 1
        # 现场要求：画完框/线只挂一个【空白检测项】，检测方式/参数由操作员自己选
        item = Item(id=f"item{n}", type="find_blobs", name=f"框{n}", roi=None,
                    params={k: d for k, _t, d, _s in OP_SCHEMA.get("find_blobs", [])})
        item.params["pick_zone"] = "白色中下"       # 现场默认抓取方向
        if auto:
            item.params["auto"] = True
        self.recipe.items.append(item)
        self._cur_id = item.id
        self._refresh_item_list(select=len(self.recipe.items) - 1)
        self._sync_view()

    def dup_item(self) -> None:
        self._push_undo()
        item = self._current_item()
        if item is None:
            return
        import copy
        clone = copy.deepcopy(item)
        clone.id = f"{item.id}_copy"
        clone.name = (item.name or item.id) + " 副本"
        self.recipe.items.insert(self._current_index() + 1, clone)
        self._refresh_item_list(select=self._current_index() + 1)

    def del_item(self) -> None:
        self._push_undo()
        i = self._current_index()
        if 0 <= i < len(self.recipe.items):
            gone = self.recipe.items.pop(i)
            # 1) 引用这个框的规则（两点距离/三点角度）一起删掉
            ref_keys = ("from_item", "to_item", "p1_item", "p2_item", "p3_item", "src", "template")
            keep = []
            for it in self.recipe.items:
                p = getattr(it, "params", {}) or {}
                if any(str(p.get(k, "")) == gone.id for k in ref_keys):
                    continue                 # 依赖被删框 → 这条规则也删
                keep.append(it)
            removed_rules = len(self.recipe.items) - len(keep)
            self.recipe.items = keep
            # 2) 跟踪基准框没了 → 跟踪作废，需要重新定位
            tr = self.recipe.track
            if tr.roi is not None and getattr(gone, "roi", None) == tr.roi:
                tr.roi = None
                tr.enabled = False
            self.statusBar().showMessage(
                f"已删除 {gone.id}" + (f"（连带删除 {removed_rules} 条依赖规则）" if removed_rules else "")
                + ("；跟踪框已重置，请重新设置" if tr.roi is None else ""), 6000)
            self._refresh_item_list(select=min(i, len(self.recipe.items) - 1))
            self._sync_view()

    def move_item(self, delta: int) -> None:
        self._push_undo()
        i = self._current_index()
        j = i + delta
        if 0 <= i < len(self.recipe.items) and 0 <= j < len(self.recipe.items):
            self.recipe.items[i], self.recipe.items[j] = self.recipe.items[j], self.recipe.items[i]
            self._refresh_item_list(select=j)

    def _on_item_selected(self, row: int) -> None:
        if self._building:
            return
        # 填充编辑器期间必须屏蔽信号：cb_type.setCurrentText 会触发 _on_type_changed，
        # 而那个处理器会把 item.params 重置成默认值（等于点一下就丢掉调好的参数）。
        self._building = True
        try:
            self._fill_editor()
        finally:
            self._building = False
        item = self._current_item()
        if item is not None:
            self._cur_id = item.id
        if getattr(self, "_keep_multi", False):
            return                    # 正在恢复多选，别把选中重置成单个
        self.view.set_active_roi(item.id if item else None)

    def _on_type_changed(self, type_name: str) -> None:
        if self._building:
            return
        item = self._current_item()
        if item is None:
            return
        item.type = type_name or ""
        if type_name:
            item.params = {k: d for k, _t, d, _s in OP_SCHEMA.get(type_name, [])}
        else:
            item.params = {}
        self._rebuild_param_form(item)
        self._refresh_item_list(select=self._current_index())

    def _refresh_rule_preview(self) -> None:
        """把判定翻译成一行"设计 1 ± 0.08 → [0.920, 1.080]"，现场对表用。"""
        item = self._current_item()
        label = getattr(self, "lbl_rule", None)
        if label is None:
            return
        judge = item.judge if item is not None else None
        text = rule_preview_text(judge)
        if item is not None:
            warn = threshold_warnings(item.params.get("threshold_low"),
                                      item.params.get("threshold_high"))
            if warn:
                text += "　｜　" + warn
        label.setText(text)

    def _sync_judge_rows(self, kind: str) -> None:
        """按判定类型显示/隐藏对应行（range: 下限/上限；design: 设计值/容差/绝对值）。"""
        form = getattr(self, "_judge_form", None)
        if form is None:
            return
        is_range = kind == "range"
        is_design = kind == "design"
        fields = {1: self.sp_lo, 2: self.sp_hi, 3: self.sp_design,
                  4: self.sp_tol_up, 5: self.sp_tol_lo, 6: self.ck_abs}
        for row, show in ((1, is_range), (2, is_range),
                          (3, is_design), (4, is_design), (5, is_design), (6, is_design)):
            try:
                form.setRowVisible(row, show)          # Qt 6.4+
                continue
            except Exception:                          # Qt5 / 旧 Qt6 没有该 API
                pass
            w = fields.get(row)
            if w is None:
                continue
            w.setVisible(show)
            lbl = form.labelForField(w)
            if lbl is not None:
                lbl.setVisible(show)

    def _fill_detect_settings(self, item) -> None:
        """把当前检测项的阈值/抓取方向填到【检测设置】。"""
        self._suppress_preview = True
        try:
            self._fill_detect_settings_inner(item)
        finally:
            self._suppress_preview = False

    def _fill_detect_settings_inner(self, item) -> None:
        """实际填值（内部用，外面包了预览屏蔽）。"""
        is_blob = getattr(item, "type", "") in ("find_blobs", "")
        try:
            self.box_detect.setVisible(is_blob)
            self.box_params.setVisible(not is_blob)
        except Exception:                                # noqa: BLE001
            pass
        if not is_blob:
            return
        p = dict(item.params or {})
        lo = int(p.get("threshold_low", 111) or 0)
        hi = int(p.get("threshold_high", 255) or 0)
        self.sp_det_lo.blockSignals(True)
        self.sp_det_hi.blockSignals(True)
        self.sp_det_lo.setValue(max(0, min(255, lo)))
        self.sp_det_hi.setValue(max(0, min(255, hi)))
        self.sp_det_lo.blockSignals(False)
        self.sp_det_hi.blockSignals(False)
        for _ck, _key in ((self.ck_det_force_h, "force_h"), (self.ck_det_force_v, "force_v"),
                          (self.ck_det_max_area, "max_area_on")):
            _ck.blockSignals(True)
            _ck.setChecked(bool(p.get(_key, True)))
            _ck.blockSignals(False)
        try:
            self._refresh_rules_label()
            self.box_rule.setVisible(True)
        except Exception:                                # noqa: BLE001
            pass
        zone = str(p.get("pick_zone", "白色中下"))
        idx = self.cb_pick_dir.findText(zone)
        self.cb_pick_dir.blockSignals(True)
        self.cb_pick_dir.setCurrentIndex(idx if idx >= 0 else self.cb_pick_dir.findText("白色中下"))
        self.cb_pick_dir.blockSignals(False)
        item.params["pick_zone"] = self.cb_pick_dir.currentText()   # 界面=配方，避免显示不一致
        try:
            self._refresh_box_combos()
            if item.roi is not None and not item.roi.is_line():
                _n = 0
                for _it in self.recipe.items:
                    if _it.roi is None or _it.roi.is_line():
                        continue
                    _n += 1
                    if _it.id == item.id:
                        for _cb in (self.cb_det_from, self.cb_det_to):
                            _i = _cb.findData(_n)
                            if _i >= 0:
                                _cb.setCurrentIndex(_i)
                        break
            self._refresh_rule_options()
            self._sync_rule_base_enabled()
        except Exception:                                # noqa: BLE001
            pass
        # 记录"原阈值"，清屏时恢复
        self._th_snapshot = {"lo": lo, "hi": hi, "mode": str(p.get("threshold_mode", "dark")),
                             "item": item.id}

    def _on_pick_dir_changed(self, text: str) -> None:
        item = self._current_item()
        if item is None or text in ("", "（不选）"):
            return
        item.params["pick_zone"] = text
        try:
            self._sync_pick_lines()
        except Exception:                                # noqa: BLE001
            pass
        self._update_stats_dummy = None

    def _on_detect_flag(self, key: str, on: bool) -> None:
        item = self._current_item()
        if item is None:
            return
        item.params[key] = bool(on)

    def _box_numbers(self) -> list:
        """当前配方里所有矩形框的编号（1..N）。"""
        nums = []
        n = 0
        for it in self.recipe.items:
            if it.roi is None or it.roi.is_line():
                continue
            n += 1
            nums.append(n)
        return nums

    def _sync_pick_lines(self) -> None:
        """每个框按抓取方向画一条与框同宽的绿色抓取线（随框移动/缩放）。"""
        from ..qtcompat import QLineF

        lines = []
        for it in self.recipe.items:
            roi = it.roi
            if roi is None or roi.is_line() or getattr(it, "type", "") not in ("find_blobs", ""):
                continue
            x, y, w, h = [int(v) for v in roi.as_tuple()]
            zone = str(it.params.get("pick_zone", "白色中下"))
            if "顶部" in zone or "中上" in zone:
                yy = y + 1
                lines.append(QLineF(x, yy, x + w, yy))
            elif "底部" in zone or "中下" in zone:
                yy = y + h - 1               # 贴框最底部（与框线重叠也算）
                lines.append(QLineF(x, yy, x + w, yy))
            elif "左侧" in zone:
                xx = x + 1
                lines.append(QLineF(xx, y, xx, y + h))
            elif "右侧" in zone:
                xx = x + w - 1
                lines.append(QLineF(xx, y, xx, y + h))
            # 抓取方向里带"中"：再画一条竖直中心线（现场要能看到十字）
            if "中" in zone and ("上" in zone or "下" in zone):
                xx = x + w // 2
                lines.append(QLineF(xx, y, xx, y + h))
        self.view.set_pick_lines(lines)

    def _refresh_box_combos(self) -> None:
        """检测设置/规则设定的起始、结束框都用下拉，只列实际存在的框号。"""
        nums = self._box_numbers()
        for cb in (getattr(self, "cb_det_from", None), getattr(self, "cb_det_to", None),
                   getattr(self, "cb_rule_from", None), getattr(self, "cb_rule_to", None)):
            if cb is None:
                continue
            cur = cb.currentData()
            is_rule = cb in (getattr(self, "cb_rule_from", None), getattr(self, "cb_rule_to", None))
            cb.blockSignals(True)
            cb.clear()
            if is_rule:
                cb.addItem("无", None)
            for n in nums:
                cb.addItem(str(n), n)             # 只显示数字 1、2、3…
            if not nums and not is_rule:
                cb.addItem("无", None)
            if cur is not None:
                idx = cb.findData(cur)
            else:
                idx = 0 if is_rule else (1 if cb.count() > 1 else 0)
            cb.setCurrentIndex(max(0, idx))
            if cb is getattr(self, "cb_det_from", None) and cb.count() > 1:
                cb.setCurrentIndex(0)                       # 起始框默认第一个
            if cb is getattr(self, "cb_det_to", None) and cb.count() > 1:
                cb.setCurrentIndex(cb.count() - 1)          # 结束框默认最后一个
            cb.blockSignals(False)
    def _refresh_rule_options(self) -> None:
        """基准框下拉：左边画了几个框就列几个框号，另外列出直线 L1..LN。"""
        cur = self.cb_rule_base.currentData()
        self.cb_rule_base.blockSignals(True)
        self.cb_rule_base.clear()
        self.cb_rule_base.addItem("无", "")
        n = 0
        for it in self.recipe.items:
            if it.roi is None or it.roi.is_line():
                continue
            n += 1
            self.cb_rule_base.addItem(str(n), str(n))
        ln = 0
        for it in self.recipe.items:
            if it.roi is None or not it.roi.is_line():
                continue
            ln += 1
            self.cb_rule_base.addItem(f"L{ln}", f"L{ln}")
        idx = self.cb_rule_base.findData(cur) if cur else 0
        self.cb_rule_base.setCurrentIndex(max(0, idx))
        self.cb_rule_base.blockSignals(False)

    def _sync_rule_base_enabled(self) -> None:
        """面积/参差度不需要基准框；其它规则必须选。"""
        kind = str(self.cb_rule_kind.currentData() or "")
        need = kind not in ("area", "waviness")
        self.cb_rule_base.setEnabled(need)
        self.cb_rule_base.setToolTip("" if need else "面积/参差度不需要基准框")

    def _add_rule(self) -> None:
        kind = self.cb_rule_kind.currentData()
        fno = int(self.cb_rule_from.currentData() or 0)
        tno = int(self.cb_rule_to.currentData() or 0)
        base = self.cb_rule_base.currentData() or ""
        if fno <= 0 or tno <= 0:
            QMessageBox.warning(self, "规则错误", "请先选择起始框和结束框")
            return
        if kind == "baseline":
            if abs(tno - fno) != 1:
                QMessageBox.warning(self, "基准线错误", "基准线为相邻的两个检测框（例如起始 1、结束 2）")
                return
            base = ""
        elif kind in ("area", "waviness"):
            base = ""
        elif not base:
            QMessageBox.warning(self, "缺少基准框", "需添加基准框对象")
            return
        self._push_undo()
        self.recipe.rules.append({
            "kind": kind, "from_no": fno, "to_no": tno, "base": base,
            "abs": bool(self.ck_rule_abs.isChecked()),
            "target": float(self.sp_rule_target.value()),
            "tol_up": float(self.sp_rule_tol_up.value()),
            "tol_lo": float(self.sp_rule_tol_lo.value()),
            "note": self.ed_rule_note.text().strip(),
        })
        if getattr(self, "ck_rule_inc", None) is not None and self.ck_rule_inc.isChecked():
            _next_from = fno + 1
            _next_to = tno + 1
            for _cb, _val in ((self.cb_rule_from, _next_from), (self.cb_rule_to, _next_to)):
                _i = _cb.findData(_val)
                if _i >= 0:
                    _cb.setCurrentIndex(_i)
            if kind == "x":
                _b = self.cb_rule_base.currentData() or ""
                if str(_b).isdigit():
                    _i = self.cb_rule_base.findData(str(int(_b) + 1))
                    if _i >= 0:
                        self.cb_rule_base.setCurrentIndex(_i)
        self._refresh_rules_label()
        self._refresh_still()
        self.statusBar().showMessage("规则已添加（结果见【检测结果】）", 5000)

    def _del_rule(self) -> None:
        if not self.recipe.rules:
            return
        self._push_undo()
        self.recipe.rules.pop()
        self._refresh_rules_label()
        self.statusBar().showMessage("已删除最后一条规则", 4000)

    def _refresh_rules_label(self) -> None:
        names = {"x": "X方向", "y": "Y方向", "waviness": "参差度", "area": "面积",
                 "baseline": "生成基准线"}
        rows = []
        for i, r in enumerate(self.recipe.rules or [], start=1):
            txt = f"#{i} {names.get(r.get('kind'), r.get('kind'))}  {r.get('from_no')}→{r.get('to_no')}"
            if r.get("base"):
                txt += f"  基准{r.get('base')}"
            if r.get("target"):
                txt += f"  设定{r.get('target'):g}(+{r.get('tol_up'):g}/{r.get('tol_lo'):g})"
            if r.get("note"):
                txt += f"  备注:{r.get('note')}"
            rows.append(txt)
        self.lbl_rules.setText("规则：\n" + "\n".join(rows) if rows else "规则：（空）")

    def _on_detect_threshold_live(self) -> None:
        """拖动阈值滑条：立即作用于当前框（没有当前框就作用于全部框），并刷新画面。"""
        if getattr(self, "_suppress_preview", False):
            return
        item = self._current_item()
        if item is None:
            boxes = [it for it in self.recipe.items
                     if it.roi is not None and not it.roi.is_line()]
            if not boxes:
                return
            item = boxes[0]
        lo = int(self.sp_det_lo.value())
        hi = int(self.sp_det_hi.value())
        if lo > hi:
            lo, hi = hi, lo
        item.params["threshold_low"] = lo
        item.params["threshold_high"] = hi
        self._preview_threshold()                  # 立即刷新一次
        t = getattr(self, "_live_timer", None)
        if t is None:
            t = self._live_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._preview_threshold)
        t.start(150)

    def _restore_raw_view(self) -> None:
        """把画面恢复成原图（清掉阈值预览的红色覆盖）。"""
        st = self.station
        frame = getattr(self, "_raw_frame", None)
        if frame is None:
            frame = getattr(st.runner.snapshot(), "last_frame", None)
        if frame is None:
            frame = getattr(self, "_last_view_frame", None)
        if frame is None:
            return
        base = frame.bgr if hasattr(frame, "bgr") else frame
        if base is not None:
            self.view.set_frame(base)
        self._preview_until = 0

    def _preview_threshold(self) -> None:
        """拖动阈值：框内二值化，选中的像素用红色半透明覆盖（白抓取=亮部，黑抓取=暗部）。"""
        import time as _t

        import cv2

        st = self.station
        frame = getattr(self, "_raw_frame", None)
        if frame is None:
            frame = getattr(st.runner.snapshot(), "last_frame", None)
        if frame is None:
            frame = getattr(self, "_last_view_frame", None)
        if frame is None:
            self.statusBar().showMessage("还没有画面：先点【检测一次】或连接相机", 4000)
            self._diag("预览：没有可用画面")
            return
        base = frame.bgr if hasattr(frame, "bgr") else frame
        if base is None:
            return
        img = base.copy()
        self._preview_until = _t.time() + 1.5
        try:
            _lo_no = int(self.cb_det_from.currentData() or 0)
            _hi_no = int(self.cb_det_to.currentData() or _lo_no)
        except Exception:                                # noqa: BLE001
            _lo_no = _hi_no = 0
        if _lo_no and _hi_no < _lo_no:
            _lo_no, _hi_no = _hi_no, _lo_no
        n = 0
        _tinted = 0
        _outside = 0
        lo = int(self.sp_det_lo.value())
        hi = int(self.sp_det_hi.value())
        for it in self.recipe.items:
            roi = it.roi
            if roi is None or roi.is_line() or it.type not in ("find_blobs", ""):
                continue
            x, y, w, h = [int(v) for v in roi.as_tuple()]
            x, y = max(0, x), max(0, y)
            x2, y2 = min(img.shape[1], x + w), min(img.shape[0], y + h)
            if x2 <= x or y2 <= y:
                continue
            sub = img[y:y2, x:x2]
            gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY) if sub.ndim == 3 else sub
            _cur = self._current_item()
            try:
                _no = int(str(self._box_no(it)).lstrip("L"))
            except Exception:                            # noqa: BLE001
                _no = 0
            if _lo_no and _hi_no:
                if not (_lo_no <= _no <= _hi_no):
                    continue                               # 选了范围：范围内一起调
            elif _cur is not None and it.id != _cur.id:
                continue                                   # 没选范围：只调当前框
            lo = int(self.sp_det_lo.value())
            hi = int(self.sp_det_hi.value())
            zone_now = self.cb_pick_dir.currentText()
            white = str(zone_now).startswith("白色")
            if white:
                _, bw = cv2.threshold(gray, max(0, min(255, lo)), 255, cv2.THRESH_BINARY)
                if hi < 255:
                    _, over = cv2.threshold(gray, min(255, hi), 255, cv2.THRESH_BINARY)
                    bw = cv2.bitwise_and(bw, cv2.bitwise_not(over))
            else:
                _, bw = cv2.threshold(gray, max(0, min(255, lo)), 255, cv2.THRESH_BINARY_INV)
            # 红色半透明覆盖选中像素（现场最直观）
            # 现场要求：红色 = 被阈值排除掉的像素（白抓取时黑色部被盖红，反之亦然）
            mask = bw <= 0           # 红色 = 被阈值排除的像素（要抓的地方保持原样）
            if mask.any():
                _tinted += int(mask.sum())
                sub[mask] = (sub[mask] * 0.45 + np.array([0, 0, 255]) * 0.55).astype(np.uint8)
            else:
                # 阈值把整框都选中了：给选中区加一层淡绿，拖动时能看出变化
                sel = bw > 0
                if sel.any():
                    _tinted += int(sel.sum())
                    sub[sel] = (sub[sel] * 0.72 + np.array([0, 160, 0]) * 0.28).astype(np.uint8)
            cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                if cv2.contourArea(c) < 3:
                    continue
                n += 1
                cv2.drawContours(img, [c + (x, y)], -1, (0, 255, 0), 1)
        try:
            _now2 = time.time()
            if _now2 - getattr(self, "_diag_ts", 0) > 1.0:
                self._diag_ts = _now2
                self._diag(f"预览：frame={base.shape[1]}x{base.shape[0]} 框={n} 红像素={_tinted}"
                           f" 阈值={lo}~{hi} 范围={_lo_no}~{_hi_no}")
        except Exception:                            # noqa: BLE001
            pass
        if n == 0 and _tinted == 0:
            self.statusBar().showMessage(
                f"没有可预览的像素：画面 {base.shape[1]}x{base.shape[0]}，阈值 {lo}~{hi}"
                f"（框={len(self.recipe.items)}）", 6000)
            return
        self.view.set_frame(img)
        self.statusBar().showMessage(
            f"阈值预览：连通域 {n} 个，红色 {_tinted} 像素（红=被排除）", 4000)

    def _clear_detect_threshold(self) -> None:
        """清屏：把阈值恢复成选中这一项时的原始值（拉错了用）。"""
        self._stop_preview_timer()
        snap = getattr(self, "_th_snapshot", None)
        item = self._current_item()
        if not snap or item is None or snap.get("item") != item.id:
            self.statusBar().showMessage("没有可恢复的原阈值", 4000)
            return
        self._push_undo()
        item.params["threshold_low"] = int(snap["lo"])
        item.params["threshold_high"] = int(snap["hi"])
        item.params["threshold_mode"] = snap.get("mode", "dark")
        self._fill_detect_settings(item)
        self._restore_raw_view()
        self._sync_pick_lines()
        self.statusBar().showMessage(f"已恢复原阈值：{snap['lo']} ~ {snap['hi']}", 5000)

    def _stop_preview_timer(self) -> None:
        """停掉延时预览，并解除预览锁，红色不再回来。"""
        try:
            t = getattr(self, "_live_timer", None)
            if t is not None:
                t.stop()
        except Exception:                                # noqa: BLE001
            pass
        self._preview_until = 0.0

    def _apply_detect_threshold(self) -> None:
        """设定阈值：把当前上/下阈值 + 抓取方向应用到 起始框..结束框 范围的检测项。"""
        self._stop_preview_timer()
        lo = int(self.sp_det_lo.value())
        hi = int(self.sp_det_hi.value())
        if lo > hi:
            lo, hi = hi, lo
            self.sp_det_lo.blockSignals(True)
            self.sp_det_hi.blockSignals(True)
            self.sp_det_lo.setValue(lo)
            self.sp_det_hi.setValue(hi)
            self.sp_det_lo.blockSignals(False)
            self.sp_det_hi.blockSignals(False)
        zone = self.cb_pick_dir.currentText()
        zone_key = {"白色中上": "白色中上", "白色中下": "白色中下", "黑色中上": "黑色中上",
                    "黑色中下": "黑色中下", "白色顶部": "白色顶部", "白色底部": "白色底部",
                    "黑色顶部": "黑色顶部", "黑色底部": "黑色底部", "白色左侧": "白色左侧",
                    "白色右侧": "白色右侧", "黑色左侧": "黑色左侧", "黑色右侧": "黑色右侧"}.get(zone, zone)
        lo_no = int(self.cb_det_from.currentData() or 0)
        hi_no = int(self.cb_det_to.currentData() or lo_no)
        if lo_no <= 0:
            self.statusBar().showMessage("还没有可设定的检测框", 4000)
            return
        if lo_no > hi_no:
            lo_no, hi_no = hi_no, lo_no
        self._push_undo()
        n = 0
        for it in self.recipe.items:
            if it.roi is None or it.type != "find_blobs":
                continue
            try:
                no = int(str(self._box_no(it)).lstrip("L"))
            except Exception:                            # noqa: BLE001
                continue
            if lo_no <= no <= hi_no:
                it.params["threshold_low"] = lo
                it.params["threshold_high"] = hi
                it.params["pick_zone"] = zone_key
                it.params["threshold_mode"] = "light" if zone.startswith("白色") else "dark"
                n += 1
        self._restore_raw_view()
        self._sync_pick_lines()
        self.statusBar().showMessage(f"已对 {n} 个框设定阈值 {lo}~{hi}、抓取方向 {zone}", 6000)

    def _on_judge_changed(self) -> None:
        if self._building:
            return
        item = self._current_item()
        if item is None:
            return
        kind = self.cb_judge.currentData()
        if kind == "design":
            item.judge = {
                "kind": "design",
                "design": float(self.sp_design.value()),
                "tol_upper": float(self.sp_tol_up.value()),
                "tol_lower": float(self.sp_tol_lo.value()),
                "abs": bool(self.ck_abs.isChecked()),
            }
        elif kind == "range":
            item.judge = {"kind": "range",
                          "min": float(self.sp_lo.value()), "max": float(self.sp_hi.value())}
        elif kind == "max":
            item.judge = {"kind": "max", "value": float(self.sp_hi.value())}
        else:
            item.judge = {"kind": "min", "value": float(self.sp_lo.value())}
        self._sync_judge_rows(kind)
        self._fill_detect_settings(item)
        self._refresh_rule_preview()

    def _on_roi_edited(self, item_id: str, rect: Rect | None) -> None:
        item = next((it for it in self.recipe.items if it.id == item_id), None)
        if item is None:
            return
        item.roi = rect
        self._pending_sel = list(self.view.selection()) or [item_id]
        # 整组拖动会连续触发很多次：合并成一次刷新（否则一拖就卡）
        t = getattr(self, "_commit_timer", None)
        if t is None:
            t = self._commit_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._commit_edits)
        t.start(40)

    def _commit_edits(self) -> None:
        """一批编辑结束后统一刷新一次（不跑检测，避免松手卡住）。"""
        sel = list(getattr(self, "_pending_sel", []) or [])
        self._keep_multi = True
        try:
            self._sync_view()
            self._refresh_item_list(select=self._current_index())
            if sel:
                self.view.set_selection(sel)
        finally:
            self._keep_multi = False

    def _defer_item_menu(self, item_id: str, idx: int) -> None:
        """右键弹菜单：延迟到下一个事件循环 tick，避免在鼠标事件里弹模态菜单（会闪退）。"""
        if idx != self.active:
            return

        def _open() -> None:
            try:
                self._show_item_menu(item_id)
            except Exception as exc:                    # noqa: BLE001
                self._log_crash("item menu", exc)

        QTimer.singleShot(0, _open)

    def _diag(self, text: str) -> None:
        """把现场诊断信息追加到 logs/gui.log（排查用，不影响界面）。"""
        try:
            d = os.path.join(BASE_DIR, "logs")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "gui.log"), "a", encoding="utf-8") as fp:
                fp.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
        except Exception:                                # noqa: BLE001
            pass

    def _log_crash(self, where: str, exc: BaseException) -> None:
        """把异常写进 logs/crash-日期.log，方便现场排查。"""
        import traceback

        try:
            d = os.path.join(BASE_DIR, "logs")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"crash-{time.strftime('%Y%m%d')}.log")
            with open(path, "a", encoding="utf-8") as fp:
                fp.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} {where} ====\n")
                fp.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        except Exception:                               # noqa: BLE001
            pass
        self.statusBar().showMessage(f"出错了（已记录日志）：{exc}", 8000)

    def _show_item_menu(self, item_id: str) -> None:
        """右键点检测框：删除 / 复制 / 重命名 / 去改参数。"""
        from ..qtcompat import QMenu, QInputDialog

        item = next((it for it in self.recipe.items if it.id == item_id), None)
        if item is None:
            return
        row = self.recipe.items.index(item)
        self.list_items.setCurrentRow(row)
        menu = QMenu(self)
        a_param = menu.addAction("改参数（右侧面板）")
        a_dup = menu.addAction("复制这个框")
        a_rename = menu.addAction("重命名…")
        menu.addSeparator()
        a_del = menu.addAction("删除这个检测项")
        act = menu.exec(self.cursor().pos())
        if act is a_param:
            self.editor_panel.setFocus()
            self.statusBar().showMessage("参数在右侧【参数】区，阈值可直接拖动", 4000)
        elif act is a_dup:
            self.dup_item()
        elif act is a_rename:
            text, ok = QInputDialog.getText(self, "重命名", "新名称", text=item.name or item.id)
            if ok and text.strip():
                item.name = text.strip()
                self._refresh_item_list(select=row)
        elif act is a_del:
            self.del_item()

    def _on_view_cleared(self) -> None:
        self._user_cleared = True

    def _on_roi_selected(self, item_id: str) -> None:
        self._user_cleared = False
        for it in self.recipe.items:
            if it.id == item_id:
                self._cur_id = it.id
                self._building = True
                try:
                    self._fill_editor()
                finally:
                    self._building = False
                break
        for i, it in enumerate(self.recipe.items):
            if it.id == item_id:
                self.list_items.setCurrentRow(i)
                break

    def _on_select_mode_toggled(self, on: bool) -> None:
        """选择/移动模式：勾上就不能画新框（避免误画），取消才画。"""
        self.view.set_select_mode(on)
        if on:
            self.view.set_line_mode(False)
            if self.act_multi_draw.isChecked():
                self.act_multi_draw.setChecked(False)
            self.statusBar().showMessage("选择/移动：点框选中，拖框内移动，空白拖=框选，方向键微调", 6000)
        else:
            self.statusBar().showMessage("画框模式：在空白处拖动 = 新建检测框", 5000)

    def _on_multi_draw_toggled(self, on: bool) -> None:
        self.view.set_line_mode(False)          # 切到矩形模式：一定退出直线模式
        if on and getattr(self, "act_select", None) is not None and self.act_select.isChecked():
            self.act_select.setChecked(False)   # 画框时自动退出"选择/移动"
        self._multi_draw = bool(on)
        self.statusBar().showMessage(
            "连画模式：开（连续画矩形，每拖一个 = 新建一个检测项）" if on else "连画模式：关", 4000)

    def nudge_selected(self, dx: int, dy: int) -> None:
        """按步长把选中的框（或当前项）上下左右微调。"""
        step = int(self.sp_nudge.value())
        keys = (list(self.view.selection())
                or list(getattr(self, "_last_move_keys", []))
                or ([self.view._active] if self.view._active else []))
        if not keys:
            cur = self._current_item()
            if cur is not None and cur.roi is not None:
                keys = [cur.id]
        if not keys:
            self.statusBar().showMessage("先选中一个框，再用箭头微调", 4000)
            return
        self._push_undo()
        fw, fh = self.view.frame_size()
        moved = 0
        for it in self.recipe.items:
            if it.id not in keys or it.roi is None:
                continue
            # 分了小框的：小框跟着整体一起移动
            if it.params.get("cells"):
                it.params["cells"] = [[c[0] + dx * step, c[1] + dy * step, c[2], c[3]]
                                      for c in it.params["cells"]]
            if it.roi.is_line():
                x1, y1, x2, y2 = it.roi.line
                it.roi = Rect.from_line(x1 + dx * step, y1 + dy * step,
                                        x2 + dx * step, y2 + dy * step)
            else:
                r = Rect(it.roi.x + dx * step, it.roi.y + dy * step, it.roi.w, it.roi.h)
                it.roi = _clamp_rect(r, fw, fh) if fw > 0 else r
            moved += 1
        self._last_move_keys = list(keys)      # 记住这一组，下次还能一起动
        self._keep_multi = True
        try:
            self._sync_view()
            self._refresh_item_list(select=self._current_index())
            self.view.set_selection(keys)      # 保持多选，下一次还能整组动
        finally:
            self._keep_multi = False
        self.statusBar().showMessage(f"已移动 {moved} 个框（{step}px）", 3000)

    def delete_selected_boxes(self) -> None:
        """删除选中的框（保留检测项）。"""
        keys = list(self.view.selection()) or ([self.view._active] if self.view._active else [])
        if not keys:
            cur = self._current_item()
            if cur is not None and cur.roi is not None:
                keys = [cur.id]
        if not keys:
            self.statusBar().showMessage("先点选一个框，或先在右侧列表选一项，再点【删除框】", 5000)
            return
        self._push_undo()
        n = 0
        for it in self.recipe.items:
            if it.id in keys:
                it.roi = None
                it.params["cells"] = []
                n += 1
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self.statusBar().showMessage(f"已删除 {n} 个检测框（检测项还在）", 5000)

    def select_all_boxes(self) -> None:
        keys = [it.id for it in self.recipe.items if it.roi is not None]
        if not keys:
            self.statusBar().showMessage("还没有检测框", 3000)
            return
        self.view.set_selection(keys)
        self.statusBar().showMessage(f"已选中 {len(keys)} 个检测框", 4000)

    def delete_all_boxes(self) -> None:
        """全部删除：不弹确认，连检测项一起清空（撤销可恢复）。"""
        self._push_undo()
        n = len(self.recipe.items)
        self.recipe.items = []
        self._refresh_item_list()
        self._sync_view()
        self.statusBar().showMessage(f"已清空全部 {n} 个检测项和框（可点【撤销】恢复）", 6000)

    def _on_roi_created(self, rect: Rect) -> None:
        """画新框：画框模式下每次都新建；选择/移动模式下重叠则复用。"""
        item = self._current_item()
        # 矩形框只用"找斑块"类检测项；当前项是规则项（间距/角度/面积）时自动新建框项
        if item is None or getattr(item, "type", "") not in ("", "find_blobs"):
            self.add_item(auto=True)
            item = self._current_item()
            if item is None:
                return
        reused = False
        draw_mode = bool(getattr(self, "act_multi_draw", None) and self.act_multi_draw.isChecked()) \
            or (hasattr(self, "view") and self.view.is_line_mode())
        # 只有"选择/移动"模式才把重叠当成改现有框；画框模式下一律新建
        for it in ([] if draw_mode else self.recipe.items):
            if it.roi is None or it.roi.is_line():
                continue
            ix, iy, iw, ih = it.roi.as_tuple()
            ox = max(ix, rect.x)
            oy = max(iy, rect.y)
            ow = min(ix + iw, rect.x + rect.w) - ox
            oh = min(iy + ih, rect.y + rect.h) - oy
            inter = max(0, ow) * max(0, oh)
            union = iw * ih + rect.w * rect.h - inter
            if union > 0 and inter / float(union) > 0.9:
                item = it
                reused = True
                break

        if item.roi is not None and not reused:
            # 已经有框（且不是同一位置）就自动新建一项，保证可以继续画
            self.add_item(auto=True)
            item = self._current_item()
            if item is None:
                return
            item.roi = None
        self._push_undo()
        item.roi = rect
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self.statusBar().showMessage("框已更新（按【单帧】或【开始】刷新检测结果）", 3000)


    def _sync_view(self) -> None:
        # 先清掉所有旧小框 + 不属于任何检测项的图形（野框/野线）
        self.view.clear_all_cells()
        self.view.purge_unknown({it.id for it in self.recipe.items})
        # 分了小框的项不再画父框（只要 N 个小框）
        _ordered = [it for it in self._ordered_with_roi() if not it.params.get("cells")]
        self.view.sync_rois([(it.id, it.roi) for it in _ordered],
                            labels={it.id: self._box_no(it) for it in _ordered})
        try:
            self._sync_pick_lines()          # 框内抓取线
        except Exception:                    # noqa: BLE001
            pass
        try:
            self._refresh_box_combos()      # 框号下拉跟着画面同步
            self._refresh_rule_options()    # 基准框（框/L线）跟着同步
        except Exception:
            pass
        self.view.set_track_rect(self.recipe.track.roi if self.recipe.track.valid else None)
        loc = dict(self.recipe.locator or {})
        loc_rect = None
        if loc.get("rect"):
            lr = loc["rect"]
            loc_rect = Rect(*lr) if isinstance(lr, (list, tuple)) else lr
        # 把检测框夹到画面范围内（超出画面就选不中，现场要求不允许）
        fw, fh = self.view.frame_size()
        if fw > 0 and fh > 0:
            changed = False
            for it in self.recipe.items:
                it.roi = _clamp_rect(it.roi, fw, fh)
                if it.roi is not None:
                    changed = True
            for it in self.recipe.items:
                it.params["cells"] = [_clamp_cell(c, fw, fh) for c in (it.params.get("cells") or [])]
        self.view.set_extra_rects(self.recipe.detect_area, loc_rect)
        # 当前检测项的小框（有就画出来，可单独拖）
        cur = self._current_item()
        if cur is not None:
            try:
                pno = int(self._box_no(cur))
            except ValueError:
                pno = 0
            self.view.set_cells(cur.id, list(cur.params.get("cells") or []), parent_no=pno)
            for it in self.recipe.items:
                if it is not cur:
                    self.view.clear_cells(it.id)

    # ------------------------------------------------------------------ 标定
    def _toggle_calib(self, on: bool) -> None:
        self._calib_points = []
        self.view.set_calib_mode(on)
        if on:
            self.view.clear_calib_marks()
            self.statusBar().showMessage("标定：请在图像上点第 1 个点（已知长度的起点）", 8000)
        else:
            self._show_calib_marks()

    def _on_calib_point(self, x: float, y: float) -> None:
        self._calib_points.append((x, y))
        self.view.show_calib_points(self._calib_points)
        if len(self._calib_points) == 1:
            self.statusBar().showMessage("标定：再点第 2 个点（已知长度的终点）", 8000)
            return
        p1, p2 = self._calib_points[-2], self._calib_points[-1]
        dist_px = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        known, ok = QInputDialog.getDouble(
            self, "两点标定",
            f"这两点的实际长度（mm）？\n（当前像素距离 {dist_px:.2f} px）",
            10.0, 0.0001, 1e6, 4)
        if ok and known > 0:
            try:
                self.recipe.calibration.set_by_two_points(p1, p2, known, axis="auto")
                self.statusBar().showMessage(f"标定完成：{self.recipe.calibration.describe()}", 8000)
            except ValueError as exc:
                self.statusBar().showMessage(f"标定失败：{exc}", 6000)
        else:
            self.view.clear_calib_marks()
            self._show_calib_marks()
        self.act_calib.setChecked(False)
        self._refresh_calib_label()

    def calibrate_by_value(self) -> None:
        """标定(数值)：直接填 X/Y 比例，或用「真实值 vs 实测值」反算（现场常用）。"""
        dlg = CalibrationDialog(self.recipe.calibration, self)
        if dlg.exec() and dlg.result_cal is not None:
            self.recipe.calibration = dlg.result_cal
            self._refresh_calib_label()
            self.statusBar().showMessage(f"标定已更新：{self.recipe.calibration.describe()}", 8000)

    # ------------------------------------------------------------------ 跟踪框
    # ------------------------------------------------------------------ 批量对齐
    def _selected_roi_ids(self) -> list[str]:
        """当前选中的对象（不足 2 个时退回"所有有 ROI 的对象"）。"""
        ids: list[str] = []
        for k in self.view.selection():
            if not k:
                continue
            base = k.split("#")[0]          # 小框 → 归到它所属的检测项
            if base not in ids:
                ids.append(base)
        if len(ids) < 2:
            ids = [it.id for it in self.recipe.items if it.roi is not None]
        return ids

    def batch_align(self, action: str) -> None:
        """把选中的多个 ROI 一次性摆齐（上/下/左/右对齐、等大小、等间距）。"""
        ids = self._selected_roi_ids()
        if len(ids) < 2:
            self.statusBar().showMessage("批量对齐：请先选中至少 2 个有 ROI 的检测项（Ctrl 点选或 Ctrl 拖框）", 6000)
            return
        rects = {it.id: it.roi for it in self.recipe.items
                 if it.id in ids and it.roi is not None}

        # 现场用法：选中 1 条直线 + 若干框，点对齐 = 以直线为基准摆框
        _lines = {k: r for k, r in rects.items() if r.is_line() and r.line}
        _boxes = {k: r for k, r in rects.items() if not r.is_line()}
        if len(_lines) == 1 and _boxes and action in ("align_top", "align_bottom",
                                                      "align_left", "align_right"):
            ln = next(iter(_lines.values()))
            x1, y1, x2, y2 = ln.line
            new = {}
            for k, r in _boxes.items():
                if action == "align_bottom":
                    new[k] = Rect(r.x, int(round(max(y1, y2) - r.h)), r.w, r.h)
                elif action == "align_top":
                    new[k] = Rect(r.x, int(round(min(y1, y2))), r.w, r.h)
                elif action == "align_left":
                    new[k] = Rect(int(round(min(x1, x2))), r.y, r.w, r.h)
                else:
                    new[k] = Rect(int(round(max(x1, x2) - r.w)), r.y, r.w, r.h)
            self._push_undo()
            for it in self.recipe.items:
                if it.id in new:
                    old_roi = it.roi
                    it.roi = new[it.id]
                    if (old_roi is not None and it.params.get("cells")
                            and not it.roi.is_line()):
                        ddx = it.roi.x - old_roi.x
                        ddy = it.roi.y - old_roi.y
                        it.params["cells"] = [[c[0] + ddx, c[1] + ddy, c[2], c[3]]
                                              for c in it.params["cells"]]
            self._refresh_item_list(select=self._current_index())
            self._sync_view()
            self.view.set_selection([k for k in ids])
            self.statusBar().showMessage(
                f"已以直线为基准{ACTION_LABELS[action]}：{len(new)} 个框", 4000)
            return

        try:
            new = align_rects(rects, action, order=ids)
        except ValueError as exc:
            self.statusBar().showMessage(f"批量对齐：{exc}", 6000)
            return
        # 留一份快照用于撤销
        self._push_undo()
        self._align_undo = [(it.id, it.roi) for it in self.recipe.items if it.id in rects]
        for it in self.recipe.items:
            if it.id in new:
                old_roi = it.roi
                it.roi = new[it.id]
                if (old_roi is not None and not old_roi.is_line()
                        and it.params.get("cells") and not it.roi.is_line()):
                    ddx = it.roi.x - old_roi.x
                    ddy = it.roi.y - old_roi.y
                    it.params["cells"] = [[c[0] + ddx, c[1] + ddy, c[2], c[3]]
                                          for c in it.params["cells"]]
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self.view.set_selection([k for k in ids if k in {i.id for i in self.recipe.items}])
        self.statusBar().showMessage(
            f"已{ACTION_LABELS[action]}：{len(new)} 个对象", 4000)

    def undo_align(self) -> None:
        """撤销上一次批量对齐。"""
        snapshot = getattr(self, "_align_undo", None)
        if not snapshot:
            self.statusBar().showMessage("没有可撤销的对齐操作", 3000)
            return
        for item_id, rect in snapshot:
            for it in self.recipe.items:
                if it.id == item_id:
                    it.roi = rect
        self._align_undo = None
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self.statusBar().showMessage("已撤销上一次对齐", 3000)

    def select_all_rois(self) -> None:
        self.view.set_selection([it.id for it in self.recipe.items if it.roi is not None])

    def clear_selection(self) -> None:
        self.view.clear_selection()

    def draw_line_roi(self) -> None:
        if getattr(self, "act_multi_draw", None) is not None and self.act_multi_draw.isChecked():
            self.act_multi_draw.setChecked(False)   # 画直线时一定退出矩形模式
        if getattr(self, "act_select", None) is not None and self.act_select.isChecked():
            self.act_select.setChecked(False)
        """画线形 ROI（任意角度；按住 Shift 吸附 0/45/90 度）。"""
        self.view.set_line_mode(True)
        self.statusBar().showMessage("画线：在画面上拖一条线（按住 Shift 吸附水平/45°/垂直）", 10000)

    def _on_line_created(self, rect: Rect) -> None:
        item = self._current_item()
        # 当前项已经有框（矩形或线）时，新建一项，绝不覆盖已有框
        if item is not None and item.roi is not None:
            self.add_item()
            item = self._current_item()
        if item is None:
            self.add_item()
            item = self._current_item()
            if item is None:
                return
        item.roi = rect
        self._sync_view()          # 先让线/框立刻显示
        QTimer.singleShot(0, lambda: self._refresh_item_list(select=self._current_index()))
        ln = rect.line or (0, 0, 0, 0)
        # 保持"画直线"模式：可以接着画第二根、第三根；想调整就点【选择/移动】或按 Esc
        self.view.set_line_mode(True)
        self.statusBar().showMessage(
            f"{item.id} 已画直线 ({ln[0]},{ln[1]})→({ln[2]},{ln[3]})；可继续画下一根（按 Esc 或点【选择/移动】退出）", 6000)

    def draw_track_roi(self) -> None:
        """进入"画跟踪框"模式：在画面上拖一个框，框住产品上稳定的特征。"""
        self.view.set_track_mode(True)
        self.statusBar().showMessage("跟踪框：请在画面上拖一个框，框住产品上位置稳定的特征（螺丝孔/丝印/边缘）", 10000)

    def _on_cells_edited(self, item_id: str, rects: list) -> None:
        item = next((it for it in self.recipe.items if it.id == item_id), None)
        if item is None:
            return
        self._push_undo()
        sel = list(self.view.selection())
        item.params["cells"] = [[int(v) for v in r] for r in (rects or [])]
        if sel:
            QTimer.singleShot(0, lambda: self.view.set_selection(sel))
        self.statusBar().showMessage("框已更新（按【单帧】或【开始】刷新检测结果）", 3000)
        self.statusBar().showMessage(f"{_op_label(item)}：已更新 {len(rects)} 个小框", 4000)

    def _on_area_created(self, rect: Rect) -> None:
        self._push_undo()
        self.recipe.detect_area = rect
        self.statusBar().showMessage(f"检测区域已设置：{rect.w}x{rect.h}（黄色框），检测框/定位模板都要在里面", 7000)
        self._sync_view()

    def _on_locator_created(self, rect: Rect) -> None:
        self._push_undo()
        self.recipe.locator = {"rect": list(rect.as_tuple()),
                               "path": self.recipe.locator.get("path", ""),
                               "score": int(self.recipe.locator.get("score", 80) or 80)}
        area = self.recipe.detect_area
        if area is not None and not (rect.x >= area.x and rect.y >= area.y
                                     and rect.x + rect.w <= area.x + area.w
                                     and rect.y + rect.h <= area.y + area.h):
            QMessageBox.warning(self, "定位模板超出检测区域",
                                "定位模板必须完全落在黄色检测区域内，否则每次都会判定为定位失败。请重新画一个更小的定位框。")
            self.statusBar().showMessage("定位模板超出检测区域 —— 请重画", 8000)
        else:
            self.statusBar().showMessage("定位框已设置（青色）→ 点【截取模板】保存当前画面", 7000)
        self._sync_view()

    def _on_track_created(self, rect: Rect) -> None:
        self.recipe.track.roi = rect
        self.recipe.track.enabled = True
        self._sync_view()
        self._refresh_track_label()
        self.statusBar().showMessage(f"跟踪框已设置：{rect.w}x{rect.h} @ {rect.x},{rect.y}", 8000)

    def clear_track(self) -> None:
        self.recipe.track.roi = None
        self.recipe.track.enabled = False
        self._sync_view()
        self._refresh_track_label()
        self.statusBar().showMessage("跟踪框已清除", 4000)

    def _on_track_score_changed(self, value: float) -> None:
        self.recipe.track.min_score = float(value)

    def _refresh_track_label(self) -> None:
        """跟踪面板已移除；状态改在状态栏/跟踪设置里显示。"""
        tr = self.recipe.track
        if tr.roi is not None:
            self.statusBar().showMessage(
                f"跟踪基准：{tr.roi.w}x{tr.roi.h} @ {tr.roi.x},{tr.roi.y}（相似度 ≥{tr.min_score:.2f}）", 4000)

    def clear_calibration(self) -> None:
        self.recipe.calibration = Calibration()
        self.view.clear_calib_marks()
        self._refresh_calib_label()
        self.statusBar().showMessage("标定已清除", 4000)

    def _show_calib_marks(self) -> None:
        cal = self.recipe.calibration
        if cal.valid and len(cal.points_px) >= 2:
            self.view.show_calib_points([tuple(p) for p in cal.points_px])
        else:
            self.view.clear_calib_marks()

    def _refresh_calib_label(self) -> None:
        cal = self.recipe.calibration
        label = "标定：" + (cal.describe() if cal.valid else "未标定")
        self.lbl_calib.setText(label)
        self._show_calib_marks()

    # ------------------------------------------------------------------ 文件
    DEFAULT_ITEMS = (
        ("间距测量", "distance_pp", {"axis": "x"}),
        ("Y方向距离", "distance_pp", {"axis": "y"}),
        ("平整度测量", "angle_ppp", {}),
        ("面积测量", "area_of_blob", {"judge": {"kind": "range", "min": 10, "max": 999999}}),
    )

    def new_recipe(self) -> None:
        if any(it.roi is not None for it in self.recipe.items) and not self._ask_save_before("新建模板"):
            return
        # 运行中换配方会让采集线程读到半成品 → 先停再换
        try:
            if self.manager.running():
                self.manager.stop_all()
        except Exception:                                # noqa: BLE001
            pass
        from ..ops import Item

        rec = Recipe(name="untitled")      # 新模板空白：不预置默认检测项
        self.station.set_recipe(rec, "")
        self._cur_id = rec.items[0].id if rec.items else None
        self._active_path = ""
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()

    def open_recipe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开配方", self.recipes_dir, "配方 (*.json)")
        if path:
            self._load_recipe(path)

    def save_recipe(self) -> None:
        path = self.station.recipe_path or self._active_path
        _fr = getattr(self, "_last_view_frame", None)
        if _fr is not None and getattr(_fr, "shape", None):
            self.recipe.frame_width = int(_fr.shape[1])   # 记住画框时的画面宽度
        if not path:
            self.save_recipe_as()
            return
        try:
            self.recipe.save(path)
            self.station.recipe_path = path
            self.statusBar().showMessage(f"已保存 {path}", 4000)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))

    def save_recipe_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", os.path.join(self.recipes_dir, f"{self.recipe.name}.json"), "配方 (*.json)")
        if not path:
            return
        self.recipe.name = os.path.splitext(os.path.basename(path))[0]
        self.recipe.save(path)
        self.station.recipe_path = path
        self._active_path = path
        self.statusBar().showMessage(f"已保存 {path}", 4000)

    def import_hpro(self) -> None:
        from ..hpro import parse_file
        from ..hpro_import import import_template, load_mapping

        if not path:
            return
        map_path = os.path.join(BASE_DIR, "config", "hpro_types.json")
        if not os.path.exists(map_path):
            QMessageBox.critical(self, "导入失败", f"找不到映射表: {map_path}")
            return
        try:
            tpl = parse_file(path)
            result = import_template(tpl, load_mapping(map_path))
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.station.set_recipe(result.recipe, "")
        self._active_path = ""
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()
        msg = f"导入 {result.mapped} 项，跳过 {result.skipped} 项"
        if result.issues:
            msg += "\n\n" + "\n".join(f"[{i.level}] 站{i.station} 项{i.item}: {i.message}"
                                      for i in result.issues[:10])
        QMessageBox.information(self, "导入完成", msg)
        self.statusBar().showMessage(msg.splitlines()[0], 6000)

    # ------------------------------------------------------------------ 关闭
    def keyPressEvent(self, event) -> None:            # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._esc_exit()
            return
        super().keyPressEvent(event)

    def _esc_exit(self) -> None:
        """Esc：退出画线/画框，回到选择/移动。"""
        self.view.set_line_mode(False)
        if getattr(self, "act_select", None) is not None:
            self.act_select.setChecked(True)
            self.view.set_select_mode(True)
        self.statusBar().showMessage("已退出绘制，回到选择/移动", 3000)

    def _ask_save_before(self, action: str) -> bool:
        """关闭软件/新建模板前问一句：是否保存当前模板？（是 / 否 / 取消）"""
        try:
            from ..qtcompat import QApplication as _App
            if _App.platformName() == "offscreen":
                return True                     # 自测/无界面环境不弹窗
        except Exception:                       # noqa: BLE001
            pass
        box = QMessageBox(self)
        box.setWindowTitle("保存模板")
        box.setText(f"{action}前，是否保存当前模板？")
        box.setInformativeText(f"当前模板：{self.recipe.name}\n不保存的话，刚画的框和规则会丢。")
        b_yes = box.addButton("是", QMessageBox.ButtonRole.AcceptRole)
        b_no = box.addButton("否", QMessageBox.ButtonRole.DestructiveRole)
        b_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_yes)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_yes:
            self.save_recipe()
            return True
        if clicked is b_no:
            return True
        if clicked is b_cancel:
            return False
        return False

    def closeEvent(self, event) -> None:               # noqa: N802
        if any(it.roi is not None for it in self.recipe.items) and not self._ask_save_before("关闭软件"):
            event.ignore()
            return
        try:
            self.manager.stop_all()
            self.manager.close_all()
            self.settings.save()
        finally:
            super().closeEvent(event)
