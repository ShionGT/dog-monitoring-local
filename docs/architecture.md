# Architecture

The Dog Monitor is a small, **privacy-first** web application for monitoring a
dog on a Raspberry Pi 5. This document explains the design, the data flow, and
the reasoning behind the key architectural decisions.

---

## 1. Guiding principles

The spec (section 36) prioritises, in order:

```
security > reliability > privacy > low resource usage > maintainability > simplicity
```

Everything in this design follows that. In particular:

* **Privacy by default.** The camera is *off* at startup (GREEN). The live video
  feed is only ever exposed while the state is RED — the instant a browser is
  actually watching. In YELLOW the camera runs (local processing only) but the feed
  is **not** sent to viewers, so a closed browser cannot leave a person
  unknowingly broadcasting.
* **Single source of truth.** There is exactly one object that owns the
  monitoring state, and every other component *asks* that object to change state.
  This is the single most important design decision — it eliminates the entire
  class of "GPIO and web disagree" bugs.
* **Separation of concerns.** The web layer, the hardware layer, the camera
  layer, and the detection layer are independent packages. None of them import
  the others' internals; they communicate only through abstractions and the
  shared state machine.
* **Hardware-agnostic by abstraction.** Every physical interaction (GPIO, camera)
  sits behind an interface with a **mock** implementation, so the whole system can
  run and be tested on a laptop with no hardware.

---

## 2. Component diagram

```
                        ┌──────────────────────────────────────┐
                        │            Flask application            │
                        │                                        │
  Browser ──HTTP───▶    │   routes (blueprints)                 │
                          │     pages  │ api  │ stream           │
                          │       │      │      │                │
                          │       └──────┼──────┘                │
                          │             ▼                        │
                        │   ┌──────────────────────┐           │
                        │   │  MonitorStateMachine  │◀─ single  │
                        │   │  (thread-safe, RLock) │  source    │
                        │   └──────┬───────────────┘  of truth  │
                        │          │ add_listener(…)            │
                        │     ┌────┴─────┬──────────┬─────────┐│
                        │     ▼          ▼          ▼          ▼│
   RPi.GPIO ◀───   LED Ctrl   Button Ctrl  SSE bus  Camera     │
 (rpi-lgpio)      (set LED)  (cycle state) (pub/sub) (FrameSrc) │
                          │                                 │     │
                          │        ┌──────────────────────┘     │
                          │        ▼                             │
                          │   DetectionEngine (background thread)│
                          │     motion → pet detector → notifier │
                          │                       │              │
                          │                  DiscordNotifier     │
                          └───────────────────────┼─────────────┘
                                                  ▼
                                             Discord webhook
```

---

## 3. The state machine (heart of the system)

`dog_monitoring/state/monitoring_state.py` defines:

* **`MonitoringState`** — a `str` enum with three members: `RED`, `YELLOW`, `GREEN`.
  Being a `str` enum means it serialises cleanly in JSON and is easy to compare.
* **`MonitorStateMachine`** — the single source of truth. It:
  * holds the current state under a `threading.RLock` (safe under concurrent
    writes from the web thread, the button's GPIO callback thread, and the
    detection background thread);
  * exposes `set_state(target, source=…)`, `cycle(source=…)` (the button's
    `GREEN → YELLOW → RED → GREEN` behaviour), and a read-only `snapshot()`;
  * maintains a list of **listeners** called (outside the lock) on every change,
    so other subsystems can react without reaching in and reading private state.

**Why listeners, not direct coupling:** the camera controller, LED controller, and
SSE bus each register a callback. When the state changes, the machine calls each
listener. None of them imports the others. This keeps the wiring declarative and
makes tests trivial — you can assert "when state is RED, the red LED is on and
the live feed responds 200" without any of the subsystems knowing about each
other.

### Valid transitions

```
RED     → {YELLOW, GREEN}
YELLOW  → {RED, GREEN}
GREEN   → {RED, YELLOW}
```

(There is no self-transition that changes state; a `set_state` to the current
state is a no-op that returns an idempotent `StateChange`.)

---

## 4. Hardware abstraction (GPIO)

`dog_monitoring/hardware/` contains:

* **`GpioBackend`** (abstract) — a tiny interface: `led_on/off`, `led_state`,
  `register_button_callback`, `close`.
