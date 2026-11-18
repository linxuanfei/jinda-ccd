"""UI 主题层：设计令牌 + 全局 QSS + 语义状态。

约定（见 docs/compose/spec/ui-theme.md）：
- 业务代码禁止硬编码色值/字号/内联 setStyleSheet；
- 动态状态一律走 set_state()（动态属性 state = ok/ng/warn/idle）；
- 数值排版用 mono_font()，正文用 ui_font()。
"""

from __future__ import annotations

import re

from ..qtcompat import QFont
from ..qtcompat import QApplication, QWidget

# ---------------------------------------------------------------- 令牌

COLORS: dict[str, str] = {
    # 背景层级
    "bg.base": "#0B0E14",
    "bg.panel": "#121A24",
    "bg.rise": "#18222E",
    # 描边
    "border.subtle": "#22303C",
    "border.focus": "#22D3EE",
    # 文字
    "text.primary": "#E6EDF5",
    "text.secondary": "#93A4B8",
    "text.muted": "#5E7186",
    # 强调
    "accent.cyan": "#22D3EE",
    "accent.cyan.dim": "#0E3A47",
    "accent.cyan.hover": "#67E8F9",
    # 状态
    "state.ok": "#2FD67B",
    "state.ng": "#FF5D5D",
    "state.warn": "#FFB454",
    "state.idle": "#5E7186",
}

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16}
RADIUS = {"sm": 4, "md": 6}

_UI_FONT_STACK = '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
_MONO_FONT_STACK = '"Consolas", "JetBrains Mono", "DejaVu Sans Mono", monospace'

STATES = ("ok", "ng", "warn", "idle")

# ROI 多项区分色（内容色，按序分配给各检测项）
ROI_PALETTE = [
    "#00e676", "#29b6f6", "#ffca28", "#ab47bc",
    "#ef5350", "#26a69a", "#ff7043", "#8d6e63",
]


def color(name: str) -> str:
    return COLORS[name]


