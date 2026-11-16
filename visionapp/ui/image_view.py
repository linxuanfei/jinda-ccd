"""图像视图：显示画面 + ROI 框选/拖动/缩放。"""

from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np
from ..qtcompat import QLineF, QPointF, QRectF, Qt, Signal
from ..qtcompat import QBrush, QColor, QImage, QPen, QPixmap
from ..qtcompat import (
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView,
)

from ..core import Rect
from ..tuning import clamp_horizontal_line
from . import theme

PALETTE = theme.ROI_PALETTE
HANDLE = 10.0          # 角点命中半径（图像像素）


class RoiItem(QGraphicsRectItem):
    """可拖动 / 可缩放 / 可删除的 ROI 矩形。"""

    def __init__(self, key: str, rect: QRectF, color: str, view: "ImageView"):
        super().__init__(rect)
        self.key = key
        self._view = view
        self.setPen(QPen(QColor(color), 2))
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)
        self._drag_mode = None      # 'move' | 'tl' | 'tr' | 'bl' | 'br' | None
        self._start_pos = QPointF()
        self._start_rect = QRectF()

    # ---- 角点判定 ----
    def _hit_corner(self, pos: QPointF) -> str | None:
        r = self.rect()
        corners = {
            "tl": QPointF(r.left(), r.top()),
            "tr": QPointF(r.right(), r.top()),
            "bl": QPointF(r.left(), r.bottom()),
            "br": QPointF(r.right(), r.bottom()),
        }
        for name, pt in corners.items():
            if abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE:
                return name
        return None

    def mousePressEvent(self, event) -> None:      # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            corner = self._hit_corner(pos)
            self._start_pos = pos
            self._start_rect = QRectF(self.rect())
            if corner:
                self._drag_mode = corner
            elif self.rect().contains(pos):
                self._drag_mode = "move"
            else:
                self._drag_mode = None
            event.accept()
            self._view.set_active_roi(self.key)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        if self._drag_mode is None:
            super().mouseMoveEvent(event)
            return
        delta = event.pos() - self._start_pos
        r = QRectF(self._start_rect)
        if self._drag_mode == "move":
            r.translate(delta)
        else:
            if self._drag_mode in ("tl", "bl"):
                r.setLeft(min(self._start_rect.left() + delta.x(), self._start_rect.right() - 5))
            if self._drag_mode in ("tr", "br"):
                r.setRight(max(self._start_rect.right() + delta.x(), self._start_rect.left() + 5))
            if self._drag_mode in ("tl", "tr"):
                r.setTop(min(self._start_rect.top() + delta.y(), self._start_rect.bottom() - 5))
            if self._drag_mode in ("bl", "br"):
                r.setBottom(max(self._start_rect.bottom() + delta.y(), self._start_rect.top() + 5))
        self.setRect(r.normalized())
        self._view.on_roi_geometry(self.key, self.rect())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        self._drag_mode = None
        self._view.on_roi_geometry(self.key, self.rect(), final=True)
        super().mouseReleaseEvent(event)


