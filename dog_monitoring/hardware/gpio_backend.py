"""Abstract GPIO backend interface.

A ``GpioBackend`` is the lowest level of the hardware layer. It knows *only*
how to:

1. Turn individual LED pins on/off (or read their state for the mock).
2. Register a callback that fires when the push button is pressed.

The backend knows nothing about application states, LEDs-as-colours, or the
camera. That keeps it trivially testable and hardware-agnostic.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class LedPin:
    """A single LED pin configuration.

    ``name`` is the human/colour label used by the application (``RED`` /
    ``YELLOW`` / ``GREEN``). ``pin`` is the BCM GPIO pin number.
    """

    name: str
    pin: int


@dataclass(frozen=True)
class ButtonPin:
    """A single push-button pin configuration.

    ``pull_up`` mirrors GPIO Zero's ``pull_up`` parameter. ``True`` means the
    pin is pulled *high* and the button pulls it *low* when pressed (the common
    wiring). ``False`` is the inverse (pull down, button pulls high).
    """

    name: str
    pin: int
    pull_up: bool = True
    bounce: Optional[float] = 0.05   # seconds; hardware debounce window


class GpioBackend(abc.ABC):
    """Abstract GPIO backend.

    Implementations must be safe to call from multiple threads (Flask request
    threads and the button callback thread may both touch the backend).
    """

    # --- LED control ---

    @abc.abstractmethod
    def led_on(self, name: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def led_off(self, name: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def led_state(self, name: str) -> bool:
        """Return ``True`` if the LED is currently on."""
        raise NotImplementedError

    # --- button input ---

    @abc.abstractmethod
    def register_button_callback(self, callback: "Callable[[], None]") -> None:
        """Register ``callback`` to be invoked (on the button's own
        thread on hardware) whenever the physical button is pressed.
        """
        raise NotImplementedError

    # --- lifecycle ---

    @abc.abstractmethod
    def close(self) -> None:
        """Release all hardware resources (call at shutdown)."""
        raise NotImplementedError

    def __enter__(self) -> "GpioBackend":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
