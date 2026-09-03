"""Notification abstraction and the Discord webhook notifier.

The spec (sections 18-19) requires:

* a pluggable :class:`NotificationService` so Discord can later be
replaced with another provider;
* a **thread-safe 20-minute cooldown** (``NOTIFICATION_COOLDOWN_SECONDS``)
*not* reset by continued motion.

The cooldown is implemented as a single ``last_sent`` timestamp guarded by a
``threading.Lock``.  ``can_send`` / ``mark_sent`` are the only two places
that touch it, so the "motion continues -> keep ignoring -> fire only after
the window elapses" behaviour from the spec is guaranteed.
"""
from __future__ import annotations
import abc
import threading
import time
from logging import getLogger

try:
    import requests   # optional; only needed for the real DiscordNotifier
except ImportError:   # pragma: no cover - dev/machine without requests
    requests = None   # type: ignore

logger = getLogger("dog_monitoring.notifications")


class NotificationService(abc.ABC):
    """Abstract notification provider.

    ``send`` returns True on success and False on any failure (timeout,
    4xx/5xx, missing webhook, etc.).  Implementations must be safe to
    call from a single background worker thread.
    """

    @abc.abstractmethod
    def send(self, text: str, image_bytes: bytes | None = None) -> bool:
        """Deliver ``text`` (optionally with an attached screenshot).

        Return True if delivery succeeded, False otherwise.
        """
        ...

    @abc.abstractmethod
    def is_enabled(self) -> bool:
        """True if this provider is configured and should be used."""
        ...


class DiscordNotifier(NotificationService):
    """Concretely posts to a Discord webhook and enforces the cooldown.

    The cooldown is *independent* of continued motion: once a notification
    fires, no further notification can fire until ``cooldown_seconds`` have
    elapsed -- exactly the 10:00 -> 10:20+ behaviour in the spec.
    """

    def __init__(
        self,
        webhook_url: str,
        cooldown_seconds: int = 1200,
        timeout_s: float = 10.0,
    ) -> None:
        self._webhook_url = webhook_url
        self._cooldown = max(0, int(cooldown_seconds))
        self._timeout = max(1.0, float(timeout_s))
        self._lock = threading.Lock()
        self._last_sent = 0.0
        self._sent_ever = False
        self.total_sent = 0
        self.total_suppressed = 0

    def is_enabled(self) -> bool:
        return bool(self._webhook_url and self._webhook_url not in ("", "unset"))

    @property
    def seconds_since_last(self) -> float:
        with self._lock:
            if not self._sent_ever:
                return float("inf")
            return time.time() - self._last_sent

    def can_send(self, now: float | None = None) -> bool:
        """True if the cooldown has elapsed (or no message was ever sent).

        Thread-safe.  Does *not* advance the cooldown; call ``mark_sent``
        to do that.
        """
        with self._lock:
            if not self._sent_ever:
                return True
            if now is None:
                now = time.time()
            elapsed = now - self._last_sent
            return elapsed >= self._cooldown

    def mark_sent(self, now: float | None = None) -> float:
        """Record that a message was sent at ``now`` (or ``time.time()``).

        Returns the new ``last_sent`` timestamp.
        """
        with self._lock:
            if now is None:
                now = time.time()
            self._last_sent = now
            self._sent_ever = True
            self.total_sent += 1
            return self._last_sent

    def record_suppressed(self) -> None:
        with self._lock:
            self.total_suppressed += 1

    def send(self, text: str, image_bytes: bytes | None = None) -> bool:
        """Send ``text`` (and optional JPEG screenshot) to Discord.

        Enforces the cooldown: if suppressed, returns False and does
        *not* reset the timer (motion continuing must not extend it).
        """
        if not self.can_send():
            self.record_suppressed()
            logger.debug("notification suppressed by cooldown")
            return False
        if not self.is_enabled():
            logger.warning("Discord webhook not configured; skipping send")
            return False
        if requests is None:
            logger.error("'requests' not installed; cannot send Discord message")
            return False
        try:
            files = None
            if image_bytes:
                files = {"file": ("screenshot.jpg", image_bytes, "image/jpeg")}
            resp = requests.post(
                self._webhook_url,
                data={"content": text},
                files=files,
                timeout=self._timeout,
            )
        except Exception as exc:   # pragma: no cover - network path
            logger.exception("Discord webhook request failed: %s", exc)
            return False
        if 200 <= resp.status_code < 300:
            self.mark_sent()
            logger.info("Discord notification sent (status=%d)", resp.status_code)
            return True
        logger.warning(
            "Discord webhook returned HTTP %d: %s",
            resp.status_code, resp.text[:200],
        )
        return False

    def stats(self) -> dict:
        with self._lock:
            never = not self._sent_ever
            return {
                "total_sent": self.total_sent,
                "total_suppressed": self.total_suppressed,
                "seconds_since_last": (
                    None if never else round(time.time() - self._last_sent, 1)
                    ),
            }


class NullNotifier(NotificationService):
    """A no-op notifier for tests and for when notifications are disabled."""

    def send(self, text: str, image_bytes: bytes | None = None) -> bool:
        return True

    def is_enabled(self) -> bool:
        return False
