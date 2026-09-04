"""Flask application factory for the Dog Monitoring System.

The factory :func:`create_app` is the single entry point that wires together
the whole system: the monitoring state machine, the hardware (LED + push
button), the camera (shared frame source), the detection engine, the
notification service, the viewer registry, and the SSE bus.  All individual
components are plain objects that could be unit-tested on their own; the
factory only *connects* them and registers the HTTP routes.

Architecture invariants (see the project spec, sections 2, 6, 10, 13, 14,
15, 37):

* The :class:`MonitorStateMachine` is the single source of truth.  Neither
the GPIO controllers, the camera, nor the routes decide state on their own
-- they all call ``set_state`` / ``cycle``.
* The camera is only active in RED and YELLOW, and stopped in GREEN
(privacy).  The live stream is only served in RED (privacy, section 13).
* The physical push button *cycles* GREEN -> YELLOW -> RED -> GREEN
(section 4); the web UI offers explicit state selection.
* A remote viewer present on the live feed moves the system to RED; when
the last viewer leaves it demotes to YELLOW (section 14).
* The notification/detection engine runs in a background thread and only ever
*notifies* -- it never changes the monitoring state (section 15).
* State changes are pushed to the web UI over Server-Sent Events (section 10).
"""
from __future__ import annotations

import atexit
import os

from flask import Flask

from .camera import SharedCameraManager
from .config import Config, load_config
from .detection import (
    DetectionConfig,
    DetectionEngine,
    DiscordNotifier,
    MockPetDetector,
    NullNotifier,
)
from .hardware import ButtonController, LedController, MockGpioBackend
from .logging import getLogger
from .security import _AppContext
from .sse import SSEBus
from .state import MonitorStateMachine, MonitoringState

log = getLogger("dog_monitoring.app")


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------
def _build_gpio_backend(config: Config):
    """Return the appropriate GPIO backend (mock or real hardware)."""
    if not config.is_mock:
        try:
            from .hardware.hw_gpio import HardwareGpioBackend

            return HardwareGpioBackend(
                led_pins={
                    "RED": config.red_led_pin,
                    "YELLOW": config.yellow_led_pin,
                    "GREEN": config.green_led_pin,
                },
                button_pin=config.button_pin,
            )
        except (ImportError, RuntimeError) as exc:
            log.warning(
                "Hardware GPIO backend unavailable (%s); falling back to mock.",
                exc,
            )
    return MockGpioBackend()


def _build_frame_source(config: Config):
    """Return the appropriate frame source based on ``CAMERA_SOURCE``.

    Selection order:
      * explicit ``mock`` / ``webcam`` / ``picamera2`` -> that source;
      * ``auto`` (default) -> picamera2 when we're on a Pi, else webcam
        if one opens successfully (macOS camera permission permitting),
        falling back to mock so the app always runs.

    A webcam that fails to open (e.g. macOS camera permission denied)
    does not crash startup: the error is logged and surfaced via
    ``/api/health`` (``camera.last_error``) while the app keeps running.
    """
    from .camera.frame_source import MockFrameSource, Picamera2FrameSource

    fps = max(1, config.camera_fps)
    mock = MockFrameSource(config.camera_width, config.camera_height, fps=fps)

    def _webcam():
        from .camera.frame_source import WebcamFrameSource

        return WebcamFrameSource(
            config.camera_width,
            config.camera_height,
            fps=fps,
            device_index=config.webcam_device_index,
        )

    def _picamera2():
        try:
            return Picamera2FrameSource(
                config.camera_width,
                config.camera_height,
                fps=fps,
            )
        except Exception as exc:  # noqa: BLE001 (e.g. picamera2 not installed)
            log.warning("picamera2 unavailable (%s)", exc)
            return None

    source_name = config.camera_source or "auto"

    if source_name == "mock":
        return mock
    if source_name == "webcam":
        src = _webcam()
        src.start()  # primes the pipeline; permission errors land in last_error
        return src if src.is_running else mock
    if source_name == "picamera2":
        src = _picamera2()
        return src if src is not None else mock

    # auto:
    # An explicit USE_MOCK_CAMERA=1 forces the synthetic feed (dev/testing)
    # without turning the GPIO layer mock — the LEDs stay real.
    if config.mock_cameras and "USE_MOCK_CAMERA" in os.environ:
        return mock

    # Prefer the native camera for this platform, then webcam, then mock.
    if not config.is_mock:  # on a Pi (hardware mode)
        src = _picamera2()
        if src is not None:
            try:
                src.start()  # actually probe: no CSI camera -> fails here
            except Exception as exc:  # noqa: BLE001
                log.warning("Pi camera module unavailable (%s); trying webcam", exc)
            else:
                if src.is_running:
                    return src
            try:
                src.stop()
            except Exception:  # noqa: BLE001
                pass
    # Non-Pi (or picamera2 unavailable): try the webcam.
    cam = _webcam()
    cam.start()
    if cam.is_running:
        return cam
    log.warning(
        "No usable camera (webcam error: %s); falling back to mock feed.",
        cam.last_error or "unknown",
    )
    # Carry the failure reason into the mock so /api/health can explain why
    # the feed is synthetic instead of silently showing a test pattern.
    mock.last_error = (
        f"No real camera available; serving synthetic feed. "
        f"Last camera error: {cam.last_error or 'unknown'}"
    )
    return mock


