"""权限：操作员 / 管理员（工程师）/ 超级管理员。

- 操作员：可以开始、停止、看结果，不必登录；
- 管理员：可以改检测框和规则，必须走登录对话；
- 超级管理员：可以改相机路数、品牌和 X/Y 比例。另有一份摘要，
  为空时登录失败，不能靠菜单把操作员抬上去。

密码只存 SHA-256 摘要，不存明文。本模块不打印摘要。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

ROLE_OPERATOR = "operator"
ROLE_ENGINEER = "engineer"
ROLE_SUPER = "super"
ROLE_LABELS = {
    ROLE_OPERATOR: "操作员",
    ROLE_ENGINEER: "管理员",
    ROLE_SUPER: "超级管理员",
}


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


@dataclass
class SecurityConfig:
    require_login: bool = False        # 关闭时默认就是工程师模式
    engineer_password_hash: str = ""   # 空 = 还没设置密码
    super_password_hash: str = ""      # 空 = 不能进入超级管理员

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SecurityConfig":
        data = data or {}
        return cls(
            require_login=bool(data.get("require_login", False)),
            engineer_password_hash=data.get("engineer_password_hash", "") or "",
            super_password_hash=data.get("super_password_hash", "") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_login": self.require_login,
            "engineer_password_hash": self.engineer_password_hash,
            "super_password_hash": self.super_password_hash,
        }

    # ------------------------------------------------------------------ 判定
    @property
    def has_password(self) -> bool:
        return bool(self.engineer_password_hash)

    def check(self, pw: str) -> bool:
        return self.has_password and hash_password(pw) == self.engineer_password_hash

    def set_password(self, pw: str) -> None:
        self.engineer_password_hash = hash_password(pw)
        self.require_login = True

    def clear_password(self) -> None:
        self.engineer_password_hash = ""
        self.require_login = False

    def check_super(self, pw: str) -> bool:
        """超级管理员校验。摘要为空时一律失败，不接受工程师口令顶替。"""
        if not self.super_password_hash or not pw:
            return False
        return hash_password(pw) == self.super_password_hash

    def set_super_password(self, pw: str) -> None:
        """只更新超级管理员摘要，不改变当前角色。"""
        self.super_password_hash = hash_password(pw)
