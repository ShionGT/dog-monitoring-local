"""Tests for the GPIO hardware layer (mock backend + controllers)."""
from __future__ import annotations

import pytest

from dog_monitoring.hardware.mock_gpio import MockGpioBackend
from dog_monitoring.hardware.led_controller import LedController
from dog_monitoring.hardware.button_controller import ButtonController
from dog_monitoring.state.monitoring_state import MonitoringState


# --- MockGpioBackend -------------------------------------------------------


def test_led_on_off():
    b = MockGpioBackend()
    b.led_on("RED")
    assert b.led_state("RED") is True
    b.led_off("RED")
    assert b.led_state("RED") is False


def test_led_unknown_off_by_default():
    b = MockGpioBackend()
    assert b.led_state("NONEXISTENT") is False


def test_button_callback_registered():
    b = MockGpioBackend()
    fired = []
    b.register_button_callback(lambda: fired.append(1))
    n = b.press_button()
    assert n == 1
    assert fired == [1]


def test_multiple_button_callbacks_all_fire():
    b = MockGpioBackend()
    a = []
    c = []
    b.register_button_callback(lambda: a.append(1))
    b.register_button_callback(lambda: c.append(1))
    assert b.press_button() == 2
    assert a == c == [1]


def test_close_clears_state():
    b = MockGpioBackend()
    b.led_on("GREEN")
    b.close()
    assert b.led_state("GREEN") is False


# --- LedController ---------------------------------------------------------


def test_led_set_state_red():
    b = MockGpioBackend()
    led = LedController(b)
    result = led.set_state("RED")
    assert result == {"RED": True, "YELLOW": False, "GREEN": False}
    assert b.led_state("RED") is True


def test_led_set_state_yellow():
    b = MockGpioBackend()
    led = LedController(b)
    led.set_state("YELLOW")
    assert b.led_state("YELLOW") is True
    assert b.led_state("RED") is False
    assert b.led_state("GREEN") is False


def test_led_set_state_green():
    b = MockGpioBackend()
    led = LedController(b)
    led.set_state("GREEN")
    assert b.led_state("GREEN") is True
    assert b.led_state("RED") is False


def test_led_status_reads_hardware():
    b = MockGpioBackend()
    led = LedController(b)
    led.set_state("RED")
    assert led.status()["RED"] is True


def test_led_set_state_accepts_enum():
    b = MockGpioBackend()
    led = LedController(b)
    led.set_state(MonitoringState.YELLOW)
    assert b.led_state("YELLOW") is True


# --- ButtonController ------------------------------------------------------


def test_button_press_triggers_callback():
    b = MockGpioBackend()
    pressed = []
    ctrl = ButtonController(b, on_press=lambda: pressed.append(1))
    ctrl.start()
    b.press_button()
    assert pressed == [1]
    assert ctrl.press_count == 1


def test_button_press_count_increments():
    b = MockGpioBackend()
    ctrl = ButtonController(b, on_press=lambda: None)
    ctrl.start()
    for _ in range(3):
        b.press_button()
    assert ctrl.press_count == 3


def test_button_callback_exception_does_not_stop_button():
    b = MockGpioBackend()
    ok = []

    def bad():
        raise ValueError("boom")

    # First callback raises, second should still work.
    ctrl = ButtonController(b, on_press=bad)
    ctrl.start()
    b.register_button_callback(lambda: ok.append(1))
    b.press_button()
    # The second callback (ok) should run despite the first failing.
    assert ok == [1]
