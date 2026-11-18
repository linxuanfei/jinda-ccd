"""通用设置：图片保存路径 / 保存内容（NG、OK、全部）/ 清空已保存图片。"""

from __future__ import annotations

import os
import shutil

from ..qtcompat import Qt
from ..qtcompat import (
    QButtonGroup, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QRadioButton, QSpinBox, QVBoxLayout,
)
from . import theme


MODE_NG = "ng"
MODE_OK = "ok"
MODE_ALL = "all"


class GeneralSettingsDialog(QDialog):
    """现场最常改的几项，集中在一个窗口。"""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("通用设置")
        self.resize(560, 360)
        v = theme.tune_layout(QVBoxLayout(self))

        v.addWidget(QLabel("图片保存路径（结果 / NG / OK 都放这里）"))
        row = QHBoxLayout()
        self.ed_root = QLineEdit(str(settings.get("results_root", "results")))
        btn = QPushButton("浏览…")
        btn.setFixedWidth(70)
        btn.clicked.connect(self._pick_dir)
        row.addWidget(self.ed_root, 1)
        row.addWidget(btn)
        v.addLayout(row)

        v.addWidget(QLabel("保存内容"))
        self.rb_ng = QRadioButton("只保存 NG 图")
        self.rb_ok = QRadioButton("只保存 OK 图")
        self.rb_all = QRadioButton("全部保存（NG + OK）")
        self.grp = QButtonGroup(self)
        for rb in (self.rb_ng, self.rb_ok, self.rb_all):
            self.grp.addButton(rb)
            v.addWidget(rb)
        mode = str(settings.get("save_mode", MODE_NG) or MODE_NG).lower()
        {MODE_NG: self.rb_ng, MODE_OK: self.rb_ok, MODE_ALL: self.rb_all}.get(mode, self.rb_ng).setChecked(True)
        self.rb_ng.setToolTip("只存判 NG 的图（省空间，现场常用）")
        self.rb_all.setToolTip("NG 和 OK 都存（做追溯/分析用，占空间大）")

        v.addWidget(QLabel("其它"))
        line = QHBoxLayout()
        line.addWidget(QLabel("保留天数"))
        self.sp_keep = QSpinBox()
        self.sp_keep.setRange(0, 3650)
        self.sp_keep.setSuffix(" 天")
        self.sp_keep.setValue(int(settings.get("ng_keep_days", 30) or 0))
        self.sp_keep.setToolTip("只保留最近这么多天；0 = 永不清理")
        line.addWidget(self.sp_keep)
        line.addSpacing(16)
        line.addWidget(QLabel("剩余提醒"))
        self.sp_free = QSpinBox()
        self.sp_free.setRange(0, 100000)
        self.sp_free.setSuffix(" GB")
        self.sp_free.setValue(int(round(float(settings.get("disk_min_free_gb", 5.0) or 5.0))))
        line.addWidget(self.sp_free)
        line.addStretch(1)
        v.addLayout(line)

        v.addStretch(1)

        btn_row = QHBoxLayout()
        self.btn_open = QPushButton("打开保存目录")
        self.btn_open.clicked.connect(self._open_dir)
        btn_row.addWidget(self.btn_open)
        self.btn_clear = QPushButton("清空已保存图片…")
        self.btn_clear.clicked.connect(self._clear_images)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_ok)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        v.addLayout(btn_row)

    # ------------------------------------------------------------------ 工具
    def _abs_root(self) -> str:
        root = self.ed_root.text().strip() or "results"
        if not os.path.isabs(root):
            from ..settings import BASE_DIR

            root = os.path.join(BASE_DIR, root)
        return root

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择图片保存目录", self._abs_root())
        if d:
            self.ed_root.setText(d)

    def _open_dir(self) -> None:
        root = self._abs_root()
        os.makedirs(root, exist_ok=True)
        os.startfile(root)                              # noqa: S606

    def _clear_images(self) -> None:
        """清空已保存的图片（可只清 NG / 只清 OK）。"""
        root = self._abs_root()
        if not os.path.isdir(root):
            QMessageBox.information(self, "没有图片", "保存目录还不存在，没有可清空的图片。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空已保存图片")
        box.setText(f"要清空哪个范围？\n\n目录：{root}")
        b_ng = box.addButton("只清 NG", QMessageBox.ButtonRole.DestructiveRole)
        b_ok = box.addButton("只清 OK", QMessageBox.ButtonRole.DestructiveRole)
        b_all = box.addButton("全部清空", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked not in (b_ng, b_ok, b_all):
            return
        kinds = {b_ng: ("NG",), b_ok: ("OK",), b_all: ("NG", "OK")}[clicked]
        removed = 0
        for dirpath, dirs, files in os.walk(root, topdown=False):
            parts = {p.upper() for p in dirpath.split(os.sep)}
            if not (parts & set(kinds)):
                continue
            for f in files:
                try:
                    os.remove(os.path.join(dirpath, f))
                    removed += 1
                except OSError:
                    continue
            for d in dirs:
                try:
                    os.rmdir(os.path.join(dirpath, d))
                except OSError:
                    pass
        QMessageBox.information(self, "清理完成", f"已删除 {removed} 个图片/快照文件。")

    # ------------------------------------------------------------------ 结果
    def result(self) -> dict:
        mode = MODE_NG
        if self.rb_ok.isChecked():
            mode = MODE_OK
        elif self.rb_all.isChecked():
            mode = MODE_ALL
        return {
            "results_root": self.ed_root.text().strip() or "results",
            "save_mode": mode,
            "ng_keep_days": int(self.sp_keep.value()),
            "disk_min_free_gb": float(self.sp_free.value()),
        }
