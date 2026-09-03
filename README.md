# 🐕 Dog Monitor — Secure Raspberry Pi 5 Camera Monitoring System

A small, secure, self-hosted dog-monitoring web app. It runs a **Raspberry Pi 5**
with a Pi camera, three status LEDs and a physical push button, exposed over
the web for private, privacy-first monitoring.

It is designed so that **almost everything can be developed and tested on a
Mac today** (with simulated hardware and a simulated camera) and then moved to
the Raspberry Pi 5 with minimal code changes when the hardware is available.

> **Privacy-first default:** on startup the system starts in **GREEN (camera
> off)**. The camera is only powered on when you explicitly start monitoring,
> and the *live video feed is only exposed while the state is RED* — the
> moment a browser viewer is actually watching.

---

## What it does

| State (LED) | Meaning | Camera | Live feed to viewers |
|-------------|---------|--------|----------------------|
| 🟢 **GREEN** | Camera Off | Stopped (no capture) | No |
| 🟡 **YELLOW** | Private Monitoring | Active, local processing only | **No** |
| 🔴 **RED** | Live Monitoring | Active | **Yes** — only while a browser is watching |

* **Central state machine** — a single thread-safe `MonitorStateMachine` is the
  *only* place state is changed. The camera, the GPIO button, and the web
  routes all call into it; none of them write state directly.
* **Physical push button** — a short press *cycles* the state
  `GREEN → YELLOW → RED → GREEN`. Press handling is debounced.
* **Three status LEDs** — only the LED matching the current state is on
  (RED / YELLOW / GREEN), so the physical hardware always agrees with the
  website.
* **Live video** — a lightweight MJPEG stream (`multipart/x-mixed-replace`)
  fed from a *single shared camera* to many viewers (not one pipeline per
  browser).
* **Real-time status** — the web UI updates **without refreshing the page**,
  via Server-Sent Events (SSE), even when the state is changed by the physical
  button or another device.
* **Discord notifications (future-ready)** — a background *detection engine*
  can run frame-difference motion detection, an optional lightweight
  pet/dog detector, and (respecting a 20-minute cooldown that is **not** reset
  by continued motion) post a snapshot to a Discord webhook.
* **Security** — session-based login, per-session CSRF tokens on every
  state-changing request, per-IP rate limiting, secure cookies, and (in
  production behind HTTPS) HSTS. The live feed is never exposed while the
  state is GREEN or YELLOW.

---

## Why this architecture (key decisions)

* **MJPEG, not WebRTC, for the MVP.** MJPEG is a one-line
  `multipart/x-mixed-replace` stream over plain HTTP — trivial to run on a
  Pi with no extra server, and adequate for a few viewers. WebRTC is a
  better long-term choice for low-latency secure remote viewing and is
  supported by the `VideoStreamProvider` abstraction (see
  [docs/architecture.md](docs/architecture.md)).
* **Raspberry Pi Connect is *not* the web-access layer.** Pi Connect provides
  remote **VNC desktop + shell + remote update**, but it does **not** port
  forward arbitrary TCP, so it cannot expose the Flask HTTP port. Remote web
  access must go through one of: SSH tunnel, a private VPN / Tailscale +
  HTTPS, or a reverse proxy. See [docs/deployment.md](docs/deployment.md)
  and [docs/security.md](docs/security.md).
* **`picamera2` (libcamera) on Raspberry Pi OS Bookworm** is the current
  camera stack; `picamera` (the old v1) is deprecated. See
  [docs/hardware.md](docs/hardware.md).
* **GPIO Zero** for the LEDs and button — simple, well-documented, and it
  handles debouncing for the button.

---

## Quick start

### On your Mac (mock mode — no hardware required)

```bash
# 1. Clone / enter the project
cd dog-monitoring

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # or: pip install -r requirements-dev.txt

# 3. (Optional) copy the example config
cp .env.example .env

# 4. Run the app (mock mode is auto-detected on non-Pi machines)
python run.py
```

Then open <http://127.0.0.1:8080> in your browser.

> On a non-Pi machine `PLATFORM=auto` **auto-detects** that GPIO/camera
> hardware is unavailable and falls back to the **mock** backend, so
> `python run.py` "just works" for development and testing with **no
> hardware**. LED state is tracked in software; the camera shows a simulated
> test-pattern feed. You can force it explicitly with
> `PLATFORM=mock python run.py`.

Run the test suite (it needs no hardware):

```bash
pytest -q
```

### On the Raspberry Pi 5

See [docs/deployment.md](docs/deployment.md) for the full install, and
[docs/hardware.md](docs/hardware.md) for the wiring.

```bash
# Minimal (full steps in docs/deployment.md)
python3 -m venv /opt/dogmon/.venv
source /opt/dogmon/.venv/bin/activate
pip install -r requirements.txt
# enable the Pi camera:  sudo raspi-config  ->  Interface Options  ->  Camera
export PLATFORM=hardware
python run.py
```

---

## Configuration

All configuration is via environment variables (or a `.env` file).
[`.env.example`](.env.example) documents every variable with a safe default.
**Never commit a real `.env`** — it is git-ignored.

