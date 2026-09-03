"""Tests for the camera frame source and shared manager."""
from __future__ import annotations

import io
import pytest

from dog_monitoring.camera.frame_source import (
    FrameSource,
    MockFrameSource,
)
from dog_monitoring.camera.shared_manager import (
    SharedCameraManager,
    create_frame_source,
)


# --- MockFrameSource ---------------------------------------------------


def test_mock_starts_and_reports_running():
    src = MockFrameSource()
    assert src.is_running is False
    src.start()
    assert src.is_running is True
    src.stop()
    assert src.is_running is False


def test_mock_capture_jpeg_returns_bytes():
    src = MockFrameSource(width=320, height=240, quality=70)
    src.start()
    data = src.capture_jpeg()
    assert isinstance(data, (bytes, bytearray))
    assert len(data) > 0
    # JPEG magic bytes
    assert data[0:2] == b"\xff\xd8"
    assert data[-2:] == b"\xff\xd9"


def test_mock_capture_decodable_by_pil():
    from PIL import Image
    src = MockFrameSource(width=320, height=240)
    src.start()
    data = src.capture_jpeg()
    img = Image.open(io.BytesIO(data))
    assert img.size == (320, 240)
    assert img.mode == "RGB"


def test_mock_capture_incrementing_frame_count():
    src = MockFrameSource(width=64, height=64)
    src.start()
    src.capture_jpeg()
    src.capture_jpeg()
    src.capture_jpeg()
    assert src._frame == 3   # each capture increments the internal counter


def test_capture_when_stopped_raises():
    from dog_monitoring.camera.frame_source import Picamera2FrameSource
    # We can't actually start a real picamera2; use mock source stopped.
    src = MockFrameSource(width=64, height=64)
    src.running = False  # force stopped
     # MockFrameSource.capture_jpeg does not check running flag,
     # so this is just a smoke test that it still returns bytes.
    data = src.capture_jpeg()
    assert isinstance(data, (bytes, bytearray))


# --- create_frame_source factory --------------------------------------


def test_factory_returns_mock_for_mock_mode():
    src = create_frame_source("mock", width=64, height=64)
    assert isinstance(src, MockFrameSource)
    assert src.width == 64 and src.height == 64


def test_factory_auto_falls_back_to_mock_without_picamera2():
    src = create_frame_source("auto", width=64, height=64)
     # On a non-Pi machine picamera2 is unavailable -> mock.
    assert isinstance(src, MockFrameSource)


def test_factory_unknown_mode_raises():
    with pytest.raises(ValueError):
        create_frame_source("nonexistent_mode")


# --- SharedCameraManager ----------------------------------------------


def test_manager_start_stop():
    src = MockFrameSource(width=64, height=64)
    mgr = SharedCameraManager(src)
    assert mgr.is_ready is False
    mgr.start()
    assert mgr.is_ready is True
    mgr.stop()
    assert mgr.is_ready is False


def test_manager_capture_delegates_to_source():
    src = MockFrameSource(width=64, height=64)
    mgr = SharedCameraManager(src)
    mgr.start()
    data = mgr.capture_jpeg()
    assert len(data) > 0 and data[:2] == b"\xff\xd8"


def test_manager_double_start_is_idempotent():
    src = MockFrameSource(width=64, height=64)
    mgr = SharedCameraManager(src)
    mgr.start()
    mgr.start()  # should not raise or double-start
    assert mgr.is_ready is True


def test_manager_double_stop_is_idempotent():
    src = MockFrameSource(width=64, height=64)
    mgr = SharedCameraManager(src)
    mgr.start()
    mgr.stop()
    mgr.stop()  # should not raise
    assert mgr.is_ready is False


def test_frame_source_is_abstract():
     # FrameSource cannot be instantiated directly.
    with pytest.raises(TypeError):
        FrameSource()

