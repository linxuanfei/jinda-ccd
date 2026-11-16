"""相机路数与触发方式。

默认硬件触发。改了曝光、增益或帧率就改记为连续模式。
若调用方传入已打开的迈德威视相机，这些值会下发到 SDK。
"""

from __future__ import annotations

from typing import Any

MAX_CAMERAS = 16
BRAND_UNCONNECTED = "未接入"
BRAND_MINDVISION = "迈德威视"
MODEL_MV_GE500M_TPO = "MV-GE500M-TPO"


def trigger_of(source: dict[str, Any] | None) -> str:
    source = source or {}
    return str(source.get("trigger") or "hardware")


def update_camera_numbers(
    source: dict[str, Any],
    *,
    exposure: float | None = None,
    gain: float | None = None,
    fps: float | None = None,
    brand: str | None = None,
    model: str | None = None,
    camera: Any = None,
) -> str:
    """保存曝光/增益/帧率/品牌。曝光、增益、帧率有变化时切到连续模式。

    camera 是已打开的迈德威视源时，把当前值下发到 SDK。
    """
    source.setdefault("trigger", "hardware")
    before = (source.get("exposure"), source.get("gain"), source.get("fps"))
    if exposure is not None:
        source["exposure"] = float(exposure)
    if gain is not None:
        source["gain"] = float(gain)
    if fps is not None:
        source["fps"] = float(fps)
    if brand is not None:
        source["brand"] = brand or BRAND_UNCONNECTED
    if model is not None:
        source["model"] = model
    after = (source.get("exposure"), source.get("gain"), source.get("fps"))
    if after != before:
        source["trigger"] = "continuous"
    if camera is not None and hasattr(camera, "apply_tune"):
        camera.apply_tune(
            exposure_ms=source.get("exposure") if exposure is not None else None,
            gain=source.get("gain") if gain is not None else None,
            fps=source.get("fps") if fps is not None else None,
            trigger=str(source.get("trigger") or "hardware"),
        )
    return str(source["trigger"])
