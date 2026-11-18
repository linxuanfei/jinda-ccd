"""Qt 绑定兼容层：同一套代码同时支持 PySide6（Win10/11）与 PySide2（Win7）。

为什么需要：
- Win10/11 用 **PySide6（Qt6）**，画面/性能更好；
- **Win7 SP1 只能跑 Qt5**（Qt6 官方不支持 Win7），所以那台老机器要用 **PySide2 5.15**。

两者 API 差异主要有两类：
1. **枚举位置**：Qt6 是 `Qt.MouseButton.LeftButton`，Qt5 是 `Qt.LeftButton`；
2. **QAction 位置**：Qt6 在 `QtGui`，Qt5 在 `QtWidgets`。

本模块在导入 PySide2 时，把上表这些"Qt6 风格"的枚举名**动态补到 PySide2 对象上**，
于是界面代码只写一种写法，两个版本都能跑。
"""

from __future__ import annotations

import sys

QT6 = False
BINDING = ""

try:                                            # 优先 Qt6
    import PySide6 as _pyside                      # noqa: F401
    from PySide6.QtCore import *                   # noqa: F401,F403
    from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal  # noqa: F401
    from PySide6.QtGui import *                    # noqa: F401,F403
    from PySide6.QtGui import QAction, QBrush, QColor, QFont, QImage, QPen, QPixmap  # noqa: F401
    from PySide6.QtWidgets import *                # noqa: F401,F403
    from PySide6.QtWidgets import (                # noqa: F401
        QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
        QFileDialog, QFormLayout, QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem,
        QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QGridLayout,
        QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
        QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
        QTableWidget, QTableWidgetItem, QToolBar, QToolButton, QVBoxLayout, QWidget,
    )
    QT6 = True
    BINDING = "PySide6/" + _pyside.__version__
except ImportError:                             # 退回 Qt5（Win7）
    import PySide2 as _pyside                      # noqa: F401
    from PySide2.QtCore import *                   # noqa: F401,F403
    from PySide2.QtCore import QLineF, QPointF, QRectF, Qt, Signal  # noqa: F401
    from PySide2.QtGui import *                    # noqa: F401,F403
    from PySide2.QtGui import QBrush, QColor, QFont, QImage, QPen, QPixmap  # noqa: F401
    from PySide2.QtWidgets import *                # noqa: F401,F403
    from PySide2.QtWidgets import (                # noqa: F401
        QAction, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QGraphicsEllipseItem, QGraphicsItem,
        QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
        QGraphicsView, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
        QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
        QToolBar, QToolButton, QVBoxLayout, QWidget,
    )
    import PySide2.QtGui as _qtgui
    import PySide2.QtWidgets as _qtwidgets

    # QAction 在 Qt5 位于 QtWidgets
    _qtgui.QAction = _qtwidgets.QAction

    def _enum(name: str, **members):
        return type(name, (), members)

    # Qt6 风格的嵌套枚举 → 补到 Qt5 对象上
    Qt.MouseButton = _enum("MouseButton", LeftButton=Qt.LeftButton, RightButton=Qt.RightButton,
                           MiddleButton=Qt.MiddleButton)
    Qt.KeyboardModifier = _enum("KeyboardModifier", NoModifier=Qt.NoModifier,
                                ControlModifier=Qt.ControlModifier, ShiftModifier=Qt.ShiftModifier,
                                AltModifier=Qt.AltModifier)
    Qt.Key = _enum("Key", Key_Delete=Qt.Key_Delete, Key_Escape=Qt.Key_Escape,
                   Key_A=Qt.Key_A, Key_Return=Qt.Key_Return, Key_Enter=Qt.Key_Enter)
    Qt.PenStyle = _enum("PenStyle", NoPen=Qt.NoPen, DashLine=Qt.DashLine,
                        SolidLine=Qt.SolidLine, DotLine=Qt.DotLine)
    Qt.BrushStyle = _enum("BrushStyle", NoBrush=Qt.NoBrush, SolidPattern=Qt.SolidPattern)
    Qt.ScrollBarPolicy = _enum("ScrollBarPolicy",
                               ScrollBarAlwaysOff=Qt.ScrollBarAlwaysOff,
                               ScrollBarAlwaysOn=Qt.ScrollBarAlwaysOn,
                               ScrollBarAsNeeded=Qt.ScrollBarAsNeeded)
    Qt.CursorShape = _enum("CursorShape", CrossCursor=Qt.CrossCursor,
                           ArrowCursor=Qt.ArrowCursor, PointingHandCursor=Qt.PointingHandCursor)
    Qt.AspectRatioMode = _enum("AspectRatioMode", KeepAspectRatio=Qt.KeepAspectRatio)
    Qt.Orientation = _enum("Orientation", Horizontal=Qt.Horizontal, Vertical=Qt.Vertical)
    Qt.AlignmentFlag = _enum("AlignmentFlag", AlignLeft=Qt.AlignLeft, AlignCenter=Qt.AlignCenter,
                             AlignRight=Qt.AlignRight, AlignVCenter=Qt.AlignVCenter)
    Qt.ItemDataRole = _enum("ItemDataRole", UserRole=Qt.UserRole)

    QImage.Format = _enum("Format", Format_RGB888=QImage.Format_RGB888)
    QDialogButtonBox.StandardButton = _enum(
        "StandardButton", Ok=QDialogButtonBox.Ok, Cancel=QDialogButtonBox.Cancel,
        Yes=QDialogButtonBox.Yes, No=QDialogButtonBox.No, Save=QDialogButtonBox.Save)
    QGraphicsItem.GraphicsItemFlag = _enum(
        "GraphicsItemFlag",
        ItemIsSelectable=QGraphicsItem.ItemIsSelectable,
        ItemSendsGeometryChanges=QGraphicsItem.ItemSendsGeometryChanges)
    QGraphicsView.DragMode = _enum("DragMode", NoDrag=QGraphicsView.NoDrag,
                                   ScrollHandDrag=QGraphicsView.ScrollHandDrag)
    QGraphicsView.ViewportAnchor = _enum(
        "ViewportAnchor", AnchorUnderMouse=QGraphicsView.AnchorUnderMouse,
        NoAnchor=QGraphicsView.NoAnchor)
    QToolButton.ToolButtonPopupMode = _enum(
        "ToolButtonPopupMode", InstantPopup=QToolButton.InstantPopup,
        MenuButtonPopup=QToolButton.MenuButtonPopup)
    QSizePolicy = _qtwidgets.QSizePolicy
    QFrame = _qtwidgets.QFrame

    # Qt5 里 QtWidgets 的 exec 叫 exec_()，补一个 exec() 别名让上层代码只写一种
    for _cls_name in ("QApplication", "QDialog", "QMenu"):
        _cls = globals().get(_cls_name)
        if _cls is not None and not hasattr(_cls, "exec") and hasattr(_cls, "exec_"):
            try:
                setattr(_cls, "exec", _cls.exec_)
            except Exception:                          # noqa: BLE001
                pass

    BINDING = "PySide2/" + _pyside.__version__


def binding_info() -> str:
    """当前绑定信息（写进日志/About，排查用）。"""
    import sys as _s

    return f"{BINDING}（Python {_s.version.split()[0]}，{'Qt6' if QT6 else 'Qt5'}）"


__all__ = [name for name in dir() if not name.startswith("_")]
