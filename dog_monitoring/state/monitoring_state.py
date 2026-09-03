"""The monitoring state machine.

Three primary states (Project spec, section 2):

* MonitoringState.RED      -> LIVE STREAM: camera active and at least one
authorised remote browser is viewing the live feed.
* MonitoringState.YELLOW   -> PRIVATE MONITORING: camera active, local
processing permitted, but the live feed is not exposed to remote viewers.
* MonitoringState.GREEN    -> CAMERA OFF: camera stopped, nothing running.

The single source of truth for the whole application is the current
:class:`MonitoringState`; neither GPIO, camera, nor Flask routes make decisions
about state. They all go through the :class:`MonitorStateMachine`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ..logging import getLogger


class MonitoringState(str, Enum):
    """The three monitoring states.

    The name of the state (RED / YELLOW / GREEN) is also the canonical string
    used in API responses and the UI, so ``state.name`` is the public
    identifier.  It is a ``str`` enum so it serialises cleanly as its name.
    """

    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"

    @classmethod
    def from_name(cls, name: "str | MonitoringState") -> "MonitoringState":
        """Parse a state from its case-insensitive public name.

        Raises:
            ValueError: if the name is not one of the three allowed states.
        """
        if isinstance(name, cls):
            return name
        key = str(name).strip().upper()
        for member in cls:
            if member.name == key:
                return member
        raise ValueError(f"Unknown monitoring state: {name!r}")

    def to_name(self) -> str:
        return self.name

    def to_json(self) -> dict:
        """A small serialisable description used in API/SSE payloads."""
        return {"state": self.name, "label": STATE_LABELS[self.name]}


# Human readable labels (never rely on colour alone; see spec section 29).
STATE_LABELS: dict[str, str] = {
    "RED": "Live Monitoring",
    "YELLOW": "Private Monitoring",
    "GREEN": "Camera Off",
}

# Short descriptions for the UI.
STATE_DESCRIPTIONS: dict[str, str] = {
    "RED": "The camera is active and the live feed is currently being viewed.",
    "YELLOW": "The camera is active for local monitoring, but the live feed "
            "is not streamed to remote viewers.",
    "GREEN": "The camera is off. No camera processing is occurring.",
}

# The LED (colour) that should be lit for each state; the rest must be OFF.
LED_FOR_STATE: dict[str, str] = {
    "RED": "RED",
    "YELLOW": "YELLOW",
    "GREEN": "GREEN",
}

# Valid transitions between states. Any pair not listed is rejected.
VALID_TRANSITIONS: dict[MonitoringState, frozenset] = {
    MonitoringState.RED: frozenset({MonitoringState.YELLOW, MonitoringState.GREEN}),
    MonitoringState.YELLOW: frozenset({MonitoringState.RED, MonitoringState.GREEN}),
    MonitoringState.GREEN: frozenset({MonitoringState.RED, MonitoringState.YELLOW}),
}


@dataclass
class StateChange:
    """A single state transition event, broadcast to listeners."""

    old: MonitoringState
    new: MonitoringState
    timestamp: float = field(default_factory=time.time)
    source: str = "unknown"  # 'button', 'web', 'system', 'test', ...

    def to_dict(self) -> dict:
        return {
            "old": self.old.name,
            "new": self.new.name,
            "timestamp": self.timestamp,
            "source": self.source,
            "label": STATE_LABELS[self.new.name],
            "description": STATE_DESCRIPTIONS[self.new.name],
        }


class MonitorStateMachine:
    """Thread-safe central state manager.

    Both the physical button handler and the Flask API call :meth:`set_state`
    (or the convenience :meth:`cycle`) to change state. No other component
    directly writes to the state.

    Listeners
        Register callback functions with :meth:`add_listener`. Each listener is
        invoked (from the writer's thread, *outside* the state lock) with a
        :class:`StateChange` object whenever the state changes.
    """

    def __init__(self, initial: MonitoringState = MonitoringState.GREEN) -> None:
        self._state = initial
        self._lock = threading.RLock()
        self._listeners: list = []
        self._last_change: StateChange | None = None
        self._logger = getLogger("dog_monitoring.state")
        self._logger.info("State machine initialised in %s", initial.name)

    # --- public read API ---

    @property
    def state(self) -> MonitoringState:
        """The current monitoring state (always a MonitoringState)."""
        with self._lock:
            return self._state

    @property
    def lock(self):
        """Expose the internal RLock for external readers that need to
    read state under the same lock used by the state machine (e.g.
    the viewer-registry callbacks that need a consistent read-then-
    transition)."""
        return self._lock

    @property
    def last_change(self) -> StateChange | None:
        with self._lock:
            return self._last_change

    # --- public write API ---

    def set_state(
        self,
        target: "MonitoringState | str",
        *,
        source: str = "system",
    ) -> StateChange:
        """Transition to ``target`` and broadcast to listeners.

        Raises:
            ValueError: if ``target`` is not a valid MonitoringState name.
            RuntimeError: if the transition is not allowed by the machine.

        Returns:
            StateChange describing this transition. When the state does not
            actually change, an idempotent change with ``old == new`` is
            returned so callers always get a usable object.
        """
        try:
            target_state = MonitoringState.from_name(target)
        except ValueError as exc:
            self._logger.error("Rejecting invalid state name: %s", target)
            raise ValueError(str(exc)) from exc

        with self._lock:
            current = self._state
            listeners = list(self._listeners)
            if target_state == current:
                change = StateChange(current, current, time.time(), source)
                self._last_change = change
                self._logger.info("State already %s (no transition)", current.name)
            elif target_state not in VALID_TRANSITIONS[current]:
                allowed = sorted(s.name for s in VALID_TRANSITIONS[current])
                msg = (
                    f"Illegal transition {current.name} -> {target_state.name}; "
                    f"allowed: {allowed}"
                )
                self._logger.warning(msg)
                raise RuntimeError(msg)
            else:
                self._state = target_state
                change = StateChange(current, target_state, time.time(), source)
                self._last_change = change

        # Notify outside the lock so a slow listener cannot deadlock transitions.
        self._logger.info(
            "State changed: %s -> %s (source=%s)",
            current.name, change.new.name, source,
        )
        for fn in listeners:
            try:
                fn(change)
            except Exception:  # noqa: BLE001
                self._logger.exception(
                    "Listener %r raised; continuing to notify others",
                    getattr(fn, "__name__", "anon"),
                )
        return change

    def cycle(self, *, source: str = "button") -> StateChange:
        """Cycle the state forward: GREEN -> YELLOW -> RED -> GREEN.

        This is the default behaviour of the physical push button (short press).
        Explicit state selection from the web is preferred for the UI
        (see routes/api.py), but the button uses this cycle for physical
        convenience.
        """
        order = {
            MonitoringState.GREEN: MonitoringState.YELLOW,
            MonitoringState.YELLOW: MonitoringState.RED,
            MonitoringState.RED: MonitoringState.GREEN,
        }
        with self._lock:
            next_state = order[self._state]
        return self.set_state(next_state, source=source)

    # --- listener API ---

    def add_listener(self, fn: Callable) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    # --- snapshot / introspection helpers ---

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot of the current state."""
        with self._lock:
            state = self._state
            last = self._last_change
        payload: dict = {
            "state": state.name,
            "label": STATE_LABELS[state.name],
            "description": STATE_DESCRIPTIONS[state.name],
            "led": LED_FOR_STATE[state.name],
        }
        if last is not None:
            payload["last_change"] = last.to_dict()
        return payload

    def __repr__(self) -> str:
        return f"<MonitorStateMachine state={self.state.name}>"


__all__ = [
    "MonitoringState",
    "MonitorStateMachine",
    "StateChange",
    "STATE_LABELS",
    "STATE_DESCRIPTIONS",
    "LED_FOR_STATE",
    "VALID_TRANSITIONS",
]
