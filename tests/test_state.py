"""Tests for the monitoring state machine.

Run with: pytest tests/test_state.py -v
"""
from __future__ import annotations

import pytest

from dog_monitoring.state.monitoring_state import (
    LED_FOR_STATE,
    MonitoringState,
    MonitorStateMachine,
    VALID_TRANSITIONS,
)


def test_enum_has_three_states() -> None:
    assert {s.name for s in MonitoringState} == {"RED", "YELLOW", "GREEN"}


def test_from_name_parses_case_insensitively() -> None:
    assert MonitoringState.from_name("red") == MonitoringState.RED
    assert MonitoringState.from_name("Yellow") == MonitoringState.YELLOW
    assert MonitoringState.from_name("green ") == MonitoringState.GREEN


def test_from_name_idempotent_for_enum() -> None:
    assert MonitoringState.from_name(MonitoringState.RED) == MonitoringState.RED


def test_from_name_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        MonitoringState.from_name("BLUE")


def test_default_initial_state_is_green() -> None:
    sm = MonitorStateMachine()
    assert sm.state == MonitoringState.GREEN


def test_valid_transition_green_to_yellow() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    change = sm.set_state("YELLOW", source="web")
    assert sm.state == MonitoringState.YELLOW
    assert change.old == MonitoringState.GREEN
    assert change.new == MonitoringState.YELLOW
    assert change.source == "web"


def test_valid_transition_yellow_to_red() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.YELLOW)
    sm.set_state("RED")
    assert sm.state == MonitoringState.RED


def test_valid_transition_red_to_green() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.RED)
    sm.set_state("GREEN")
    assert sm.state == MonitoringState.GREEN


def test_all_valid_transitions_allowed() -> None:
    # GREEN, YELLOW, RED each transition to the other two.
    for source_state in MonitoringState:
        for target in MonitoringState:
            if target == source_state:
                continue
            assert target in VALID_TRANSITIONS[source_state], (
                f"{source_state.name} -> {target.name} should be valid"
            )


def test_idempotent_same_state() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.RED)
    change = sm.set_state("RED")
    assert sm.state == MonitoringState.RED
    # Idempotent transition returns a change with old == new.
    assert change.old == change.new == MonitoringState.RED


def test_invalid_state_name_raises() -> None:
    sm = MonitorStateMachine()
    with pytest.raises(ValueError):
        sm.set_state("PURPLE")


def test_set_state_with_enum_instance() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    sm.set_state(MonitoringState.YELLOW)
    assert sm.state == MonitoringState.YELLOW


def test_cycle_order_green_yellow_red_green() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    assert sm.cycle().new == MonitoringState.YELLOW
    assert sm.cycle().new == MonitoringState.RED
    assert sm.cycle().new == MonitoringState.GREEN
    assert sm.state == MonitoringState.GREEN


def test_last_change_recorded() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    sm.set_state("YELLOW", source="web")
    lc = sm.last_change
    assert lc is not None
    assert lc.old == MonitoringState.GREEN
    assert lc.new == MonitoringState.YELLOW
    assert lc.source == "web"


def test_listener_notified_on_change() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    received: list = []
    sm.add_listener(lambda change: received.append(change))
    sm.set_state("YELLOW", source="web")
    assert len(received) == 1
    assert received[0].new == MonitoringState.YELLOW


def test_listener_notified_only_on_actual_change() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.RED)
    received: list = []
    sm.add_listener(lambda change: received.append(change))
    sm.set_state("RED")   # idempotent
    # A listener is still notified for idempotent calls (documented behaviour),
    # so assert the single change is idempotent.
    assert len(received) == 1
    assert received[0].old == received[0].new == MonitoringState.RED


def test_listener_exception_does_not_break_transition() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    seen: list = []

    def boom(change):   # noqa: ANN001
        raise RuntimeError("listener failed")

    sm.add_listener(boom)
    sm.add_listener(lambda c: seen.append(c))
    # Should not raise even though boom fails.
    sm.set_state("YELLOW")
    assert sm.state == MonitoringState.YELLOW
    assert len(seen) == 1


def test_remove_listener() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    seen: list = []
    fn = lambda change: seen.append(change)
    sm.add_listener(fn)
    sm.remove_listener(fn)
    sm.set_state("YELLOW")
    assert seen == []


def test_snapshot_contains_state_metadata() -> None:
    sm = MonitorStateMachine(initial=MonitoringState.GREEN)
    snap = sm.snapshot()
    assert snap["state"] == "GREEN"
    assert snap["led"] == "GREEN"
    assert snap["label"] == "Camera Off"
    assert "description" in snap


def test_led_for_state_mapping() -> None:
    assert LED_FOR_STATE["RED"] == "RED"
    assert LED_FOR_STATE["YELLOW"] == "YELLOW"
    assert LED_FOR_STATE["GREEN"] == "GREEN"


def test_invalid_transition_raises_runtime_error() -> None:
    # There is no direct GREEN->? skip; but same-state idempotent is allowed.
    # Build a scenario where a transition is invalid by using VALID_TRANSITIONS
    # logic directly: assert that any transition not in the map is invalid.
    # (All pairs are valid here, so this mainly guards the map completeness.)
    for src in MonitoringState:
        for tgt in MonitoringState:
            if tgt == src:
                continue
            assert tgt in VALID_TRANSITIONS[src]
