# Hardware — Raspberry Pi 5 Wiring & Setup

This document covers the physical hardware: the **3 LEDs**, the **1 push
button**, and the **camera**, wired to a **Raspberry Pi 5** and driven through
**GPIO Zero** (LEDs + button) and **picamera2 / libcamera** (camera).

> **All GPIO pins use BCM numbering** (e.g. "pin 17" = GPIO17 in the BCM
> scheme), *not* the physical pin number on the 40-pin header. This is the
> convention GPIO Zero and the spec both use.

---

## 1. Default pin assignments

The defaults (configurable via env in `.env`) are:

| Component       | Env var        | Default pin (BCM) |
|-----------------|----------------|-------------------|
| Red LED         | `RED_LED_PIN`  | **17**            |
| Yellow LED      | `YELLOW_LED_PIN` | **27**            |
| Green LED       | `GREEN_LED_PIN` | **22**            |
| Push button     | `BUTTON_PIN`   | **23**            |

These are safe, non-conflicting general-purpose pins on the Pi 5. **Verify
each pin against a current Raspberry Pi 5 pinout before wiring** — different
hardware revisions and any custom hat can repurpose a pin.

A sensible pin choice **must not** conflict with:
- The camera's CSI-2 ribbon (uses the dedicated camera connector, not GPIO).
- Any GPIO already in use by a HAT, display, or the Pi's own firmware
   (e.g. the on-board RGB LED on some boards is on GPIO pin 46 — avoid it).
- Pins needed for SPI/I2C if you plan to add them.

---

## 2. LED wiring

Each LED needs to be protected from over-current. A **current-limiting
resistor** in series with the LED is **required**: a LED fed straight into a
3.3 V GPIO will burn out or damage the pin.

### LED resistor calculation

The Pi 5 GPIO outputs **3.3 V**. A typical red LED forward voltage (Vf) is
~2.0 V; a yellow/orange LED ~2.2 V; a green LED ~3.0 V. The GPIO can sink
~16 mA max, so a **220–330 Ω** resistor is a safe, standard choice.

```
R = (Vsupply − Vf) / If
  = (3.3 − 2.0) / 0.015 A
  ≈ 87 Ω      → use a standard 220 Ω for a comfortable margin
```

### LED connection (active-high)

```
GPIO Pin (e.g. GPIO17)
      │
      │  ┌─────┐
      └──┤ 220Ω├────┬────┬────(+5V / 3V3 is not used; GPIO drives the LED)
          └─────┘    │
                     LED (anode → cathode)
                     cathode ──────── GND (pin 6/9/14/20/25/30/34/39/40)
```

In short:
- **GPIO pin → resistor → LED anode → LED cathode → GND**.
- The LED is **active-high**: when the GPIO is HIGH (3.3 V) the LED is lit;
  LOW = off.
- Use a **common GND** for all three LEDs and the button.

The application (via **`LedController`**) guarantees exactly **one** LED is on
at a time — the one matching the current `MonitoringState` (RED → red LED,
YELLOW → yellow LED, GREEN → green LED).

> **GPIO voltage note:** Pi 5 GPIOs are **3.3 V logic**. Do **not** connect a
> 5 V logic output directly to a GPIO input; a 3.3 V GPIO cannot tolerate 5 V
> input. (Our LEDs are driven at 3.3 V, so this is fine as long as you do
> not add extra 5 V hardware without a level shifter.)

---

## 3. Push button wiring

The button is an **active-low momentary push button with a pull-up**:

```
GPIO17 (e.g.)
      │
      │  ┌─────┐  (internal or external 10kΩ pull-up)
      └──┤ 10kΩ├── 3V3
          └──┬──┘
             │
         [Button pin A]
             │
         [Button pin B]
             │
            GND
```

- **Press** → GPIO driven LOW (button closes the circuit to GND).
- **Release** → pull-up drives GPIO HIGH again.
- **Debounce** is handled by GPIO Zero's `bounce=` parameter (default 0.05 s)
  so a single physical press is **never** treated as multiple presses
   (spec section 4).

When pressed, the button triggers `state_machine.cycle()`, which advances the
state `GREEN → YELLOW → RED → GREEN`.

> **Why the button *cycles* rather than selecting a specific state:** a single
> one-press cycle is the most intuitive for one physical button. A user who
> wants "private monitoring" presses once (GREEN→YELLOW); to get "live" they
> press twice (→ RED); to get "off" they press a third time (→ GREEN). This is
> the "preferred behavior" from the spec (section 4). The web UI provides
> *explicit* state selection for users who want precision.

---

## 4. Camera (Raspberry Pi Camera Module)

Use a **Raspberry Pi Camera v3** (or a v2 with the right driver) on the Pi 5.

### Enabling the camera

On Raspberry Pi OS **Bookworm** the camera is enabled through `raspi-config`:

```bash
sudo raspi-config
   → "Interface Options"
   → "Camera"     →  Enable
   → reboot
```

Then confirm it is detected:

```bash
libcamera-hello --list   # libcamera-based (current stack)
# or, with picamera2 installed:
python3 -c "from picamera2 import Picamera2; p=Picamera2(); print('camera ok')"
```

### The software stack: libcamera + picamera2

On **Bookworm** (and later) the camera is driven by **libcamera**, and the
recommended Python API is **picamera2** (this project uses
`Picamera2FrameSource`). The old `picamera` (v1) API is **deprecated** and does
**not** support the Pi 5.

`picamera2` (via libcamera) supports:
- still capture (`Picamera2().configure("still", ...)`)
- video streaming (not used here; the feed is built from stills for MVP simplicity)
- hardware rotation/resolution control

This project uses the **still capture** path for simplicity and compatibility.

### Camera is stopped when in GREEN

The `SharedCameraManager` / `Picamera2FrameSource` start the camera **only** in
RED and YELLOW; in **GREEN** the camera is physically **stopped** (no capture
runs), which is the privacy-critical behaviour of section 13. In mock mode the
"camera" is a synthetic image generator, so no hardware is required for
development.

---

## 5. Full wiring diagram (one page)

```
   40-pin header (Pi 5)                 Components
  ┌─────────────────────────┐        ┌───────────────────┐
  │  GPIO17  ───────────┐   │        │  Red LED + 220Ω    │
  │  GPIO27  ───────────┤   │        │  Yellow LED + 220Ω │
  │  GPIO22  ───────────┤   │        │  Green LED + 220Ω  │
  │                      │   │        │   (anode → GND)     │
  │  GPIO23  ────────────────────▶   │  Push button       │
  │  3V3     ───────────────────────▶│    (active-low,     │
  │                      │   │        │     pull-up to 3V3) │
  │  GND     ───────────────────────▶│    (cathode/pin B  │
  │           (common ground for      │      to GND)        │
  │         all LEDs + button)         │                     │
  └─────────────────────────┘        └───────────────────┘
        │
        └── Camera ribbon to dedicated CSI-2 camera connector (not GPIO)
```

---

## 6. Safety checklist (before power-on)

- [ ] **Every LED has a current-limiting resistor** in series (220–330 Ω).
- [ ] **The button has a pull-up** (10 kΩ — internal or external).
- [ ] **All grounds are connected** to a common GND pin.
- [ ] **Pins verified** against the current Pi 5 pinout (no conflict with the
     camera connector or other hardware).
- [ ] **No 5 V logic** is connected to a 3.3 V GPIO without a level shifter.
- [ ] **Button is debounced** (GPIO Zero `bounce=`, default 0.05 s — already set).
