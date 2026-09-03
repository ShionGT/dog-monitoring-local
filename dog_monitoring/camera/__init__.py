"""Camera frame source abstraction and implementations."""

from .frame_source import FrameSource, MockFrameSource
from .shared_manager import SharedCameraManager

__all__ = ["FrameSource", "MockFrameSource", "SharedCameraManager"]
