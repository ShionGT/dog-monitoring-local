# Security

The camera feed is **privacy-sensitive personal data**: it shows a person's
home and their dog. This document describes the threat model and the
controls the system implements, in line with the spec (sections 11, 12,
13, 33).

> **Design axiom (spec section 33):** treat this device as a *real
> Internet-connected system, even during development.* The safest default
> state after boot is **GREEN — camera off.**

---

## 1. Threat model

| # | Threat | Likelihood | Impact | Mitigation in this system |
|---|--------|-----------|--------|---------------------------|
| T1 | Someone reaches the live camera feed and watches/records | Medium | High (privacy) | Feed **only served in RED**; 403 otherwise. Auth+CSRF on control endpoints. |
| T2 | Unauthorized state change (someone turns the camera on/off) | Medium | High (privacy/availability) | All state changes go through `MonitorStateMachine`, behind **CSRF + auth**. |
| T3 | CSRF: a malicious site triggers a state change from a victim's browser | Medium | High | Every mutating `POST` requires a per-session **CSRF token**. |
| T4 | Brute-force / credential stuffing against login | Medium | High | **Per-IP rate limiting** on the API; session is short-lived; passwords never logged. |
| T5 | Exfiltration of secrets (webhook URL, admin password, session key) | Medium | High | Secrets stay in env/`.env` (git-ignored); **never logged** (redaction filter). |
| T6 | DDoS / resource exhaustion from many browser viewers | Low–Med | Medium (availability) | **`STREAM_MAX_VIEWERS`** hard cap; viewer registry with heartbeat reaping. |
| T7 | A camera failure crashes the whole app / other viewers | Low | Medium | Capture errors are caught, logged, and retried; one bad viewer cannot stop others. |
| T8 | A detection thread (motion/CV) blocks the web | Low | Medium | Detection runs **off the request path** in a daemon thread. |
| T9 | Pi Connect "securely" exposes the app to the public Internet | — | — | **Pi Connect does NOT port-forward TCP** (VNC desktop + shell only), so a naive `0.0.0.0` bind is *not* the deployment — see §4. |
| T10 | Insecure cookies / session hijacking | Medium | High | Secure cookies (HTTPS-only) + HttpOnly + SameSite=Lax in production; HSTS. |

---

## 2. Controls implemented

### 2.1 Authentication (section 12)
- Session-based login via `ADMIN_USERNAME` / `ADMIN_PASSWORD` from the
  **environment** — credentials are **never hard-coded or committed**.
- Passwords are compared with `secrets.compare_digest` (constant-time) to
  avoid timing side-channels.
- In **production** (`APP_ENV=production`) a non-empty `SECRET_KEY` is
  **required** before the server will start (checked in `run.py`); an
  empty secret is rejected loudly.

### 2.2 CSRF protection (sections 12, 24)
- Every mutating request (`POST /api/state`, `POST /api/cycle`,
  `POST /login`) must present a **per-session CSRF token** — sent as the
  `X-CSRF-Token` header **or** the `csrf_token` form field.
- The token is generated per session (in the app context processor) and
  validated on every protected `POST`. A missing or wrong token → **403**.

### 2.3 Rate limiting
- A **per-IP token-bucket** rate limiter (`RATE_LIMIT_PER_MINUTE`, default
  120/min) guards the API against brute force and floods. Exceeding it →
  **429**.

### 2.4 Session & cookie security (section 11)
- `SESSION_COOKIE_SECURE` (HTTPS-only cookies), `SESSION_COOKIE_HTTPONLY`,
  and `SESSION_COOKIE_SAMESITE=Lax` are enabled **in production**.
- `PERMANENT_SESSION_LIFETIME` is bounded by `SESSION_LIFETIME_HOURS`.
- **HSTS** (`Strict-Transport-Security`) is added to every response in
  production (`max-age=31536000; includeSubDomains; preload`).

### 2.5 Privacy by design (sections 13, 33)
- **Startup state is GREEN = camera off** (configurable, but the default is
  the most private).
- The **live video feed is only served while `state == RED`** — never in
  GREEN (camera off) or YELLOW (private monitoring, no remote feed).
- When the last live viewer disconnects, the state **demotes RED → YELLOW**
  so the camera keeps running for local monitoring but the **remote feed is
  no longer exposed**.
