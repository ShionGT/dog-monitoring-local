"""Application configuration.

All tunables live here as environment variables (with sensible, privacy-safe
defaults). Real secrets are never committed; developers copy
``.env.example`` to ``.env``. The module deliberately depends on nothing outside
the standard library so it can be imported in any environment (tests, CI, Pi, Mac).

Platform selection
------------------
* ``PLATFORM=mock``          -> force simulation (used on macOS / CI).
* ``PLATFORM=hardware``       -> force real GPIO + camera (fails loudly if missing).
* ``PLATFORM=auto`` (default) -> real hardware *only if* the GPIO/camera libraries
are importable and we appear to be on a Raspberry Pi; otherwise fall back to
mock. This makes ``python run.py`` "just work" on a laptop while still using
real hardware on a Pi.

Defaults bias toward privacy: the safe startup state is GREEN (camera off)
(Project spec, sections 33 & 13).
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from typing import Optional


def _load_dotenv(path: Optional[str] = None) -> None:
    """Very small ``.env`` loader (``KEY=VALUE`` lines) with no third-party deps.

    Values already present in the real environment take precedence: we only
    *set* a variable if it is not already defined, mirroring ``python-dotenv``'s
    ``override=False`` default so tests and shell exports win.
    """
    candidates: list[str] = []
    if path:
        candidates.append(path)
    else:
        candidates.append(os.environ.get("DOTENV_PATH", ".env"))
    for p in candidates:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):]
                    if "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    # Strip surrounding quotes, or a trailing " # comment".
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    elif " #" in value:
                        value = value.split(" #", 1)[0].strip()
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            # A malformed/binary .env must not crash startup.
            continue


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _as_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Optional[str], default: float) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


#: GPIO/pin numbers used as defaults. These are example BCM numbers from the
#: project spec and can be overridden via the environment.
DEFAULT_RED_LED_PIN = 17
DEFAULT_YELLOW_LED_PIN = 27
DEFAULT_GREEN_LED_PIN = 22
DEFAULT_BUTTON_PIN = 23

#: Which startup state to use. Privacy default is GREEN (camera off).
DEFAULT_STARTUP_STATE = "GREEN"


@dataclass
class Config:
    """Snapshot of all application settings.

    Build a fresh ``Config`` via :func:`load_config`; individual fields are
    plain attributes so tests can override them freely.
    """

    # --- environment / security ---
    app_env: str = "development"             # 'development' | 'production'
    secret_key: str = ""                    # required in production; see run.py
    debug: bool = False
    admin_username: str = ""                # web login username (env ADMIN_USERNAME)
    admin_password: str = ""                # web login password (env ADMIN_PASSWORD)
    auth_enabled: bool = True               # set AUTH_ENABLED=0 to disable (dev only)

    # --- platform / hardware backend ---
    platform_mode: str = "auto"            # 'auto' | 'mock' | 'hardware'
    mock_cameras: bool = True             # if True, use simulated frames

    # --- GPIO pin assignment (BCM numbering) ---
    red_led_pin: int = DEFAULT_RED_LED_PIN
    yellow_led_pin: int = DEFAULT_YELLOW_LED_PIN
    green_led_pin: int = DEFAULT_GREEN_LED_PIN
    button_pin: int = DEFAULT_BUTTON_PIN

    # --- camera ---
    camera_width: int = 1280
    camera_height: int = 720
    camera_fps: int = 24
    camera_rotation: int = 0

    # --- streaming ---
    stream_fps: int = 15                   # frames the MJPEG provider may emit/s
    stream_max_viewers: int = 8

    # --- state machine ---
    startup_state: str = DEFAULT_STARTUP_STATE

    # --- motion detection (future; disabled by default in MVP) ---
    motion_enabled: bool = False
    motion_threshold: float = 0.02         # fraction of pixels that changed
    motion_min_area: int = 200             # min changed-pixel count to count
    motion_cooldown_seconds: int = 1200    # 20 minutes

    # --- pet detection (future; disabled by default) ---
    pet_detection_enabled: bool = False
    pet_model: str = "yolov8n"

    # --- notifications ---
    discord_webhook_url: str = ""
    notification_cooldown_seconds: int = 1200  # 20 minutes

    # --- live status / viewer tracking ---
    viewer_timeout_seconds: int = 5        # no heartbeat in 5s -> viewer gone
    sse_heartbeat_seconds: float = 15.0

    # --- security hardening ---
    rate_limit_per_minute: int = 120       # per (ip) for API endpoints
    csrf_enabled: bool = True
    session_lifetime_hours: int = 24
    require_https_in_prod: bool = True      # Secure cookie / HSTS in production

    data_dir: str = "media"               # temp capture location (git-ignored)

    _resolved_is_mock: bool = field(default=True, repr=False)

    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    def requires_secret_key(self) -> bool:
        # A real secret is mandatory outside pure development/testing.
        return self.is_production() or os.environ.get("SECRET_KEY_REQUIRED", "") == "1"

    @property
    def is_mock(self) -> bool:
        """True when the app should run against simulated hardware/camera."""
        return self._resolved_is_mock


def _detect_platform() -> str:
    """Decide platform backend without third-party imports.

    Real hardware is only assumed when we appear to be *on* a Raspberry-Pi-like
    machine (ARM Linux with ``/dev/gpiochip0``). Everything else (macOS, x86 CI)
    resolves to mock so the app runs everywhere.
    """
    mode = os.environ.get("PLATFORM", "auto").strip().lower()
    if mode in {"mock", "hardware"}:
        return mode
    # auto:
    system = platform.system()
    machine = platform.machine().lower()
    on_pi = (
        system == "Linux"
        and machine.startswith("arm")
        and os.path.exists("/dev/gpiochip0")
    )
    return "hardware" if on_pi else "mock"


def load_config(env: "Optional[dict[str, str]]" = None, *, path: Optional[str] = None) -> Config:
    """Load a :class:`Config` from the environment (and optional ``.env`` file).

    ``env`` lets tests inject a synthetic environment; otherwise the real
    ``os.environ`` is used (after loading ``.env`` if present).
    """
    # Snapshot the current environment so a test-provided env does not leak.
    saved = dict(os.environ)
    try:
        if env is not None:
            os.environ.clear()
            os.environ.update({k: str(v) for k, v in env.items()})
        else:
            _load_dotenv(path)

        c = Config()
        c.app_env = (os.environ.get("APP_ENV", c.app_env) or "development").strip()
        c.secret_key = os.environ.get("SECRET_KEY", "")
        c.debug = _as_bool(os.environ.get("DEBUG"), c.debug)
        c.admin_username = os.environ.get("ADMIN_USERNAME", "").strip()
        c.admin_password = os.environ.get("ADMIN_PASSWORD", "")
        c.auth_enabled = _as_bool(os.environ.get("AUTH_ENABLED"), True)
        c.platform_mode = _detect_platform()
        c.mock_cameras = _as_bool(os.environ.get("USE_MOCK_CAMERA"), True)
        c.red_led_pin = _as_int(os.environ.get("RED_LED_PIN"), DEFAULT_RED_LED_PIN)
        c.yellow_led_pin = _as_int(os.environ.get("YELLOW_LED_PIN"), DEFAULT_YELLOW_LED_PIN)
        c.green_led_pin = _as_int(os.environ.get("GREEN_LED_PIN"), DEFAULT_GREEN_LED_PIN)
        c.button_pin = _as_int(os.environ.get("BUTTON_PIN"), DEFAULT_BUTTON_PIN)
        c.camera_width = _as_int(os.environ.get("CAMERA_WIDTH"), c.camera_width)
        c.camera_height = _as_int(os.environ.get("CAMERA_HEIGHT"), c.camera_height)
        c.camera_fps = _as_int(os.environ.get("CAMERA_FPS"), c.camera_fps)
        c.camera_rotation = _as_int(os.environ.get("CAMERA_ROTATION"), c.camera_rotation)
        c.stream_fps = _as_int(os.environ.get("STREAM_FPS"), c.stream_fps)
        c.stream_max_viewers = _as_int(os.environ.get("STREAM_MAX_VIEWERS"), c.stream_max_viewers)
        c.startup_state = (os.environ.get("STARTUP_STATE", c.startup_state) or "GREEN").strip().upper()
        c.motion_enabled = _as_bool(os.environ.get("MOTION_ENABLED"), c.motion_enabled)
        c.motion_threshold = _as_float(os.environ.get("MOTION_THRESHOLD"), c.motion_threshold)
        c.motion_min_area = _as_int(os.environ.get("MOTION_MIN_AREA"), c.motion_min_area)
        c.motion_cooldown_seconds = _as_int(
            os.environ.get("MOTION_COOLDOWN_SECONDS"), c.motion_cooldown_seconds
        )
        c.pet_detection_enabled = _as_bool(os.environ.get("PET_DETECTION_ENABLED"), c.pet_detection_enabled)
        c.pet_model = os.environ.get("PET_MODEL", c.pet_model)
        c.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        c.notification_cooldown_seconds = _as_int(
            os.environ.get("NOTIFICATION_COOLDOWN_SECONDS"), c.notification_cooldown_seconds
        )
        c.viewer_timeout_seconds = _as_int(os.environ.get("VIEWER_TIMEOUT_SECONDS"), c.viewer_timeout_seconds)
        c.sse_heartbeat_seconds = _as_float(os.environ.get("SSE_HEARTBEAT_SECONDS"), c.sse_heartbeat_seconds)
        c.rate_limit_per_minute = _as_int(os.environ.get("RATE_LIMIT_PER_MINUTE"), c.rate_limit_per_minute)
        c.csrf_enabled = _as_bool(os.environ.get("CSRF_ENABLED"), c.csrf_enabled)
        c.session_lifetime_hours = _as_int(os.environ.get("SESSION_LIFETIME_HOURS"), c.session_lifetime_hours)
        c.require_https_in_prod = _as_bool(os.environ.get("REQUIRE_HTTPS_IN_PROD"), c.require_https_in_prod)
        c.data_dir = os.environ.get("DATA_DIR", c.data_dir)

        # Resolve the mock flag: forced by platform mode, else by mock_cameras.
        if c.platform_mode == "mock":
            c._resolved_is_mock = True
        elif c.platform_mode == "hardware":
            c._resolved_is_mock = False
        else:  # auto
            c._resolved_is_mock = c.mock_cameras
        return c
    finally:
        # Restore the environment: tests must not mutate the real os.environ.
        os.environ.clear()
        os.environ.update(saved)


# A module-level default instance is handy for convenience, but production code
# should call ``load_config`` explicitly so it is deterministic and testable.
config = load_config()
