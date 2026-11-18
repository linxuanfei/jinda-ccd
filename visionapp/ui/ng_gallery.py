"""NG 图库：按相机号浏览历史 NG 图，双击/按钮把某张图载入当前机位重新调试。

设计要点（现场诉求）：
- NG 图按 **相机号** 分文件夹：`results/<配方>/相机1/NG/<日期>/*.png`
- 停机也能打开：这个对话框只读磁盘，不依赖相机/运行
- 可重新调试：把该图所在目录切成本机位的图片源，配方保持不动，改参数后点【单帧】就能复现
"""

from __future__ import annotations

import os
import shutil

from ..qtcompat import Qt
from ..qtcompat import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPixmap, QPushButton, QVBoxLayout,
)
from ..imgio import imread
from . import theme


class NgGalleryDialog(QDialog):
    """历史 NG 图浏览 / 载入调试。"""

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NG 图库（按相机分文件夹）")
        self.resize(1080, 720)
        self.root = root
        self._files: list[str] = []
        self.selected: str = ""          # 选中要载入的图片
        self.selected_dir: str = ""      # 它所在目录（做图片源用）

        v = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("相机目录"))
        self.cb_cam = QComboBox()
        self.cb_cam.currentIndexChanged.connect(self._reload_files)
        top.addWidget(self.cb_cam, 1)
        btn_choose = QPushButton("选择文件夹…")
        btn_choose.setToolTip("任意目录都行（不限于按相机分的那种）")
        btn_choose.clicked.connect(self._choose_dir)
        top.addWidget(btn_choose)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._scan)
        top.addWidget(btn_refresh)
        v.addLayout(top)

        body = QHBoxLayout()
        self.list_files = QListWidget()
        self.list_files.currentRowChanged.connect(self._on_pick)
        self.list_files.itemDoubleClicked.connect(lambda _i: self._accept_pick())
        body.addWidget(self.list_files, 1)

        right = QVBoxLayout()
        self.lbl_preview = QLabel("（选一张图看预览）")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(520, 380)
        theme.set_class(self.lbl_preview, "hint")
        right.addWidget(self.lbl_preview, 1)
        self.lbl_info = QLabel("")
        theme.set_class(self.lbl_info, "hint")
        right.addWidget(self.lbl_info)
        body.addLayout(right, 1)
        v.addLayout(body, 1)

        bb = QDialogButtonBox()
        self.btn_load = bb.addButton("载入调试", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_open = bb.addButton("打开文件夹", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_del = bb.addButton("删除这张", QDialogButtonBox.ButtonRole.DestructiveRole)
        bb.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_load.clicked.connect(self._accept_pick)
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_del.clicked.connect(self._delete_pick)
        self.btn_prune = bb.addButton("清理旧图…", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_prune.setToolTip("按天数删除过期的 NG/OK 日期目录")
        self.btn_prune.clicked.connect(self._prune)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._scan()

    # ------------------------------------------------------------------ 扫描
    def _choose_dir(self) -> None:
        """选任意文件夹看图（本地保存的图随便哪个相机/哪台机器都能打开）。"""
        from ..qtcompat import QFileDialog

        d = QFileDialog.getExistingDirectory(self, "选择图片文件夹", self.root)
        if not d:
            return
        self.root = d
        self.cb_cam.clear()
        self.cb_cam.addItem("（本文件夹）")
        self.cb_cam.blockSignals(True)
        self.cb_cam.setCurrentIndex(0)
        self.cb_cam.blockSignals(False)
        self._reload_files()

    def _scan(self) -> None:
        self.cb_cam.blockSignals(True)
        self.cb_cam.clear()
        cams: list[str] = []
        if os.path.isdir(self.root):
            for name in sorted(os.listdir(self.root)):
                p = os.path.join(self.root, name)
                if os.path.isdir(p):
                    cams.append(name)
        for c in cams:
            self.cb_cam.addItem(c)
        if not cams:
            cams = ["（本文件夹）"]          # 没子目录就直接看这个文件夹的图
            self.cb_cam.addItem("（还没有 NG 图）")
        self.cb_cam.blockSignals(False)
        self._reload_files()

    def _reload_files(self) -> None:
        self.list_files.clear()
        self._files = []
        cam = self.cb_cam.currentText()
        cam_dir = self.root if cam.startswith("（") else os.path.join(self.root, cam)
        if not os.path.isdir(cam_dir):
            return
        # 有 NG/OK 就只列它们；没有（自己选的任意目录）就全列
        has_kind = any(d.upper() in ("NG", "OK") for d, _x, _y in [])
        for dirpath, dirs, files in os.walk(cam_dir):
            if any(d.upper() in ("NG", "OK") for d in dirs) or os.path.basename(dirpath).upper() in ("NG", "OK"):
                has_kind = True
        for dirpath, _dirs, files in os.walk(cam_dir):
            parts = [p.upper() for p in os.path.relpath(dirpath, cam_dir).split(os.sep)]
            if has_kind and not ({"NG", "OK"} & set(parts)):
                continue
            for f in sorted(files, reverse=True):
                if not f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, cam_dir)
                self._files.append(full)
                item = QListWidgetItem(rel)
                item.setData(Qt.ItemDataRole.UserRole, full)
                self.list_files.addItem(item)
        self.lbl_info.setText(f"共 {len(self._files)} 张（{cam}）")

    # ------------------------------------------------------------------ 预览
    def _current_path(self) -> str:
        it = self.list_files.currentItem()
        if it is None:
            return ""
        return str(it.data(Qt.ItemDataRole.UserRole) or "")

    def _on_pick(self, _row: int) -> None:
        path = self._current_path()
        if not path or not os.path.isfile(path):
            return
        img = imread(path)
        if img is None:
            self.lbl_preview.setText("读不出这张图")
            return
        h, w = img.shape[:2]
        rgb = img[:, :, ::-1].copy() if img.ndim == 3 else img
        from ..qtcompat import QImage

        qimg = QImage(rgb.data, w, h, 3 * w if rgb.ndim == 3 else w,
                      QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg).scaled(
            self.lbl_preview.width(), self.lbl_preview.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.lbl_preview.setPixmap(pix)
        side = os.path.splitext(path)[0] + ".json"
        info = f"{os.path.basename(path)}   {w}x{h}"
        if os.path.isfile(side):
            info += f"   同目录有结果快照 {os.path.basename(side)}"
        self.lbl_info.setText(info)

    # ------------------------------------------------------------------ 动作
    def _accept_pick(self) -> None:
        path = self._current_path()
        if not path:
            return
        self.selected = path
        self.selected_dir = os.path.dirname(path)
        self.accept()

    def _open_folder(self) -> None:
        path = self._current_path() or self.root
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        if not os.path.isdir(folder):
            self.lbl_info.setText(f"目录不存在：{folder}")
            return
        try:
            os.startfile(folder)                      # noqa: S606
        except Exception:                             # noqa: BLE001
            import subprocess

            subprocess.Popen(["explorer", os.path.normpath(folder)])

    def _prune(self) -> None:
        """按天数清理旧 NG/OK 目录（手动触发）。"""
        from ..qtcompat import QInputDialog

        days, ok = QInputDialog.getInt(self, "清理旧图", "保留最近多少天？（0 = 不清理）", 30, 0, 3650)
        if not ok or days <= 0:
            return
        import datetime as _dt
        import shutil

        cutoff = _dt.date.today() - _dt.timedelta(days=int(days))
        removed = 0
        for cam in (self.cb_cam.itemText(i) for i in range(self.cb_cam.count())):
            cam_dir = os.path.join(self.root, cam)
            if not os.path.isdir(cam_dir):
                continue
            for dirpath, dirs, _files in os.walk(cam_dir):
                for d in list(dirs):
                    if d.upper() not in ("NG", "OK"):
                        continue
                    kind_dir = os.path.join(dirpath, d)
                    for name in os.listdir(kind_dir):
                        p = os.path.join(kind_dir, name)
                        if not os.path.isdir(p):
                            continue
                        try:
                            day = _dt.datetime.strptime(name, "%Y-%m-%d").date()
                        except ValueError:
                            continue
                        if day < cutoff:
                            shutil.rmtree(p, ignore_errors=True)
                            removed += 1
        self._scan()
        self.lbl_info.setText(f"已清理 {removed} 个过期目录（保留 {days} 天）")

    def _delete_pick(self) -> None:
        path = self._current_path()
        if not path:
            return
        try:
            os.remove(path)
            side = os.path.splitext(path)[0] + ".json"
            if os.path.isfile(side):
                os.remove(side)
        except OSError as exc:
            self.lbl_info.setText(f"删除失败：{exc}")
            return
        self._reload_files()
