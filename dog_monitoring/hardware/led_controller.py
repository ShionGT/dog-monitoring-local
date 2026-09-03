"""LED controller -- turns the three physical LEDs on/off based on state.

This is the *only* place that knows which physical LED corresponds to which
monitoring state. It never touches the state machine directly; it is driven by
the state machine via a listener (see ``app.py`` / ``main.py``).
"""
from __future__ import annotations

from ..logging import getLogger
from ..state.monitoring_state import LED_FOR_STATE, MonitoringState


class LedController:
    """Drive the three LEDs in response to monitoring-state changes.

    Args:
        backend: a :class:`GpioBackend` (mock or hardware).
        names: the three LED names to control (default ``RED``, ``YELLOW``,
        ``GREEN``).
    """

    def __init__(
        self,
        backend,
        names=("RED", "YELLOW", "GREEN"),
    ) -> None:
        self._backend = backend
        self._names = list(names)
        self._logger = getLogger("dog_monitoring.led")

    def set_state(self, state: "MonitoringState | str") -> dict:
        """Turn on the LED matching ``state`` and turn the others off.

        Returns:
            A dict mapping each LED name to ``True``/``False`` (on/off),
            useful for tests and status display.
        """
        state = MonitoringState.from_name(state)
        active = LED_FOR_STATE.get(state.name, "RED")
        result = {}
        for name in self._names:
            on = (name == active)
            try:
                if on:
                    self._backend.led_on(name)
                else:
                    self._backend.led_off(name)
            except Exception:  # noqa: BLE001
                # A single LED failure must not break the others.
                self._logger.exception("Failed to set LED %s", name)
                on = False
            result[name] = on
        self._logger.info(
            "LEDs updated for state %s: %s",
            state.name, {k: ("ON" if v else "off") for k, v in result.items()},
        )
        return result

    def status(self) -> dict:
        """Return the current on/off state of every LED (read from hardware)."""
        return {name: bool(self._backend.led_state(name)) for name in self._names}

    def __repr__(self) -> str:
        return f"<LedController {self.status()}>"
