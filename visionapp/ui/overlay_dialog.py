"""画面显示设置：统计文字字号、OK/NG 字号、X/Y 位置。"""
from __future__ import annotations

from ..qtcompat import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                        QSpinBox, QVBoxLayout)

DEFAULTS = {"size_stat": 20, "size_okng": 46, "x": 14, "y": 10}


class OverlayDialog(QDialog):
    """调整画面左上角统计的显示（字号 + 位置）。"""

    def __init__(self, cfg: dict | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("画面显示设置")
        self.setMinimumWidth(420)

        cur = dict(DEFAULTS)
        cur.update(cfg or {})

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("画面左上角的统计信息（状态 / OK / NG / 总数 / 良品数 / 良品率）"))

        form = QFormLayout()
        self.sp_stat = QSpinBox()
        self.sp_stat.setRange(8, 48)
        self.sp_stat.setValue(int(cur["size_stat"]))
        self.sp_stat.setSuffix(" px")
        form.addRow("统计文字字号（总数量/良品数/NG数/良品率/状态）", self.sp_stat)

        self.sp_okng = QSpinBox()
        self.sp_okng.setRange(16, 160)
        self.sp_okng.setValue(int(cur["size_okng"]))
        self.sp_okng.setSuffix(" px")
        form.addRow("OK / NG 字号", self.sp_okng)

        self.sp_x = QSpinBox()
        self.sp_x.setRange(0, 1200)
        self.sp_x.setValue(int(cur["x"]))
        self.sp_x.setSuffix(" px")
        form.addRow("X 位置（左右）", self.sp_x)

        self.sp_y = QSpinBox()
        self.sp_y.setRange(0, 900)
        self.sp_y.setValue(int(cur["y"]))
        self.sp_y.setSuffix(" px")
        form.addRow("Y 位置（上下）", self.sp_y)
        lay.addLayout(form)

        lay.addWidget(QLabel("提示：调整后可点【应用】立即看效果，不满意点【取消】还原。"))

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self._on_apply = None

    def values(self) -> dict:
        return {
            "size_stat": int(self.sp_stat.value()),
            "size_okng": int(self.sp_okng.value()),
            "x": int(self.sp_x.value()),
            "y": int(self.sp_y.value()),
        }

    def bind_apply(self, func) -> None:
        """实时预览：任何数值变化都会回调 func(cfg)。"""
        self._on_apply = func
        for w in (self.sp_stat, self.sp_okng, self.sp_x, self.sp_y):
            w.valueChanged.connect(lambda _v: self._on_apply(self.values()) if self._on_apply else None)
