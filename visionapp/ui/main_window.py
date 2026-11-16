"""VisionApp 主界面（多机位版）：6 路画面同屏 + 单机位编辑 + 整线判定。"""

from __future__ import annotations

import math
import os
from typing import Any

import cv2
import numpy as np
from ..qtcompat import Qt, QTimer
from ..qtcompat import QAction, QColor
from ..qtcompat import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..align import ACTION_LABELS, align_rects
from ..alarm import Alarm, AlarmConfig
from ..calibration import Calibration
from ..camera_cfg import (
    BRAND_MINDVISION, BRAND_UNCONNECTED, MODEL_MV_GE500M_TPO, trigger_of, update_camera_numbers,
)
from ..mindvision import MindVisionError, MindVisionSource
from ..core import Frame, Rect
from ..security import ROLE_ENGINEER, ROLE_LABELS, ROLE_OPERATOR, ROLE_SUPER, SecurityConfig
from ..ops import REGISTRY, Item
from ..outputs import OutputConfig, OutputManager
from ..paths import ensure_data_dir
from ..recipe import Pipeline, Recipe
from ..recorder import Recorder, RecorderConfig
from ..runner import MODE_CONTINUOUS, MODE_LABELS, MODE_SINGLE, Runner
from ..settings import AppSettings
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
from .security_dialog import LoginDialog, PasswordDialog, SuperPasswordDialog
from ..tracking import TrackConfig
from ..tuning import rule_preview, selection_after_draw
from .station_tile import StationTile

BASE_DIR = ensure_data_dir()

OP_SCHEMA: dict[str, list[tuple[str, str, Any, str]]] = {
    "find_blobs": [
        ("threshold_mode", "choice:dark,light", "dark", "阈值模式"),
        ("threshold", "int", 128, "阈值下限"),
        ("threshold_low", "int", 111, "下阈值（与上阈值一起用；0/255 拦截）"),
        ("threshold_high", "int", 254, "阈值上限（1–254，0/255 拦截）"),
        ("polarity", "choice:黑色底部,白色底部,白色中下,黑色中下,白色中上,白色左侧", "白色底部", "黑白极性"),
        ("min_width", "int", 0, "最小宽度（像素，0=不滤）"),
        ("min_height", "int", 0, "最小高度（像素，0=不滤）"),
        ("min_circularity", "float", 0.0, "最小圆度（0=不滤）"),
        ("pick_zone", "choice:top,upper,center,lower,bottom", "center",
         "抓取位置·区位（top=上/upper=上1/4/center=中/lower=下3/4/bottom=下）"),
        ("show_pick", "choice:off,on", "off", "画出抓取点"),
        ("show_mask", "choice:off,on", "off", "显示阈值屏蔽区（红色=被排除的像素）"),
        ("blur", "int", 5, "高斯模糊核（0=不做）"),
        ("morph_kernel", "int", 5, "形态学核（0=不做）"),
        ("min_area", "float", 500.0, "最小面积"),
        ("max_area", "float", 50000.0, "最大面积"),
    ],
    "distance_pp": [
        ("src", "str", "pins", "依赖的斑块项 id"),
        ("blob_a", "int", 0, "起始斑块序号"),
        ("blob_b", "int", 1, "终止斑块序号"),
        ("axis", "choice:xy,x,y", "xy", "X=间距 Y=贴板"),
        ("datum", "str", "", "基准对象"),
        ("nominal", "float", 0.0, "设计值"),
        ("tol_lower", "float", 0.0, "容差下限"),
        ("tol_upper", "float", 0.0, "容差上限"),
        ("each_step", "choice:否,是", "否", "逐段（否则只测起始到终止）"),
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
        ("points", "str", "", "三点坐标 x1,y1;x2,y2;x3,y3"),
    ],
}

