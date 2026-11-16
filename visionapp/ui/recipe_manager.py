"""配方管理对话框：列配方、切换、新建/复制/重命名/删除。"""

from __future__ import annotations

import os
import shutil

from ..qtcompat import Qt
from ..qtcompat import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from ..recipe import Recipe
from . import theme


class RecipeManagerDialog(QDialog):
    """管理 recipes 目录下的配方文件。"""

    def __init__(self, recipes_dir: str, current: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("配方管理")
        self.resize(560, 460)
        self.recipes_dir = recipes_dir
        self.current = current
        self.selected: str = ""            # 用户选择要加载的配方路径

        v = theme.tune_layout(QVBoxLayout(self))
        lbl_dir = QLabel(f"配方目录：{recipes_dir}")
        theme.set_class(lbl_dir, "hint")
        v.addWidget(lbl_dir)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._load_selected())
        v.addWidget(self.list, 1)

        row = QHBoxLayout()
        for text, slot in (
            ("加载", self._load_selected),
            ("新建", self._new_recipe),
            ("复制", self._duplicate),
            ("重命名", self._rename),
            ("删除", self._delete),
            ("刷新", self.refresh),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.refresh()

    # ------------------------------------------------------------------ 列表
    def refresh(self) -> None:
        self.list.clear()
        if not os.path.isdir(self.recipes_dir):
            return
        for name in sorted(os.listdir(self.recipes_dir)):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(self.recipes_dir, name)
            try:
                recipe = Recipe.load(path)
                sub = f"{len(recipe.items)} 项"
            except Exception:                          # noqa: BLE001
                sub = "读取失败"
            item = QListWidgetItem(f"{name}    （{sub}）")
            item.setData(Qt.ItemDataRole.UserRole, path)
            if path == self.current:
                item.setBackground(Qt.GlobalColor.lightGray)
            self.list.addItem(item)

    def _selected_path(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _load_selected(self) -> None:
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "提示", "请先选择一个配方")
            return
        self.selected = path
        self.accept()

    # ------------------------------------------------------------------ 操作
    def _new_recipe(self) -> None:
        name, ok = QInputDialog.getText(self, "新建配方", "配方名（不含 .json）：")
        if not ok or not name.strip():
            return
        path = os.path.join(self.recipes_dir, f"{name.strip()}.json")
        if os.path.exists(path):
            QMessageBox.warning(self, "已存在", f"{path} 已存在")
            return
        Recipe(name=name.strip()).save(path)
        self.refresh()

    def _duplicate(self) -> None:
        path = self._selected_path()
        if not path:
            return
        base = os.path.splitext(os.path.basename(path))[0]
        name, ok = QInputDialog.getText(self, "复制配方", "新配方名：", text=f"{base}_copy")
        if not ok or not name.strip():
            return
        dst = os.path.join(self.recipes_dir, f"{name.strip()}.json")
        shutil.copyfile(path, dst)
        self.refresh()

    def _rename(self) -> None:
        path = self._selected_path()
        if not path:
            return
        base = os.path.splitext(os.path.basename(path))[0]
        name, ok = QInputDialog.getText(self, "重命名", "新名字：", text=base)
        if not ok or not name.strip() or name.strip() == base:
            return
        dst = os.path.join(self.recipes_dir, f"{name.strip()}.json")
        os.replace(path, dst)
        self.refresh()

    def _delete(self) -> None:
        path = self._selected_path()
        if not path:
            return
        if QMessageBox.question(self, "删除", f"确定删除 {os.path.basename(path)} ?") != QMessageBox.StandardButton.Yes:
            return
        os.remove(path)
        self.refresh()
