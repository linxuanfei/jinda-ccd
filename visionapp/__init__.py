"""VisionApp：开源视觉检测系统（一期）。"""

from .core import Frame, Point, Rect, SourceInfo
from .ops import Blob, Item, ItemResult, REGISTRY
from .recipe import Pipeline, PipelineResult, Recipe
from .sources import (
    FolderSource,
    FrameSource,
    RtspSource,
    SyntheticSource,
    UvcSource,
    open_source,
)

__all__ = [
    "Frame", "Point", "Rect", "SourceInfo",
    "Blob", "Item", "ItemResult", "REGISTRY",
    "Pipeline", "PipelineResult", "Recipe",
    "FolderSource", "FrameSource", "RtspSource", "SyntheticSource", "UvcSource", "open_source",
]

__version__ = "0.1.0"
