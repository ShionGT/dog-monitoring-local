"""Hardware GPIO backend -- GPIO Zero on a real Raspberry Pi 5.

This backend wraps GPIO Zero's ``LED`` and ``Button`` objects. It is only
importable on a machine with the ``gpiozero`` package installed (Raspberry Pi
OS). On a non-Pi machine, :func:`create_backend` will fall back to
:class:`~dog_monitoring.hardware.mock_gpio.MockGpioBackend`.
"""
from __future__ import annotations

import threading
from typing import Callable, List

from ..logging import getLogger
from .gpio_backend import GpioBackend

_log = getLogger("dog_monitoring.gpio_hw")


class HardwareGpioBackend(GpioBackend):
    """A GPIO Zero-backed implementation of :class:`GpioBackend`."""

    def __init__(
        self,
        led_pins,
        button_name: str = "TRIGGER",
        button_pin: int = 17,
        pull_up: bool = True,
        bounce: float = 0.05,
    ) -> None:
        try:
            from gpiozero import LED, Button
        except ImportError:
            raise RuntimeError(
                "gpiozero is not installed. Install it with: pip install gpiozero"
            )
        self._leds = {}
        for name, pin in led_pins.items():
            led = LED(pin)
            led.off()
            self._leds[name] = led
        self._button = Button(
            button_pin,
            pull_up=pull_up,
            bounce=bounce,
            hold_time=None,
        )
        self._callbacks: List = []
        self._lock = threading.Lock()
        self._button.when_pressed = self._on_press
        _log.info(
            "HardwareGpioBackend initialised: leds=%s button=pin %d (pull_up=%s)",
            list(led_pins.keys()),
            button_pin,
            pull_up,
        )

    def _on_press(self) -> None:
        with self._lock:
            cbs = list(self._callbacks)
        for cb in cbs:
            try:
                cb()
            except Exception:  # noqa: BLE001
                _log.exception("Button callback raised")

    def led_on(self, name: str) -> None:
        led = self._leds.get(name)
        if led is None:
            raise KeyError(f"LED {name!r} not configured")
        led.on()

    def led_off(self, name: str) -> None:
        led = self._leds.get(name)
        if led is None:
            raise KeyError(f"LED {name!r} not configured")
        led.off()

    def led_state(self, name: str) -> bool:
        led = self._leds.get(name)
        if led is None:
            raise KeyError(f"LED {name!r} not configured")
        return bool(led._on)

    def register_button_callback(self, callback: Callable) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def close(self) -> None:
        with self._lock:
            self._button.close()
            self._callbacks = []
        for led in self._leds.values():
            led.off()
            led.close()
        self._leds.clear()

    def __repr__(self) -> str:
        return f"<HardwareGpioBackend leds={list(self._leds.keys())}>"


def create_backend(led_pins, button_pin: int = 17):
    """Create the appropriate GPIO backend for the current platform.

    Returns:
        An instance satisfying the :class:`GpioBackend` interface.
    """
    try:
        return HardwareGpioBackend(led_pins, button_pin=button_pin)
    except (ImportError, RuntimeError):
        from .mock_gpio import MockGpioBackend as _Mock
        _log.info("gpiozero unavailable -- falling back to MockGpioBackend")
        return _Mock()
