"""Tests for the detection engine: motion, pet detection, notification cooldown.

Run: pytest tests/test_detection.py -v
"""
from __future__ import annotations
import io
import time
from PIL import Image
import pytest
from dog_monitoring.camera.frame_source import MockFrameSource
from dog_monitoring.detection.motion_detector import MotionDetector
from dog_monitoring.detection.pet_detector import Detection, MockPetDetector, PetDetector
from dog_monitoring.detection.notification import DiscordNotifier, NullNotifier
from dog_monitoring.detection.detection_engine import DetectionConfig, DetectionEngine


def _solid_jpeg(value: int, w: int = 160, h: int = 120) -> bytes:
    img = Image.new("RGB", (w, h), (value, value, value))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_motion_detector_no_motion_on_identical_frames():
    src = MockFrameSource(width=160, height=120)
    src.start()
    det = MotionDetector(min_changed=10, min_area_fraction=0.0)
    frame = src.capture_jpeg()    # one fixed frame
    # seed the baseline with the frame, then analyse the *same* bytes again
    det.analyze(frame)
    r = det.analyze(frame)
    assert r.has_motion is False
    assert r.changed_pixels == 0


def test_motion_detector_detects_motion():
    det = MotionDetector(min_changed=10, min_area_fraction=0.0)
    det.analyze(_solid_jpeg(20))
    r = det.analyze(_solid_jpeg(240))
    assert r.has_motion is True
    assert r.changed_pixels > 1000


def test_motion_reset_discards_baseline():
    det = MotionDetector(min_changed=10, min_area_fraction=0.0)
    det.analyze(_solid_jpeg(20))
    det.reset()
    r = det.analyze(_solid_jpeg(240))
    assert r.has_motion is False


def test_pet_detector_is_abstract():
    with pytest.raises(TypeError):
        PetDetector()


def test_mock_pet_disabled_reports_none():
    p = MockPetDetector(enabled=False)
    assert p.detect(b"") is None


def test_mock_pet_enabled_reports_dog():
    p = MockPetDetector(enabled=True)
    d = p.detect(b"")
    assert d is not None
    assert d.is_dog is True
    assert d.confidence > 0.5


def test_detection_is_pet_and_is_dog():
    dog = Detection(label="dog", confidence=0.9)
    assert dog.is_dog is True
    assert dog.is_pet is True
    cat = Detection(label="cat", confidence=0.9)
    assert cat.is_pet is True
    assert cat.is_dog is False
    person = Detection(label="person", confidence=0.9)
    assert person.is_pet is False


def test_discord_can_send_before_first_send():
    n = DiscordNotifier(webhook_url="https://example.invalid", cooldown_seconds=1200)
    assert n.can_send() is True


def test_discord_cannot_send_within_cooldown():
    now = 1000000.0
    n = DiscordNotifier(webhook_url="https://example.invalid", cooldown_seconds=1200)
    n.mark_sent(now=now)
    assert n.can_send(now=now + 1) is False
    assert n.can_send(now=now + 1199) is False
    assert n.can_send(now=now + 1200) is True


def test_cooldown_not_reset_by_suppressed_sends():
    now = 0.0
    n = DiscordNotifier(webhook_url="https://example.invalid", cooldown_seconds=1200)
    n.mark_sent(now=now)
    for t in (1, 5, 15, 19):
        assert n.can_send(now=now + t) is False
    assert n.can_send(now=now + 1200) is True
    assert n.seconds_since_last >= 0


def test_null_notifier_is_not_enabled():
    n = NullNotifier()
    assert n.is_enabled() is False
    assert n.send("hi") is True


def test_discord_is_enabled_with_url():
    n = DiscordNotifier(webhook_url="https://hooks.example/webhook", cooldown_seconds=60)
    assert n.is_enabled() is True
    n2 = DiscordNotifier(webhook_url="", cooldown_seconds=60)
    assert n2.is_enabled() is False


def _make_engine(mock_pet_enabled, cfg):
    src = MockFrameSource(width=160, height=120)
    src.start()
    motion = MotionDetector(min_changed=10, min_area_fraction=0.0)
    pet = MockPetDetector(enabled=mock_pet_enabled)
    notifier = NullNotifier()
    return DetectionEngine(src, notifier, pet, motion=motion, config=cfg)


def test_engine_disabled_does_not_run():
    cfg = DetectionConfig(enabled=False, poll_interval_s=0.05)
    eng = _make_engine(mock_pet_enabled=True, cfg=cfg)
    eng.start()
    time.sleep(0.1)
    assert eng.is_running is False
    eng.stop()


def test_engine_starts_and_stops_cleanly():
    cfg = DetectionConfig(enabled=True, poll_interval_s=0.05, require_pet_detection=False)
    eng = _make_engine(mock_pet_enabled=False, cfg=cfg)
    eng.start()
    assert eng.is_running is True
    time.sleep(0.3)
    st = eng.status()
    assert st["running"] is True
    eng.stop()
    assert eng.is_running is False
    eng.stop()


def test_engine_reports_status():
    cfg = DetectionConfig(enabled=True, poll_interval_s=0.05)
    eng = _make_engine(mock_pet_enabled=True, cfg=cfg)
    eng.start()
    st = eng.status()
    assert st["enabled"] is True
    assert st["cooldown_seconds"] == 1200
    eng.stop()


def test_engine_stats_available():
    cfg = DetectionConfig(enabled=True, poll_interval_s=0.05)
    eng = _make_engine(mock_pet_enabled=True, cfg=cfg)
    eng.start()
    time.sleep(0.1)
    assert "total_frames" in eng.stats()
    eng.stop()
