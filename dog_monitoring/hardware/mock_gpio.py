"""Mock GPIO backend -- in-memory simulation.

This is the default backend for development and CI. It implements the
``GpioBackend`` interface without touching any hardware, allowing the full
application to run on any computer (including a macOS dev machine).
"""
from __future__ import annotations

from typing import Callable, List, Optional

from .gpio_backend import GpioBackend, LedPin, ButtonPin


class MockGpioBackend(GpioBackend):
    """An in-memory GPIO backend that records LED and button state.

    LED state is tracked in a dict keyed by name. Button callbacks are stored
    and can be triggered via :meth:`press_button` (simulating a physical press).
    """

    def __init__(self) -> None:
        self._leds: dict[str, bool] = {}
        self._callbacks: List[Callable[[], None]] = []

    def led_on(self, name: str) -> None:
        self._leds[name] = True

    def led_off(self, name: str) -> None:
        self._leds[name] = False

    def led_state(self, name: str) -> bool:
        return self._leds.get(name, False)

    def register_button_callback(self, callback: Callable[[], None]) -> None:
        self._callbacks.append(callback)

    def press_button(self) -> int:
        """Simulate a physical button press by invoking all registered callbacks.

        Returns:
            The number of callbacks that were invoked.
        """
        n = 0
        for cb in list(self._callbacks):
            cb()
            n += 1
        return n

    def close(self) -> None:
        self._leds.clear()
        self._callbacks.clear()

    def __repr__(self) -> str:
        return f"<MockGpioBackend leds={self._leds} callbacks={len(self._callbacks)}>"
