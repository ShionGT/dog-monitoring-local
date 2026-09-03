"""Detection engine and notification package.

Provides:

- :class:`MotionDetector` - lightweight frame-differencing motion
detection on downscaled grayscale frames (low CPU / RAM).
- :class:`PetDetector` - abstract pet/dog detection interface with a
mock stub (a real NCNN/YOLO model is a future extension).
- :class:`NotificationService` - abstract notification interface.
- :class:`DiscordNotifier` - a concrete Discord webhook notifier that
enforces a thread-safe cooldown so continuous motion does not spam.
- :class:`DetectionEngine` - orchestrates the pipeline and runs a
background worker that never blocks Flask or streaming.
"""

from .motion_detector import MotionDetector
from .pet_detector import PetDetector, MockPetDetector
from .notification import NotificationService, DiscordNotifier, NullNotifier
from .detection_engine import DetectionEngine, DetectionConfig

__all__ = [
    "MotionDetector",
    "PetDetector",
    "MockPetDetector",
    "NotificationService",
    "DiscordNotifier",
    "DetectionEngine",
]