class LineRoiItem(QGraphicsLineItem):
    """线形 ROI（任意角度）。

    - 左键拖**端点** = 改角度/长度（按住 Shift 吸附 0°/45°/90°）；
    - 左键或**右键**拖线身 = 整条平移；
    - 被选中时描边变红（与现场"选中的框显示红色"一致）。
    """

    def __init__(self, key: str, line: "QLineF", color: str, view: "ImageView"):
        super().__init__(line)
        self.key = key
        self._view = view
        self.setPen(QPen(QColor(color), 2))
        self.setFlags(
            QGraphicsLineItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsLineItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(12)
        self.setAcceptHoverEvents(True)
        self._mode: str | None = None      # 'p1' | 'p2' | 'move'
        self._start_pos = QPointF()
        self._start_line = QLineF()

    # ---- 命中判定
    def _near(self, pos: QPointF, pt: QPointF) -> bool:
        return abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE

    def _hit_endpoint(self, pos: QPointF) -> str | None:
        line = self.line()
        if self._near(pos, line.p1()):
            return "p1"
        if self._near(pos, line.p2()):
            return "p2"
        return None

    def _hit_body(self, pos: QPointF) -> bool:
        line = self.line()
        # 点到线段距离
        x1, y1, x2, y2 = line.x1(), line.y1(), line.x2(), line.y2()
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        if seg2 <= 0:
            return self._near(pos, line.p1())
        t = max(0.0, min(1.0, ((pos.x() - x1) * dx + (pos.y() - y1) * dy) / seg2))
        px, py = x1 + t * dx, y1 + t * dy
        return abs(pos.x() - px) <= HANDLE and abs(pos.y() - py) <= HANDLE

    def mousePressEvent(self, event) -> None:      # noqa: N802
        pos = event.pos()
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            end = self._hit_endpoint(pos)
            if end:
                self._mode = end
            elif self._hit_body(pos):
                self._mode = "move"
            else:
                self._mode = None
            self._start_pos = pos
            self._start_line = QLineF(self.line())
            if self._mode:
                event.accept()
                self._view.set_active_roi(self.key)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        if self._mode is None:
            super().mouseMoveEvent(event)
            return
        pos = event.pos()
        delta = pos - self._start_pos
        ln = QLineF(self._start_line)
        if self._mode == "move":
            ln.translate(delta)
        else:
            if self._mode == "p1":
                ln.setP1(pos)
            else:
                ln.setP2(pos)
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                ln = self._snap(ln, self._mode)
        self.setLine(ln)
        self._view.on_line_geometry(self.key, self.line(), final=False)
        event.accept()

    @staticmethod
    def _snap(ln: QLineF, fixed: str) -> QLineF:
        """把角度吸附到最近的 0°/45°/90°（以固定端为圆心）。"""
        import math

        anchor = ln.p1() if fixed == "p2" else ln.p2()
        moving = ln.p2() if fixed == "p2" else ln.p1()
        dx = moving.x() - anchor.x()
        dy = moving.y() - anchor.y()
        length = math.hypot(dx, dy)
        if length <= 0:
            return ln
        ang = math.degrees(math.atan2(dy, dx))
        snapped = min((0, 45, 90, 135, 180, -45, -90, -135), key=lambda a: abs(a - ang))
        rad = math.radians(snapped)
        nx = anchor.x() + length * math.cos(rad)
        ny = anchor.y() + length * math.sin(rad)
        out = QLineF(ln)
        if fixed == "p2":
            out.setP1(QPointF(nx, ny))
        else:
            out.setP2(QPointF(nx, ny))
        return out

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        if self._mode is not None:
            self._mode = None
            self._view.on_line_geometry(self.key, self.line(), final=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ImageView(QGraphicsView):
    """显示 BGR 帧，并管理每帧上叠加的 ROI 框。"""

    roiEdited = Signal(str, object)        # (item_id, Rect) —— 用户拖动/新建/删除后触发
    roiSelected = Signal(str)              # 用户点选某个 ROI
    roiCreated = Signal(object)            # 用户空白处拖出新框
    calibPointClicked = Signal(float, float)   # 标定模式下的点击（图像坐标）
    viewClicked = Signal()                     # 用户点了这个视图（用于切换当前机位）
    trackCreated = Signal(object)              # 用户画出跟踪框
    lineCreated = Signal(object)               # 用户画出一条线（线形 ROI）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(theme.color("bg.base"))))

        self._pix_item: QGraphicsPixmapItem | None = None
        self._rois: dict[str, RoiItem] = {}
        self._lines: dict[str, LineRoiItem] = {}
        self._colors: dict[str, str] = {}
        self._active: str | None = None
        self._selected: list[str] = []      # 多选（保持点击顺序）
        self._sel_rubber: QGraphicsRectItem | None = None
        self._sel_start: QPointF | None = None
        self._frame_size = (0, 0)
        self._rubber: QGraphicsRectItem | None = None
        self._rubber_start: QPointF | None = None
        self._calib_mode = False
        self._track_mode = False
        self._line_mode = False
        self._line_start: QPointF | None = None
        self._line_temp: QGraphicsLineItem | None = None
        self._track_item: QGraphicsRectItem | None = None
        self._calib_marks: list = []
        self._calib_points: list[tuple[float, float]] = []
        self.draw_tool = "rect"          # rect | hline（水平基准只向右）
        self.last_draw_ctrl = False

    # ------------------------------------------------------------------ 画面
    def set_frame(self, bgr: np.ndarray) -> None:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(img)
        if self._pix_item is None:
            self._pix_item = self._scene.addPixmap(pix)
            self._pix_item.setZValue(0)
        else:
            self._pix_item.setPixmap(pix)
        changed = self._frame_size != (w, h)
        self._frame_size = (w, h)
        if changed:
            self._scene.setSceneRect(QRectF(0, 0, w, h))
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:          # noqa: N802
        super().resizeEvent(event)
        if self._pix_item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------------ ROI
    def sync_rois(self, items: Iterable[tuple[str, Rect | None]]) -> None:
        """把配方里的 ROI 同步到视图（矩形 / 线形，保留未变化的对象）。"""
        wanted = {item_id: rect for item_id, rect in items}
        for key in list(self._rois):
            r = wanted.get(key)
            if r is None or r.is_line():
                self._scene.removeItem(self._rois.pop(key))
        for key in list(self._lines):
            r = wanted.get(key)
            if r is None or not r.is_line():
                self._scene.removeItem(self._lines.pop(key))
        for idx, (key, rect) in enumerate(wanted.items()):
            color = self._colors.setdefault(key, PALETTE[idx % len(PALETTE)])
            if rect is None:
                continue
            if rect.is_line() and rect.line is not None:
                ln = QLineF(*rect.line)
                if key in self._lines:
                    self._lines[key].setLine(ln)
                    self._lines[key].setPen(QPen(QColor(color), 2))
                else:
                    litem = LineRoiItem(key, ln, color, self)
                    self._scene.addItem(litem)         # 必须加入场景才会显示
                    self._lines[key] = litem
                continue
            qr = QRectF(rect.x, rect.y, rect.w, rect.h)
            if key in self._rois:
                self._rois[key].setRect(qr)
                self._rois[key].setPen(QPen(QColor(color), 2))
            else:
                item = RoiItem(key, qr, color, self)
                self._scene.addItem(item)              # 必须加入场景才会显示
                self._rois[key] = item

    # ------------------------------------------------------------------ 选择
    def _repaint_pens(self) -> None:
        red = theme.color("state.ng")
        for k, item in self._rois.items():
            if k in self._selected:
                item.setPen(QPen(QColor(red), 3))
            else:
                item.setPen(QPen(QColor(self._colors.get(k, PALETTE[0])), 2))
        for k, line in self._lines.items():
            if k in self._selected:
                line.setPen(QPen(QColor(red), 3))
            else:
                line.setPen(QPen(QColor(self._colors.get(k, PALETTE[0])), 2))

    def set_active_roi(self, key: str | None) -> None:
        """单选某个对象（会清掉其它选择）。"""
        self._active = key
        self._selected = [key] if key else []
        self._repaint_pens()

    def selection(self) -> list[str]:
        return list(self._selected)

    def toggle_selection(self, key: str) -> None:
        if key in self._selected:
            self._selected.remove(key)
        else:
            self._selected.append(key)
        self._active = self._selected[-1] if self._selected else None
        self._repaint_pens()

    def add_selection(self, key: str) -> None:
        if key not in self._selected:
            self._selected.append(key)
        self._active = key
        self._repaint_pens()

    def set_selection(self, keys: list[str], active: str | None = None) -> None:
        self._selected = [k for k in keys]
        if active is not None:
            self._active = active
        else:
            self._active = self._selected[-1] if self._selected else None
        self._repaint_pens()

    def selected_keys(self) -> list[str]:
        return list(self._selected)

    def note_selection(self, key: str, ctrl: bool) -> None:
        if ctrl:
            self.toggle_selection(key)
        else:
            self.set_active_roi(key)
        self.roiSelected.emit(key)

    def clear_selection(self) -> None:
        self._selected = []
        self._active = None
        self._repaint_pens()

    def active_key(self) -> str | None:
        return self._active

    def rubber_rect(self, x0: float, y0: float, x1: float, y1: float) -> Rect:
        """把拖拽端点收成矩形。水平基准线锁在起始高度，只向右。"""
        if self.draw_tool == "hline":
            x0, y0, x1, _y1 = clamp_horizontal_line(x0, y0, x1, y1)
            return Rect(int(x0), int(y0), max(1, int(round(x1 - x0))), 1)
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        return Rect(int(left), int(top), max(1, int(round(right - left))), max(1, int(round(bottom - top))))

    # ------------------------------------------------------------------ 线形 ROI
    def set_line_mode(self, on: bool) -> None:
        self._line_mode = bool(on)
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    def is_line_mode(self) -> bool:
        return self._line_mode

    def on_line_geometry(self, key: str, line: "QLineF", final: bool = False) -> None:
        if not final:
            return
        self.roiEdited.emit(key, Rect.from_line(line.x1(), line.y1(), line.x2(), line.y2()))

    # ------------------------------------------------------------------ 标定
    def set_calib_mode(self, on: bool) -> None:
        self._calib_mode = bool(on)
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)
        if not on:
            self.clear_calib_marks()

    # ------------------------------------------------------------------ 跟踪框
    def set_track_mode(self, on: bool) -> None:
        self._track_mode = bool(on)
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    def is_track_mode(self) -> bool:
        return self._track_mode

    def set_track_rect(self, rect: Rect | None) -> None:
        """显示跟踪框（青色虚线，不参与拖动/缩放）。"""
        if self._track_item is not None:
            self._scene.removeItem(self._track_item)
            self._track_item = None
        if rect is None:
            return
        item = QGraphicsRectItem(QRectF(rect.x, rect.y, rect.w, rect.h))
        pen = QPen(QColor(theme.color("accent.cyan")), 2, Qt.PenStyle.DashLine)
        item.setPen(pen)
        item.setZValue(18)
        self._scene.addItem(item)
        self._track_item = item

    def is_calib_mode(self) -> bool:
        return self._calib_mode

    def show_calib_points(self, points: list[tuple[float, float]]) -> None:
        """在画面上画出标定点与连线（纯展示）。"""
        self.clear_calib_marks()
        pen = QPen(QColor(theme.color("accent.cyan")), 2)
        for (x, y) in points:
            r = 10.0
            mark = QGraphicsEllipseItem(x - r, y - r, r * 2, r * 2)
            mark.setPen(pen)
            mark.setZValue(30)
            self._scene.addItem(mark)
            self._calib_marks.append(mark)
        if len(points) >= 2:
            line = QGraphicsLineItem(points[0][0], points[0][1], points[1][0], points[1][1])
            line.setPen(QPen(QColor(theme.color("accent.cyan")), 2, Qt.PenStyle.DashLine))
            line.setZValue(29)
            self._scene.addItem(line)
            self._calib_marks.append(line)
        self._calib_points = list(points)

    def clear_calib_marks(self) -> None:
        for item in self._calib_marks:
            try:
                self._scene.removeItem(item)
            except Exception:                          # noqa: BLE001
                pass
        self._calib_marks = []

    # ------------------------------------------------------------------ 事件
    def on_roi_geometry(self, key: str, rect: QRectF, final: bool = False) -> None:
        if final:
            r = rect.normalized()
            self.roiEdited.emit(key, Rect(int(r.x()), int(r.y()), int(r.width()), int(r.height())))

    def mousePressEvent(self, event) -> None:      # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.viewClicked.emit()
        if self._calib_mode and event.button() == Qt.MouseButton.LeftButton:
            pos = self.mapToScene(event.pos())
            self.calibPointClicked.emit(float(pos.x()), float(pos.y()))
            event.accept()
            return
        if self._line_mode and event.button() == Qt.MouseButton.LeftButton:
            # 画线模式：按下 = 起点
            scene_pos = self.mapToScene(event.pos())
            self._line_start = scene_pos
            self._line_temp = QGraphicsLineItem(QLineF(scene_pos, scene_pos))
            self._line_temp.setPen(QPen(QColor(theme.color("accent.cyan")), 2))
            self._line_temp.setZValue(22)
            self._scene.addItem(self._line_temp)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            hit = self._scene.itemAt(scene_pos, self.transform())
            if isinstance(hit, (RoiItem, LineRoiItem)):
                mods = event.modifiers()
                if mods & Qt.KeyboardModifier.ControlModifier:
                    self.toggle_selection(hit.key)
                elif mods & Qt.KeyboardModifier.ShiftModifier:
                    self.add_selection(hit.key)
                else:
                    self.set_active_roi(hit.key)
                self.roiSelected.emit(hit.key)
                super().mousePressEvent(event)
                return
            if hit is self._pix_item:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    # Ctrl + 空白拖拽 = 框选多个对象（不新建 ROI）
                    self._sel_start = scene_pos
                    self._sel_rubber = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
                    self._sel_rubber.setPen(QPen(QColor(theme.color("state.ng")), 1, Qt.PenStyle.DashLine))
                    self._sel_rubber.setZValue(21)
                    self._scene.addItem(self._sel_rubber)
                    event.accept()
                    return
                # 空白处开始拉新框
                self._rubber_start = scene_pos
                self._rubber = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
                self._rubber.setPen(QPen(QColor(theme.color("text.primary")), 1, Qt.PenStyle.DashLine))
                self._rubber.setZValue(20)
                self._scene.addItem(self._rubber)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        if self._sel_rubber is not None and self._sel_start is not None:
            cur = self.mapToScene(event.pos())
            self._sel_rubber.setRect(QRectF(self._sel_start, cur).normalized())
            event.accept()
            return
        if self._line_temp is not None and self._line_start is not None:
            cur = self.mapToScene(event.pos())
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                cur = self._snap_point(self._line_start, cur)
            self._line_temp.setLine(QLineF(self._line_start, cur))
            event.accept()
            return
        if self._rubber is not None and self._rubber_start is not None:
            cur = self.mapToScene(event.pos())
            if self.draw_tool == "hline":
                made = self.rubber_rect(self._rubber_start.x(), self._rubber_start.y(), cur.x(), cur.y())
                self._rubber.setRect(QRectF(made.x, made.y, made.w, made.h))
            else:
                self._rubber.setRect(QRectF(self._rubber_start, cur).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    @staticmethod
    def _snap_point(anchor: QPointF, moving: QPointF) -> QPointF:
        """按住 Shift 时，把线段角度吸附到 0°/45°/90°…（与 LineRoiItem 同一套规则）。"""
        import math

        dx = moving.x() - anchor.x()
        dy = moving.y() - anchor.y()
        length = math.hypot(dx, dy)
        if length <= 0:
            return moving
        ang = math.degrees(math.atan2(dy, dx))
        snapped = min((0, 45, 90, 135, 180, -45, -90, -135), key=lambda a: abs(a - ang))
        rad = math.radians(snapped)
        return QPointF(anchor.x() + length * math.cos(rad), anchor.y() + length * math.sin(rad))

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        if self._sel_rubber is not None:
            box = self._sel_rubber.rect().normalized()
            self._scene.removeItem(self._sel_rubber)
            self._sel_rubber = None
            self._sel_start = None
            picked: list[str] = []
            for store in (self._rois, self._lines):
                for key, item in store.items():
                    if box.intersects(item.sceneBoundingRect()) and key not in picked:
                        picked.append(key)
            self.set_selection(picked)
            event.accept()
            return
        if self._line_temp is not None:
            ln = self._line_temp.line()
            self._scene.removeItem(self._line_temp)
            self._line_temp = None
            self._line_start = None
            self.set_line_mode(False)
            if abs(ln.x2() - ln.x1()) > 3 or abs(ln.y2() - ln.y1()) > 3:
                self.lineCreated.emit(Rect.from_line(ln.x1(), ln.y1(), ln.x2(), ln.y2()))
            event.accept()
            return
        if self._rubber is not None:
            r = self._rubber.rect().normalized()
            self._scene.removeItem(self._rubber)
            self._rubber = None
            self._rubber_start = None
            self.last_draw_ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if self.draw_tool == "hline":
                made = self.rubber_rect(r.left(), r.top(), r.right(), r.bottom())
                if made.w > 1:
                    self.roiCreated.emit(made)
                event.accept()
                return
            if r.width() > 4 and r.height() > 4:
                if self._track_mode:
                    self.set_track_mode(False)
                    self.trackCreated.emit(Rect(int(r.x()), int(r.y()), int(r.width()), int(r.height())))
                    event.accept()
                    return
                self.roiCreated.emit(Rect(int(r.x()), int(r.y()), int(r.width()), int(r.height())))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:        # noqa: N802
        if event.key() == Qt.Key.Key_Delete and self._active:
            self.roiEdited.emit(self._active, None)      # None 表示删除
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:           # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        event.accept()