| Variable | Default | Purpose |
|----------|---------|---------|
| `PLATFORM` | `auto` | `auto` \| `mock` \| `hardware` — which backend to use |
| `STARTUP_STATE` | `GREEN` | Initial state (privacy-safe = camera off) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | *(empty)* | Web login credentials |
| `AUTH_ENABLED` | `1` | `0` to disable auth in dev (not recommended) |
| `DISCORD_WEBHOOK_URL` | *(empty)* | Discord webhook (empty = notifications off) |
| `NOTIFICATION_COOLDOWN_SECONDS` | `1200` | 20 min between Discord alerts |
| `MOTION_ENABLED` / `PET_DETECTION_ENABLED` | `0` | Toggle the detection pipeline |
| `SECRET_KEY` | *(generated in dev)* | **Required** in production |
| `REQUIRE_HTTPS_IN_PROD` | `1` | Secure cookies + HSTS in production |
| `STREAM_MAX_VIEWERS` | `8` | Hard cap on simultaneous live viewers |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-IP rate limit on the API |
| `*_PIN` (`RED_LED_PIN`, `YELLOW_LED_PIN`, `GREEN_LED_PIN`, `BUTTON_PIN`) | `17/27/22/23` | GPIO pins (BCM numbering) |

> **Note:** GPIO pins are configurable and use **BCM numbering** (not physical
> pin numbers). Verify the chosen pins against a Pi 5 pinout before wiring.

---

## Project structure

```
dog-monitoring/
├── run.py                  # entry point (loads config, validates, runs)
├── requirements.txt        # runtime deps (Flask, Pillow, gpiozero, picamera2)
├── requirements-dev.txt    # + pytest
├── .env.example            # documented, safe, git-ignored real .env
├── .gitignore
│
├── dog_monitoring/
│   ├── __init__.py
│   ├── app.py              # Flask application factory (wires everything)
│   ├── config.py           # environment/config loader
│   ├── logging.py          # logging + secret redaction
│   ├── security.py         # auth, CSRF, rate limiting
│   ├── viewer.py           # live-viewer registry (heartbeat + cap)
│   ├── sse.py              # SSE pub/sub bus
│   │
│   ├── state/              # MonitoringState enum + MonitorStateMachine
│   ├── hardware/           # GpioBackend (mock + GPIO Zero), LED + button
│   ├── camera/             # FrameSource (mock + Picamera2) + SharedCameraManager
│   ├── detection/          # motion, pet detector, Discord notifier, engine
│   │
│   ├── routes/             # pages.py, api.py, stream.py (blueprints)
│   ├── templates/          # index.html, login.html, error.html
│   └── static/             # css/, js/
│
├── tests/                  # 77 unit tests (state, gpio, camera, api, …)
├── scripts/                # helper scripts (e.g. fix_indent.py)
├── docs/                   # architecture / hardware / deployment / security
└── Project_ Secure Raspberry Pi Dog Monitoring System.md   # the requirements spec
```

---

## How it works (the data flow)

```
                       ┌───────────────────────────┐
  Internet (tunnel)    │      Raspberry Pi 5       │
        │             │                            │
        └──────────▶  │   Flask app               │
                       │        │                  │
                       │   MonitorStateMachine  ◀── single source of truth
                       │        │                  │
              ┌────────┼─────────┼────────┐       │
              ▼        ▼         ▼        ▼       │
           Camera    GPIO      Detection  SSE    │
         (Picamera2)(GPIO Zero) (background thread)→ web UI
                       └───▶ LEDs        └───▶ Discord (cooldown)
```

* **Web routes** (`/api/state`, `/api/cycle`, `/live.mjpeg`) only ever call
  `state_machine.set_state()` / `.cycle()` / `snapshot()`.
* **Physical button** calls `state_machine.cycle(source="button")`.
* **State change listeners** (registered in `app.py`): update the LEDs, power
  the camera on/off to match the state, and push the new state to the web UI
  over SSE.
* **Viewer registry** watches who is actually receiving the live feed and, when
  the last viewer leaves, demotes RED → YELLOW (camera stays on locally).
* **Detection engine** (background thread) does *not* change monitoring state —
  it only detects motion / a dog and (respecting the cooldown) sends a Discord
  notification.

---

## Security summary

The camera feed is privacy-sensitive. The system is designed to be treated as a
real networked device, even in development:

* **State-changing endpoints** (`POST /api/state`, `POST /api/cycle`) require a
  valid **CSRF token** (per session) **and**, when auth is enabled, an
  authenticated session.
* **The live feed is only served in RED** — never in GREEN or YELLOW.
* **Rate limiting** (per IP) on the API.
* **Secure cookies** + **HSTS** in production behind HTTPS.
* **Never commit secrets** — `SECRET_KEY`, `DISCORD_WEBHOOK_URL`,
  `ADMIN_PASSWORD` all live only in the environment / `.env` (git-ignored).

See [docs/security.md](docs/security.md) for the full threat model and the
remote-access recommendation.

---

## Documentation

* [docs/architecture.md](docs/architecture.md) — design, component diagram,
  and the reasoning behind key decisions (MJPEG vs WebRTC, Pi Connect, etc.).
* [docs/hardware.md](docs/hardware.md) — Raspberry Pi 5 wiring, GPIO pins,
  LED/button, and camera setup.
* [docs/deployment.md](docs/deployment.md) — from install to a `systemd`
  service, plus secure remote access.
* [docs/security.md](docs/security.md) — threat model, attack surface, and
  recommended production setup.

---

## License

Provided as-is for personal / educational use.
