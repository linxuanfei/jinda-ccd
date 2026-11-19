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
from . import theme

PALETTE = theme.ROI_PALETTE
HANDLE = 10.0          # 角点命中半径（图像像素）


def _pen(color, width: int = 1, style=None) -> "QPen":
    """细线画笔：cosmetic=True → 不管放大缩小都只占 1 个屏幕像素，不会看起来变粗。"""
    p = QPen(QColor(color), width, style if style is not None else Qt.PenStyle.SolidLine)
    p.setCosmetic(True)
    return p


class RoiItem(QGraphicsRectItem):
    """可拖动 / 可缩放 / 可删除的 ROI 矩形。"""

    def __init__(self, key: str, rect: QRectF, color: str, view: "ImageView"):
        super().__init__(rect)
        self.key = key
        self._view = view
        self.setPen(_pen(color))
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)
        self._drag_mode = None      # 'move' | 'tl' | 'tr' | 'bl' | 'br' | None
        self._start_pos = QPointF()
        self._start_rect = QRectF()
        self.label = ""             # 序号（01、02…），由界面在重建时填

    def set_label(self, text: str) -> None:
        self.label = text
        self.update()

    def boundingRect(self):                      # noqa: N802
        r = super().boundingRect()
        _v = getattr(self, "_view", None)
        z = max(0.05, float(_v.transform().m11() or 1.0)) if _v is not None else 1.0
        up = max(34.0, 34.0 / z)                 # 序号画在框外左上角，要算进重绘区域
        return r.adjusted(-up, -up, 4, 4)

    def paint(self, painter, option, widget=None) -> None:      # noqa: N802
        super().paint(painter, option, widget)
        if not self.label or not getattr(self._view, "_show_numbers", True):
            return
        r = self.rect()
        painter.save()
        font = painter.font()
        z = max(0.05, float(self._view.transform().m11() or 1.0))
        # 屏幕上恒定 ~18px，但设上限：画面缩得很小时不至于变成巨大的字盖住图
        font.setPixelSize(min(28, max(12, int(round(18 / z)))))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(self.label) + 8
        th = metrics.height() + 2
        bx = r.left() + 2
        by = r.top() - th - 3      # 序号放在框的左上角外侧，避免压住检测区域
        if by < r.top() - th - 3:
            by = r.top() - th - 3
        if by < 0:                 # 框贴到画面顶边时退回框内
            by = r.top() + 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 200))
        painter.drawRect(QRectF(bx, by, tw, th))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 220, 0), 1))
        painter.drawText(QPointF(bx + 4, by + th - 4), self.label)
        painter.restore()

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
        # 右键拖动 = 移动整个框（现场习惯：点一下选中变红，再右键拖）
        if event.button() == Qt.MouseButton.RightButton:
            self._rb_start = QPointF(event.scenePos())      # 先记下，拖动算移动
            self._start_pos = event.pos()
            self._start_scene = QPointF(event.scenePos())
            self._start_rect = QRectF(self.rect())
            self._drag_mode = None
            self._view.set_active_roi(self.key)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            if self._view.begin_group_drag(self.key, event.scenePos()):
                event.accept()
                return
            corner = self._hit_corner(pos)
            self._start_pos = pos
            self._start_scene = QPointF(event.scenePos())
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
        if getattr(self, "_rb_start", None) is not None and self._drag_mode is None:
            d = event.scenePos() - self._rb_start
            if abs(d.x()) + abs(d.y()) > 3:                 # 真的在拖 → 进入移动
                self._rb_start = None
                if self._view.begin_group_drag(self.key, event.scenePos()):
                    event.accept()
                    return
                self._drag_mode = "move"
                self._start_scene = QPointF(event.scenePos())
                self._start_rect = QRectF(self.rect())
        if getattr(self._view, "_drag_item_key", None) is not None:
            self._view.update_group_drag(event.scenePos())      # 整组拖动
            event.accept()
            return
        if self._drag_mode is None:
            super().mouseMoveEvent(event)
            return
        # 用场景坐标算位移：移动过程中图元原点会变，用 item 坐标会越拖越快
        start_scene = getattr(self, "_start_scene", None)
        if start_scene is not None:
            delta = event.scenePos() - start_scene
        else:
            delta = event.pos() - self._start_pos
        r = QRectF(self._start_rect)
        z = max(0.05, float(self._view.transform().m11() or 1.0))
        if self._drag_mode == "move":
            r.translate(delta / z)
        else:
            if self._drag_mode in ("tl", "bl"):
                r.setLeft(min(self._start_rect.left() + delta.x(), self._start_rect.right() - 5))
            if self._drag_mode in ("tr", "br"):
                r.setRight(max(self._start_rect.right() + delta.x(), self._start_rect.left() + 5))
            if self._drag_mode in ("tl", "tr"):
                r.setTop(min(self._start_rect.top() + delta.y(), self._start_rect.bottom() - 5))
            if self._drag_mode in ("bl", "br"):
                r.setBottom(max(self._start_rect.bottom() + delta.y(), self._start_rect.top() + 5))
        self.setRect(self._view.clamp_rect(r.normalized()))
        self._view.on_roi_geometry(self.key, self.rect())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        if getattr(self, "_rb_start", None) is not None:
            self._rb_start = None
            self._view.clear_selection()                       # 右键没拖动 = 取消选中
            event.accept()
            return
        if getattr(self._view, "_drag_item_key", None) is not None:
            self._view.finish_group_drag()                     # 整组拖动结束
            event.accept()
            return
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
        self.setPen(_pen(color))
        self.setFlags(
            QGraphicsLineItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsLineItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(12)
        self.setAcceptHoverEvents(True)
        self._mode: str | None = None      # 'p1' | 'p2' | 'move'
        self._start_pos = QPointF()
        self._start_line = QLineF()
        self.label = ""

    def set_label(self, text: str) -> None:
        self.label = text
        self.update()

    def boundingRect(self):                      # noqa: N802
        r = super().boundingRect()
        # 编号画在线上方，必须算进重绘区域，否则拖动会有拖影
        z = max(0.05, float(self._view.transform().m11() or 1.0))
        up = max(40.0, 40.0 / z)
        return r.adjusted(-up, -up, up, 12)

    def paint(self, painter, option, widget=None) -> None:      # noqa: N802
        super().paint(painter, option, widget)
        if not self.label or not getattr(self._view, "_show_numbers", True):
            return
        ln = self.line()
        painter.save()
        font = painter.font()
        z = max(0.05, float(self._view.transform().m11() or 1.0))
        font.setPixelSize(max(18, int(round(22 / z))))     # 屏幕上恒定 22px
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(self.label) + 8
        th = metrics.height() + 2
        bx = ln.x1() + 2
        by = ln.y1() - th - 4
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 190))
        painter.drawRect(QRectF(bx, by, tw, th))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 220, 0), 1))
        painter.drawText(QPointF(bx + 4, by + th - 4), self.label)
        painter.restore()

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
        z = max(0.05, float(self._view.transform().m11() or 1.0))
        tol = HANDLE / z * 1.6 + 2          # 低倍率时也要点得中
        return abs(pos.x() - px) <= tol and abs(pos.y() - py) <= tol

    def mousePressEvent(self, event) -> None:      # noqa: N802
        pos = event.pos()
        if event.button() == Qt.MouseButton.RightButton:
            self._rb_start = QPointF(event.scenePos())
            self._start_pos = pos
            self._start_line = QLineF(self.line())
            self._mode = None
            self._view.set_active_roi(self.key)
            event.accept()
            return
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            end = self._hit_endpoint(pos)
            if end:
                self._mode = end
            elif self._hit_body(pos):
                self._mode = "move"
            else:
                self._mode = None
            self._start_pos = pos
            self._start_scene = QPointF(event.scenePos())
            self._start_line = QLineF(self.line())
            if self._mode:
                event.accept()
                self._view.set_active_roi(self.key)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        if getattr(self, "_rb_start", None) is not None and self._mode is None:
            d = event.scenePos() - self._rb_start
            if abs(d.x()) + abs(d.y()) > 3:                 # 真的在拖 → 整条平移
                self._rb_start = None
                if self._view.begin_group_drag(self.key, event.scenePos()):
                    event.accept()
                    return
                self._mode = "move"
                self._start_pos = event.pos()
                self._start_scene = QPointF(event.scenePos())
                self._start_line = QLineF(self.line())
        if getattr(self._view, "_drag_item_key", None) is not None:
            self._view.update_group_drag(event.scenePos())
            event.accept()
            return
        if self._mode is None:
            super().mouseMoveEvent(event)
            return
        pos = event.pos()
        start_scene = getattr(self, "_start_scene", None)
        delta = (event.scenePos() - start_scene) if start_scene is not None else (pos - self._start_pos)
        ln = QLineF(self._start_line)
        if self._mode == "move":
            z = max(0.05, float(self._view.transform().m11() or 1.0))
            ln.translate(delta / z)
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
    roiSelected = Signal(str)
    selectionCleared = Signal()              # 用户点选某个 ROI
    roiCreated = Signal(object)            # 用户空白处拖出新框
    calibPointClicked = Signal(float, float)   # 标定模式下的点击（图像坐标）
    viewClicked = Signal()                     # 用户点了这个视图（用于切换当前机位）
    trackCreated = Signal(object)              # 用户画出跟踪框
    lineCreated = Signal(object)               # 用户画出一条线（线形 ROI）
    itemMenuRequested = Signal(str)            # 右键点了某个检测框（要弹菜单）
    areaCreated = Signal(object)               # 画出检测区域（黄色大框）
    locatorCreated = Signal(object)            # 画出定位模板框（青色）
    cellsEdited = Signal(str, object)          # (item_id, [[x,y,w,h], ...]) 小框被拖动/缩放

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_numbers = True          # 默认显示编号
        self._stats = {}                   # 左上角统计（状态/OK/NG/总数/良率）
        self._ov = {"size_stat": 20, "size_okng": 46, "x": 14, "y": 10}
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        # 拖动时整屏刷新：否则序号/框会留下"虚影"残影
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        # 不开背景缓存：缓存会把旧像素留在框内，看起来像一块黑色阴影
        try:
            self.setCacheMode(QGraphicsView.CacheModeFlag(0))
        except Exception:
            pass
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
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
        self._select_mode = True        # True=选择/移动（空白框选、框内拖动）；False=画新框
        self._item_state: dict[str, bool] = {}   # item_id -> 是否 OK（False 画红框）
        self._drag_item_key: str | None = None   # 右键在画面任意处拖动选中的框
        self._drag_item_keys: list[str] = []     # 右键拖动时一起移动的框（多选）
        self._drag_start_rects: dict[str, QRectF] = {}
        self._drag_start_lines: dict = {}
        self._drag_origin = None                 # 右键拖动起点（场景坐标）
        self._cells: dict[str, list[str]] = {}   # 小框：item_id -> [小框 key…]
        self._parent_rects: dict = {}             # 父框上一次的位置（算整体移动用）
        self._cell_of: dict[str, str] = {}       # 小框 key -> item_id
        self._area_mode = False                  # 画"检测区域"
        self._locator_mode = False               # 画"定位模板"框
        self._area_item = None                   # 检测区域黄框
        self._locator_item = None                # 定位模板青框
        self._drag_item_last = None
        self._right_press: tuple[str, QPointF] | None = None
        self._auto_fit = True           # True=跟随窗口铺满；用户滚轮缩放后变 False
        self._panning = False           # 中键 / 空格+左键 拖动平移
        self._pan_start = None
        self._space_down = False

    # ------------------------------------------------------------------ 画面
    def set_stats(self, **kw) -> None:
        """更新左上角统计（running/verdict/ok/ng/total/yield_text）。"""
        self._stats = dict(kw)
        self.viewport().update()

    def set_overlay(self, cfg: dict | None = None) -> None:
        """设置统计文字的字号与位置（size_stat / size_okng / x / y）。"""
        if cfg:
            self._ov.update({k: cfg[k] for k in ("size_stat", "size_okng", "x", "y") if k in cfg})
        self.viewport().update()

    def drawForeground(self, painter, rect) -> None:      # noqa: N802
        """在画面左上角画状态与统计（图元坐标之外，永远压在最上层）。"""
        st = self._stats
        if not st:
            return
        s = int(self._ov.get("size_stat", 20))
        so = int(self._ov.get("size_okng", 46))
        x = int(self._ov.get("x", 14))
        y = int(self._ov.get("y", 10))
        painter.save()
        painter.resetTransform()          # 用视口坐标画，字号不受缩放影响
        # 半透明底，保证在黑背景上也看得清
        f1 = painter.font()
        f1.setPixelSize(s)
        f1.setBold(True)
        painter.setFont(f1)
        lines = [
            ("状态：" + ("运行中" if st.get("running") else "待机中"), "#7fd1ff"),
            (f"总数量：{st.get('total', 0)}", "#2fd67b"),
            (f"良品数：{st.get('ok', 0)}", "#2fd67b"),
            (f"NG 数：{st.get('ng', 0)}", "#ff5d5d"),
            (f"良品率：{st.get('yield_text', '--')}", "#2fd67b"),
        ]
        fm = painter.fontMetrics()
        width = max(fm.horizontalAdvance(txt) for txt, _c in lines) + 24
        height = fm.height() * (len(lines) + 1) + so + 22
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRect(QRectF(x - 8, y - 6, width, height))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        ty = y + fm.ascent()
        for txt, color in lines:
            painter.setPen(QPen(QColor(color), 1))
            painter.drawText(QPointF(x, ty), txt)
            ty += fm.height()
        # OK / NG 大字
        verdict = st.get("verdict") or ""
        if verdict:
            f2 = painter.font()
            f2.setPixelSize(so)
            f2.setBold(True)
            painter.setFont(f2)
            painter.setPen(QPen(QColor("#2fd67b" if verdict == "OK" else "#ff5d5d"), 1))
            painter.drawText(QPointF(x, ty + so - 6), verdict)
        painter.restore()

    def set_numbers_visible(self, on: bool) -> None:
        self._show_numbers = bool(on)
        for it in list(self._rois.values()) + list(self._lines.values()):
            it.update()

    def clamp_rect(self, r: QRectF) -> QRectF:
        """把矩形夹进画面范围（不能拖到画面外，否则就选不中了）。"""
        img = getattr(self, "_image_rect", None)
        if img is None:
            return r
        x = max(0.0, min(r.x(), img.width() - 2))
        y = max(0.0, min(r.y(), img.height() - 2))
        w = max(2.0, min(r.width(), img.width() - x))
        h = max(2.0, min(r.height(), img.height() - y))
        return QRectF(x, y, w, h)

    def frame_size(self) -> tuple[int, int]:
        """当前画面尺寸（宽, 高）；没有帧时返回 (0, 0)。"""
        return (int(self._frame_size[0]), int(self._frame_size[1]))

    def frame_bgr(self):
        """当前画面上显示的这一帧（BGR）。阈值预览等需要"就用手上这张图"时用。"""
        return getattr(self, "_last_bgr", None)

    def set_frame(self, bgr: np.ndarray) -> None:
        self._last_bgr = bgr
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        # 不 copy：QPixmap.fromImage 会自己拷一份，rgb 在本函数内保持存活
        img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(img)
        del img
        if self._pix_item is None:
            self._pix_item = self._scene.addPixmap(pix)
            self._pix_item.setZValue(0)
        else:
            self._pix_item.setPixmap(pix)
        changed = self._frame_size != (w, h)
        self._frame_size = (w, h)
        if changed:
            self._image_rect = QRectF(0, 0, w, h)
            # 场景比图片大一圈：铺满时也能用鼠标拖动画面（否则没有可滚动区域）
            self._scene.setSceneRect(self._image_rect.adjusted(-w * 0.5, -h * 0.5, w * 0.5, h * 0.5))
            if self._auto_fit and not getattr(self, "_fitted_once", False):
                self.fitInView(getattr(self, "_image_rect", self._scene.sceneRect()),
                               Qt.AspectRatioMode.KeepAspectRatio)
                self._fitted_once = True

    def resizeEvent(self, event) -> None:          # noqa: N802
        super().resizeEvent(event)
        if self._pix_item is not None and self._auto_fit:
                self.fitInView(getattr(self, "_image_rect", self._scene.sceneRect()), Qt.AspectRatioMode.KeepAspectRatio)

    def fit_window(self) -> None:
        """恢复"铺满窗口"（双击画面或按 F 键调用）。"""
        self._auto_fit = True
        if self._pix_item is not None:
                self.fitInView(getattr(self, "_image_rect", self._scene.sceneRect()), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------------ ROI
    def sync_rois(self, items: Iterable[tuple[str, Rect | None]], labels: dict | None = None) -> None:
        # 已分列小框的项：不画父框（现场只要 N 个小框，不要大框）
        skip_parent = {k for k, v in (getattr(self, "_cells", {}) or {}).items() if v}
        """把配方里的 ROI 同步到视图（矩形 / 线形，保留未变化的对象）。"""
        wanted = {item_id: rect for item_id, rect in items}
        for key in list(self._rois):
            r = wanted.get(key)
            if r is None or r.is_line() or key in skip_parent:
                self._scene.removeItem(self._rois.pop(key))
        for key in list(self._lines):
            r = wanted.get(key)
            if r is None or not r.is_line():
                self._scene.removeItem(self._lines.pop(key))
        # 统一编号：按配方里的顺序 01、02…（矩形和线一起排，现场靠它认框）
        _num = 0
        for idx, (key, rect) in enumerate(wanted.items()):
            # 编辑态一律绿色；红色只用于"选中"（避免取消选中后还残留红色）
            color = theme.color("state.ok")
            if rect is None or key in skip_parent:
                continue
            _num += 1
            _label = (labels or {}).get(key) or str(_num)
            if rect.is_line() and rect.line is not None:
                ln = QLineF(*rect.line)
                if key in self._lines:
                    self._lines[key].setLine(ln)
                    self._lines[key].setPen(_pen(color))
                    if hasattr(self._lines[key], "set_label"):
                        self._lines[key].set_label(_label)
                else:
                    litem = LineRoiItem(key, ln, color, self)
                    self._scene.addItem(litem)         # 必须加入场景才会显示
                    self._lines[key] = litem
                    if hasattr(litem, "set_label"):
                        litem.set_label(_label)
                continue
            qr = QRectF(rect.x, rect.y, rect.w, rect.h)
            if key in self._rois:
                self._rois[key].setRect(qr)
                self._rois[key].setPen(_pen(color))
                self._rois[key].set_label(_label)
            else:
                item = RoiItem(key, qr, color, self)
                self._scene.addItem(item)              # 必须加入场景才会显示
                item.set_label(_label)
                self._rois[key] = item

        self._repaint_pens()      # 同步完统一刷一次颜色：选中红、其它绿

    # ------------------------------------------------------------------ 选择
    def set_item_states(self, states: dict[str, bool]) -> None:
        """按检测结果给框上色：OK 绿、NG 红（只改颜色，不动位置）。"""
        if states == self._item_state:
            return
        self._item_state = dict(states)
        self._repaint_pens()

    def _repaint_pens(self) -> None:
        red = theme.color("state.ng")
        green = theme.color("state.ok")
        # 现场要求：只把"当前这一根/这一个"标红（细线），其它一律绿
        for k, item in self._rois.items():
            item.setPen(_pen(red if k in self._selected else green))
        for k, line in self._lines.items():
            line.setPen(_pen(red if k in self._selected else green))

    def set_active_roi(self, key: str | None) -> None:
        """设置当前对象。若它已在多选里，则只改"当前"，不打散多选。"""
        self._active = key
        if key is None:
            self._selected = []
        elif key in self._selected and len(self._selected) > 1:
            pass                       # 保留多选（现场要求：整组移动别被点散）
        else:
            self._selected = [key]
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

    def set_selection(self, keys: list[str]) -> None:
        self._selected = [k for k in keys]
        self._active = self._selected[-1] if self._selected else None
        self._repaint_pens()

    def clear_selection(self) -> None:
        self._selected = []
        self._active = None
        self._repaint_pens()
        try:
            self.selectionCleared.emit()
        except Exception:                            # noqa: BLE001
            pass

    def active_key(self) -> str | None:
        return self._active

    # ------------------------------------------------------------------ 线形 ROI
    def set_select_mode(self, on: bool) -> None:
        """选择/移动模式：空白拖=框选，框内拖=移动；关掉后空白拖=画新框。"""
        self._select_mode = bool(on)
        self.setCursor(Qt.CursorShape.ArrowCursor if on else Qt.CursorShape.CrossCursor)

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
    def update_group_drag(self, scene_pos) -> None:
        """按当前鼠标位置整组平移（键盘/右键拖动过程中调用）。"""
        if self._drag_item_key is None or self._drag_origin is None:
            return
        dx = scene_pos.x() - self._drag_origin.x()
        dy = scene_pos.y() - self._drag_origin.y()
        for k in (self._drag_item_keys or [self._drag_item_key]):
            it = self._rois.get(k)
            start = self._drag_start_rects.get(k)
            if it is not None and start is not None:
                it.setRect(self.clamp_rect(QRectF(start).translated(dx, dy)))
                continue
            ln = self._lines.get(k)
            lstart = self._drag_start_lines.get(k)
            if ln is not None and lstart is not None:
                ln.setLine(lstart.translated(dx, dy))

    def finish_group_drag(self) -> None:
        """松开鼠标：把整组的结果写回配方。"""
        for k in (self._drag_item_keys or []):
            it = self._rois.get(k)
            if it is not None:
                self.on_roi_geometry(k, it.rect(), final=True)
                continue
            ln = self._lines.get(k)
            if ln is not None:
                line = ln.line()
                self.roiEdited.emit(k, Rect.from_line(line.x1(), line.y1(), line.x2(), line.y2()))
        self._drag_item_key = None
        self._drag_item_keys = []
        self._drag_origin = None
        self._drag_start_rects = {}
        self._drag_start_lines = {}

    def begin_group_drag(self, key: str, scene_pos) -> bool:
        """右键点在框/线上时：如果当前是多选，或者上次是整组，就整组一起拖。"""
        sel = [k for k in (self._selected or []) if k in self._rois or k in self._lines]
        last = list(getattr(self, "_last_group", []) or [])
        if len(sel) < 2 and key in last:
            sel = last                              # 上一次整组移动过，这次继续整组
        if len(sel) < 2 or key not in sel:
            return False
        self._last_group = list(sel)
        self._drag_item_key = key
        self._drag_item_keys = sel
        self._drag_start_rects = {k: QRectF(self._rois[k].rect()) for k in sel if k in self._rois}
        self._drag_start_lines = {k: QLineF(self._lines[k].line()) for k in sel if k in self._lines}
        self._drag_origin = QPointF(scene_pos)
        self._drag_item_last = QPointF(scene_pos)
        return True

    def set_pick_lines(self, lines: list) -> None:
        """框内抓取线（绿色细线，随框刷新）。"""
        for old in getattr(self, "_pick_items", []) or []:
            try:
                self._scene.removeItem(old)
            except Exception:                            # noqa: BLE001
                pass
        self._pick_items = []
        for ln in lines:
            item = LineRoiItem("__pick__", ln, theme.color("accent.cyan"), self)
            item.setZValue(9)
            item.setPen(_pen(theme.color("accent.cyan"), 2))
            self._scene.addItem(item)
            self._pick_items.append(item)

    def set_pick_points(self, points: list) -> None:
        """把检测出来的抓取中心点画成十字（品红），现场一眼看到抓到哪。"""
        for old in getattr(self, "_pick_pts", []) or []:
            try:
                self._scene.removeItem(old)
            except Exception:                            # noqa: BLE001
                pass
        self._pick_pts = []
        for p in points or []:
            ln_h = QLineF(p[0] - 12, p[1], p[0] + 12, p[1])
            ln_v = QLineF(p[0], p[1] - 12, p[0], p[1] + 12)
            for ln in (ln_h, ln_v):
                it = LineRoiItem("__pt__", ln, theme.color("state.ng"), self)
                it.setZValue(11)
                it.setPen(_pen(theme.color("state.ng"), 1))
                self._scene.addItem(it)
                self._pick_pts.append(it)

    def set_cells(self, item_id: str, rects: list, parent_no: int = 1) -> None:
        rects = []          # 主画面不画小框（避免看起来像杂线），小框在检测框设置里看
        """显示某个检测项分割出来的小框（每个都能单独拖动/缩放）。"""
        # 把这个项名下的所有旧小框都清掉（包括记录丢失的"野"小框，否则会留下红影）
        old = [k for k in list(self._rois) if k.startswith(f"{item_id}#")]
        old += [k for k in self._cells.get(item_id, []) if k not in old]
        for k in old:
            it = self._rois.pop(k, None)
            if it is not None:
                self._scene.removeItem(it)
            self._cell_of.pop(k, None)
        if rects:
            self._cells[item_id] = []
        else:
            self._cells.pop(item_id, None)
        parent = self._rois.get(item_id)
        if parent is not None:
            self._parent_rects[item_id] = QRectF(parent.rect())
        for i, r in enumerate(rects or []):
            key = f"{item_id}#{i}"
            qr = QRectF(r[0], r[1], r[2], r[3])
            it = RoiItem(key, qr, theme.color("state.ok"), self)
            it.set_label(f"{parent_no}-{i + 1}")
            self._scene.addItem(it)
            self._rois[key] = it
            self._cell_of[key] = item_id
            self._cells[item_id].append(key)

    def purge_unknown(self, valid_ids: set) -> int:
        """删掉不属于任何检测项的图形（野线/野框）。"""
        n = 0
        for k in list(self._rois):
            base = k.split("#")[0]
            if base not in valid_ids:
                it = self._rois.pop(k, None)
                if it is not None:
                    self._scene.removeItem(it)
                self._cell_of.pop(k, None)
                n += 1
        for k in list(self._lines):
            if k not in valid_ids:
                ln = self._lines.pop(k, None)
                if ln is not None:
                    self._scene.removeItem(ln)
                n += 1
        for k in list(self._cells):
            if k not in valid_ids:
                self._cells.pop(k, None)
        return n

    def clear_all_cells(self) -> None:
        """把画面上所有小框清掉（防止残留野框）。"""
        for k in [k for k in list(self._rois) if "#" in k]:
            it = self._rois.pop(k, None)
            if it is not None:
                self._scene.removeItem(it)
            self._cell_of.pop(k, None)
        self._cells.clear()

    def clear_cells(self, item_id: str) -> None:
        self.set_cells(item_id, [])

    def _emit_cells(self, item_id: str) -> None:
        keys = self._cells.get(item_id, [])
        rects = []
        for k in keys:
            it = self._rois.get(k)
            if it is not None:
                r = it.rect()
                rects.append([int(r.x()), int(r.y()), int(r.width()), int(r.height())])
        self.cellsEdited.emit(item_id, rects)

    def set_extra_rects(self, area: "Rect | None" = None, locator: "Rect | None" = None) -> None:
        """画面上显示检测区域（黄）与定位模板框（青）。"""
        for name in ("_area_item", "_locator_item"):
            it = getattr(self, name, None)
            if it is not None:
                self._scene.removeItem(it)
                setattr(self, name, None)
        if area is not None:
            from ..qtcompat import QGraphicsRectItem, QPen, QColor

            item = QGraphicsRectItem(QRectF(area.x, area.y, area.w, area.h))
            item.setPen(QPen(QColor(255, 215, 0), 2, Qt.PenStyle.DashLine))
            item.setZValue(18)
            self._scene.addItem(item)
            self._area_item = item
        if locator is not None:
            from ..qtcompat import QGraphicsRectItem, QPen, QColor

            item = QGraphicsRectItem(QRectF(locator.x, locator.y, locator.w, locator.h))
            pen = QPen(QColor(0, 255, 255), 2)
            item.setPen(pen)
            item.setZValue(19)
            self._scene.addItem(item)
            self._locator_item = item

    def set_area_mode(self, on: bool) -> None:
        self._area_mode = bool(on)
        if on:
            self._locator_mode = False
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    def set_locator_mode(self, on: bool) -> None:
        self._locator_mode = bool(on)
        if on:
            self._area_mode = False
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

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
        if key in self._cell_of:
            if final:
                self._emit_cells(self._cell_of[key])
            return
        # 父框移动 → 它分出来的小框整体跟着走（现场要"整体移动"）
        prev = self._parent_rects.get(key)
        if prev is not None and key in self._cells:
            dx = rect.x() - prev.x()
            dy = rect.y() - prev.y()
            if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                for ck in self._cells.get(key, []):
                    ci = self._rois.get(ck)
                    if ci is not None:
                        ci.setRect(ci.rect().translated(dx, dy))
        self._parent_rects[key] = QRectF(rect)
        if final:
            r = rect.normalized()
            self.roiEdited.emit(key, Rect(int(r.x()), int(r.y()), int(r.width()), int(r.height())))

    def mousePressEvent(self, event) -> None:      # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._rb_press = QPointF(event.pos())
            item = self._scene.itemAt(self.mapToScene(event.pos()), self.transform())
            hit = item
            while hit is not None and not isinstance(hit, (RoiItem, LineRoiItem)):
                hit = hit.parentItem()
            self._rb_blank = hit is None
        # 中键，或按住空格 + 左键 = 拖动画面（放大后看局部）
        if event.button() == Qt.MouseButton.MiddleButton or (
                event.button() == Qt.MouseButton.LeftButton and self._space_down):
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            # 现场要求：框选中后，鼠标在画面任意位置右键拖动都能移动它
            keys = [k for k in (self._selected or []) if k in self._rois or k in self._lines]
            key = self._active or (keys[0] if keys else None)
            if getattr(self, "_rb_blank", False):
                key = None                      # 空白处：等真的拖动再启动
            if key and (key in self._rois or key in self._lines):
                self._drag_item_key = key
                self._drag_item_keys = keys or [key]
                self._drag_start_rects = {
                    k: QRectF(self._rois[k].rect()) for k in self._drag_item_keys if k in self._rois
                }
                self._drag_start_lines = {
                    k: QLineF(self._lines[k].line()) for k in self._drag_item_keys if k in self._lines
                }
                self._drag_item_last = self.mapToScene(event.pos())
                self._drag_origin = self.mapToScene(event.pos())
                self.set_active_roi(key)
                event.accept()
                return
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
                if self._select_mode:
                    # 选择模式：空白拖 = 框选多个检测框（不新建）
                    self._sel_start = scene_pos
                    self._sel_rubber = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
                    self._sel_rubber.setPen(QPen(QColor(theme.color("state.ok")), 1, Qt.PenStyle.DashLine))
                    self._sel_rubber.setZValue(21)
                    self._scene.addItem(self._sel_rubber)
                    event.accept()
                    return
                # 画框模式：空白处开始拉新框
                self._rubber_start = scene_pos
                self._rubber = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
                self._rubber.setPen(QPen(QColor(theme.color("text.primary")), 1, Qt.PenStyle.DashLine))
                self._rubber.setZValue(20)
                self._scene.addItem(self._rubber)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        # 空白处右键拖动：真的拖过 3px 再启动整组移动（点一下则是取消选中）
        if (getattr(self, "_rb_blank", False) and self._drag_item_key is None
                and self._rb_press is not None):
            st = self._rb_press
            if abs(event.pos().x() - st.x()) + abs(event.pos().y() - st.y()) > 3:
                keys = [k for k in (self._selected or []) if k in self._rois or k in self._lines]
                key = self._active or (keys[0] if keys else None)
                if key is not None:
                    self._drag_item_key = key
                    self._drag_item_keys = keys or [key]
                    self._drag_start_rects = {
                        k: QRectF(self._rois[k].rect()) for k in self._drag_item_keys if k in self._rois}
                    self._drag_start_lines = {
                        k: QLineF(self._lines[k].line()) for k in self._drag_item_keys if k in self._lines}
                    self._drag_origin = self.mapToScene(st.toPoint())
                    self._drag_item_last = self._drag_origin
        if self._drag_item_key is not None and self._drag_origin is not None:
            cur = self.mapToScene(event.pos())
            delta_total_x = cur.x() - self._drag_origin.x()
            delta_total_y = cur.y() - self._drag_origin.y()
            _z = max(0.05, float(self.transform().m11() or 1.0))
            delta_total_x /= _z
            delta_total_y /= _z
            for k in (self._drag_item_keys or [self._drag_item_key]):
                it = self._rois.get(k)
                start = self._drag_start_rects.get(k)
                if it is not None and start is not None:
                    it.setRect(self.clamp_rect(QRectF(start).translated(delta_total_x, delta_total_y)))
                    self.on_roi_geometry(k, it.rect(), final=False)
                    continue
                li = self._lines.get(k)
                lstart = self._drag_start_lines.get(k)
                if li is not None and lstart is not None:
                    li.setLine(lstart.translated(delta_total_x, delta_total_y))
            event.accept()
            return
        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start          # QPoint - QPoint
            self._pan_start = event.pos()
            scale = self.transform().m11() or 1.0
            center = self.mapToScene(self.viewport().rect().center())
            self.centerOn(center.x() - delta.x() / scale, center.y() - delta.y() / scale)
            event.accept()
            return
        if self._sel_rubber is not None and self._sel_start is not None:
            cur = self.mapToScene(event.pos())
            self._sel_rubber.setRect(QRectF(self._sel_start, cur).normalized())
            event.accept()
            return
        if self._line_temp is not None and self._line_start is not None:
            cur = self.mapToScene(event.pos())
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                cur = self._snap_point(self._line_start, cur)
            else:
                # 现场要求：画出的一定是"直"的（水平或垂直），鼠标上下不改变方向
                dx = cur.x() - self._line_start.x()
                dy = cur.y() - self._line_start.y()
                if abs(dx) >= abs(dy):
                    cur = QPointF(cur.x(), self._line_start.y())   # 水平：y 固定
                else:
                    cur = QPointF(self._line_start.x(), cur.y())   # 垂直：x 固定
            self._line_temp.setLine(QLineF(self._line_start, cur))
            event.accept()
            return
        if self._rubber is not None and self._rubber_start is not None:
            cur = self.mapToScene(event.pos())
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

    def mouseDoubleClickEvent(self, event) -> None:      # noqa: N802
        """双击画面 = 恢复"铺满窗口"（看局部后想拉回全图）。"""
        self.fit_window()
        self.viewClicked.emit()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        if event.button() == Qt.MouseButton.RightButton and getattr(self, "_rb_blank", False):
            moved = 0.0
            if self._rb_press is not None:
                p = event.pos()
                moved = abs(p.x() - self._rb_press.x()) + abs(p.y() - self._rb_press.y())
            if moved < 4 and self._drag_item_key is None:
                self._rb_blank = False
                self.clear_selection()
                event.accept()
                return
            self._rb_blank = False
        if self._panning:
            self._panning = False
            self._pan_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if self._drag_item_key is not None:
            for k in (self._drag_item_keys or []):
                it = self._rois.get(k)
                if it is not None:
                    self.on_roi_geometry(k, it.rect(), final=True)
                    continue
                li = self._lines.get(k)
                if li is not None:
                    ln = li.line()
                    self.roiEdited.emit(k, Rect.from_line(ln.x1(), ln.y1(), ln.x2(), ln.y2()))
            self._drag_item_key = None
            self._drag_item_keys = []
            self._drag_item_last = None
            self._drag_start_rects = {}
            self._drag_start_lines = {}
            self._drag_origin = None
            event.accept()
            return
        if self._sel_rubber is not None:
            rect = self._sel_rubber.rect().normalized()
            box = rect
            self._scene.removeItem(self._sel_rubber)
            self._sel_rubber = None
            start = self._sel_start
            self._sel_start = None
            picked: list[str] = []
            for store in (self._rois, self._lines):
                for key, item in store.items():
                    if box.intersects(item.sceneBoundingRect()) and key not in picked:
                        picked.append(key)
            # 现场约定：左键从左往右划 = 选中；从右往左划 = 取消选中
            left_to_right = True
            if start is not None:
                left_to_right = self.mapToScene(event.pos()).x() >= start.x()
            if left_to_right:
                self.set_selection(picked)
            else:
                remain = [k for k in self._selected if k not in picked]
                self.set_selection(remain)
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
            if r.width() <= 4 or r.height() <= 4:
                # 空白处只是单击（没拖动）→ 取消所有选中
                if self._select_mode:
                    self.clear_selection()
                event.accept()
                return
            if r.width() > 4 and r.height() > 4:
                if self._area_mode:
                    self._area_mode = False
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    self.areaCreated.emit(Rect(int(r.x()), int(r.y()), int(r.width()), int(r.height())))
                    event.accept()
                    return
                if self._locator_mode:
                    self._locator_mode = False
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    self.locatorCreated.emit(Rect(int(r.x()), int(r.y()), int(r.width()), int(r.height())))
                    event.accept()
                    return
                if self._track_mode:
                    self.set_track_mode(False)
                    self.trackCreated.emit(Rect(int(r.x()), int(r.y()), int(r.width()), int(r.height())))
                    event.accept()
                    return
                r = self.clamp_rect(r)
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

    def keyPressEvent(self, event) -> None:        # noqa: N802
        _step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        _dx = _dy = 0
        if event.key() == Qt.Key.Key_Left:
            _dx = -_step
        elif event.key() == Qt.Key.Key_Right:
            _dx = _step
        elif event.key() == Qt.Key.Key_Up:
            _dy = -_step
        elif event.key() == Qt.Key.Key_Down:
            _dy = _step
        if _dx or _dy:
            for key in (self._selected or ([self._active] if self._active else [])):
                item = self._rois.get(key)
                if item is not None:
                    r = item.rect().translated(_dx, _dy)
                    item.setRect(r)
                    self.on_roi_geometry(key, r, final=True)
                line = self._lines.get(key)
                if line is not None:
                    ln = line.line().translated(_dx, _dy)
                    line.setLine(ln)
                    self.roiEdited.emit(key, Rect.from_line(ln.x1(), ln.y1(), ln.x2(), ln.y2()))
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self._space_down = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:      # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._space_down = False
            if not self._panning:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def wheelEvent(self, event) -> None:           # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        cur = self.transform().m11()
        new = cur * factor
        if new < 0.05 or new > 20:                 # 限制 5% ~ 2000%，避免缩没了/撑爆
            event.accept()
            return
        self._auto_fit = False                     # 用户手动缩放过，别再自动铺满
        self.scale(factor, factor)
        event.accept()
