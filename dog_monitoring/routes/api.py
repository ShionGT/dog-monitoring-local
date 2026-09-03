"""JSON API routes.

All state-changing endpoints require a valid CSRF token on POST (via the
``protect`` decorator from :mod:`dog_monitoring.security`), are rate-limited
per client IP, and go through the centralized :class:`MonitorStateMachine`
(the single source of truth).  No route here touches GPIO or the camera
directly (spec sections 5, 9, 12, 13).

Endpoints
---------
GET  /api/state            -- current state snapshot (JSON)
POST /api/state            -- set state to a requested target (CSRF + auth)
POST /api/cycle            -- cycle the state forward (CSRF + auth)
GET  /api/health           -- liveness + basic component status
POST /api/auth/login       -- (alternative JSON login)
POST /api/auth/logout      -- clear the session
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ..logging import getLogger
from ..security import protect
from ..state.monitoring_state import MonitoringState

log = getLogger("dog_monitoring.routes.api")

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/state")
def get_state():
    """Return the current state as a JSON snapshot."""
    return jsonify({"success": True, "state": g.state_machine.snapshot()})


@bp.post("/state")
@protect
def set_state():
    """Transition to a requested target state.

    Accepts ``state`` (e.g. ``"RED"``) in JSON or form.  Any invalid or
    disallowed transition is reported with the list of allowed states.
    """
    raw = None
    if request.is_json:
        raw = (request.get_json(silent=True) or {}).get("state")
    if raw is None:
        raw = request.form.get("state")

    if not raw:
        return jsonify({"success": False, "error": "missing 'state'"}), 400

    try:
        target = MonitoringState.from_name(raw)
    except ValueError as exc:
        return (
            jsonify({"success": False, "error": f"invalid state: {exc}"}),
            400,
        )

    sm = g.state_machine
    try:
        change = sm.set_state(target, source="web_api")
    except RuntimeError as exc:
        return (
            jsonify({"success": False, "error": str(exc)}),
            409,
        )
    return jsonify(
        {
            "success": True,
            "state": sm.snapshot(),
            "source": change.source,
            "message": f"State set to {change.new.name}",
        }
    )


@bp.post("/cycle")
@protect
def cycle_state():
    """Cycle the state forward (GREEN -> YELLOW -> RED -> GREEN...)."""
    change = g.state_machine.cycle(source="web_api")
    return jsonify(
        {
            "success": True,
            "state": g.state_machine.snapshot(),
            "source": change.source,
            "message": f"Cycled to {change.new.name}",
        }
    )


@bp.get("/health")
def health():
    """Liveness + a lightweight snapshot of the core components."""
    sm = g.state_machine
    det = getattr(g, "detection", None)
    viewers = getattr(g, "viewers", None)
    return jsonify(
        {
            "ok": True,
            "state": sm.snapshot(),
            "is_mock": g.dogmon_config.is_mock,
            "viewers": viewers.count() if viewers else 0,
            "detection": det.status() if det and hasattr(det, "status") else None,
        }
    )
