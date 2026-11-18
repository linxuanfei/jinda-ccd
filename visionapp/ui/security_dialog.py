"""权限对话框：设置/修改工程师密码、登录。"""

from __future__ import annotations

from ..qtcompat import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout,
)

from ..security import SecurityConfig, hash_password
from . import theme


class LoginDialog(QDialog):
    """工程师登录。"""

    def __init__(self, sec: SecurityConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("工程师登录")
        self.resize(360, 160)
        self.sec = sec
        v = theme.tune_layout(QVBoxLayout(self))
        form = QFormLayout()
        self.ed_pw = QLineEdit()
        self.ed_pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("工程师密码", self.ed_pw)
        v.addLayout(form)
        self.lbl = QLabel("")
        theme.set_class(self.lbl, "error")
        v.addWidget(self.lbl)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("登录")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _accept(self) -> None:
        if self.sec.check(self.ed_pw.text()):
            self.accept()
        else:
            self.lbl.setText("密码错误")


class PasswordDialog(QDialog):
    """设置 / 修改 / 清除工程师密码。"""

    def __init__(self, sec: SecurityConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("权限设置")
        self.resize(420, 240)
        self.sec = sec
        v = theme.tune_layout(QVBoxLayout(self))
        form = QFormLayout()

        self.ed_old = QLineEdit()
        self.ed_old.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_new = QLineEdit()
        self.ed_new.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_new2 = QLineEdit()
        self.ed_new2.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_old.setEnabled(sec.has_password)
        form.addRow("原密码", self.ed_old)
        form.addRow("新密码", self.ed_new)
        form.addRow("再输一次", self.ed_new2)
        v.addLayout(form)

        if sec.has_password:
            v.addWidget(QLabel("留空新密码并点“清除密码”可取消权限控制"))

        bb = QDialogButtonBox()
        ok = bb.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        clear = bb.addButton("清除密码", QDialogButtonBox.ButtonRole.DestructiveRole)
        cancel = bb.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        ok.clicked.connect(self._save)
        clear.clicked.connect(self._clear)
        cancel.clicked.connect(self.reject)
        v.addWidget(bb)

    def _save(self) -> None:
        if self.sec.has_password and not self.sec.check(self.ed_old.text()):
            QMessageBox.warning(self, "错误", "原密码不正确")
            return
        if not self.ed_new.text():
            QMessageBox.warning(self, "错误", "新密码不能为空")
            return
        if self.ed_new.text() != self.ed_new2.text():
            QMessageBox.warning(self, "错误", "两次输入不一致")
            return
        self.sec.set_password(self.ed_new.text())
        self.accept()

    def _clear(self) -> None:
        if self.sec.has_password and not self.sec.check(self.ed_old.text()):
            QMessageBox.warning(self, "错误", "原密码不正确")
            return
        self.sec.clear_password()
        self.accept()
