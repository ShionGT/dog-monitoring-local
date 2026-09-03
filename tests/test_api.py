"""Tests for the HTTP API and the authentication / CSRF / rate-limit layer.

These exercise the Flask app end-to-end in *mock* mode (no hardware).
"""
from __future__ import annotations

import pytest

from dog_monitoring.app import create_app
from dog_monitoring.config import Config


def _make_app(**overrides):
    """Build an app with mock mode forced and the given config overrides."""
    cfg = Config()
    cfg.platform_mode = "mock"
    cfg.mock_cameras = True
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return create_app(cfg)


class TestBasicAPISurface:
    def test_dashboard_renders(self):
        app = _make_app()
        c = app.test_client()
        r = c.get("/")
        assert r.status_code == 200
        assert b"DOG MONITOR" in r.data

    def test_get_state_returns_current(self):
        app = _make_app(startup_state="GREEN")
        c = app.test_client()
        r = c.get("/api/state")
        assert r.status_code == 200
        assert r.get_json()["state"]["state"] == "GREEN"

    def test_health_ok_in_mock(self):
        app = _make_app(startup_state="GREEN")
        c = app.test_client()
        r = c.get("/api/health")
        assert r.status_code == 200
        j = r.get_json()
        assert j["ok"] is True
        assert j["is_mock"] is True
        assert j["viewers"] == 0

    def test_state_change_without_csrf_rejected(self):
        # Auth disabled, but CSRF is still enforced on POST.
        app = _make_app(startup_state="GREEN", auth_enabled=False)
        c = app.test_client()
        r = c.post("/api/state", data={"state": "RED"})
        assert r.status_code == 403
        assert r.get_json()["error"] == "csrf invalid"

    def test_state_change_with_csrf_succeeds(self):
        app = _make_app(startup_state="GREEN", auth_enabled=False)
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf_token"] = "tok"
        r = c.post("/api/state", data={"state": "RED", "csrf_token": "tok"})
        assert r.status_code == 200
        assert r.get_json()["success"] is True
        assert r.get_json()["state"]["state"] == "RED"

    def test_invalid_state_name_rejected(self):
        app = _make_app(startup_state="GREEN", auth_enabled=False)
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf_token"] = "tok"
        r = c.post("/api/state", data={"state": "BLUE", "csrf_token": "tok"})
        assert r.status_code == 400

    def test_cycle_transitions_forward(self):
        app = _make_app(startup_state="GREEN", auth_enabled=False)
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf_token"] = "tok"
        r = c.post("/api/cycle", data={"csrf_token": "tok"})
        assert r.status_code == 200
        assert r.get_json()["state"]["state"] == "YELLOW"


class TestLiveStreamGating:
    def test_live_forbidden_in_green(self):
        app = _make_app(startup_state="GREEN", auth_enabled=False)
        c = app.test_client()
        r = c.get("/live.mjpeg")
        assert r.status_code == 403

    def test_live_allowed_in_red(self):
        app = _make_app(startup_state="RED", auth_enabled=False)
        c = app.test_client()
        with c.get("/live.mjpeg") as resp:
            assert resp.status_code == 200
            # Read one frame worth of data.
            first = next(resp.response, b"")
        assert len(first) > 0
        # It is an MJPEG frame (starts with the multipart boundary).
        assert b"image/jpeg" in first or b"dogmon-frame" in first

    def test_sse_endpoint_streams(self):
        app = _make_app(startup_state="GREEN", auth_enabled=False)
        c = app.test_client()
        r = c.get("/events")
        assert r.status_code == 200
        assert r.content_type.startswith("text/event-stream")


class TestAuth:
    """Authentication is enabled and enforces protection on POST."""

    def test_api_state_requires_auth_when_enabled(self):
        # With auth enabled but no login, POST /api/state -> 401.
        app = _make_app(
            startup_state="GREEN",
            auth_enabled=True,
            admin_username="admin",
            admin_password="secret",
        )
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf_token"] = "tok"
        r = c.post("/api/state", data={"state": "RED", "csrf_token": "tok"})
        assert r.status_code == 401

    def test_login_success_sets_session(self):
        app = _make_app(
            startup_state="GREEN",
            auth_enabled=True,
            admin_username="admin",
            admin_password="secret",
        )
        c = app.test_client()
        r = c.post("/login", data={"username": "admin", "password": "secret"})
        assert r.status_code == 302   # redirect to dashboard
        # After login the session should be authed.
        with c.session_transaction() as s:
            assert s.get("authed") is True

    def test_login_wrong_password_rejected(self):
        app = _make_app(
            startup_state="GREEN",
            auth_enabled=True,
            admin_username="admin",
            admin_password="secret",
        )
        c = app.test_client()
        r = c.post("/login", data={"username": "admin", "password": "wrong"})
        assert r.status_code == 200  # re-renders login, no redirect
        with c.session_transaction() as s:
            assert not s.get("authed")

    def test_logout_clears_session(self):
        app = _make_app(
            startup_state="GREEN",
            auth_enabled=True,
            admin_username="admin",
            admin_password="secret",
        )
        c = app.test_client()
        c.post("/login", data={"username": "admin", "password": "secret"})
        c.get("/logout")
        with c.session_transaction() as s:
            assert not s.get("authed")
