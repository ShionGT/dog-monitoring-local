"""Camera frame source abstraction and implementations."""

from .frame_source import FrameSource, MockFrameSource, WebcamFrameSource
from .shared_manager import SharedCameraManager

__all__ = [
    "FrameSource",
    "MockFrameSource",
    "WebcamFrameSource",
    "SharedCameraManager",
]

