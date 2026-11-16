"""机位画面块：标题（LED+名称+统计）+ 图像视图。"""

from __future__ import annotations

from ..qtcompat import Qt, Signal
from ..qtcompat import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import theme
from .image_view import ImageView


class StationTile(QWidget):
    """一个机位的显示单元。"""

    clicked = Signal(int)

    def __init__(self, index: int, name: str, parent=None):
        super().__init__(parent)
        self.index = index
        self.name = name
        # QWidget 子类必须显式声明 StyledBackground，QSS 的边框/背景才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        head = QWidget()
        head.setFixedHeight(28)
        hh = QHBoxLayout(head)
        hh.setContentsMargins(6, 0, 8, 0)
        hh.setSpacing(8)

        self.led = QLabel()
        self.led.setObjectName("tileLed")
        theme.set_state(self.led, "idle")
        hh.addWidget(self.led)

        self.header = QLabel(f"{name}   --")
        theme.set_class(self.header, "fieldLabel", "mono")
        hh.addWidget(self.header, 1)

        v.addWidget(head)

        self.view = ImageView()
        self.view.setMinimumHeight(180)
        self.view.viewClicked.connect(lambda: self.clicked.emit(self.index))
        v.addWidget(self.view, 1)

        self.set_active(False)

    # ------------------------------------------------------------------ 状态
    def set_active(self, active: bool) -> None:
        theme.set_prop(self, "selected", "true" if active else "false")

    def update_stats(self, snap, enabled: bool = True) -> None:
        if not enabled:
            self.header.setText(f"{self.name}   已停用")
            theme.set_state(self.led, "idle")
            return
        verdict = "待机"
        state = "idle"
        if snap.last_result is not None:
            ok = bool(snap.last_result.get("ok"))
            verdict = "OK" if ok else "NG"
            state = "ok" if ok else "ng"
        rate = f"{snap.yield_rate * 100:.0f}%" if snap.total else "--"
        self.header.setText(
            f"{self.name}   {verdict}   OK {snap.ok}  NG {snap.ng}  {rate}   {snap.last_elapsed_ms:.0f}ms")
        theme.set_state(self.led, state)
