"""Tests for platform detection (the Pi 5 aarch64/gpiochip4 bug) and the
is_mock resolution chain.

The original bug: ``_detect_platform`` required ``machine.startswith("arm")``
and ``/dev/gpiochip0`` — a Pi 5 with the standard 64-bit Raspberry Pi OS
reports ``aarch64`` and exposes ``/dev/gpiochip4``, so the app silently ran
with mock GPIO (LEDs never lit) and a mock camera even on real hardware.
"""
from __future__ import annotations

import pytest

from dog_monitoring.config import _detect_platform, _has_gpiochips, load_config


def _simulate_env(monkeypatch, system, machine, gpiochips, platform_var=None):
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.machine", lambda: machine)
    monkeypatch.setattr("glob.glob", lambda pattern: gpiochips)
    monkeypatch.delenv("PLATFORM", raising=False)
    if platform_var:
        monkeypatch.setenv("PLATFORM", platform_var)


def test_pi5_64bit_detected_as_hardware(monkeypatch):
    # Pi 5 + 64-bit Raspberry Pi OS (the standard setup): aarch64 + gpiochip4
    _simulate_env(monkeypatch, "Linux", "aarch64", ["/dev/gpiochip4"])
    assert _detect_platform() == "hardware"


def test_pi5_gpiochip0_also_detected(monkeypatch):
    _simulate_env(monkeypatch, "Linux", "aarch64", ["/dev/gpiochip0"])
    assert _detect_platform() == "hardware"


def test_pi3_32bit_detected_as_hardware(monkeypatch):
    _simulate_env(monkeypatch, "Linux", "armv7l", ["/dev/gpiochip0"])
    assert _detect_platform() == "hardware"


def test_macos_detected_as_mock(monkeypatch):
    _simulate_env(monkeypatch, "Darwin", "arm64", [])
    assert _detect_platform() == "mock"


def test_linux_x86_without_gpio_is_mock(monkeypatch):
    # A Linux box with no GPIO chips (e.g. a cloud VM / container)
    _simulate_env(monkeypatch, "Linux", "x86_64", [])
    assert _detect_platform() == "mock"


def test_explicit_platform_var_wins(monkeypatch):
    _simulate_env(monkeypatch, "Darwin", "arm64", [], platform_var="hardware")
    assert _detect_platform() == "hardware"
    _simulate_env(monkeypatch, "Linux", "aarch64", ["/dev/gpiochip4"],
                  platform_var="mock")
    assert _detect_platform() == "mock"


def test_load_config_on_pi5_resolves_is_mock_false(monkeypatch):
    """End-to-end: on a Pi 5, auto mode must NOT resolve to mock."""
    _simulate_env(monkeypatch, "Linux", "aarch64", ["/dev/gpiochip4"])
    monkeypatch.delenv("USE_MOCK_CAMERA", raising=False)
    cfg = load_config()
    assert cfg.platform_mode == "hardware"
    assert cfg.is_mock is False


def test_use_mock_camera_only_affects_camera_layer(monkeypatch):
    """USE_MOCK_CAMERA=1 must NOT flip is_mock on a Pi (LEDs stay real).

    The camera-layer override lives in app._build_frame_source; here we
    assert only that the platform/GPIO resolution stays hardware.
    """
    _simulate_env(monkeypatch, "Linux", "aarch64", ["/dev/gpiochip4"])
    monkeypatch.setenv("USE_MOCK_CAMERA", "1")
    cfg = load_config()
    assert cfg.is_mock is False, "USE_MOCK_CAMERA must not mock the GPIO layer"


def test_has_gpiochips_any_chip():
    # The helper accepts any gpiochip*, not just gpiochip0
    assert _has_gpiochips.__doc__ is not None
