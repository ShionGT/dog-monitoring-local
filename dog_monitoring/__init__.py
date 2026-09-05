"""Dog Monitoring System — secure Raspberry Pi camera monitor with LED/button control.

This package is designed to run both on a real Raspberry Pi 5 (Picamera2 + RPi.GPIO)
and on a developer's machine (e.g. macOS) in *mock* mode, where GPIO and the camera
are simulated so the whole application can be developed and tested without hardware.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
