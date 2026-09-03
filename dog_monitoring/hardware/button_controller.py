"""Button controller -- turns a physical push button press into a state change.

The button is wired to the GPIO backend. On each press it calls the
``on_press`` callback supplied by the application (typically a closure that
tells the :class:`MonitorStateMachine` to cycle to the next state).
"""
from __future__ import annotations

from typing import Callable

from ..logging import getLogger


class ButtonController:
    """Connect a GPIO button to a high-level press callback.

    Args:
        backend: the :class:`GpioBackend` driving the physical button.
        on_press: callable invoked on each button press.
    """

    def __init__(self, backend, on_press: Callable[[], None]) -> None:
        self._backend = backend
        self._on_press = on_press
        self._presses = 0
        self._logger = getLogger("dog_monitoring.button")

    def start(self) -> None:
        """Register the press callback with the backend (idempotent-safe)."""
        self._backend.register_button_callback(self._dispatch)
        self._logger.info("Button controller started (pin registered via backend)")

    def _dispatch(self) -> None:
        """Internal dispatcher guarded so a callback error cannot kill the button."""
        self._presses += 1
        try:
            self._on_press()
        except Exception:   # noqa: BLE001
            self._logger.exception("on_press callback failed")

    @property
    def press_count(self) -> int:
        "Number of times the button has been pressed (for tests/monitoring)."
        return self._presses

    def stop(self) -> None:
        self._logger.info("Button controller stopped")
        # The backend owns the callback list; on close it is cleared.

    def __repr__(self) -> str:
        return f"<ButtonController presses={self._presses}>"
