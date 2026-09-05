"""Hardware GPIO backend -- RPi.GPIO on a real Raspberry Pi 5.

This backend wraps the ``RPi.GPIO`` API (``GPIO.setup`` / ``output`` /
``input`` / ``add_event_detect``). It is only importable on a machine with an
``RPi.GPIO`` implementation installed.

**Pi 5 note:** the *original* RPi.GPIO package (by Ben Croston) does **not**
work on the Raspberry Pi 5. The RP1 south-bridge moved the GPIO header to a
separate chip (``/dev/gpiochip4``) that RPi.GPIO's direct ``/dev/mem``
register access cannot reach. Instead, install **rpi-lgpio**::

    pip install rpi-lgpio        # or: sudo apt install python3-rpi-lgpio

which provides the *same* ``RPi.GPIO`` API backed by the Linux gpiod/libgpiod
(lgpio) library -- and that **does** work on the Pi 5. Do not install the
original ``RPi.GPIO`` package alongside it (both provide the same module name).

On a non-Pi machine (macOS dev / CI), :func:`create_backend` falls back to
:class:`~dog_monitoring.hardware.mock_gpio.MockGpioBackend`.

All pins use **BCM** numbering (matching the config's pin numbers).
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, List

from ..logging import getLogger
from .gpio_backend import GpioBackend

_log = getLogger("dog_monitoring.gpio_hw")


class HardwareGpioBackend(GpioBackend):
    """An RPi.GPIO-backed implementation of :class:`GpioBackend`.

    LEDs are active-high outputs (HIGH = lit). The button is an input with a
    pull-up, wired to GND (active-low): pressing drives the pin LOW, which we
    detect as a FALLING edge. ``bounce`` is given in seconds and converted to
    RPi.GPIO's millisecond ``bouncetime``.

    The button callback fires on RPi.GPIO's own event-detection thread, so
    :meth:`_on_press` is guarded by a lock (the Flask request threads and that
    thread may both touch the backend).
    """

    def __init__(
        self,
        led_pins: Dict[str, int],
        button_name: str = "TRIGGER",
        button_pin: int = 17,
        pull_up: bool = True,
        bounce: float = 0.05,   # seconds (RPi.GPIO bouncetime is ms)
    ) -> None:
        try:
            import RPi.GPIO as GPIO   # provided by rpi-lgpio on a Pi 5
        except ImportError:
            raise RuntimeError(
                "RPi.GPIO is not installed. On a Raspberry Pi 5 install the "
                "gpiod-backed drop-in replacement: pip install rpi-lgpio"
            )

        self._gpio = GPIO
        # BCM numbering matches the config's pin numbers. (Idempotent if the
        # backend is ever constructed twice in one process.)
        GPIO.setmode(GPIO.BCM)

        # --- LEDs: outputs, start off -------------------------------------
        self._led_pins = dict(led_pins)
        for name, pin in led_pins.items():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

        # --- Button: input with a pull; pressed drives the pin to GND -----
        self._button_pin = button_pin
        if pull_up:
            GPIO.setup(button_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            edge = GPIO.FALLING      # pressed -> LOW (button to GND)
        else:
            GPIO.setup(button_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            edge = GPIO.RISING       # pressed -> HIGH (button to 3V3)

        self._callbacks: List[Callable] = []
        self._lock = threading.Lock()
        GPIO.add_event_detect(
            button_pin, edge, callback=self._on_press,
            bouncetime=int(bounce * 1000),   # seconds -> milliseconds
        )

        _log.info(
            "HardwareGpioBackend initialised (RPi.GPIO): leds=%s button=pin %d",
            list(led_pins.keys()), button_pin,
        )

    # -- internal ----------------------------------------------------------

    def _on_press(self, channel=None) -> None:
        """Invoked by RPi.GPIO's event thread on each (debounced) press.

        RPi.GPIO passes the channel number to the callback; we ignore it
        (there is only one button). The ``=None`` default also lets tests and
        the mock backend call it with no argument.
        """
        with self._lock:
            cbs = list(self._callbacks)
        for cb in cbs:
            try:
                cb()
            except Exception:  # noqa: BLE001 -- one bad callback must not kill the rest
                _log.exception("Button callback raised")

    # -- GpioBackend: LED control ------------------------------------------

    def led_on(self, name: str) -> None:
        pin = self._led_pins.get(name)
        if pin is None:
            raise KeyError(f"LED {name!r} not configured")
        self._gpio.output(pin, self._gpio.HIGH)

    def led_off(self, name: str) -> None:
        pin = self._led_pins.get(name)
        if pin is None:
            raise KeyError(f"LED {name!r} not configured")
        self._gpio.output(pin, self._gpio.LOW)

    def led_state(self, name: str) -> bool:
        """Return ``True`` if the LED is currently on (read back from the pin)."""
        pin = self._led_pins.get(name)
        if pin is None:
            raise KeyError(f"LED {name!r} not configured")
        return bool(self._gpio.input(pin))

    # -- GpioBackend: button input -----------------------------------------

    def register_button_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    # -- GpioBackend: lifecycle --------------------------------------------

    def close(self) -> None:
        GPIO = self._gpio
        with self._lock:
            self._callbacks = []
        try:
            GPIO.remove_event_detect(self._button_pin)
        except Exception:  # noqa: BLE001 -- already removed / never added
            pass
        for pin in self._led_pins.values():
            try:
                GPIO.output(pin, GPIO.LOW)   # LEDs off on shutdown
            except Exception:  # noqa: BLE001
                pass
        try:
            GPIO.cleanup()   # release all pins we configured
        except Exception:  # noqa: BLE001
            pass

    def __repr__(self) -> str:
        return f"<HardwareGpioBackend leds={list(self._led_pins.keys())}>"


def create_backend(led_pins, button_pin: int = 17):
    """Create the appropriate GPIO backend for the current platform.

    Returns:
        An instance satisfying the :class:`GpioBackend` interface -- real
        RPi.GPIO on a Pi, or the mock backend elsewhere.
    """
    try:
        return HardwareGpioBackend(led_pins, button_pin=button_pin)
    except (ImportError, RuntimeError):
        from .mock_gpio import MockGpioBackend as _Mock
        _log.info("RPi.GPIO unavailable -- falling back to MockGpioBackend")
        return _Mock()
