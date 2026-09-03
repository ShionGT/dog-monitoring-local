"""Background detection engine -- motion + pet detection + Discord notify.

The spec (sections 15-16) requires a ``DetectionEngine`` abstraction and a
**background worker** that never blocks Flask or streaming.  This module
implements that worker as a daemon thread that:

1. Captures a JPEG frame from the :class:`FrameSource`.
2. Runs :class:`MotionDetector` on it.
3. If motion is seen and ``require_pet_detection`` is set, runs the
:class:`PetDetector` and checks it is a confident dog.
4. Checks the notification cooldown (thread-safe, never reset by continued
motion -- see :class:`DiscordNotifier`).
5. Builds the notification text and posts it via the
:class:`NotificationService`.

The worker sleeps in small 0.1s slices so ``stop()`` is responsive
(<= 0.2s latency) without busy-waiting.
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass
from logging import getLogger

from ..camera.frame_source import FrameSource
from .motion_detector import MotionDetector
from .notification import NotificationService
from .pet_detector import PetDetector

logger = getLogger("dog_monitoring.detection_engine")


@dataclass
class DetectionConfig:
    """Tunable detection parameters (mirrors the env vars in the spec)."""

    enabled: bool = False
    poll_interval_s: float = 1.0
    motion_threshold: int = 18
    motion_min_area: float = 0.01
    motion_min_changed: int = 25
    cooldown_seconds: int = 1200
    require_pet_detection: bool = True
    pet_confidence_min: float = 0.5


class DetectionEngine:
    """Background worker that ties the detection pipeline together.

    Call ``start()`` / ``stop()`` from a single thread (typically Flask
    init/teardown).  Reading :meth:`status` / :meth:`stats` is always
    safe from the request path.
    """

    def __init__(
        self,
        frame_source: FrameSource,
        notifier: NotificationService,
        pet_detector: PetDetector,
        motion: MotionDetector | None = None,
        config: DetectionConfig | None = None,
    ) -> None:
        self._source = frame_source
        self._notifier = notifier
        self._pet = pet_detector
        self._motion = motion or MotionDetector()
        self._cfg = config or DetectionConfig()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_event_s = 0.0
        self._motion_count = 0
        self._notify_count = 0
        self._notify_suppressed = 0

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._cfg.enabled is False:
            logger.info("Detection engine disabled (config.enabled=False)")
            return
        if self._thread is not None and self._thread.is_alive():
            logger.debug("Detection engine already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="detection-engine", daemon=True)
        self._thread.start()
        logger.info(
            "Detection engine started (poll=%.1fs, cooldown=%ds)",
            self._cfg.poll_interval_s, self._cfg.cooldown_seconds,
        )

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("Detection engine stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- main loop ------------------------------------------------------
    def _run(self) -> None:
        try:
            self._pet.warmup()
        except Exception as exc:
            logger.exception("Pet detector warmup failed: %s", exc)

        logger.info("Detection worker loop started")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Detection tick failed: %s", exc)
            remaining = self._cfg.poll_interval_s
            while remaining > 0 and not self._stop.is_set():
                time.sleep(min(0.1, remaining))
                remaining -= 0.1
        logger.info("Detection worker loop exited")

    # ---- per-tick pipeline ----------------------------------------------
    def _tick(self) -> None:
        """Run one pass of the pipeline.

        Motion -> pet detection -> cooldown -> build text -> send.
        Each stage may short-circuit the rest.
        """
        jpeg = self._source.capture_jpeg()
        if jpeg is None:
            logger.debug("No frame available for detection")
            return

        motion_result = self._motion.analyze(jpeg)
        if not motion_result.has_motion:
            return

        self._motion_count += 1
        logger.debug(
            "Motion detected (changed=%d, area=%.3f)",
            motion_result.changed_pixels, motion_result.area_fraction,
        )

        if self._cfg.require_pet_detection:
            detection = self._pet.detect(jpeg)
            if detection is None or detection.confidence < self._cfg.pet_confidence_min:
                return
            if not detection.is_dog:
                return
            pet_label = detection.label
            pet_conf = detection.confidence
        else:
            pet_label = "motion"
            pet_conf = 1.0

        if not self._notifier.can_send():
            self._notify_suppressed += 1
            logger.debug("Notification suppressed by cooldown")
            return

        text = self._build_message(pet_label, pet_conf, motion_result)
        ok = self._notifier.send(text, image_bytes=jpeg)
        if ok:
            self._notify_count += 1
            logger.info("Notification sent for %s (conf=%.2f)", pet_label, pet_conf)

    def _build_message(self, label: str, conf: float, motion) -> str:
        now = time.strftime("%Y-%m-%d %H:%M:%S +09:00", time.localtime())
        return (
            f"**Dog detected at {now}**\n"
            f"- Label: {label}\n"
            f"- Confidence: {conf:.2f}\n"
            f"- Motion area: {motion.area_fraction:.3f}\n"
            f"- Source: Raspberry Pi 5 monitoring camera\n"
            f"- Safety: if the dog is distressed, check its location or "
            f"switch to the GREEN state for a quick visual check."
        )

    # ---- read-only introspection (safe for the request path) ------------
    def status(self) -> dict:
        return {
            "enabled": self._cfg.enabled,
            "running": self.is_running,
            "poll_interval_s": self._cfg.poll_interval_s,
            "cooldown_seconds": self._cfg.cooldown_seconds,
            "last_event_at": self._last_event_s,
            "motion_events": self._motion_count,
            "notifications_sent": self._notify_count,
            "notifications_suppressed": self._notify_suppressed,
        }

    def stats(self) -> dict:
        return self._motion.stats