JUDGE_KINDS = [("range", "区间"), ("design", "设计值±容差"), ("max", "上限"), ("min", "下限")]


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
        self.login_attempts = 0
        self._multi_draw = False
        self._align_undo = None
        self._edit_actions: list[QAction] = []
        self._edit_widgets: list[Any] = []

        self.security = SecurityConfig.from_dict(self.settings.security_cfg)
        self.role = ROLE_ENGINEER
        self.merged_alarm = Alarm(AlarmConfig.from_dict(self.settings.alarm_cfg))

        self.manager = self._make_manager(line, recipe, source, path)
        self.setWindowTitle(f"VisionApp - {self.manager.line.name}")
        self.resize(1720, 1000)

        self._build_ui()
        self._refresh_station_tabs()
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()
        self._refresh_track_label()
        self.role = self._initial_role()
        self._apply_role()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(33)

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
        act("新建配方", self.new_recipe)
        act_open = act("打开", self.open_recipe)
        a_save = act("保存", self.save_recipe)
        a_saveas = act("另存为", self.save_recipe_as)
        a_import = act("导入 .HPro", self.import_hpro)
        self._edit_actions += [a_save, a_saveas, a_import]
        tb.addSeparator()

        # 配置组
        tb.addWidget(QLabel(" 当前机位 "))
        self.cb_station = QComboBox()
        self.cb_station.setMinimumWidth(110)
        self.cb_station.currentIndexChanged.connect(self._on_station_changed)
        tb.addWidget(self.cb_station)

        tb.addWidget(QLabel(" 合并 "))
        self.cb_merge = QComboBox()
        for rule in MERGE_RULES:
            self.cb_merge.addItem(MERGE_LABELS[rule], rule)
        self.cb_merge.setCurrentIndex(max(0, self.cb_merge.findData(self.manager.line.merge_rule)))
        self.cb_merge.currentIndexChanged.connect(self._on_merge_changed)
        tb.addWidget(self.cb_merge)
        act(" 管理 ", self.open_recipe_manager)

        a_cam = act("相机", self.open_source_dialog)
        a_rec = act("记录设置", self.open_record_settings)
        a_out = act("输出/报警", self.open_output_settings)
        a_line = act("产线配置", self.open_line_config)
        a_auth = act("权限", self.open_security)
        self._edit_actions += [a_cam, a_rec, a_out, a_line]

        btn_align = QToolButton()
        btn_align.setText(" 对齐 ")
        btn_align.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_align = QMenu(btn_align)
        for _act_name, _label in ACTION_LABELS.items():
            _a = QAction(_label, self)
            _a.triggered.connect(lambda _=False, name=_act_name: self.batch_align(name))
            menu_align.addAction(_a)
        btn_align.setMenu(menu_align)
        tb.addWidget(btn_align)
        self.act_undo_align = act(" 撤销对齐 ", self.undo_align)
        self._edit_actions += [btn_align, self.act_undo_align]
        self.act_multi_draw = QAction("连画", self, checkable=True)
        self.act_multi_draw.setToolTip("勾上后：每拖一个框就新建一个检测项")
        self.act_multi_draw.toggled.connect(self._on_multi_draw_toggled)
        tb.addAction(self.act_multi_draw)
        self._edit_actions.append(self.act_multi_draw)
        self.act_line = act(" 画线 ", self.draw_line_roi)
        self._edit_actions.append(self.act_line)
        act("水平基准", self.toggle_hline)
        self.act_track = act(" 跟踪框 ", self.draw_track_roi)
        self._edit_actions.append(self.act_track)
        self.act_calib = QAction("标定(两点)", self, checkable=True)
        self.act_calib.toggled.connect(self._toggle_calib)
        tb.addAction(self.act_calib)
        self.act_calib_value = act(" 标定(数值) ", self.calibrate_by_value)
        self._edit_actions.append(self.act_calib_value)
        self.act_clear_calib = act(" 清除标定 ", self.clear_calibration)
        self._edit_actions.append(self.act_clear_calib)
        act("X/Y标定", self.calibrate_axis)
        act("相机参数", self.open_camera_tune)
        act("超级管理员口令", self.open_super_password)
        tb.addSeparator()

        # 运行组
        tb.addWidget(QLabel(" 模式 "))
        self.cb_mode = QComboBox()
        self.cb_mode.addItem("连续", MODE_CONTINUOUS)
        self.cb_mode.addItem("单次", MODE_SINGLE)
        tb.addWidget(self.cb_mode)
        a_start = act(" ▶ 开始 ", self.start_run)
        a_start.setShortcut("F5")
        a_start.setToolTip("开始 (F5)")
        tb.widgetForAction(a_start).setObjectName("btnStart")
        a_stop = act(" ■ 停止 ", self.stop_run)
        a_stop.setShortcut("F6")
        a_stop.setToolTip("停止 (F6)")
        tb.widgetForAction(a_stop).setObjectName("btnStop")
        a_step = act(" 单次检测 ", self.step_once)
        a_step.setShortcut("F7")
        a_step.setToolTip("单帧 (F7)")
        act(" 清空计数 ", self.reset_stats)

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
        split.addWidget(right)
        split.setSizes([1180, 500])
        self.setCentralWidget(split)
        self.statusBar().showMessage("就绪")

        self.lbl_role = QLabel("")
        self.lbl_role.setObjectName("lblRole")
        self.statusBar().addPermanentWidget(self.lbl_role)

        # 整线判定条
        self.lbl_merged = QLabel("整线：待机")
        self.lbl_merged.setObjectName("lblMerged")
        theme.set_class(self.lbl_merged, "mono")
        theme.set_state(self.lbl_merged, "idle")
        self.statusBar().addPermanentWidget(self.lbl_merged)

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
        self.lbl_fps = QLabel("--")
        self.lbl_cost = QLabel("--")
        for lab in (self.lbl_verdict, self.lbl_ok, self.lbl_ng, self.lbl_yield,
                    self.lbl_total, self.lbl_fps, self.lbl_cost):
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.set_state(self.lbl_verdict, "idle")
        for lab in (self.lbl_ok, self.lbl_ng, self.lbl_yield,
                    self.lbl_total, self.lbl_fps, self.lbl_cost):
            theme.set_class(lab, "statValue")

        def stat_key(text: str) -> QLabel:
            lab = QLabel(text)
            theme.set_class(lab, "statKey")
            return lab

        grid.addWidget(self.lbl_verdict, 0, 0, 1, 3)
        grid.addWidget(stat_key("OK"), 1, 0); grid.addWidget(stat_key("NG"), 1, 1); grid.addWidget(stat_key("良率"), 1, 2)
        grid.addWidget(self.lbl_ok, 2, 0); grid.addWidget(self.lbl_ng, 2, 1); grid.addWidget(self.lbl_yield, 2, 2)
        grid.addWidget(stat_key("总数"), 3, 0); grid.addWidget(stat_key("FPS"), 3, 1); grid.addWidget(stat_key("单帧ms"), 3, 2)
        grid.addWidget(self.lbl_total, 4, 0); grid.addWidget(self.lbl_fps, 4, 1); grid.addWidget(self.lbl_cost, 4, 2)
        self.lbl_calib = QLabel("标定：未标定")
        self.lbl_calib.setObjectName("lblCalib")
        theme.set_class(self.lbl_calib, "mono")
        grid.addWidget(self.lbl_calib, 5, 0, 1, 3)
        rv.addWidget(box_stats)

        box_track = QGroupBox("跟踪（补偿放料偏差）")
        tl = QFormLayout(box_track)
        self.lbl_track = QLabel("跟踪框：未设置")
        theme.set_class(self.lbl_track, "hint")
        tl.addRow(self.lbl_track)
        self.sp_track_score = QDoubleSpinBox()
        self.sp_track_score.setRange(0.05, 0.99)
        self.sp_track_score.setSingleStep(0.05)
        self.sp_track_score.setDecimals(2)
        self.sp_track_score.setValue(0.70)
        self.sp_track_score.valueChanged.connect(self._on_track_score_changed)
        tl.addRow("最低相似度", self.sp_track_score)
        trow = QHBoxLayout()
        self.btn_track_draw = QPushButton("画跟踪框")
        self.btn_track_draw.clicked.connect(self.draw_track_roi)
        self.btn_track_clear = QPushButton("清除")
        self.btn_track_clear.clicked.connect(self.clear_track)
        trow.addWidget(self.btn_track_draw)
        trow.addWidget(self.btn_track_clear)
        tl.addRow(trow)
        rv.addWidget(box_track)

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
        rv.addWidget(box_items, 1)

        box_base = QGroupBox("基本")
        fb = QFormLayout(box_base)
        self.ed_id = QLineEdit()
        self.ed_name = QLineEdit()
        self.cb_type = QComboBox()
        self.cb_type.addItems(sorted(REGISTRY.keys()))
        self.cb_type.currentTextChanged.connect(self._on_type_changed)
        fb.addRow("id", self.ed_id)
        fb.addRow("名称", self.ed_name)
        fb.addRow("算子", self.cb_type)
        rv.addWidget(box_base)

        box_params = QGroupBox("参数")
        self.form_params = QFormLayout(box_params)
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
        self.ck_abs = QCheckBox("结果取绝对值")
        for sp in (self.sp_lo, self.sp_hi, self.sp_design, self.sp_tol_up, self.sp_tol_lo):
            sp.setRange(-1e9, 1e9)
            sp.setDecimals(4)
        self.cb_judge.currentIndexChanged.connect(self._on_judge_changed)
        for sp in (self.sp_lo, self.sp_hi, self.sp_design, self.sp_tol_up, self.sp_tol_lo):
            sp.valueChanged.connect(self._on_judge_changed)
        self.ck_abs.toggled.connect(self._on_judge_changed)
        fj.addRow("类型", self.cb_judge)
        fj.addRow("下限", self.sp_lo)
        fj.addRow("上限", self.sp_hi)
        fj.addRow("设计值", self.sp_design)
        fj.addRow("容差上限", self.sp_tol_up)
        fj.addRow("容差下限", self.sp_tol_lo)
        fj.addRow("", self.ck_abs)
        self._judge_form = fj
        self._sync_judge_rows("range")
        self.lbl_rule_preview = QLabel("规则预览：未写设计值")
        theme.set_class(self.lbl_rule_preview, "mono")
        fj.addRow(self.lbl_rule_preview)
        rv.addWidget(box_judge)

        box_res = QGroupBox("本帧结果")
        rl = QVBoxLayout(box_res)
        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(["对象", "基准", "设计值", "实测", "面积", "NG次数", "判定"])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.tbl)
        rv.addWidget(box_res, 1)
        return right

    # ------------------------------------------------------------------ 机位
    def _refresh_station_tabs(self) -> None:
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w is not None:
                w.setParent(None)
        self.tiles = []
        cols = 3 if len(self.manager.stations) > 2 else 1
        for idx, st in enumerate(self.manager.stations):
            tile = StationTile(idx, st.cfg.display_name())
            tile.view.roiCreated.connect(lambda rect, i=idx: self._on_roi_created(rect) if i == self.active else None)
            tile.view.roiEdited.connect(lambda key, rect, i=idx: self._on_roi_edited(key, rect) if i == self.active else None)
            tile.view.roiSelected.connect(lambda key, i=idx: self._on_roi_selected(key) if i == self.active else None)
            tile.view.calibPointClicked.connect(
                lambda x, y, i=idx: self._on_calib_point(x, y) if i == self.active else None)
            tile.view.lineCreated.connect(
                lambda rect, i=idx: self._on_line_created(rect) if i == self.active else None)
            tile.view.trackCreated.connect(
                lambda rect, i=idx: self._on_track_created(rect) if i == self.active else None)
            self.grid.addWidget(tile, idx // cols, idx % cols)
            self.tiles.append(tile)

        self._building = True
        self.cb_station.clear()
        for st in self.manager.stations:
            self.cb_station.addItem(st.cfg.display_name())
        self.cb_station.setCurrentIndex(self.active)
        self._building = False

    def _on_station_changed(self, idx: int) -> None:
        if self._building or not (0 <= idx < len(self.tiles)):
            return
        self.active = idx
        for i, tile in enumerate(self.tiles):
            tile.set_active(i == idx)
        self._refresh_item_list()
        self._sync_view()
        self._refresh_calib_label()
        self._refresh_track_label()

    def _on_merge_changed(self) -> None:
        self.manager.line.merge_rule = self.cb_merge.currentData()

    # ------------------------------------------------------------------ 权限
    def _apply_role(self) -> None:
        eng = self.role in (ROLE_ENGINEER, ROLE_SUPER)
        self.editor_panel.setEnabled(eng)
        for a in self._edit_actions:
            a.setEnabled(eng)
        self.act_calib.setEnabled(eng)
        self.lbl_role.setText(f"权限：{ROLE_LABELS[self.role]}")
        theme.set_state(self.lbl_role, "warn" if eng else "idle")

    def open_security(self) -> None:
        if self.role in (ROLE_ENGINEER, ROLE_SUPER):
            dlg = PasswordDialog(self.security, self)
            dlg.setWindowTitle("权限设置（管理员）")
            if dlg.exec():
                self.settings.set("security", self.security.to_dict())
                self.settings.save()
                self._apply_role()
                self.statusBar().showMessage("权限设置已保存", 4000)
            return
        self.login_attempts += 1
        dlg = LoginDialog(self.security, self)
        if dlg.exec():
            self.role = ROLE_ENGINEER
            self._apply_role()
            self.statusBar().showMessage("已进入管理员", 4000)

    def _require_engineer(self) -> bool:
        """改检测框和规则。未登录时必须弹出原来的登录对话。"""
        if self.role in (ROLE_ENGINEER, ROLE_SUPER):
            return True
        self.login_attempts += 1
        dlg = LoginDialog(self.security, self)
        if not dlg.exec():
            self.statusBar().showMessage("未登录，不能改检测框或规则", 4000)
            return False
        self.role = ROLE_ENGINEER
        self._apply_role()
        return True

    def _require_super(self) -> bool:
        """改相机路数、品牌、X/Y 比例。只认超级管理员摘要，菜单不能直接升级。"""
        if self.role == ROLE_SUPER:
            return True
        self.login_attempts += 1
        dlg = LoginDialog(
            self.security, self,
            checker=self.security.check_super,
            title="超级管理员登录",
        )
        if not dlg.exec():
            self.statusBar().showMessage("未通过超级管理员登录，相机数量、品牌和比例未改", 5000)
            return False
        self.role = ROLE_SUPER
        self._apply_role()
        return True

    def open_super_password(self) -> None:
        """设置超级管理员口令。取消登录时角色保持不动。"""
        if self.role not in (ROLE_ENGINEER, ROLE_SUPER):
            self.login_attempts += 1
            dlg = LoginDialog(self.security, self)
            if not dlg.exec():
                self.statusBar().showMessage("未登录，不能升级权限", 4000)
                return
            self.role = ROLE_ENGINEER
            self._apply_role()
        dlg2 = SuperPasswordDialog(self.security, self)
        if dlg2.exec():
            self.settings.set("security", self.security.to_dict())
            self.settings.save()
            self.statusBar().showMessage("超级管理员口令已保存，进入该权限仍要再登录", 5000)

    # ------------------------------------------------------------------ 产线配置
    def open_line_config(self) -> None:
        if not self._require_super():
            return
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
        self.cb_merge.setCurrentIndex(max(0, self.cb_merge.findData(line.merge_rule)))
        self.setWindowTitle(f"VisionApp - {line.name}")
        errors = self.manager.open_all()
        for e in errors:
            self.statusBar().showMessage(f"机位打开失败：{e}", 8000)

    # ------------------------------------------------------------------ 运行
    def start_run(self) -> None:
        if self.manager.running():
            return
        if not any(st.source for st in self.manager.stations if st.cfg.enabled):
            QMessageBox.warning(self, "无法运行", "没有可用的相机/帧源，请先点“相机”连接")
            return
        self.merged_alarm.cfg = AlarmConfig.from_dict(self.settings.alarm_cfg)
        self.manager.start_all(self.cb_mode.currentData())
        self.statusBar().showMessage("开始运行（整线）")

    def stop_run(self) -> None:
        self.manager.stop_all()
        self.statusBar().showMessage("已停止")

    def step_once(self) -> None:
        if self.manager.running():
            return
        for st in self.manager.stations:
            if st.cfg.enabled and st.source is not None:
                st.open_recorder()
                st.open_outputs()
                st.runner.step()
        self._poll()

    def reset_stats(self) -> None:
        for st in self.manager.stations:
            st.runner.reset_stats()

    def _poll(self) -> None:
        merged_frames = 0
        for idx, st in enumerate(self.manager.stations):
            snap = st.snapshot()
            tile = self.tiles[idx]
            if snap.last_annotated is not None:
                img = snap.last_annotated
                if idx != self.active and img.shape[1] > 900:
                    scale = 900.0 / img.shape[1]
                    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                tile.view.set_frame(img)
            tile.update_stats(snap, st.cfg.enabled)
            merged_frames = max(merged_frames, snap.total)

        for i, tile in enumerate(self.tiles):
            tile.set_active(i == self.active)

        snap = self.station.snapshot()
        self._update_stats(snap)
        if snap.last_result:
            self._fill_result_table(snap.last_result)
        self._sync_view()

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

    def _update_stats(self, snap) -> None:
        self.lbl_ok.setText(str(snap.ok))
        self.lbl_ng.setText(str(snap.ng))
        self.lbl_total.setText(str(snap.total))
        self.lbl_yield.setText(f"{snap.yield_rate * 100:.1f}%" if snap.total else "--")
        self.lbl_fps.setText(f"{snap.fps:.1f}" if snap.fps else "--")
        self.lbl_cost.setText(f"{snap.last_elapsed_ms:.1f}" if snap.total else "--")
        if snap.last_error:
            self.lbl_verdict.setText("异常")
            theme.set_state(self.lbl_verdict, "warn")
            self.statusBar().showMessage(snap.last_error, 8000)
        elif snap.last_result is not None:
            ok = snap.last_result.get("ok")
            self.lbl_verdict.setText("OK" if ok else "NG")
            theme.set_state(self.lbl_verdict, "ok" if ok else "ng")
        else:
            self.lbl_verdict.setText("运行中" if snap.running else "待机")
            theme.set_state(self.lbl_verdict, "idle")

    def _fill_result_table(self, data: dict[str, Any]) -> None:
        rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for item_id, res in (data.get("items") or {}).items():
            steps = res.get("steps") or [res]
            for step in steps:
                rows.append((item_id, res, step))
        self.tbl.setRowCount(len(rows))
        for r, (item_id, res, step) in enumerate(rows):
            src = step if step is not res else res
            self.tbl.setItem(r, 0, QTableWidgetItem(item_id))
            self.tbl.setItem(r, 1, QTableWidgetItem(str(src.get("datum", "") or "")))
            self.tbl.setItem(r, 2, QTableWidgetItem(str(src.get("design", "") or "")))
            val = src.get("value", None)
            unit = src.get("unit", "")
            text = "-" if val is None else f"{float(val):.3f} {unit}".strip()
            cell_val = QTableWidgetItem(text)
            cell_val.setFont(theme.mono_font(12))
            self.tbl.setItem(r, 3, cell_val)
            area = src.get("area", None)
            self.tbl.setItem(r, 4, QTableWidgetItem("-" if area is None else f"{float(area):.0f}"))
            self.tbl.setItem(r, 5, QTableWidgetItem(str(res.get("ng_count", 0))))
            ok = src.get("ok")
            cell = QTableWidgetItem({True: "OK", False: "NG", None: "-"}.get(ok, "-"))
            cell.setForeground(QColor(theme.color("state.ok") if ok else theme.color("state.ng")))
            cell.setFont(theme.mono_font(12))
            self.tbl.setItem(r, 6, cell)

    # ------------------------------------------------------------------ 相机
    def open_source_dialog(self) -> None:
        st = self.station
        dlg = SourceDialog(st.cfg.source, self)
        if dlg.exec() and dlg.result_cfg:
            try:
                new_src = build_source(dlg.result_cfg)
                new_src.open()
            except Exception as exc:                   # noqa: BLE001
                QMessageBox.critical(self, "连接失败", str(exc))
                return
            if st.runner.running:
                st.stop()
            st.close_source()
            st.source = new_src
            st.runner.set_source(new_src)
            st.cfg.source = dict(dlg.result_cfg)
            self.settings.set("last_source", dlg.result_cfg)
            info = new_src.info
            self.statusBar().showMessage(
                f"{st.cfg.display_name()} 已连接 {info.name} {info.width}x{info.height}", 6000)

    def open_record_settings(self) -> None:
        cfg = RecorderConfig.from_dict(self.settings.recorder_cfg)
        dlg = RecordSettingsDialog(cfg, self)
        if dlg.exec():
            new_cfg = dlg.result_cfg()
            self.settings.set("results_root", new_cfg.root)
            self.settings.set("csv_enabled", new_cfg.csv_enabled)
            self.settings.set("save_ng_images", new_cfg.save_ng_images)
            self.settings.set("save_ok_images", new_cfg.save_ok_images)
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
        i = self._current_index()
        if 0 <= i < len(self.recipe.items):
            return self.recipe.items[i]
        return None

    def _refresh_item_list(self, select: int | None = None) -> None:
        self._building = True
        self.list_items.clear()
        for it in self.recipe.items:
            if it.roi is None:
                roi = "-"
            elif it.roi.is_line() and it.roi.line is not None:
                ln = it.roi.line
                roi = f"LINE({ln[0]},{ln[1]})→({ln[2]},{ln[3]})"
            else:
                roi = f"{it.roi.x},{it.roi.y} {it.roi.w}x{it.roi.h}"
            self.list_items.addItem(f"{it.id}  [{it.type}]  {roi}")
        if select is not None and 0 <= select < self.list_items.count():
            self.list_items.setCurrentRow(select)
        self._building = False
        self._fill_editor()

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
        self.cb_type.setCurrentText(item.type)
        self._rebuild_param_form(item)
        self._refresh_rule_preview(item)
        judge = item.judge or {}
        idx = max(0, self.cb_judge.findData(judge.get("kind", "range")))
        self.cb_judge.setCurrentIndex(idx)
        self.sp_lo.setValue(float(judge.get("min", judge.get("tol_lower", 0)) or 0))
        self.sp_hi.setValue(float(judge.get("max", judge.get("tol_upper", 0)) or 0))
        self.sp_design.setValue(float(judge.get("design", 0) or 0))
        self.sp_tol_up.setValue(float(judge.get("tol_upper", 0) or 0))
        self.sp_tol_lo.setValue(float(judge.get("tol_lower", 0) or 0))
        self.ck_abs.setChecked(bool(judge.get("abs", False)))
        self._sync_judge_rows(str(judge.get("kind", "range")))

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
        from ..qtcompat import QSpinBox
        if kind == "int":
            w = QSpinBox()
            w.setRange(-1_000_000, 1_000_000)
            w.setValue(int(value or 0))
            w.valueChanged.connect(lambda v, k=key: self._set_param(k, int(v)))
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
            w.addItems(kind.split(":", 1)[1].split(","))
            w.setCurrentText(str(value))
            w.currentTextChanged.connect(lambda v, k=key: self._set_param(k, v))
            return w
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
        if self._building:
            return
        item = self._current_item()
        if item is None:
            return
        if key in ("threshold", "threshold_low", "threshold_high"):
            try:
                extreme = int(value) in (0, 255)
            except (TypeError, ValueError):
                extreme = False
            if extreme:
                QMessageBox.warning(self, "阈值", "0 和 255 不能用。数值会留下，但这次定位和测量会被拦住。")
        item.params[key] = value
        if key in ("nominal", "tol_lower", "tol_upper", "axis"):
            self._refresh_rule_preview(item)

    def _refresh_rule_preview(self, item: Item) -> None:
        if not hasattr(self, "lbl_rule_preview"):
            return
        p = item.params
        if "nominal" not in p:
            self.lbl_rule_preview.setText("规则预览：未写设计值")
            return
        self.lbl_rule_preview.setText("规则预览：" + rule_preview(
            float(p.get("nominal") or 0),
            float(p.get("tol_lower") or 0),
            float(p.get("tol_upper") or 0),
            str(p.get("axis") or "xy"),
        ))

    # ------------------------------------------------------------------ 检测项操作
    def add_item(self) -> None:
        n = 1
        existing = {it.id for it in self.recipe.items}
        while f"item{n}" in existing:
            n += 1
        item = Item(id=f"item{n}", type="find_blobs", roi=None,
                    params={k: d for k, _t, d, _s in OP_SCHEMA.get("find_blobs", [])})
        self.recipe.items.append(item)
        self._refresh_item_list(select=len(self.recipe.items) - 1)
        self._sync_view()

    def dup_item(self) -> None:
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
        i = self._current_index()
        if 0 <= i < len(self.recipe.items):
            self.recipe.items.pop(i)
            self._refresh_item_list(select=min(i, len(self.recipe.items) - 1))
            self._sync_view()

    def move_item(self, delta: int) -> None:
        i = self._current_index()
        j = i + delta
        if 0 <= i < len(self.recipe.items) and 0 <= j < len(self.recipe.items):
            self.recipe.items[i], self.recipe.items[j] = self.recipe.items[j], self.recipe.items[i]
            self._refresh_item_list(select=j)

    def _on_item_selected(self, row: int) -> None:
        if self._building:
            return
        self._fill_editor()
        item = self._current_item()
        self.view.set_active_roi(item.id if item else None)

    def _on_type_changed(self, type_name: str) -> None:
        if self._building:
            return
        item = self._current_item()
        if item is None:
            return
        item.type = type_name
        item.params = {k: d for k, _t, d, _s in OP_SCHEMA.get(type_name, [])}
        self._rebuild_param_form(item)
        self._refresh_item_list(select=self._current_index())

    def _sync_judge_rows(self, kind: str) -> None:
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
                form.setRowVisible(row, show)
                continue
            except Exception:                          # noqa: BLE001
                pass
            w = fields.get(row)
            if w is None:
                continue
            w.setVisible(show)
            lbl = form.labelForField(w)
            if lbl is not None:
                lbl.setVisible(show)

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

    def _on_roi_edited(self, item_id: str, rect: Rect | None) -> None:
        item = next((it for it in self.recipe.items if it.id == item_id), None)
        if item is None:
            return
        item.roi = rect
        self._refresh_item_list(select=self._current_index())
        self._sync_view()

    def _on_roi_selected(self, item_id: str) -> None:
        for i, it in enumerate(self.recipe.items):
            if it.id == item_id:
                self.list_items.setCurrentRow(i)
                break

    def _on_roi_created(self, rect: Rect) -> None:
        ctrl = bool(self.view.last_draw_ctrl)
        if getattr(self, "_multi_draw", False) or ctrl or self._current_item() is None:
            self.add_item()
        item = self._current_item()
        if item is None:
            self.statusBar().showMessage("请先在右侧选中一个检测项，再画框（或打开连画）", 4000)
            return
        item.roi = rect
        chosen = selection_after_draw(self.view.selected_keys(), item.id, ctrl)
        self._refresh_item_list(select=self.recipe.items.index(item))
        self._sync_view()
        self.view.set_selection(chosen, item.id)
        self.statusBar().showMessage(f"{item.id} ROI 已设置: {rect.as_tuple()}", 3000)

    def _on_multi_draw_toggled(self, on: bool) -> None:
        self._multi_draw = bool(on)
        self.statusBar().showMessage(
            "连画模式：开（拖一个框 = 新建一个检测项）" if on else "连画模式：关", 4000)

    def toggle_hline(self) -> None:
        self.view.draw_tool = "rect" if self.view.draw_tool == "hline" else "hline"
        if self.view.draw_tool == "hline":
            self.statusBar().showMessage("水平基准线：只向右延伸", 3000)
        else:
            self.statusBar().showMessage("矩形检测框", 3000)

    def align_selected(self, mode: str) -> None:
        """旧入口：未登录时仍要弹出登录。已登录则转到按中心对齐。"""
        if not self._require_engineer():
            return
        mapping = {
            "top": "align_top", "bottom": "align_bottom", "left": "align_left",
            "right": "align_right", "equal": "same_size", "hspace": "space_h", "vspace": "space_v",
        }
        self.batch_align(mapping.get(mode, mode))

    def _selected_roi_ids(self) -> list[str]:
        ids = [k for k in self.view.selection() if k]
        if len(ids) < 2:
            ids = [it.id for it in self.recipe.items if it.roi is not None]
        return ids

    def batch_align(self, action: str) -> None:
        ids = self._selected_roi_ids()
        if len(ids) < 2:
            self.statusBar().showMessage("批量对齐：请先选中至少 2 个有 ROI 的检测项", 6000)
            return
        rects = {it.id: it.roi for it in self.recipe.items if it.id in ids and it.roi is not None}
        try:
            new = align_rects(rects, action, order=ids)
        except ValueError as exc:
            self.statusBar().showMessage(f"批量对齐：{exc}", 6000)
            return
        self._align_undo = [(it.id, it.roi) for it in self.recipe.items if it.id in rects]
        for it in self.recipe.items:
            if it.id in new:
                it.roi = new[it.id]
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        self.view.set_selection([k for k in ids if k in {i.id for i in self.recipe.items}])
        self.statusBar().showMessage(f"已{ACTION_LABELS.get(action, action)}：{len(new)} 个对象", 4000)

    def undo_align(self) -> None:
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
        self.view.set_line_mode(True)
        self.statusBar().showMessage("画线：在画面上拖一条线（按住 Shift 吸附水平/45°/垂直）", 10000)

    def _on_line_created(self, rect: Rect) -> None:
        item = self._current_item()
        if item is None:
            self.statusBar().showMessage("请先在右侧选中一个检测项，再画线", 4000)
            return
        item.roi = rect
        self._refresh_item_list(select=self._current_index())
        self._sync_view()
        ln = rect.line or (0, 0, 0, 0)
        self.statusBar().showMessage(
            f"{item.id} 已设为线形 ROI：({ln[0]},{ln[1]})→({ln[2]},{ln[3]})", 5000)

    def draw_track_roi(self) -> None:
        self.view.set_track_mode(True)
        self.statusBar().showMessage("跟踪框：拖一个框，框住产品上位置稳定的特征", 10000)

    def _on_track_created(self, rect: Rect) -> None:
        cfg = self._track_cfg()
        cfg.roi = rect
        cfg.enabled = True
        self.recipe.track = cfg
        self._sync_view()
        self._refresh_track_label()
        self.statusBar().showMessage(f"跟踪框已设置：{rect.w}x{rect.h} @ {rect.x},{rect.y}", 8000)

    def clear_track(self) -> None:
        cfg = self._track_cfg()
        cfg.roi = None
        cfg.enabled = False
        self.recipe.track = cfg
        self._sync_view()
        self._refresh_track_label()
        self.statusBar().showMessage("跟踪框已清除", 4000)

    def _track_cfg(self) -> TrackConfig:
        cfg = self.recipe.track
        if not isinstance(cfg, TrackConfig):
            from ..tracking import coerce_track
            cfg = coerce_track(cfg)
            self.recipe.track = cfg
        return cfg

    def _on_track_score_changed(self, value: float) -> None:
        self._track_cfg().min_score = float(value)

    def _refresh_track_label(self) -> None:
        if not hasattr(self, "lbl_track"):
            return
        cfg = self._track_cfg()
        if cfg.valid and cfg.roi is not None:
            self.lbl_track.setText(
                f"跟踪框：{cfg.roi.w}x{cfg.roi.h} @ {cfg.roi.x},{cfg.roi.y}（最低相似度 {cfg.min_score:.2f}）")
        else:
            self.lbl_track.setText("跟踪框：未设置（产品位置会漂移时不建议省略）")
        self.sp_track_score.blockSignals(True)
        self.sp_track_score.setValue(cfg.min_score)
        self.sp_track_score.blockSignals(False)

    def calibrate_axis(self) -> None:
        if not self._require_super():
            return
        axis, ok = QInputDialog.getItem(self, "X/Y 标定", "轴向", ["X 间距", "Y 贴板"], 0, False)
        if not ok:
            return
        pixel_span, ok = QInputDialog.getDouble(
            self, "X/Y 标定", "这一轴上的像素长度", 143.3, 0.0001, 1e9, 4)
        if not ok:
            return
        real_mm, ok = QInputDialog.getDouble(
            self, "X/Y 标定", "对应的实物长度（mm）", 1.0, 0.0001, 1e9, 4)
        if not ok:
            return
        which = "x" if str(axis).startswith("X") else "y"
        try:
            self.recipe.calibration.set_axis(pixel_span, real_mm, which)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return
        self._refresh_calib_label()
        self.statusBar().showMessage(f"已写入{which.upper()}轴比例，没有下发到相机", 5000)

    def open_camera_tune(self) -> None:
        if not self._require_super():
            return
        src = self.station.cfg.source
        dlg = QDialog(self)
        dlg.setWindowTitle("相机参数")
        form = QFormLayout(dlg)
        sp_e = QDoubleSpinBox()
        sp_g = QDoubleSpinBox()
        sp_f = QDoubleSpinBox()
        for sp, key in ((sp_e, "exposure"), (sp_g, "gain"), (sp_f, "fps")):
            sp.setRange(0, 1_000_000)
            sp.setDecimals(3)
            sp.setValue(float(src.get(key) or 0))
        brand = QComboBox()
        brand.addItem(BRAND_MINDVISION)
        brand.addItem(BRAND_UNCONNECTED)
        current_brand = str(src.get("brand") or BRAND_MINDVISION)
        if brand.findText(current_brand) < 0:
            brand.addItem(current_brand)
        brand.setCurrentText(current_brand)
        model = QComboBox()
        model.addItem(MODEL_MV_GE500M_TPO)
        current_model = str(src.get("model") or MODEL_MV_GE500M_TPO)
        if model.findText(current_model) < 0:
            model.addItem(current_model)
        model.setCurrentText(current_model)
        form.addRow("曝光 (ms)", sp_e)
        form.addRow("增益（模拟增益档）", sp_g)
        form.addRow("帧率 (Hz)", sp_f)
        form.addRow("品牌", brand)
        form.addRow("型号", model)
        form.addRow("当前触发", QLabel("硬件" if trigger_of(src) == "hardware" else "连续"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if not dlg.exec():
            return
        live = self.station.source if isinstance(self.station.source, MindVisionSource) else None
        try:
            mode = update_camera_numbers(
                src,
                exposure=float(sp_e.value()),
                gain=float(sp_g.value()),
                fps=float(sp_f.value()),
                brand=brand.currentText(),
                model=model.currentText(),
                camera=live,
            )
        except MindVisionError as exc:
            QMessageBox.critical(self, "下发失败", str(exc))
            return
        label = "连续" if mode == "continuous" else "硬件"
        if live is not None:
            self.statusBar().showMessage(f"已下发到迈德威视相机，触发为{label}。", 6000)
        elif brand.currentText() == BRAND_MINDVISION:
            self.statusBar().showMessage(
                f"已保存，触发记为{label}。迈德威视相机还没打开，数值尚未下发。", 6000)
        else:
            self.statusBar().showMessage(f"已保存，触发记为{label}。当前帧源不是迈德威视。", 6000)

    def _sync_view(self) -> None:
        self.view.sync_rois([(it.id, it.roi) for it in self.recipe.items])
        cfg = self.recipe.track if isinstance(self.recipe.track, TrackConfig) else None
        self.view.set_track_rect(cfg.roi if cfg is not None and cfg.valid else None)

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
                self.recipe.calibration.set_by_two_points(p1, p2, known)
                self.statusBar().showMessage(f"标定完成：{self.recipe.calibration.describe()}", 8000)
            except ValueError as exc:
                self.statusBar().showMessage(f"标定失败：{exc}", 6000)
        else:
            self.view.clear_calib_marks()
            self._show_calib_marks()
        self.act_calib.setChecked(False)
        self._refresh_calib_label()

    def calibrate_by_value(self) -> None:
        """已知真实值 + 当前实测值反算比例，或直接填 X/Y。"""
        if not self._require_super():
            return
        dlg = CalibrationDialog(self.recipe.calibration, self)
        if dlg.exec() and dlg.result_cal is not None:
            self.recipe.calibration = dlg.result_cal
            self._refresh_calib_label()
            self.statusBar().showMessage(f"标定已更新：{self.recipe.calibration.describe()}", 8000)

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
        if cal.valid:
            label = "标定：" + cal.describe()
        else:
            label = "标定：未标定（跑毫米判定前请先标定）"
        self.lbl_calib.setText(label)
        self._show_calib_marks()

    # ------------------------------------------------------------------ 文件
    def new_recipe(self) -> None:
        self.station.set_recipe(Recipe(name="untitled"), "")
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

        path, _ = QFileDialog.getOpenFileName(self, "导入 .HPro 模板", "", "HPro (*.HPro *.hpro);;所有文件 (*.*)")
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
    def closeEvent(self, event) -> None:               # noqa: N802
        try:
            self.manager.stop_all()
            self.manager.close_all()
            self.settings.save()
        finally:
            super().closeEvent(event)
