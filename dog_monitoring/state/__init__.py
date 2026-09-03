"""State machine package: ``MonitoringState`` enum and ``MonitorStateMachine``.

These are the core abstractions that every other component (hardware, camera,
detection, routes) depends on to make state changes.  They must be importable
without pulling in hardware drivers, so this package is intentionally kept
dependency-free.
"""
from __future__ import annotations

from .monitoring_state import (
    LED_FOR_STATE as LED_FOR_STATE,
    STATE_DESCRIPTIONS as STATE_DESCRIPTIONS,
    STATE_LABELS as STATE_LABELS,
    VALID_TRANSITIONS as VALID_TRANSITIONS,
    MonitorStateMachine as MonitorStateMachine,
    MonitoringState as MonitoringState,
    StateChange as StateChange,
)

__all__ = [
    "LED_FOR_STATE",
    "STATE_DESCRIPTIONS",
    "STATE_LABELS",
    "VALID_TRANSITIONS",
    "MonitorStateMachine",
    "MonitoringState",
    "StateChange",
]
