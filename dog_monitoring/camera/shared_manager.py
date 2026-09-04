"""Shared camera manager -- owns one FrameSource, exposes it safely."""
from __future__ import annotations

import os
import threading
from logging import getLogger

from .frame_source import FrameSource, MockFrameSource

logger = getLogger("dog_monitoring.camera.manager")


class SharedCameraManager:
    """Thread-safe owner of a single active :class:`FrameSource`.

    The application starts exactly one camera source (mock or real)
    and shares it across the HTTP app, the MJPEG stream, and the
    detection engine.  The manager provides:

    - :meth:`start` / :meth:`stop` to control the source lifecycle.
    - :meth:`capture_jpeg` to fetch a fresh JPEG frame.
    - :meth:`is_ready` to check the running state.
        A lock (RLock) serialises concurrent access so that only one
    thread captures at a time (important for libcamera, which is
    not re-entrant).
    """

    def __init__(self, frame_source: FrameSource) -> None:
        self._source = frame_source
        self._lock = threading.RLock()

    @property
    def source(self) -> FrameSource:
        return self._source

    def start(self) -> None:
        with self._lock:
            if self._source.is_running:
                return
            try:
                self._source.start()
            except Exception as exc:  # noqa: BLE001 — source records last_error itself
                logger.error("Camera start failed (%s): %s", self.source_name, exc)
            if not self._source.is_running:
                logger.warning(
                    "Camera source %s is NOT running after start (%s)",
                    self.source_name, self.last_error or "unknown error",
                )
            else:
                logger.info("SharedCameraManager started (source=%s)", self.source_name)

    def stop(self) -> None:
        with self._lock:
            if not self._source.is_running:
                return
            self._source.stop()
        logger.info("SharedCameraManager stopped")

    def capture_jpeg(self) -> bytes:
        with self._lock:
            try:
                frame = self._source.capture_jpeg()
            except Exception as exc:  # noqa: BLE001 — record for /api/health
                if hasattr(self._source, "last_error"):
                    self._source.last_error = str(exc)
                raise
            return frame

    @property
    def is_ready(self) -> bool:
        return self._source.is_running

    @property
    def last_error(self) -> str:
        """Last start/capture error from the underlying source ("" if healthy)."""
        return getattr(self._source, "last_error", "")

    @property
    def source_name(self) -> str:
        return type(self._source).__name__

    def close(self) -> None:
        self.stop()

    def __repr__(self) -> str:
        return (f"<SharedCameraManager source={type(self._source).__name__}"
                f" running={self._source.is_running}>")


def create_frame_source(mode: str, **kwargs) -> FrameSource:
    """Factory: build the frame source appropriate for *mode*.

    Args:
        mode: "mock" or "picamera2" (or "auto" to detect).
        **kwargs: width, height, quality, fps, camera_number.

    Returns:
        A ready-to-use :class:`FrameSource` instance.
    """
    width = kwargs.get("width", 1280)
    height = kwargs.get("height", 720)
    quality = kwargs.get("quality", 80)
    fps = kwargs.get("fps", 5)
    cam = kwargs.get("camera_number", 0)

    if mode in ("mock", "dev", "test"):
        return MockFrameSource(width=width, height=height,
                                quality=quality, fps=fps)
    if mode in ("webcam", "usb"):
        # Lazy import so machines without OpenCV never need it.
        from .frame_source import WebcamFrameSource
        return WebcamFrameSource(
            width=width, height=height,
            quality=quality, fps=fps,
            device_index=cam,
        )
    if mode in ("picamera2", "real", "hardware", "production"):
        # Lazy import so non-Pi machines never need picamera2 installed.
        from .frame_source import Picamera2FrameSource
        return Picamera2FrameSource(
            width=width, height=height,
            quality=quality, fps=fps,
            camera_number=cam,
        )
    if mode == "auto" or mode is None:
        # Auto-detect: prefer the native Pi camera, then a webcam (OpenCV),
        # falling back to MockFrameSource so the app always runs.
        try:
            from .frame_source import Picamera2FrameSource

            src = Picamera2FrameSource(
                width=width, height=height,
                quality=quality, fps=fps,
                camera_number=cam,
            )
            src.start()
            return src
        except Exception:
            logger.info("picamera2 unavailable; trying webcam")
        try:
            from .frame_source import WebcamFrameSource

            src = WebcamFrameSource(
                width=width, height=height,
                quality=quality, fps=fps,
                device_index=cam,
            )
            src.start()
            if src.is_running:
                return src
        except Exception as exc:  # noqa: BLE001 (e.g. cv2 not installed)
            logger.info("webcam unavailable (%s); falling back to MockFrameSource", exc)
        return MockFrameSource(width=width, height=height,
                                quality=quality, fps=fps)
    raise ValueError(f"Unknown camera mode: {mode!r}")
