"""Web security: session auth, CSRF protection, and request rate limiting.

These are the three classic web-attack mitigations the spec (section 12)
requires. They are intentionally dependency-light (pure Flask + the standard
library) so they can be unit-tested without a live server.

* Authentication -- a single admin user from ``ADMIN_USERNAME`` /
``ADMIN_PASSWORD``. The password is compared with a constant-time
comparison to avoid timing side-channels. In development the credentials may
be omitted, in which case auth *degenerates* to "everyone is allowed" --
a convenience that is *rejected in production* (see run.py validation).
* CSRF -- a per-session random token that must be echoed on every state-
changing POST (as ``X-CSRF-Token`` header or ``csrf_token`` form field).
* Rate limiting -- a small in-memory token-bucket per client IP for the API.
"""
from __future__ import annotations

import secrets
import time
from collections import deque
from functools import wraps

from flask import request, session, redirect, url_for, jsonify, abort

from .logging import getLogger

log = getLogger("dog_monitoring.security")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class AuthConfig:
    """Holds the (possibly absent) admin credentials loaded from Config."""

    def __init__(self, username: str, password: str, enabled: bool) -> None:
        self.username = (username or "").strip()
        self.password = password or ""
        self.enabled = bool(enabled) and bool(self.username and self.password)

    def is_authenticated(self) -> bool:
        if not self.enabled:
            # Auth is disabled: everyone is allowed (dev convenience only).
            return True
        return bool(session.get("authed"))

    def check(self, password: str) -> bool:
        """Constant-time credential check."""
        if not self.enabled:
            return False
        return secrets.compare_digest(
            str(password),
            self.password,
        )

    def authenticate(self, username: str, password: str) -> bool:
        if self.check(password) and secrets.compare_digest(
            str(username), self.username
        ):
            session["authed"] = True
            session.permanent = True
            log.info("User %r authenticated", self.username)
            return True
        log.warning("Failed login attempt for username %r", username)
        return False

    def logout(self) -> None:
        session.pop("authed", None)


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------
def _csrf_token() -> str:
    tok = session.get("csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf_token"] = tok
    return tok


def generate_csrf() -> str:
    """Return (creating if needed) the current session's CSRF token."""
    return _csrf_token()


def _validate_csrf() -> bool:
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not token:
        return False
    # Allow a small window of tokens for concurrent sessions: just compare
    # against the current session token.
    return secrets.compare_digest(str(token), _csrf_token())


# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory per-IP token bucket)
# ---------------------------------------------------------------------------
class RateLimiter:
    """A very small sliding-window rate limiter, keyed by client IP.

    This is adequate for a single-node personal device (the spec's whole
    threat model). It does not need to be distributed.
    """

    def __init__(self, max_per_minute: int = 120) -> None:
        self._max = max(1, int(max_per_minute))
        self._window_s = 60.0
        self._hits: dict[str, deque] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now or time.time()
        dq = self._hits.setdefault(key, deque())
        while dq and now - dq[0] > self._window_s:
            dq.popleft()
        if len(dq) >= self._max:
            return False
        dq.append(now)
        return True


# ---------------------------------------------------------------------------
# Decorator that composes auth + CSRF + rate-limit
# ---------------------------------------------------------------------------
class _AppContext:
    """Holds the mutable security objects created by create_app.

    Stored on the Flask app as ``app.config["SECURITY"]`` so that the
    decorators can reach them without globals.
    """

    def __init__(self) -> None:
        self.auth = AuthConfig("", "", True)
        self.rate = RateLimiter(120)
        self._rate_enabled = True

    def set_auth(self, username: str, password: str, enabled: bool) -> None:
        self.auth = AuthConfig(username, password, enabled)

    def set_rate(self, max_per_minute: int) -> None:
        self.rate = RateLimiter(max_per_minute)


def require_auth(security: "_AppContext"):
    """Decorator: require an authenticated session.

    If authentication is disabled (dev mode) it is a no-op.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not security.auth.is_authenticated():
                return redirect(url_for("pages.login"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_csrf(security: "_AppContext"):
    """Decorator: require a valid CSRF token on POST requests."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if request.method == "POST" and not _validate_csrf():
                return jsonify({"success": False, "error": "csrf invalid"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_rate(security: "_AppContext"):
    """Decorator: enforce the per-IP rate limit on API requests."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = request.remote_addr or "unknown"
            if security._rate_enabled and not security.rate.allow(key):
                return jsonify(
                    {"success": False, "error": "rate limit exceeded"}
                ), 429
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def protect(f):
    """Composable decorator: apply rate-limit + CSRF + auth in order.

    Use on state-changing POST endpoints.  Reads the ``SECURITY`` context
    from Flask ``g`` (set by an ``app.before_request`` hook in
    :mod:`dog_monitoring.app`).
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        from flask import g

        security = g.security
        # 1. rate limit
        key = request.remote_addr or "unknown"
        if security._rate_enabled and not security.rate.allow(key):
            return jsonify({"success": False, "error": "rate limit exceeded"}), 429
        # 2. CSRF (POST only)
        if request.method == "POST" and not _validate_csrf():
            return jsonify({"success": False, "error": "csrf invalid"}), 403
        # 3. auth
        if not security.auth.is_authenticated():
            return jsonify({"success": False, "error": "authentication required"}), 401
        return f(*args, **kwargs)

    return wrapper