* **`MockGpioBackend`** — an in-memory implementation for Mac/CI. It tracks LED
  state in a dict and lets tests *simulate* a button press
  (`press_button()`), so button behaviour is testable with no hardware.
* **`HardwareGpioBackend`** — an RPi.GPIO implementation (LED outputs + a
  debounced button input) for the real Pi 5. It is only importable where an
  `RPi.GPIO` module exists (on the Pi that is **rpi-lgpio**); on a laptop
  `create_gpio_backend` catches the `ImportError`/`RuntimeError` and falls back
  to mock.
* **`LedController`** — maps a `MonitoringState` to "turn on the matching LED,
  turn the others off.", so the physical LEDs always agree with the state.
* **`ButtonController`** — wraps the backend's button callback and turns a
  (debounced) press into a `state_machine.cycle()` call.

### Why RPi.GPIO (via rpi-lgpio)

The *original* `RPi.GPIO` package does **not** work on the Raspberry Pi 5
(its direct `/dev/mem` register access can't reach the RP1 south-bridge's
`/dev/gpiochip4`). This project therefore uses **rpi-lgpio**, a drop-in
replacement that provides the same `RPi.GPIO` API backed by Linux gpiod —
which **does** work on the Pi 5. RPi.GPIO also gives us a built-in
**debounce** (`bouncetime`) on `add_event_detect`, exactly what a physical
push button needs.

---

## 5. Camera abstraction

`dog_monitoring/camera/` contains:

* **`FrameSource`** (abstract) — `start()`, `stop()`, `capture_jpeg()`,
  `is_running()`.
* **`MockFrameSource`** — generates a synthetic JPEG (test pattern + timestamp),
  so the live-stream pipeline and the MJPEG encoder can be exercised with no
  camera.
* **`WebcamFrameSource`** — a USB / built-in webcam via **OpenCV**
  (AVFoundation on macOS, V4L2 on Linux). This is how a *laptop webcam* feeds
  the app during development, e.g. pointed at the dog before the Pi hardware
  exists. Selected with `CAMERA_SOURCE=webcam` (or by `auto`). It is
  resilient: a failed frame read triggers an automatic reopen, and any start /
  capture error is recorded in `last_error` (surfaced via `/api/health`) so a
  permission problem is visible instead of silently black.
