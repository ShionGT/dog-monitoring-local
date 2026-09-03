"""Lightweight frame-differencing motion detection.

The detector works on *downscaled grayscale* frames to keep CPU and
memory low (Project spec, section 16).  It is intentionally simple:

1. Decode the incoming JPEG to a small grayscale image.
2. Compare it to the previous frame (absolute per-pixel difference).
3. Count "changed" pixels (diff >= ``threshold``).
4. If the changed-pixel count >= ``min_changed`` and the changed
    region spans at least ``min_area`` of the frame, motion is reported.

It never blocks; it is meant to run in a background worker.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from logging import getLogger

logger = getLogger("dog_monitoring.motion")


@dataclass
class MotionResult:
    """Outcome of a single frame's motion analysis."""

    has_motion: bool
    changed_pixels: int
    area_fraction: float
    frame_index: int
    timestamp: float = field(default_factory=time.time)

    @property
    def summary(self) -> dict:
        return {
            "has_motion": self.has_motion,
            "changed_pixels": self.changed_pixels,
            "area_fraction": round(self.area_fraction, 4),
            "frame": self.frame_index,
        }


class MotionDetector:
    """Frame-differencing motion detector.

    Args:
        width/height: analysis resolution (frames are downsized to this).
        threshold: per-pixel gray-level difference that counts as "changed".
        min_changed: minimum number of changed pixels to register motion.
        min_area_fraction: minimum fraction (0..1) of the frame that the
            changed pixels must cover to count as significant motion
            (filters out small flicker / JPEG noise).
    """

    def __init__(
        self,
        width: int = 160,
        height: int = 120,
        threshold: int = 18,
        min_changed: int = 25,
        min_area_fraction: float = 0.01,
    ) -> None:
        self.width = max(1, width)
        self.height = max(1, height)
        self.threshold = max(0, threshold)
        self.min_changed = max(0, min_changed)
        self.min_area_fraction = min(1.0, max(0.0, min_area_fraction))
        self._prev_gray = None
        self._frame_index = 0
        self._total_frames = 0
        self._motion_frames = 0

    def reset(self) -> None:
        """Discard the previous frame (e.g. after a long gap)."""
        self._prev_gray = None

    def _to_gray(self, data: bytes):
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if img.mode != "L":
            img = img.convert("L")
        if (img.width, img.height) != (self.width, self.height):
            img = img.resize((self.width, self.height))
        return list(img.getdata())

    def analyze(self, jpeg_bytes: bytes) -> MotionResult:
        """Analyse one frame and return a :class:`MotionResult`.

        The first frame (no previous reference) is treated as "no
        motion" and seeds the baseline.
        """
        self._total_frames += 1
        self._frame_index = self._total_frames
        cur = self._to_gray(jpeg_bytes)
        changed = 0
        total = len(cur)
        if self._prev_gray is None:
            self._prev_gray = cur
            result = MotionResult(
                has_motion=False,
                changed_pixels=0,
                area_fraction=0.0,
                frame_index=self._frame_index,
            )
            return result
        for i, px in enumerate(cur):
            if abs(px - self._prev_gray[i]) >= self.threshold:
                changed += 1
        self._prev_gray = cur
        area = changed / max(1, total)
        has_motion = (
            changed >= self.min_changed and area >= self.min_area_fraction
        )
        if has_motion:
            self._motion_frames += 1
            logger.info(
                "motion: frame=%d changed=%d area=%.3f",
                self._frame_index, changed, area,
            )
        return MotionResult(
            has_motion=has_motion,
            changed_pixels=changed,
            area_fraction=area,
            frame_index=self._frame_index,
        )

    @property
    def stats(self) -> dict:
        rate = self._motion_frames / max(1, self._total_frames)
        return {
            "total_frames": self._total_frames,
            "motion_frames": self._motion_frames,
            "motion_rate": round(rate, 4),
        }
