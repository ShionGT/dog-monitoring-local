"""HTML page routes: dashboard, login, logout.

These serve the human-facing views.  All pages render the current monitoring
state and the CSRF token (injected by the app's context processor).  The
dashboard is the landing page at ``/``.
"""
from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, request, url_for

from ..logging import getLogger

log = getLogger("dog_monitoring.routes.pages")

bp = Blueprint("pages", __name__)


def _is_authenticated() -> bool:
    security = g.security
    return bool(security.auth.is_authenticated())


@bp.get("/")
@bp.get("/dashboard")
def dashboard():
    """Render the main monitoring dashboard."""
    return render_template("index.html")


@bp.get("/login")
def login():
    """Render the login page.  If already authenticated, bounce to the
    dashboard.
    """
    if _is_authenticated():
        return redirect(url_for("pages.dashboard"))
    return render_template("login.html", error=None)


@bp.post("/login")
def login_submit():
    """Validate submitted credentials; on success set the session and
    redirect to the dashboard; on failure re-render the login page with an
    error.
    """
    security = g.security
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if security.auth.authenticate(username, password):
        log.info("Web login succeeded for %r", security.auth.username)
        return redirect(url_for("pages.dashboard"))

    log.warning("Web login failed for username %r", username)
    return render_template("login.html", error="Invalid username or password"), 200


@bp.get("/logout")
@bp.post("/logout")
def logout():
    """Clear the session and return to the login page."""
    security = g.security
    security.auth.logout()
    log.info("User logged out")
    return redirect(url_for("pages.login"))
