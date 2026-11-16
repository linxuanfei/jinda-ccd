"""图像写盘封装：兼容中文/非 ASCII 路径。

cv2.imwrite() 在 Windows 上遇到非 ASCII 路径会静默返回 False（既不报错也不写文件），
这里统一改为 imencode + 二进制写入，保证路径含中文时也能正常保存。
"""

from __future__ import annotations

import os

import cv2


def imwrite(path: str, img) -> bool:
    """把图像写到 path，成功返回 True。路径可包含中文。"""
    ext = os.path.splitext(path)[1].lower() or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    with open(path, "wb") as f:
        f.write(buf.tobytes())
    return True


def imread(path: str, flags: int = cv2.IMREAD_COLOR):
    """读图，路径可包含中文；失败返回 None。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    import numpy as np

    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, flags)
