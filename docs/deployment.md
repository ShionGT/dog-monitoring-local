# Deployment — Raspberry Pi OS Bookworm

This document takes the Dog Monitor from a fresh Raspberry Pi 5 to a
**systemd service that auto-starts on boot**, and covers secure remote
access. It follows the spec (section 27).

> **Assumptions:** a Pi 5 running **Raspberry Pi OS (64-bit) Bookworm or
> later**, with a camera module connected and a current Python 3.9+ from the
> OS. The code itself has no version-locked OS dependency beyond that.

---

## 1. Raspberry Pi OS installation

1. Flash **Raspberry Pi OS (64-bit) Bookworm** to an SD card using the
   Raspberry Pi Imager (https://www.raspberrypi.com/software/).
2. In the Imager "Advanced Options", optionally pre-configure WiFi, a
   hostname (`pi-dogmon`), and enable **SSH** (so you can connect before
   a screen is attached).
3. Boot the Pi and SSH in: `ssh pi@<hostname>.local`.

Verify the OS:

```bash
cat /etc/os-release
# -> Raspbian release ... Bookworm
python3 --version
# -> Python 3.9 or 3.11 (Bookworm ships 3.11)
```

---

## 2. Python environment setup

Use a **dedicated system venv** so the app is isolated from the OS Python
and so it survives reboots.

```bash
# Make a home directory for the app
sudo -S -p '' mkdir -p /opt/dogmon
sudo -S -p '' chown $USER /opt/dogmon

# Install it there
cp -r /path/to/dog-monitoring /opt/dogmon
cd /opt/dogmon

# Create an isolated virtualenv
python3 -m venv /opt/dogmon/.venv
source /opt/dogmon/.venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt       # runtime deps (Flask, Pillow, rpi-lgpio, picamera2)
```

> On the Pi, `PLATFORM=auto` detects Linux + ARM/`aarch64` + a `/dev/gpiochip*`
> device (the Pi 5 exposes `/dev/gpiochip4` via RP1) and uses the **hardware**
> backend. You can force it with `export PLATFORM=hardware`.
> **Install `rpi-lgpio` with `pip install rpi-lgpio` (or `sudo apt
> install python3-rpi-lgpio`) —** it provides the `RPi.GPIO` module backed by
> gpiod, which works on the Pi 5. Do **not** install the original `RPi.GPIO`
> package (it doesn't work on the Pi 5 and would shadow it). Without an
> `RPi.GPIO` module the app logs `Hardware GPIO backend unavailable` and falls
> back to mock GPIO (LEDs will not light).

---

## 3. Camera setup

Enable the camera through `raspi-config` (Bookworm uses **libcamera**):

```bash
sudo -S -p '' raspi-config
#   → "Interface Options"
#   → "Camera"   →  "Enable"
#   → "Finish"   →  reboot
```

Confirm detection after reboot:

```bash
libcamera-hello --list                 # should list the camera
python /opt/dogmon/.venv/bin/python -c "from picamera2 import Picamera2; Picamera2(); print('camera OK')"
```

Set the resolution/frame rate you want via environment (`CAMERA_WIDTH`,
`CAMERA_HEIGHT`, `CAMERA_FPS` — see `.env.example`).

---

## 4. GPIO wiring

Wire **3 LEDs + 1 button** to the GPIO header per
[`hardware.md`](hardware.md). The default pins are:

| Component      | Env var        | Default (BCM) |
|----------------|----------------|---------------|
| Red LED        | `RED_LED_PIN`  | 17            |
| Yellow LED     | `YELLOW_LED_PIN` | 27          |
| Green LED      | `GREEN_LED_PIN` | 22          |
| Push button    | `BUTTON_PIN`   | 23            |

Change any of these via `.env` if your wiring differs. **Verify the pins
against a current Pi 5 pinout before power-on** (see `hardware.md` §1).

---

## 5. Dependency installation

`pip install -r requirements.txt` installs everything (section 2). The
**hardware** deps (`rpi-lgpio`, `picamera2`) are only imported when
`PLATFORM=hardware`, so on a laptop/CI (mock mode) they are **not** needed.

---

## 6. Configuration

Create `/opt/dogmon/.env` (git-ignored) and set your real values:

```bash
cd /opt/dogmon
cp .env.example .env
$EDITOR .env
```

Key things to set:
- `PLATFORM=hardware`
- `APP_ENV=production`
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` (strong, random)
- `SECRET_KEY` (a long random string — **required** in production)
- `DISCORD_WEBHOOK_URL` (optional; leave blank to disable notifications)
- `STARTUP_STATE=GREEN` (privacy-safe default)
- Pin assignments if you changed the wiring.

Generate a random `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 7. Raspberry Pi Connect setup

Raspberry Pi Connect gives you **remote VNC desktop + shell + remote update**.
**It does not expose the Flask HTTP port** (it is not a TCP port-forwarder),
so on its own it is *not* the web-access layer for the camera feed.

Use Pi Connect for **administration** (remote terminal, viewing the desktop),
and reach the web app through one of the secure remote-access methods in
**§9** (SSH tunnel, Tailscale+HTTPS, or reverse proxy).

To use Pi Connect: enable it in **Raspberry Pi OS → "Raspberry Pi Connect"**
(or `sudo raspi-config` on some versions), sign in with your Raspberry Pi
login, and it will appear at https://raspberrypi.com/connect/.

---

## 8. Application startup

First, confirm it runs in the foreground:

```bash
source /opt/dogmon/.venv/bin/activate
cd /opt/dogmon
export PLATFORM=hardware
python run.py
# logs the startup banner; serves on 127.0.0.1:8080
```

Then bind it through the systemd service below for auto-start on boot.

---

## 9. systemd service (auto-start on boot)

The unit file ships in the repo at [`deploy/dogmon.service`](../deploy/dogmon.service).
Install it:

```bash
sudo cp deploy/dogmon.service /etc/systemd/system/
# If the app lives somewhere other than /opt/dogmon, or runs as a different
# user, edit the User=/WorkingDirectory=/EnvironmentFile= lines first.
```

Enable and start it:

```bash
sudo -S -p '' systemctl daemon-reload
sudo -S -p '' systemctl enable --now dogmon.service
sudo -S -p '' systemctl status dogmon.service        # should be "active (running)"
journalctl -u dogmon -f                    # follow logs
```

If `run.py` needs `PLATFORM=hardware` and it is in `.env`, `EnvironmentFile`
handles it. Confirm the app is up:

```bash
curl -s http://127.0.0.1:8080/api/health | python -m json.tool
```

---

## 10. Secure remote access (the important part)

Because **Pi Connect does not forward the app's TCP port**, pick **one** of
these for the web UI:

### Option A — SSH tunnel (lowest attack surface; recommended for 1 user)
Run it locally on your laptop:

```bash
ssh -L 8080:127.0.0.1:8080 pi@<hostname>.local
# then open http://127.0.0.1:8080 in your browser — nothing is exposed on the net
```

### Option B — Tailscale + HTTPS (easy private network)
Join the Pi to a Tailscale network, serve over HTTPS (e.g. Caddy with
auto-TLS), and the app is reachable at `http://<pi>.local:443/` with secure
cookies + HSTS enabled (the app does this automatically in production).

### Option C — Reverse proxy (Nginx/Caddy) behind a trusted network
Terminate TLS and (optionally) add a password in the proxy; keep Flask bound
to `127.0.0.1`.

**Never** bind the app to `0.0.0.0` and call it "secure" — see
`security.md` §4 for the full rationale.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Camera unavailable` / `picamera2` import error | Camera not enabled | `sudo raspi-config → Camera → Enable`, reboot |
| Buttons not responding | Pin wrong / no pull-up | Check `BUTTON_PIN` + wiring (pull-up to 3V3) |
| LEDs don't change | Pin conflict / wiring | Verify `*_LED_PIN` + resistor + GND in `hardware.md` |
| `SECRET_KEY` required (won't start) | `APP_ENV=production` with empty key | Set a random `SECRET_KEY` in `.env` |
| Service not staying up | Check `journalctl -u dogmon` | `systemctl status`, read the log |
| No feed in browser | State not RED | Set state to RED (the feed only serves in RED) |
| Feed shows `503` | Too many viewers | Lower `STREAM_MAX_VIEWERS` usage or wait for a slot |
