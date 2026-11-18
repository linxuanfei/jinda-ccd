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
        head.setFixedHeight(int(round(28 * theme.scale())))
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

        # 画面右下角 HUD：FPS / 单帧耗时（不再占用右侧统计面板）
        self.lbl_perf = QLabel("FPS --   单帧 -- ms", self)
        self.lbl_perf.setObjectName("tilePerf")
        self.lbl_perf.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        theme.set_class(self.lbl_perf, "mono")

        self.set_active(False)

    # ------------------------------------------------------------------ 状态
    def set_active(self, active: bool) -> None:
        theme.set_prop(self, "selected", "true" if active else "false")

    def resizeEvent(self, event) -> None:      # noqa: N802
        super().resizeEvent(event)
        self._place_perf()

    def _place_perf(self) -> None:
        if not hasattr(self, "lbl_perf"):
            return
        self.lbl_perf.adjustSize()
        m = 14
        x = max(m, self.width() - self.lbl_perf.width() - m)
        y = max(m, self.height() - self.lbl_perf.height() - m)
        self.lbl_perf.move(x, y)
        self.lbl_perf.raise_()

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
            f"{self.name}   {verdict}   OK {snap.ok}  NG {snap.ng}   良率 {rate}")
        theme.set_state(self.led, state)
        # 右下角 HUD
        fps = f"{snap.fps:.1f}" if getattr(snap, "fps", 0) else "--"
        cost = f"{snap.last_elapsed_ms:.1f}" if snap.total else "--"
        self.lbl_perf.setText(f"FPS {fps}   单帧 {cost} ms")
        self._place_perf()
