"""Pet/dog identification abstraction.

The spec (section 17) calls for a pluggable ``PetDetector`` so a
real model (e.g. an Ultralytics YOLO-nano exported to NCNN for the
Pi 5) can be dropped in later without touching the rest of the system.

For the MVP we ship a :class:`MockPetDetector` that reports a fixed
result.  It makes the pipeline testable end-to-end without shipping a
heavy model, and its interface matches what a real detector would
expose: ``detect(frame_jpeg) -> Detection``.
"""
from __future__ import annotations
import abc
from dataclasses import dataclass
from logging import getLogger

logger = getLogger("dog_monitoring.pet_detector")


@dataclass
class Detection:
    """A single detection result."""
    label: str              # e.g. "dog", "cat", "person"
    confidence: float       # 0.0 .. 1.0
    box: tuple | None = None   # (x1, y1, x2, y2) in source pixels

    @property
    def is_pet(self) -> bool:
        return self.label.lower() in ("dog", "cat", "pet")

    @property
    def is_dog(self) -> bool:
        return self.label.lower() == "dog"


class PetDetector(abc.ABC):
    """Abstract pet/dog detector.

    Implementations must be safe to call from a single background
    worker thread and must never block the Flask request path.
    """
    @abc.abstractmethod
    def detect(self, jpeg_bytes: bytes) -> "Detection | None":
        """Return the best detection for one frame, or ``None``
        if nothing relevant was found.
        """
        ...

    @abc.abstractmethod
    def warmup(self) -> None:
        """Load/initialise the model (no-op for the mock)."""
        ...


class MockPetDetector(PetDetector):
    """Stub detector for development and tests.

    When ``enabled`` is True it reports a high-confidence "dog"
    detection on every frame, which lets the notification pipeline
    be exercised end-to-end.  When False it reports nothing, which
    makes the engine idle (the default for a fresh install so the
    system is quiet until the user opts in).
    """
    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._calls = 0

    def warmup(self) -> None:
        pass

    def detect(self, jpeg_bytes: bytes) -> "Detection | None":
        self._calls += 1
        if not self._enabled:
            return None
        return Detection(label="dog", confidence=0.92)

    @property
    def calls(self) -> int:
        return self._calls
