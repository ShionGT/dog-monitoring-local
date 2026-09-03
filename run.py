#!/usr/bin/env python3
"""Command-line entry point for the Dog Monitoring System.

This deliberately thin wrapper is the only file you normally run. It:

1. Loads :class:`~dog_monitoring.config.Config` from the environment
   (including an optional ``.env`` file -- see :func:`load_config`).
2. Validates production-safety invariants (a real secret, HTTPS where
   required) *before* binding any socket, so a misconfiguration fails fast
   and loudly rather than serving unauthenticated traffic.
3. Builds the application via :func:`dog_monitoring.app.create_app` and
   hands control to a WSGI server.

Usage
-----
    python run.py                       # run the development server
    PORT=8080 python run.py           # bind to a different port
    APP_ENV=production SECRET_KEY=... python run.py

By default, on a non-Pi machine (macOS/CI), the app auto-selects MOCK
hardware + camera so the whole stack runs with zero physical devices.
"""
from __future__ import annotations

import os
import sys

from dog_monitoring.config import load_config
from dog_monitoring.logging import configure_logging, getLogger


def _resolve_host_port() -> tuple[str, int]:
    """Bind address/port from the environment with safe development defaults."""
    host = os.environ.get("HOST", os.environ.get("BIND_HOST", "127.0.0.1"))
    try:
        port = int(os.environ.get("PORT", os.environ.get("BIND_PORT", "8080")))
    except ValueError:
        port = 8080
    return host, port


def _validate_production(config) -> None:
    """Fail fast, before binding, if a production deployment is misconfigured.

    Enforces the two invariants the spec (section 26 "Security") requires:
    a non-empty ``SECRET_KEY`` and, when ``REQUIRE_HTTPS_IN_PROD`` is set,
    a way to know TLS is in front of the app.
    """
    errors: list[str] = []
    if config.is_production():
        if not config.secret_key:
            errors.append(
                "APP_ENV=production requires a non-empty SECRET_KEY "
                "(set SECRET_KEY=<long-random-string>)"
            )
        if config.require_https_in_prod and not os.environ.get(
            "HTTPS_PROXY_IN_FRONT", os.environ.get("BACKEND_HTTPS", "")
        ):
            errors.append(
                "APP_ENV=production with REQUIRE_HTTPS_IN_PROD=true expects a "
                "reverse proxy in front (set BACKEND_HTTPS=1 or run behind an HTTPS "
                "reverse proxy). Secure cookies + HSTS are enabled only then."
            )
    if errors:
        for e in errors:
            print(f"[run.py][FATAL] {e}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    """Load config, validate, build the app, and serve it.

    Returns a process exit code (0 = clean shutdown, 1 = startup failure).
    """
    configure_logging(level="INFO" if not os.environ.get("DEBUG") else "DEBUG")
    log = getLogger("dog_monitoring.run")

    config = load_config()
    log.info(
        "Starting Dog Monitoring System v%s",
        __import__("dog_monitoring").__version__,
    )
    log.info(
        "  platform=%s  mock=%s  startup_state=%s",
        config.platform_mode,
        config.is_mock,
        config.startup_state,
    )

    _validate_production(config)

    # Import lazily so a missing optional dependency (picamera2/gpiozero in
    # hardware mode) surfaces as a clear, actionable error instead of an
    # import-time crash before we've printed our banner.
    try:
        from dog_monitoring.app import create_app
    except ImportError as exc:
        log.exception("Failed to import the web application: %s", exc)
        return 1

    host, port = _resolve_host_port()

    app = create_app(config)

    # Expose the built app so `flask -a` and programmatic callers can reuse it.
    app.config["DOGMON_CONFIG"] = config  # type: ignore[attr-defined]

    log.info("Serving on http://%s:%d  (Ctrl+C to stop)", host, port)
    try:
        app.run(host=host, port=port, debug=config.debug, threaded=True)
    except KeyboardInterrupt:
        log.info("Shutdown requested by user (Ctrl+C).")
    except Exception as exc:  # noqa: BLE001 - surface any startup failure cleanly
        log.exception("Fatal error during shutdown: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
