"""Viewer registry -- tracks active live-stream viewers and their heartbeats.

The spec (section 14) requires:

* distinguish "camera enabled" (YELLOW) from "camera currently being watched"
(RED);
* track active web viewers so the RED LED accurately reflects whether there
is a live remote viewer;
* a sensible lifecycle:  browser loads page -> viewer connects -> frames
flow -> viewer disconnects;
* a heartbeat timeout so a viewer that has silently dropped (network loss,
browser closed without an SSE close event) is reaped automatically;
* a hard cap on simultaneous viewers (``stream_max_viewers``).

The registry fires two callbacks (``on_add`` / ``on_remove``) so the app can
couple live-viewer presence to the monitoring state machine without the
state machine knowing anything about HTTP.

This is deliberately dependency-light (no database, no Redis) -- a single
in-memory dict under a lock is perfectly adequate for a personal single-board
device.  Callbacks are fired *outside* the lock so a slow listener cannot
deadlock the registry.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .logging import getLogger

log = getLogger("dog_monitoring.viewers")


class ViewerRegistry:
    """Thread-safe registry of active live-stream viewers.

    each viewer is identified by an opaque string (typically the browser's
    session id or a short random UUID).  "Active" means the viewer
    registered *and* has not exceeded ``timeout_seconds`` since its last
    heartbeat.
    """

    def __init__(
        self,
        timeout_seconds: int = 5,
        sse_heartbeat: float = 15.0,
        max_viewers: int = 8,
        on_add: Optional[Callable[[str], None]] = None,
        on_remove: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._viewers: dict[str, float] = {}      # viewer_id -> last_seen ts
        self._lock = threading.Lock()
        self._timeout = max(1, int(timeout_seconds))
        self._sse_heartbeat = max(1.0, float(sse_heartbeat))
        self._max = max(1, int(max_viewers))
        self._on_add = on_add
        self._on_remove = on_remove
        self._stop = threading.Event()
        self._reaper = threading.Thread(
            target=self._reaper_loop, name="viewer-reaper", daemon=True
        )
        self._reaper.start()

    # ---- public API --------------------------------------------------
    def is_full(self) -> bool:
        with self._lock:
            self._prune_locked()
            return len(self._viewers) >= self._max

    def count(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._viewers)

    def register(self, viewer_id: str) -> bool:
        """Register (or re-heartbeat) a viewer.

        Returns True if the viewer is active (newly added *or* already
    present), False if the viewer cap has been reached for a *new*
    viewer.  Re-registering an existing viewer is idempotent and
    always succeeds (it just refreshes the heartbeat).
        """
        already_present = False
        rejected = False
        with self._lock:
            self._prune_locked()
            if viewer_id in self._viewers:
                already_present = True
                self._viewers[viewer_id] = time.time()
            elif len(self._viewers) >= self._max:
                rejected = True
                log.warning(
                    "Viewer cap reached (%d); rejecting new viewer %r",
                    self._max, viewer_id,
            )
            else:
                self._viewers[viewer_id] = time.time()
                log.info(
                    "Viewer %r connected (total=%d)",
                viewer_id, len(self._viewers),
            )
        if not already_present and not rejected:
            self._fire(self._on_add, viewer_id)
        return not rejected

    def heartbeat(self, viewer_id: str) -> bool:
        """Refresh a viewer's last-seen timestamp.

        Returns True if the viewer is still recognized, False if it was
    never registered or has already been reaped.
        """
        with self._lock:
            if viewer_id not in self._viewers:
                return False
            self._viewers[viewer_id] = time.time()
            return True

    def unregister(self, viewer_id: str) -> None:
        """Remove a viewer explicitly (e.g. on SSE disconnect).

        The ``on_remove`` callback fires (at most once, on the actual
    removal).
        """
        was_present = False
        with self._lock:
            was_present = viewer_id in self._viewers
            if was_present:
                del self._viewers[viewer_id]
                log.info(
                    "Viewer %r disconnected (total=%d)",
                    viewer_id, len(self._viewers),
            )
        if was_present:
            self._fire(self._on_remove, viewer_id)

    # ---- callbacks + lifecycle ----------------------------------------
    def set_callbacks(
        self,
        on_add: Optional[Callable[[str], None]],
        on_remove: Optional[Callable[[str], None]],
    ) -> None:
        self._on_add = on_add
        self._on_remove = on_remove

    def _reaper_loop(self) -> None:
        """Periodically prune viewers that missed their heartbeat window."""
        interval = max(0.5, min(5.0, self._timeout / 2.0))
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._prune_locked()
            except Exception:
                log.exception("reaper tick failed")
            self._stop.wait(timeout=interval)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._viewers.clear()

    # ---- helpers -----------------------------------------------------
    def _prune_locked(self) -> None:
        """Drop viewers past their heartbeat timeout.  Fires ``on_remove``
        for each reaped viewer *outside* the lock (collected first, so the
    lock is never held while a callback runs).

        Caller must hold ``self._lock``.
        """
        now = time.time()
        reaped = [
            vid for vid, ts in self._viewers.items()
            if now - ts > self._timeout
        ]
        if not reaped:
            return
        for vid in reaped:
            del self._viewers[vid]
            log.info("Viewer %r reaped (no heartbeat for %ds)", vid, self._timeout)
        # Fire callbacks outside the lock, after we've released the view.
        self._fire_batch(self._on_remove, reaped)

    def _fire(self, fn: Optional[Callable[[str], None]], viewer_id: str) -> None:
        if fn is None:
            return
        try:
            fn(viewer_id)
        except Exception:
            log.exception("Viewer callback for %r raised", viewer_id)

    def _fire_batch(
        self,
        fn: Optional[Callable[[str], None]],
        viewer_ids: list,
    ) -> None:
        if fn is None:
            return
        for vid in viewer_ids:
            self._fire(fn, vid)