- The camera is **physically stopped** (no capture) in GREEN.

### 2.6 Resource limits
- `STREAM_MAX_VIEWERS` (default 8) caps simultaneous live-stream viewers;
  excess connections receive **503**.
- A **viewer heartbeat** reaps connections that stop reporting, so a closed
  tab or dropped network cannot hold a slot forever.

### 2.7 Logging & secrets
- All logging goes through the `logging` module (no stray `print`).
- A **secret-redaction filter** scrubs webhook URLs, tokens, and password-like
  values from log lines, and secrets are **never logged in the clear**.

---

## 3. Attack surface — what is exposed and how

| Endpoint | Method | Auth | CSRF | Notes |
|----------|--------|------|------|-------|
| `/` , `/dashboard` | GET | optional | n/a | Renders the UI (no live feed unless RED). |
| `/login` | GET/POST | no | POST | Login page + form submission. |
| `/logout` | GET/POST | session | n/a | Clears the session. |
| `/api/state` | GET | optional | n/a | Read-only state snapshot. |
| `/api/state` | POST | **required** | **required** | Mutate state. |
| `/api/cycle` | POST | **required** | **required** | Cycle state forward. |
| `/api/health` | GET | optional | n/a | Liveness + component snapshot. |
| `/events` | GET | optional | n/a | SSE status stream (read-only push). |
| `/live.mjpeg` | GET | **RED-only** | n/a | **Refused (403) unless state is RED.** |

> The **most privacy-critical invariant** is that `/live.mjpeg` refuses to
> stream except in RED, *regardless* of authentication. Even an authenticated
> user cannot obtain a live feed while the camera is in private (YELLOW) or
> off (GREEN) mode.

---

## 4. Remote access — does Raspberry Pi Connect expose the app?

**No.** This is the single most important security finding of the project
(spec section 11) and was verified against current Raspberry Pi Connect
documentation:

- **Raspberry Pi Connect** provides a remote **VNC desktop**, a **shell**, and
  **remote update** over the cloud — **it does not port-forward arbitrary TCP
  ports**, so it **cannot expose the Flask HTTP port to the Internet**.
- Therefore the app must **not** be assumed to be "securely exposed" merely
  because it is on a Pi running Pi Connect. A naive `app.run(host="0.0.0.0")`
  is **not** an acceptable production deployment (spec section 11).

### Recommended production topologies (pick one)

1. **SSH tunnel (simplest, lowest attack surface).** Reach the app only over
   an SSH tunnel: `ssh -L 8080:127.0.0.1:8080 pi`. Nothing is exposed on the
   network; only an authenticated SSH session can read the feed.
2. **Private VPN / Tailscale + HTTPS.** Join the Pi to a private network
   (Tailscale is easy), serve over **HTTPS**, and put a reverse proxy
   (Caddy/Nginx) in front. HSTS + secure cookies are enabled automatically in
   this mode.
3. **Reverse proxy with auth.** Put Caddy/Nginx in front of the app,
   terminating TLS and (optionally) adding its own auth, and keep the Flask
   app bound to `127.0.0.1` only.

### What to **never** do
- Never `app.run(host="0.0.0.0")` to "just make remote work."
- Never rely on Pi Connect as the app's remote-access layer.
- Never expose GPIO/pin control to the Internet (it is internal only).
- Never commit `SECRET_KEY`, `DISCORD_WEBHOOK_URL`, `ADMIN_PASSWORD`, or the
  real `.env`.

---

## 5. Hardening checklist

- [ ] `APP_ENV=production` + a long random `SECRET_KEY`.
- [ ] Serve over **HTTPS** (via a reverse proxy or tunnel) so HSTS + secure
      cookies take effect.
- [ ] Set a strong `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
- [ ] Keep the Flask app bound to `127.0.0.1` (or a trusted interface) and put
      it behind the chosen tunnel/VPN/reverse-proxy.
- [ ] Confirm the **startup state is GREEN** (or your chosen private default).
- [ ] Keep `STREAM_MAX_VIEWERS` and `RATE_LIMIT_PER_MINUTE` at sane values.
- [ ] Verify **no secrets are logged** (check the redaction filter).