* **`Picamera2FrameSource`** — the real camera on the Pi 5, built on
  **picamera2 / libcamera** (Raspberry Pi OS Bookworm's current camera stack).
* **`SharedCameraManager`** — a thin wrapper that exposes a *single* frame source
  to many callers, so there is **one camera pipeline shared by all viewers**, not
  one pipeline per browser — a critical requirement (spec section 7) for low
  resource usage and long-term stability.

### Why picamera2, not the old `piCamera`

The legacy `picamera` (v1) library is deprecated and does not support the
Raspberry Pi 5. **picamera2** is the current, maintained camera API and is the
recommended interface for Bookworm and later. All camera access goes through
`start()`/`stop()` so the camera is *physically stopped* in GREEN (privacy,
section 13) and never continuously running when not needed.

### macOS camera permission (webcam source)

macOS gates *all* camera access behind a per-app TCC permission. The process
that launches Python (Terminal, iTerm, the Hermes desktop app, …) must be
allowed under **System Settings → Privacy & Security → Camera**. The first run
shows a "… would like to access the camera" dialog; if it is denied, OpenCV
prints `not authorized to capture video (status 0)` and no device is listed.
`WebcamFrameSource.start()` records that in `last_error`, and `/api/health →
camera.last_error` tells you exactly what to fix — the app itself keeps running.

---

## 6. Live video streaming

`dog_monitoring/routes/stream.py` serves the feed at `GET /live.mjpeg` as a
`multipart/x-mixed-replace` **MJPEG** stream.

### Why MJPEG for the MVP (not WebRTC)

| | MJPEG (chosen) | WebRTC (future) |
|---|---|---|
| Complexity | One `multipart/x-mixed-replace` HTTP stream; no extra server | Signalling, ICE, STUN/TURN, codecs |
| Pi footprint | Tiny (plain HTTP over Flask/Werkzeug) | Larger; needs a media server or `aiortc` |
| Latency | ~0.5–1 s (acceptable for "is my dog ok") | Sub-second (better for live action) |
| Browser native | `<img src="/live.mjpeg">` — zero JS | Requires a WebRTC client |
| Privacy gating | Trivial: serve only in RED | Harder |

MJPEG is the pragmatic choice for the initial MVP: it is reliable, simple, and
adequate for the primary use case (periodic "is my dog ok?" checks). The design
**separates the feed from the transport** via the `VideoStreamProvider`
abstraction, so a WebRTC provider can be dropped in later without touching the
state machine, the camera, or the routes.

### Privacy gating

`/live.mjpeg` **refuses to stream unless `state == RED`** (returns 403
otherwise). This enforces the privacy model at the edge: even a logged-in user
cannot obtain a live feed in GREEN or YELLOW, by design.

---

## 7. Real-time status (SSE)

`dog_monitoring/sse.py` implements a small **pub/sub bus** for
Server-Sent Events. The `/events` route subscribes a per-connection queue;
the state machine's listener publishes the new state on every change. The web UI
uses `EventSource` to **update without a page refresh**, even when the change
originated from the physical button or another device.

### Why SSE (not WebSocket)

The spec (section 10) permits SSE, WebSocket, or short polling and asks for the
*simplest appropriate*. Here we only need a **one-way server → client push** of
status updates — the client never sends data over the channel. SSE is native
HTTP, needs no extra server, and is perfect for this. A WebSocket would add
complexity for no benefit. (A WebSocket would be the right choice if we later
need bidirectional low-latency control from the browser.)

### Heartbeat & disconnect handling

The SSE route emits a heartbeat comment (`: heartbeat\n\n`) roughly every 1.5 s
so proxies keep the connection alive. The **viewer registry** (`viewer.py`)
tracks each live-stream connection with a **last-seen timestamp**; a connection
that stops heartbeating (closed tab, dropped network) is reaped after
`VIEWER_TIMEOUT_SECONDS`, and when the last viewer is gone the state demotes
RED → YELLOW (camera stays on for local monitoring but the feed is no longer
exposed).

---

## 8. Detection & notification (future-ready, disabled by default)

`dog_monitoring/detection/` implements the pipeline the spec wants for *later*:

```
Camera → FrameSource → MotionDetector → PetDetector → NotificationDecision → Discord
```

* **`MotionDetector`** — lightweight **frame-difference** motion detection on a
  downscaled grayscale image (low CPU/RAM, per section 16).
* **`PetDetector`** (abstract) + **`MockPetDetector`** — a pluggable interface so a
  real lightweight model (e.g. a U-Net/NCNN YOLO nano) can be dropped in without
  touching the camera system.
* **`DiscordNotifier`** — posts to a Discord webhook, enforcing a **20-minute
  cooldown that is *not* reset by continued motion** (section 19). The cooldown is
  thread-safe (a single `last_sent` timestamp under a lock).
* **`DetectionEngine`** — a **background daemon thread** that ties them together.
  It *only notifies*; it never changes the monitoring state. Running it off the
  request path means motion detection **cannot block Flask or the video stream**
  (section 15/24).

All of this is **disabled by default** (`MOTION_ENABLED=0`,
`PET_DETECTION_ENABLED=0`), so the MVP is a stable, low-resource system that the
CV features can be layered onto cleanly.

---

## 9. Configuration

Everything is configured via **environment variables** (or a `.env` file), loaded
by `config.py`. There are no hard-coded pins, secrets, or thresholds.
`PLATFORM=auto` (default) **detects** whether it is on a Pi (ARM + `/dev/gpiochip0`)
and uses real hardware there, falling back to the **mock** backend everywhere
else (Mac, CI). This is what makes `python run.py` "just work" for development
and test.

---

## 10. Error handling philosophy (section 24)

* A single camera capture failure logs a warning and **retries** after a short
  pause — it never crashes the stream or other viewers.
* A slow SSE subscriber cannot stall the bus (bounded queues, drop-oldest).
* A detector/pet-model exception is caught in the background thread and logged;
  the worker keeps running.
* A failed login, an invalid state, or a CSRF miss returns a **clear JSON
  error**, never a 500.
* No credentials, webhook URLs, or secrets are ever logged (see `logging.py`
  redaction filter).
