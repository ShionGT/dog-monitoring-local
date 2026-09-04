"""Camera frame source abstraction and implementations.

The application never calls picamera2 directly. All frame fetching
goes through :class:`FrameSource`, which has two implementations:

- :class:`MockFrameSource` -- Pillow-based synthetic JPEG frames.
- :class:`Picamera2FrameSource` -- libcamera/Picamera2 on Raspberry Pi 5.
"""
from __future__ import annotations

import abc
import io
import time
from logging import getLogger

logger = getLogger("dog_monitoring.camera")


class FrameSource(abc.ABC):
    """Abstract frame source producing JPEG-encoded frames."""

    def __init__(self, width: int = 1280, height: int = 720,
                    quality: int = 80, fps: int = 5):
        self.width = width
        self.height = height
        self.quality = quality
        self.fps = fps
        self.running = False
        #: Last start/capture error, if any (surfaced via /api/health).
        self.last_error: str = ""

    @abc.abstractmethod
    def start(self) -> None:
        pass

    @abc.abstractmethod
    def stop(self) -> None:
        pass

    @abc.abstractmethod
    def capture_jpeg(self) -> bytes:
        pass

    @property
    def is_running(self) -> bool:
        return self.running


class MockFrameSource(FrameSource):
    """Pillow-based synthetic JPEG frames for dev / CI."""

    def __init__(self, width: int = 1280, height: int = 720,
                    quality: int = 80, fps: int = 5):
        super().__init__(width, height, quality, fps)
        self._frame = 0
        self._start = time.monotonic()

    def start(self) -> None:
        self.running = True
        self._start = time.monotonic()
        logger.info("MockFrameSource started (mock, %dx%d q=%d fps=%d)",
                    self.width, self.height, self.quality, self.fps)

    def stop(self) -> None:
        self.running = False
        logger.info("MockFrameSource stopped")

    def capture_jpeg(self) -> bytes:
        from PIL import Image, ImageDraw, ImageFont
        w, h = self.width, self.height
        img = Image.new("RGB", (w, h), color=(20, 20, 30))
        draw = ImageDraw.Draw(img)
        self._frame += 1
        elapsed = time.monotonic() - self._start
        # Gradient background (fast -- draw 8x8 blocks)
        for y in range(0, h, 16):
            for x in range(0, w, 16):
                s = (x + y) % 80
                draw.rectangle([x, y, x + 15, y + 15],
                            fill=(s, s + 10, s + 20))
        # 4px border
        for c in range(0, 3):
            color = (255, 200, 50) if c % 2 == 0 else (200, 50, 50)
            draw.rectangle([c, c, w - 1 - c, h - 1 - c],
                        outline=color, width=1)
        # Title & metadata
        try:
            f_big  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
            f_med  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
            f_sm   = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        except Exception:
            f_big = ImageFont.load_default()
            f_med = f_big
            f_sm  = f_big
        draw.text((16, 12), "DOG MONITOR",
                fill=(255, 255, 255), font=f_big)
        draw.text((16, 60), "MOCK CAMERA (synthetic feed)",
                fill=(200, 220, 255), font=f_med)
        draw.text((16, h - 28),
                f"Frame {self._frame} | {elapsed:.1f}s | {w}x{h}",
                fill=(180, 180, 200), font=f_sm)
        # Color test bars at bottom
        bar_h = 50
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (0, 255, 255), (255, 0, 255),
                (128, 128, 128), (255, 255, 255)]
        for i, col in enumerate(colors):
            x0 = (w // 8) * i
            draw.rectangle([x0, h - bar_h, x0 + w // 8, h], fill=col)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()


class WebcamFrameSource(FrameSource):
    """USB / built-in webcam via OpenCV (AVFoundation on macOS, V4L2 on Linux).

    This is the source to use when a *laptop webcam* (e.g. on your Mac)
    should feed the app instead of a Pi camera or the mock pattern.

    macOS note: OpenCV needs **camera permission** for the process that
    launches it (Terminal / iTerm / Python). The first time you start the
    app, macOS shows a "… would like to access the camera" dialog — click
    **Allow**. If you clicked *Don't Allow* (or the permission was denied),
    macOS silently refuses and OpenCV prints::

        OpenCV: not authorized to capture video (status 0)

    Fix it in **System Settings → Privacy & Security → Camera** by
    toggling the entry for your terminal app, then restart the app.

    The source is resilient: if a frame read fails (camera unplugged,
    permission revoked), it logs the error once and retries on subsequent
    captures instead of crashing the stream.
    """

    def __init__(self, width: int = 1280, height: int = 720,
                    quality: int = 80, fps: int = 5,
                    device_index: int = 0):
        super().__init__(width, height, quality, fps)
        self._device_index = device_index
        self._cap = None

    def _open(self):
        import cv2
        cap = cv2.VideoCapture(self._device_index)
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open webcam device {self._device_index}. "
                "On macOS this usually means the app running Python has not "
                "been granted Camera permission: System Settings → Privacy & "
                "Security → Camera → allow your terminal app, then restart."
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        return cap

    def start(self) -> None:
        try:
            self._cap = self._open()
            # Prime the pipeline so a permission problem surfaces now, not mid-stream.
            ok, _ = self._cap.read()
            if not ok:
                raise RuntimeError(
                    "Webcam opened but produced no frames — on macOS check "
                    "System Settings → Privacy & Security → Camera."
                )
            self.last_error = ""
            self.running = True
            logger.info(
                "WebcamFrameSource started (device=%d, %dx%d q=%d fps=%d)",
                self._device_index, self.width, self.height,
                self.quality, self.fps,
            )
        except Exception as exc:  # noqa: BLE001 — surface via last_error, don't crash the app
            self.last_error = str(exc)
            logger.error("WebcamFrameSource failed to start: %s", exc)

    def stop(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error releasing webcam capture: %s", exc)
            self._cap = None
        self.running = False
        logger.info("WebcamFrameSource stopped")

    def capture_jpeg(self) -> bytes:
        if self._cap is None or not self.running:
            raise RuntimeError(
                f"Webcam is not running ({self.last_error or 'not started'})"
            )
        ok, frame = self._cap.read()
        if not ok or frame is None:
            # Camera may have been unplugged / permission revoked — try to recover.
            logger.warning("Webcam frame read failed (%s); attempting reopen", self.last_error or "no data")
            try:
                self._cap.release()
            except Exception:  # noqa: BLE001
                pass
            self._cap = None
            try:
                self._cap = self._open()
                ok, frame = self._cap.read()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            if not ok or frame is None:
                raise RuntimeError(
                    f"Webcam capture failed ({self.last_error or 'no frame data'})"
                )
        import cv2 as _cv2

        encode_params = [int(_cv2.IMWRITE_JPEG_QUALITY), int(self.quality)]
        ok, buf = _cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            raise RuntimeError("Webcam JPEG encoding failed")
        return buf.tobytes()


class Picamera2FrameSource(FrameSource):
    """libcamera / Picamera2 JPEG source for Raspberry Pi 5."""

    def __init__(self, width: int = 1280, height: int = 720,
                    quality: int = 80, fps: int = 5,
                    camera_number: int = 0):
        super().__init__(width, height, quality, fps)
        self._cam_number = camera_number
        self._cam = None
        self._last_bytes = b""

    def start(self) -> None:
        try:
            import gc
            from picamera2 import Picamera2
            gc.collect()
            self._cam = Picamera2(self._cam_number)
            self._cam.configure("still",
                                size=(self.width, self.height),
                                quality=self.quality)
            self._cam.start()  # keep the sensor running; capture_still is non-blocking-ish
            self.last_error = ""
            self.running = True
            logger.info("Picamera2FrameSource started (%dx%d q=%d)",
                        self.width, self.height, self.quality)
        except ImportError as exc:
            raise RuntimeError(
                "picamera2 is not installed; install it on the Pi: pip install picamera2"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"Failed to open camera: {exc}"
            ) from exc

    def stop(self) -> None:
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception as exc:
                logger.warning("Error closing picamera2: %s", exc)
            self._cam = None
        self.running = False
        logger.info("Picamera2FrameSource stopped")

    def capture_jpeg(self) -> bytes:
        if self._cam is None or not self.running:
            raise RuntimeError("Camera is not running")
        try:
            stream = self._cam.capture_still(
                format="jpeg", quality=self.quality,
            )
            if stream is None:
                raise RuntimeError("picamera2 capture returned no data")
            self._last_bytes = stream.getvalue() if hasattr(stream, "getvalue") else bytes(stream)
            return self._last_bytes
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"picamera2 capture failed: {exc}") from exc
