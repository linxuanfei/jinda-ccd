"""批量对齐：多选若干 ROI 后一次性摆齐（对齐现场 In-Vision 的编辑菜单能力）。

支持 7 个动作（与现场原话一致）：

| 动作 | 说明 |
| --- | --- |
| ``align_top`` | 上对齐（以组内最上边为准） |
| ``align_bottom`` | 下对齐（以组内最下边为准） |
| ``align_left`` | 左对齐 |
| ``align_right`` | 右对齐 |
| ``same_size`` | 等大小（以**第一个选中**的宽高为准） |
| ``space_h`` | 水平等间距（首尾不动，中间均分） |
| ``space_v`` | 垂直等间距 |

线形 ROI 也支持：整体平移（两端一起动），等大小对线无意义会被跳过。
所有函数都是纯函数（输入 dict[id, Rect] → 输出 dict[id, Rect]），方便自测。
"""

from __future__ import annotations

from typing import Iterable

from .core import Rect

ACTION_LABELS: dict[str, str] = {
    "align_top": "上对齐",
    "align_bottom": "下对齐",
    "align_left": "左对齐",
    "align_right": "右对齐",
    "same_size": "等大小",
    "space_h": "水平等间距",
    "space_v": "垂直等间距",
}

# 需要 ≥2 个对象
MIN_ITEMS = 2


def _move(rect: Rect, dx: float, dy: float) -> Rect:
    """平移一个 ROI（线形 ROI 两端一起动）。"""
    x = int(round(rect.x + dx))
    y = int(round(rect.y + dy))
    if rect.is_line() and rect.line is not None:
        lx1, ly1, lx2, ly2 = rect.line
        return Rect(x, y, rect.w, rect.h, shape=rect.shape,
                    line=(int(round(lx1 + dx)), int(round(ly1 + dy)),
                          int(round(lx2 + dx)), int(round(ly2 + dy))))
    return Rect(x, y, rect.w, rect.h)


def align_rects(rects: dict[str, Rect], action: str,
                order: Iterable[str] | None = None) -> dict[str, Rect]:
    """返回对齐后的新 ROI 字典（不改动入参）。

    order：选中顺序，``same_size`` 用它决定参照对象（取第一个）。
    """
    if action not in ACTION_LABELS:
        raise ValueError(f"未知对齐动作: {action}")
    ids = [k for k in (order or rects.keys()) if k in rects]
    if len(ids) < MIN_ITEMS:
        raise ValueError(f"至少选中 {MIN_ITEMS} 个对象才能{ACTION_LABELS[action]}")

    boxes = {k: rects[k] for k in ids}
    out: dict[str, Rect] = dict(rects)

    if action == "align_top":
        target = min(r.y for r in boxes.values())
        for k, r in boxes.items():
            out[k] = _move(r, 0, target - r.y)
    elif action == "align_bottom":
        target = max(r.y + r.h for r in boxes.values())
        for k, r in boxes.items():
            out[k] = _move(r, 0, target - (r.y + r.h))
    elif action == "align_left":
        target = min(r.x for r in boxes.values())
        for k, r in boxes.items():
            out[k] = _move(r, target - r.x, 0)
    elif action == "align_right":
        target = max(r.x + r.w for r in boxes.values())
        for k, r in boxes.items():
            out[k] = _move(r, target - (r.x + r.w), 0)
    elif action == "same_size":
        ref = boxes[ids[0]]
        for k, r in boxes.items():
            if r.is_line():
                continue                    # 线不参与等大小
            dx = (r.w - ref.w) / 2.0        # 以中心为基准缩放，看起来更自然
            dy = (r.h - ref.h) / 2.0
            out[k] = Rect(int(round(r.x + dx)), int(round(r.y + dy)), ref.w, ref.h)
    elif action == "space_h":
        ordered = sorted(boxes.items(), key=lambda kv: kv[1].x)
        if len(ordered) < 3:
            raise ValueError("水平等间距至少要 3 个对象")
        first, last = ordered[0][1], ordered[-1][1]
        # 以"相邻左边距"均分：首尾固定，中间对象按等中心距摆放
        total = (last.x + last.w / 2) - (first.x + first.w / 2)
        step = total / (len(ordered) - 1)
        for i, (k, r) in enumerate(ordered):
            if i == 0 or i == len(ordered) - 1:
                continue
            want_cx = (first.x + first.w / 2) + step * i
            out[k] = _move(r, want_cx - (r.x + r.w / 2), 0)
    elif action == "space_v":
        ordered = sorted(boxes.items(), key=lambda kv: kv[1].y)
        if len(ordered) < 3:
            raise ValueError("垂直等间距至少要 3 个对象")
        first, last = ordered[0][1], ordered[-1][1]
        total = (last.y + last.h / 2) - (first.y + first.h / 2)
        step = total / (len(ordered) - 1)
        for i, (k, r) in enumerate(ordered):
            if i == 0 or i == len(ordered) - 1:
                continue
            want_cy = (first.y + first.h / 2) + step * i
            out[k] = _move(r, 0, want_cy - (r.y + r.h / 2))
    return out


__all__ = ["ACTION_LABELS", "MIN_ITEMS", "align_rects"]