def ui_font(px: int = 13, weight: int = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI", "PingFang SC", "Microsoft YaHei"])
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def mono_font(px: int = 13, weight: int = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(["Consolas", "JetBrains Mono", "DejaVu Sans Mono"])
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


# ---------------------------------------------------------------- 语义状态

def _repolish(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def set_prop(widget: QWidget, name: str, value: str) -> None:
    """设置动态属性并重刷样式（驱动 QSS 属性选择器）。"""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    _repolish(widget)


def set_class(widget: QWidget, *names: str) -> None:
    """设置 class 动态属性（驱动 QSS 类选择器，如 QLabel.statValue）。"""
    set_prop(widget, "class", " ".join(names))


def set_state(widget: QWidget, state: str) -> None:
    """给控件打语义状态（ok/ng/warn/idle），驱动 QSS 变色。"""
    if state not in STATES:
        raise ValueError(f"unknown state: {state}")
    set_prop(widget, "state", state)


def tune_layout(layout):
    """统一对话框根布局刻度：外边距 12、控件间距 8（S2.4）。"""
    m, s = SPACING["md"], SPACING["sm"]
    layout.setContentsMargins(m, m, m, m)
    layout.setSpacing(s)
    return layout


# ---------------------------------------------------------------- QSS

def _tokens() -> dict[str, str]:
    t = dict(COLORS)
    t["radius.sm"] = str(RADIUS["sm"])
    t["radius.md"] = str(RADIUS["md"])
    t["ui.font"] = _UI_FONT_STACK
    t["mono.font"] = _MONO_FONT_STACK
    return t


_QSS_TEMPLATE = r"""
/* ========== 基底 ========== */
QWidget {
    background: {{bg.base}};
    color: {{text.primary}};
    font-family: {{ui.font}};
    font-size: 15px;
}
QMainWindow, QDialog { background: {{bg.base}}; }
QToolTip {
    background: {{bg.rise}};
    color: {{text.primary}};
    border: 1px solid {{border.subtle}};
    padding: 4px 8px;
}

/* ========== 工具条 ========== */
QToolBar {
    background: {{bg.panel}};
    border: none;
    border-bottom: 1px solid {{border.subtle}};
    spacing: 4px;
    padding: 4px 8px;
}
QToolBar::separator { background: {{border.subtle}}; width: 1px; margin: 4px 6px; }
QMenuBar { font-size: 17px; }
QMenuBar::item { padding: 4px 10px; }
QMenu { font-size: 17px; }

QToolBar QToolButton {
    background: transparent;
    color: {{text.primary}};
    border: 1px solid transparent;
    border-radius: {{radius.sm}}px;
    padding: 5px 9px;
    font-size: 17px;
}
QToolBar QToolButton:hover { background: {{bg.rise}}; border-color: {{border.subtle}}; }
QToolBar QToolButton:pressed { background: {{accent.cyan.dim}}; border-color: {{accent.cyan}}; }
QToolBar QToolButton:checked { background: {{accent.cyan.dim}}; border-color: {{accent.cyan}}; color: {{accent.cyan}}; }
QToolBar QToolButton:disabled { color: {{text.muted}}; }
/* 溢出按钮必须可见（工具条放不下时仍能触达全部动作） */
QToolBar QToolButton#qt_toolbar_ext_button {
    background: {{bg.rise}};
    border: 1px solid {{border.subtle}};
    border-radius: {{radius.sm}}px;
    padding: 2px 4px;
}

/* 运行组主/次动作 */
QToolButton#btnStart, QPushButton#btnStart {
    background: {{accent.cyan}};
    color: {{bg.base}};
    font-weight: 700;
    border: 1px solid {{accent.cyan}};
}
QToolButton#btnStart:hover, QPushButton#btnStart:hover { background: {{accent.cyan.hover}}; }
QToolButton#btnStop, QPushButton#btnStop {
    background: transparent;
    color: {{state.ng}};
    font-weight: 700;
    border: 1px solid {{state.ng}};
}
QToolButton#btnStop:hover, QPushButton#btnStop:hover { background: rgba(255, 93, 93, 0.16); }

/* ========== 按钮 ========== */
QPushButton {
    background: {{bg.rise}};
    color: {{text.primary}};
    border: 1px solid {{border.subtle}};
    border-radius: {{radius.sm}}px;
    padding: 6px 14px;
}
QPushButton:hover { border-color: {{text.muted}}; }
QPushButton:pressed { background: {{accent.cyan.dim}}; border-color: {{accent.cyan}}; }
QPushButton:disabled { color: {{text.muted}}; background: {{bg.panel}}; }
QPushButton[variant="primary"] {
    background: {{accent.cyan}}; color: {{bg.base}}; font-weight: 700;
    border-color: {{accent.cyan}};
}
QPushButton[variant="primary"]:hover { background: {{accent.cyan.hover}}; }
QPushButton[variant="destructive"] {
    background: transparent; color: {{state.ng}};
    border-color: {{state.ng}}; font-weight: 700;
}
QPushButton[variant="destructive"]:hover { background: rgba(255, 93, 93, 0.16); }

/* ========== 输入类 ========== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: {{bg.rise}};
    color: {{text.primary}};
    border: 1px solid {{border.subtle}};
    border-radius: {{radius.sm}}px;
    padding: 4px 8px;
    selection-background-color: {{accent.cyan.dim}};
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: {{border.focus}};
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid {{text.secondary}}; margin-right: 8px; }
QComboBox QAbstractItemView {
    background: {{bg.rise}};
    color: {{text.primary}};
    border: 1px solid {{border.subtle}};
    selection-background-color: {{accent.cyan.dim}};
    selection-color: {{accent.cyan}};
}
QCheckBox, QRadioButton { spacing: 8px; color: {{text.primary}}; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked {
    border: 1px solid {{text.muted}}; background: {{bg.rise}}; border-radius: 3px;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    border: 1px solid {{accent.cyan}}; background: {{accent.cyan}}; border-radius: 3px;
}

/* ========== 分组 / 面板 ========== */
QGroupBox {
    background: {{bg.panel}};
    border: 1px solid {{border.subtle}};
    border-radius: {{radius.md}}px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    color: {{text.secondary}};
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px; top: 2px;
    padding: 0 4px;
    color: {{text.secondary}};
}
QFrame[frameShape="4"], QFrame[frameShape="5"] { /* HLine/VLine */
    background: {{border.subtle}};
    max-height: 1px;
}

/* ========== 列表 / 表格 ========== */
QListWidget, QTreeView, QTableView, QTableWidget, QPlainTextEdit, QTextEdit {
    background: {{bg.panel}};
    color: {{text.primary}};
    border: 1px solid {{border.subtle}};
    border-radius: {{radius.sm}}px;
    alternate-background-color: {{bg.rise}};
    outline: none;
}
QListWidget::item, QTreeView::item, QTableView::item, QTableWidget::item { padding: 4px 6px; }
QListWidget::item:selected, QTreeView::item:selected, QTableView::item:selected, QTableWidget::item:selected {
    background: {{accent.cyan.dim}};
    color: {{accent.cyan}};
    border: 1px solid {{accent.cyan}};
}
QHeaderView::section {
    background: {{bg.panel}};
    color: {{text.secondary}};
    border: none;
    border-right: 1px solid {{border.subtle}};
    border-bottom: 1px solid {{border.subtle}};
    padding: 4px 8px;
}

/* ========== 滚动条 / 分割 ========== */
QScrollBar:vertical { background: {{bg.base}}; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: {{border.subtle}}; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: {{text.muted}}; }
QScrollBar:horizontal { background: {{bg.base}}; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: {{border.subtle}}; border-radius: 4px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: {{text.muted}}; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QSplitter::handle { background: {{border.subtle}}; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }

/* ========== 状态栏 ========== */
QStatusBar {
    background: {{bg.panel}};
    border-top: 1px solid {{border.subtle}};
    color: {{text.secondary}};
}

/* ========== 菜单 ========== */
QMenu {
    background: {{bg.rise}};
    color: {{text.primary}};
    border: 1px solid {{border.subtle}};
    padding: 4px;
}
QMenu::item { padding: 6px 20px 6px 12px; border-radius: {{radius.sm}}px; }
QMenu::item:selected { background: {{accent.cyan.dim}}; color: {{accent.cyan}}; }
QMenu::separator { height: 1px; background: {{border.subtle}}; margin: 4px 8px; }

/* ========== 语义：状态色（通用，任意 QLabel 均可 set_state 驱动） ========== */
QLabel[state="idle"] { color: {{text.muted}}; }
QLabel[state="ok"] { color: {{state.ok}}; }
QLabel[state="ng"] { color: {{state.ng}}; }
QLabel[state="warn"] { color: {{state.warn}}; }

/* ========== 语义：状态药丸 / 数值 ========== */
QLabel#lblVerdict, QLabel#lblMerged {
    border-radius: {{radius.md}}px;
    padding: 8px 18px;
    font-size: 24px;
    font-weight: 700;
}
QLabel#lblVerdict[state="idle"], QLabel#lblMerged[state="idle"] {
    background: {{bg.rise}};
    color: {{text.muted}};
    border: 1px solid {{border.subtle}};
}
QLabel#lblVerdict[state="ok"], QLabel#lblMerged[state="ok"] {
    background: rgba(47, 214, 123, 0.14);
    color: {{state.ok}};
    border: 1px solid {{state.ok}};
}
QLabel#lblVerdict[state="ng"], QLabel#lblMerged[state="ng"] {
    background: rgba(255, 93, 93, 0.14);
    color: {{state.ng}};
    border: 1px solid {{state.ng}};
}
QLabel#lblVerdict[state="warn"], QLabel#lblMerged[state="warn"] {
    background: rgba(255, 180, 84, 0.14);
    color: {{state.warn}};
    border: 1px solid {{state.warn}};
}
QLabel#lblRole[state="warn"] { color: {{state.warn}}; font-weight: 700; }
QLabel#lblRole[state="idle"] { color: {{text.secondary}}; }
QLabel#lblCalib { color: {{accent.cyan}}; }
QLabel.statValue {
    font-family: {{mono.font}};
    font-size: 20px;
    font-weight: 600;
}
QLabel.statKey {
    color: {{text.secondary}};
    font-size: 11px;
}
QLabel.mono {
    font-family: {{mono.font}};
}
QLabel.fieldLabel { color: {{text.secondary}}; }
QLabel.hint { color: {{text.muted}}; font-size: 11px; }
QLabel.error { color: {{state.ng}}; }

/* 机位卡 */
StationTile {
    background: {{bg.panel}};
    border: 1px solid {{border.subtle}};
    border-radius: {{radius.md}}px;
}
StationTile[selected="true"] {
    border: 2px solid {{accent.cyan}};
    background: {{bg.rise}};
}
QLabel#tileLed {
    border-radius: 4px;
    min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}
QLabel#tileLed[state="ok"] { background: {{state.ok}}; }
QLabel#tileLed[state="ng"] { background: {{state.ng}}; }
QLabel#tileLed[state="warn"] { background: {{state.warn}}; }
QLabel#tileLed[state="idle"] { background: {{state.idle}}; }
"""


# ---------------------------------------------------------------- 界面缩放
# 现场反馈“字太小”，做成可调：默认 1.25 倍。
# 快捷键：Ctrl+= 放大 / Ctrl+- 缩小 / Ctrl+0 复位（会记到 config/app_settings.json）。
_SCALE = 1.45
_BASE_PX = 13


def set_scale(value: float) -> float:
    """设置界面缩放（0.85 ~ 2.0），返回生效值。"""
    global _SCALE
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 1.0
    _SCALE = max(0.85, min(2.0, round(v, 2)))
    return _SCALE


def scale() -> float:
    return _SCALE


def _apply_scale(template: str) -> str:
    """把模板里所有 `font-size: Npx` 按缩放比换算（其余尺寸不动）。"""

    def repl(m):
        px = int(round(int(m.group(1)) * _SCALE))
        return f"font-size: {max(9, px)}px"

    return re.sub(r"font-size:\s*(\d+)px", repl, template)


def build_qss() -> str:
    qss = _apply_scale(_QSS_TEMPLATE)
    for key, val in _tokens().items():
        qss = qss.replace("{{" + key + "}}", val)
    return qss


def apply(app: QApplication) -> None:
    """应用全局主题：Fusion 基样式 + 设计令牌 QSS + 全局字体。"""
    app.setStyle("Fusion")
    app.setStyleSheet(build_qss())
    app.setFont(ui_font(13))
