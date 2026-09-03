"""HTTP route blueprints for the Dog Monitoring web app.

Three blueprints, each a single concern:

* :mod:`pages`    -- HTML pages (dashboard, login, logout).
* :mod:`api`      -- JSON API (state get/set, auth login/logout, health).
* :mod:`stream`   -- the live MJPEG video feed and the SSE status stream.

All three are registered by :func:`dog_monitoring.app.create_app`.  State-
changing endpoints go through the centralized :class:`MonitorStateMachine`
(via ``g.state_machine``); nothing in a route writes to GPIO or the camera
directly (spec sections 2, 5, 9, 13).
"""
from __future__ import annotations

from .api import bp as api_bp
from .pages import bp as pages_bp
from .stream import bp as stream_bp

__all__ = ["api_bp", "pages_bp", "stream_bp"]
