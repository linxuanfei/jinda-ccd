"""产线配置对话框：机位增删、相机、配方、输出/报警（GUI 可配，用户不用改 JSON）。"""

from __future__ import annotations

import copy
import os

from ..qtcompat import Qt
from ..qtcompat import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..outputs import OutputConfig
from ..stations import MERGE_LABELS, MERGE_RULES, LineConfig, StationConfig
from . import theme
from .dialogs import OutputAlarmDialog

SOURCE_KINDS = [
    ("synthetic", "模拟相机"),
    ("folder", "图片回放"),
    ("uvc", "USB / UVC 相机"),
    ("rtsp", "网络相机 RTSP"),
    ("mindvision", "工业相机 MindVision（迈德威视）"),
]


class LineConfigDialog(QDialog):
    """编辑整条产线（机位列表 + 每机位设置）。"""

    def __init__(self, line: LineConfig, recipes_dir: str, global_output: dict,
                 global_alarm: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("产线配置")
        self.resize(980, 640)
        self.line = copy.deepcopy(line)
        self.recipes_dir = recipes_dir
        self.global_output = global_output
        self.global_alarm = global_alarm
        self._loading = False

        v = theme.tune_layout(QVBoxLayout(self))

        # 产线基本
        top = QFormLayout()
        self.ed_line_name = QLineEdit(self.line.name)
        self.cb_merge = QComboBox()
        for rule in MERGE_RULES:
            self.cb_merge.addItem(MERGE_LABELS[rule], rule)
        self.cb_merge.setCurrentIndex(max(0, self.cb_merge.findData(self.line.merge_rule)))
        self.sp_count = QSpinBox()
        self.sp_count.setRange(1, 16)
        self.sp_count.setValue(max(1, len(self.line.stations)))
        self.sp_count.setToolTip("画面数 = 相机数：接几台相机就填几个（1~16）")
        self.sp_count.valueChanged.connect(self._on_count_changed)
        top.addRow("相机数量", self.sp_count)
        top.addRow("产线名称", self.ed_line_name)
        top.addRow("合并判定", self.cb_merge)
        v.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)

        # 左：机位列表
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.list_st = QListWidget()
        self.list_st.currentRowChanged.connect(self._on_select)
        lv.addWidget(self.list_st, 1)
        row = QHBoxLayout()
        for text, slot in (("添加机位", self._add_station),
                           ("删除机位", self._del_station),
                           ("上移", lambda: self._move(-1)),
                           ("下移", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lv.addLayout(row)
        split.addWidget(left)

        # 右：机位设置
        split.addWidget(self._build_form())
        split.setSizes([260, 700])
        v.addWidget(split, 1)

        bb = QDialogButtonBox()
        ok = bb.addButton("保存并应用", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = bb.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        ok.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        v.addWidget(bb)

        self._reload_list()

    def _on_count_changed(self, n: int) -> None:
        """相机数量变化 → 增删机位（新机位沿用第一台的相机类型与配方，序号递增）。"""
        stations = self.line.stations
        n = int(n)
        if n < len(stations):
            self.line.stations = stations[:n]
        elif n > len(stations):
            base = stations[0] if stations else None
            for i in range(len(stations), n):
                if base is not None:
                    st = copy.deepcopy(base)
                else:
                    st = StationConfig()
                st.id = i + 1
                st.name = f"机位{i + 1}"
                src = dict(st.source)
                src["index"] = i
                src["serial"] = ""
                st.source = src
                self.line.stations.append(st)
        self._reload_list(select=min(self.list_st.currentRow(), len(self.line.stations) - 1))

    # ------------------------------------------------------------------ 表单
    def _build_form(self) -> QWidget:
        w = QWidget()
        f = QVBoxLayout(w)
        f.setContentsMargins(8, 0, 0, 0)

        box = QGroupBox("机位设置")
        form = QFormLayout(box)
        self.ed_name = QLineEdit()
        self.ck_enabled = QCheckBox("启用该机位")
        form.addRow("名称", self.ed_name)
        form.addRow("", self.ck_enabled)

        self.cb_kind = QComboBox()
        for kind, label in SOURCE_KINDS:
            self.cb_kind.addItem(label, kind)
        self.cb_kind.currentIndexChanged.connect(self._update_source_fields)
        form.addRow("相机类型", self.cb_kind)

        self.ed_path = QLineEdit()
        btn_path = QPushButton("...")
        btn_path.setFixedWidth(32)
        btn_path.clicked.connect(self._pick_dir)
        row_path = QHBoxLayout()
        row_path.addWidget(self.ed_path, 1)
        row_path.addWidget(btn_path)
        form.addRow("图片目录", row_path)

        self.sp_index = QSpinBox()
        self.sp_index.setRange(0, 15)
        form.addRow("相机序号", self.sp_index)

        self.ed_url = QLineEdit()
        form.addRow("RTSP 地址", self.ed_url)

        self.cb_recipe = QComboBox()
        form.addRow("配方", self.cb_recipe)
        self._load_recipes()

        # 输出 / 报警
        box2 = QGroupBox("本机位输出 / 报警")
        f2 = QFormLayout(box2)
        self.ck_out_global = QCheckBox("使用全局输出设置")
        self.ck_alarm_global = QCheckBox("使用全局报警设置")
        self.btn_edit_out = QPushButton("配置本机位…")
        self.btn_edit_out.clicked.connect(self._edit_outputs)
        self.ck_out_global.toggled.connect(self._update_out_buttons)
        self.ck_alarm_global.toggled.connect(self._update_out_buttons)
        f2.addRow("", self.ck_out_global)
        f2.addRow("", self.ck_alarm_global)
        f2.addRow("", self.btn_edit_out)
        self.lbl_out = QLabel("")
        theme.set_class(self.lbl_out, "hint")
        f2.addRow("", self.lbl_out)

        f.addWidget(box)
        f.addWidget(box2)
        f.addStretch(1)

        # 写回
        for wdg in (self.ed_name, self.ed_path, self.ed_url):
            wdg.textChanged.connect(self._flush)
        self.sp_index.valueChanged.connect(self._flush)
        self.cb_kind.currentIndexChanged.connect(self._flush)
        self.cb_recipe.currentIndexChanged.connect(self._flush)
        self.ck_enabled.toggled.connect(self._flush)
        return w

    def _load_recipes(self) -> None:
        self.cb_recipe.clear()
        self.cb_recipe.addItem("（不指定）", "")
        if os.path.isdir(self.recipes_dir):
            for name in sorted(os.listdir(self.recipes_dir)):
                if name.lower().endswith(".json"):
                    self.cb_recipe.addItem(name[:-5], os.path.join(self.recipes_dir, name))

    # ------------------------------------------------------------------ 列表
    def _reload_list(self, select: int = 0) -> None:
        self._loading = True
        self.list_st.clear()
        for st in self.line.stations:
            flag = "" if st.enabled else "（停用）"
            self.list_st.addItem(f"{st.display_name()}  ·  {st.source.get('kind', '?')}{flag}")
        self._loading = False
        if self.line.stations:
            self.list_st.setCurrentRow(min(select, len(self.line.stations) - 1))
            self._on_select(self.list_st.currentRow())
        else:
            self._set_form_enabled(False)

    def _current(self) -> StationConfig | None:
        i = self.list_st.currentRow()
        if 0 <= i < len(self.line.stations):
            return self.line.stations[i]
        return None

    def _set_form_enabled(self, on: bool) -> None:
        for w in (self.ed_name, self.ck_enabled, self.cb_kind, self.ed_path,
                  self.sp_index, self.ed_url, self.cb_recipe, self.ck_out_global,
                  self.ck_alarm_global, self.btn_edit_out):
            w.setEnabled(on)

    def _on_select(self, row: int) -> None:
        if self._loading:
            return
        st = self._current()
        if st is None:
            self._set_form_enabled(False)
            return
        self._loading = True
        self._set_form_enabled(True)
        self.ed_name.setText(st.name)
        self.ck_enabled.setChecked(st.enabled)
        kind = st.source.get("kind", "synthetic")
        self.cb_kind.setCurrentIndex(max(0, self.cb_kind.findData(kind)))
        self.ed_path.setText(st.source.get("path", "") or "")
        self.sp_index.setValue(int(st.source.get("index", 0) or 0))
        self.ed_url.setText(st.source.get("url", "") or "")
        idx = self.cb_recipe.findData(st.recipe or "")
        self.cb_recipe.setCurrentIndex(idx if idx >= 0 else 0)
        self.ck_out_global.setChecked(st.output is None)
        self.ck_alarm_global.setChecked(st.alarm is None)
        self._loading = False
        self._update_source_fields()
        self._update_out_buttons()

    # ------------------------------------------------------------------ 写回
    def _flush(self) -> None:
        if self._loading:
            return
        st = self._current()
        if st is None:
            return
        st.name = self.ed_name.text().strip()
        st.enabled = self.ck_enabled.isChecked()
        kind = self.cb_kind.currentData()
        src = {"kind": kind}
        if kind == "folder":
            src["path"] = self.ed_path.text().strip()
        elif kind in ("uvc", "mindvision"):
            src["index"] = int(self.sp_index.value())
        elif kind == "rtsp":
            src["url"] = self.ed_url.text().strip()
        st.source = src
        st.recipe = self.cb_recipe.currentData() or ""
        row = self.list_st.currentRow()
        if 0 <= row < self.list_st.count():
            flag = "" if st.enabled else "（停用）"
            self.list_st.item(row).setText(f"{st.display_name()}  ·  {kind}{flag}")

    def _update_source_fields(self) -> None:
        kind = self.cb_kind.currentData()
        self.ed_path.setEnabled(kind == "folder")
        self.sp_index.setEnabled(kind in ("uvc", "mindvision"))
        self.ed_url.setEnabled(kind == "rtsp")

    def _update_out_buttons(self) -> None:
        if self._loading:
            return
        st = self._current()
        if st is None:
            return
        if self.ck_out_global.isChecked():
            st.output = None
        elif st.output is None:
            st.output = copy.deepcopy(self.global_output)
        if self.ck_alarm_global.isChecked():
            st.alarm = None
        elif st.alarm is None:
            st.alarm = copy.deepcopy(self.global_alarm)
        custom = not (st.output is None and st.alarm is None)
        self.btn_edit_out.setEnabled(custom)
        parts = ["输出：全局" if st.output is None else "输出：本机位",
                 "报警：全局" if st.alarm is None else "报警：本机位"]
        self.lbl_out.setText(" ｜ ".join(parts))

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择图片目录", self.ed_path.text() or "")
        if d:
            self.ed_path.setText(d)

    def _edit_outputs(self) -> None:
        st = self._current()
        if st is None:
            return
        out = st.output if st.output is not None else copy.deepcopy(self.global_output)
        alm = st.alarm if st.alarm is not None else copy.deepcopy(self.global_alarm)
        dlg = OutputAlarmDialog(out, alm, self)
        dlg.setWindowTitle(f"{st.display_name()} - 输出/报警")
        if dlg.exec():
            st.output = dlg.result_output_cfg()
            st.alarm = dlg.result_alarm_cfg()
            self._loading = True
            self.ck_out_global.setChecked(False)
            self.ck_alarm_global.setChecked(False)
            self._loading = False
            parts = ["输出：本机位", "报警：本机位"]
            self.btn_edit_out.setEnabled(True)
            self.lbl_out.setText(" ｜ ".join(parts))

    # ------------------------------------------------------------------ 机位增删
    def _add_station(self) -> None:
        new_id = max([s.id for s in self.line.stations], default=0) + 1
        st = StationConfig(id=new_id, name=f"机位{new_id}", enabled=True,
                           source={"kind": "synthetic"},
                           recipe=self.line.stations[0].recipe if self.line.stations else "")
        self.line.stations.append(st)
        self._reload_list(select=len(self.line.stations) - 1)

    def _del_station(self) -> None:
        i = self.list_st.currentRow()
        if not (0 <= i < len(self.line.stations)):
            return
        if len(self.line.stations) <= 1:
            QMessageBox.information(self, "提示", "至少要保留一个机位")
            return
        if QMessageBox.question(self, "删除", f"删除 {self.line.stations[i].display_name()} ?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.line.stations.pop(i)
        self._reload_list(select=max(0, i - 1))

    def _move(self, delta: int) -> None:
        i = self.list_st.currentRow()
        j = i + delta
        if 0 <= i < len(self.line.stations) and 0 <= j < len(self.line.stations):
            self.line.stations[i], self.line.stations[j] = self.line.stations[j], self.line.stations[i]
            self._reload_list(select=j)

    # ------------------------------------------------------------------ 保存
    def _accept(self) -> None:
        self._flush()
        self.line.name = self.ed_line_name.text().strip() or "产线"
        self.line.merge_rule = self.cb_merge.currentData()
        for k, st in enumerate(self.line.stations, start=1):
            st.id = k
            if not st.name:
                st.name = f"机位{k}"
        self.accept()
