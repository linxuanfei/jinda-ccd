"""权限：操作员 / 工程师。

- 操作员：只能开始/停止、切换配方、看结果；
- 工程师：可以改检测项、参数、标定、产线配置、设置。

密码只存 SHA-256 摘要，不存明文。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

ROLE_OPERATOR = "operator"
ROLE_ENGINEER = "engineer"
ROLE_LABELS = {ROLE_OPERATOR: "操作员", ROLE_ENGINEER: "工程师"}


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


@dataclass
class SecurityConfig:
    require_login: bool = False        # 关闭时默认就是工程师模式
    engineer_password_hash: str = ""   # 空 = 还没设置密码

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SecurityConfig":
        data = data or {}
        return cls(
            require_login=bool(data.get("require_login", False)),
            engineer_password_hash=data.get("engineer_password_hash", "") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {"require_login": self.require_login,
                "engineer_password_hash": self.engineer_password_hash}

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
