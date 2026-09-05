"""Hardware abstraction: GPIO backends, LED and button controllers.

The application never touches a GPIO pin directly. All physical I/O goes
through :class:`GpioBackend`, which has two implementations:

* :class:`~dog_monitoring.hardware.mock_gpio.MockGpioBackend` -- a pure
in-memory simulation for macOS/CI (no hardware required).
* :class:`~dog_monitoring.hardware.hw_gpio.HardwareGpioBackend` -- the real
Raspberry Pi 5 implementation built on *RPi.GPIO* (via **rpi-lgpio**, the
gpiod-backed drop-in replacement that works on the Pi 5). See
:mod:`dog_monitoring.hardware.hw_gpio` for why plain RPi.GPIO does not work on
the Pi 5.

:class:`LedController` and :class:`ButtonController` sit on top of a backend
and expose high-level operations (``set_state`` / button press handling) that
the :class:`~dog_monitoring.state.monitoring_state.MonitorStateMachine` consumes.
"""
from __future__ import annotations

from .gpio_backend import GpioBackend, LedPin, ButtonPin
from .led_controller import LedController
from .button_controller import ButtonController
from .mock_gpio import MockGpioBackend

__all__ = [
    "GpioBackend",
    "LedPin",
    "ButtonPin",
    "LedController",
    "ButtonController",
    "MockGpioBackend",
]