def _build_notifier(config: Config):
    """Discord notifier if a webhook is configured, else a no-op notifier."""
    if config.discord_webhook_url and config.discord_webhook_url not in ("", "unset"):
        return DiscordNotifier(
            config.discord_webhook_url,
            cooldown_seconds=config.notification_cooldown_seconds,
        )
    return NullNotifier()


# ---------------------------------------------------------------------------
# Flask application factory
# ---------------------------------------------------------------------------
def create_app(config: Config | None = None) -> Flask:
    """Create and fully wire the Flask application.

    ``config`` may be omitted, in which case one is loaded from the
    environment (and the optional ``.env`` file).  The returned app is ready
    to be handed to Werkzeug (``app.run``) or a production WSGI server.
    Startup/teardown of the background detection worker, the camera and the
    SSE bus is managed by an ``atexit`` hook so the process shuts down
    cleanly.
    """
    if config is None:
        config = load_config()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["DOGMON_CONFIG"] = config
    app.secret_key = config.secret_key or _generate_secret()

    # --- secure-session configuration (production-aware) ------------------
    if config.is_production() and config.require_https_in_prod:
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
        app.config["PERMANENT_SESSION_LIFETIME"] = (
            config.session_lifetime_hours * 3600
        )

        @app.after_request
        def _add_hsts(response):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
            return response

    # --- 1. State machine (single source of truth) ----------------------
    sm = MonitorStateMachine()
    try:
        initial = MonitoringState.from_name(config.startup_state)
    except ValueError:
        log.warning("Invalid STARTUP_STATE %r; using GREEN", config.startup_state)
        initial = MonitoringState.GREEN
    sm.set_state(initial, source="startup")

    # --- 2. GPIO backend + controllers ----------------------------------
    backend = _build_gpio_backend(config)
    led = LedController(backend, names=("RED", "YELLOW", "GREEN"))
    # The physical button *cycles* the state (Green -> Yellow -> Red -> ...).
    button = ButtonController(backend, on_press=lambda: sm.cycle(source="button"))

    # --- 3. Camera (shared frame source + manager) ----------------------
    frame_source = _build_frame_source(config)
    camera = SharedCameraManager(frame_source)

    # Camera is only powered in RED / YELLOW (green = off, per spec 6/13).
    def _ensure_camera_for_state(state: MonitoringState) -> None:
        try:
            if state == MonitoringState.GREEN:
                camera.stop()
            else:
                camera.start()
        except Exception:  # noqa: BLE001
            log.exception("Camera control for state %s failed", state.name)

    # --- 4. Detection engine + notifier (background, notify-only) -------
    notifier = _build_notifier(config)
    pet_detector = MockPetDetector(enabled=config.pet_detection_enabled)
    dcfg = DetectionConfig(
        enabled=config.motion_enabled,
        require_pet_detection=config.pet_detection_enabled,
        cooldown_seconds=config.notification_cooldown_seconds,
    )
    detection = DetectionEngine(
        frame_source=frame_source,
        notifier=notifier,
        pet_detector=pet_detector,
        config=dcfg,
    )

    # --- 5. Viewer registry (tracks active live-stream viewers) --------
    from .viewer import ViewerRegistry

    viewers = ViewerRegistry(
        timeout_seconds=config.viewer_timeout_seconds,
        sse_heartbeat=config.sse_heartbeat_seconds,
        max_viewers=config.stream_max_viewers,
    )

    # --- 6. SSE bus (live status updates for the web UI) ---------------
    bus = SSEBus(
        max_queue=32,
        heartbeat_interval=int(config.sse_heartbeat_seconds),
    )

    # --- 7. Wire the state machine's listener: LED + camera + SSE ------
    def _on_state_change(change) -> None:
        # Keep the LEDs in lock-step with the state (spec section 37).
        led.set_state(change.new)
        # Power the camera to match the state (spec section 6/13).
        _ensure_camera_for_state(change.new)
        # Notify the web UI of the new state (spec section 10).
        # The payload mirrors StateMachine.snapshot() so the frontend's
        # onStateChanged() can read `state`, `label`, `description`, and
        # `last_change` from *every* notification source (button, another
        # device, viewer registry) — not just direct button clicks.
        payload = change.to_dict() if hasattr(change, "to_dict") else {}
        payload.setdefault("state", change.new.name)
        payload["label"] = getattr(change, "label", payload.get("label", change.new.name))
        payload["description"] = getattr(
            change, "description", payload.get("description", "")
        )
        bus.publish(payload, event="state_change")

    sm.add_listener(_on_state_change)
    # Apply the initial state immediately (the listener above already fired
    # on the startup transition, so this is a safety net for the LEDs/camera
    # on the very first render).
    led.set_state(sm.state)
    _ensure_camera_for_state(sm.state)

    # --- 8. Viewer -> state coupling (spec section 14) -----------------
    # A remote viewer present on the live feed means the system is in RED.
    # When the last viewer leaves, demote RED -> YELLOW (the camera stays on
    # for private monitoring).  We never auto-promote from GREEN (the user
    # must explicitly turn the camera on).
    def _on_viewer_added(_vid: str) -> None:
        with sm.lock:
            current = sm.state
        if current == MonitoringState.YELLOW:
            sm.set_state(MonitoringState.RED, source="viewer_connect")

    def _on_viewer_removed(_vid: str) -> None:
        with sm.lock:
            current = sm.state
        if current == MonitoringState.RED and viewers.count() == 0:
            sm.set_state(MonitoringState.YELLOW, source="viewer_disconnect")

    viewers.set_callbacks(_on_viewer_added, _on_viewer_removed)

    # --- 9. Security context (auth + CSRF + rate limit) ----------------
    from .security import generate_csrf

    security = _AppContext()
    security.set_auth(config.admin_username, config.admin_password, config.auth_enabled)
    security.set_rate(config.rate_limit_per_minute)
    app.config["security"] = security

    # --- 10. Expose components to request context via g -----------------
    @app.before_request
    def _set_security_ctx():
        from flask import g

        g.security = security
        g.state_machine = sm
        g.camera = camera
        g.viewers = viewers
        g.detection = detection
        g.bus = bus
        g.dogmon_config = config

    # --- 11. Register blueprints ---------------------------------------
    from .routes import api_bp, pages_bp, stream_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(stream_bp)

    # --- 12. Context processor: snapshot + CSRF for templates ----------
    @app.context_processor
    def _inject_state():
        return {
            "monitoring_state": sm.snapshot(),
            "dogmon_config": config,
            "viewer_count": viewers.count(),
            "csrf_token": generate_csrf(),
            "is_mock": config.is_mock,
        }

    # --- 13. Error handlers -------------------------------------------
    @app.errorhandler(404)
    def _not_found(_e):
        from flask import render_template

        return (
            render_template("error.html", code=404, message="Not Found"),
            404,
        )

    @app.errorhandler(500)
    def _internal_error(e):
        log.exception("Internal server error: %s", e)
        from flask import render_template

        return (
            render_template("error.html", code=500, message="Internal Server Error"),
            500,
        )

    # --- 14. Graceful shutdown ---------------------------------------
    def _shutdown() -> None:
        try:
            detection.stop(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            camera.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            button.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            bus.close(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_shutdown)
    app.config["_dogmon_shutdown"] = _shutdown

    log.info(
        "Flask app created (platform=%s, mock=%s, startup=%s, auth=%s)",
        config.platform_mode,
        config.is_mock,
        sm.state.name,
        config.auth_enabled,
    )
    return app


def _generate_secret() -> str:
    """Generate a random secret key (development convenience)."""
    import secrets

    return secrets.token_urlsafe(32)
