"""SSE event bus for pushing live monitoring-state changes to browsers.

The spec (section 10) requires the web UI to update automatically when state is
changed by any source (the physical button, the detection engine, an admin from
another browser) *without* a manual refresh.  Server-Sent Events (SSE) is the
simplest fit: it's a one-directional, HTTP-native, persistent text stream that
the browser consumes with ``EventSource``, avoiding the complexity of WebSockets
for this use case.

The design:
* :class:`SSEBus` is a small pub/sub with per-subscriber ``queue.Queue``s.
* A *listener* on the state machine (registered in ``app.py``) publishes a
    JSON event whenever the state changes.
* The ``/sse`` route calls :meth:`SSEBus.stream` and writes each event as
SSE-formatted lines to the HTTP response.
* A heartbeat is emitted every ``heartbeat_interval`` seconds so the
connection does not go stale through a proxy.
* The bus is *never* a source of state — it only *conveys* the current
    state, which is always owned by the :class:`MonitorStateMachine`.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Iterator, Optional

from .logging import getLogger

log = getLogger("dog_monitoring.sse")


class SSEBus:
    """A tiny, thread-safe pub/sub for server-sent events.

    Subscribers are identified by an integer id.  Each subscriber has its
    own ``queue.Queue`` that the SSE route drains in a generator.  The queue
    is bounded to prevent a slow consumer from unbounded back-pressure.
    """

    def __init__(self, max_queue: int = 32, heartbeat_interval: int = 15) -> None:
        self._subscribers: dict[int, "queue.Queue"] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        self._max_queue = max(1, max_queue)
        self._heartbeat = max(1, heartbeat_interval)
        self._stop = threading.Event()

    # ---- subscription management --------------------------------------
    def subscribe(self) -> int:
        """Register a new subscriber and return its integer id."""
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            self._subscribers[sid] = queue.Queue(maxsize=self._max_queue)
        log.info("SSE subscriber %d registered (total=%d)", sid, len(self._subscribers))
        return sid

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subscribers.pop(sid, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ---- publishing -----------------------------------------------
    def publish(self, data: dict | str, event: str | None = None) -> None:
        """Fan ``data`` out to every subscribed queue.

        ``data`` is JSON-serialised (or passed through if already a string);
    ``event`` is the SSE event name (defaults to ``"message"``).
            """
        # Serialize *once* so every subscriber gets the same payload.
        try:
            payload = json.dumps(data) if not isinstance(data, str) else str(data)
        except (TypeError, ValueError):
            payload = json.dumps({"raw": repr(data)})

        with self._lock:
            queues = list(self._subscribers.values())

        for q in queues:
            # Build the full SSE frame (event + data + double newline terminator).
            event_name = event or "message"
            frame = f"event: {event_name}\ndata: {payload}\n\n"
            try:
                q.put_nowait(frame)
            except queue.Full:
                # Drop the oldest, then try once more.
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(frame)
                except queue.Full:
                    log.warning("Dropping SSE frame: subscriber queue full")

    # ---- streaming side ---------------------------------------------
    def stream(self, sid: int) -> Iterator[str]:
        """Yield an iterator of SSE text frames for subscriber ``sid``.

        The generator blocks (non-busy-wait) on the subscriber queue; a
        heartbeat is emitted every ``heartbeat_interval`` seconds to keep the
        connection warm through proxies.  The generator exits when
        :meth:`close_stream` is called (typically from a Flask
        ``after_request`` or ``teardown`` hook).
        """
        if sid not in self._subscribers:
            raise KeyError(f"unknown subscriber id {sid}")
        q = self._subscribers[sid]
        last_beat = time.time()
        try:
            while not self._stop.is_set():
                try:
                    frame = q.get(timeout=1.0)
                    yield frame
                    last_beat = time.time()
                    continue
                except queue.Empty:
                    if time.time() - last_beat >= self._heartbeat:
                        # Emit an SSE *comment* (line starting with ':') so the
                        #  browser's EventSource doesn't treat the gap as a
                        #  connection drop.  We send a blank comment line plus
                        #  a newline so the HTTP stream stays active.
                        last_beat = time.time()
                        yield ": heartbeat\n\n"
        except GeneratorExit:
            # The caller closed the generator — just clean up.
            pass
        finally:
            log.debug("SSE stream for subscriber %d closed", sid)

    # ---- lifecycle ---------------------------------------------------
    def close(self, timeout: float = 2.0) -> None:
        """Signal all outstanding streams to stop waiting.

        Used on app teardown so that any in-flight SSE connection is
        released promptly.
            """
        self._stop.set()
        # Drain a sentinel frame into every subscriber so any blocked
        #  ``q.get`` returns immediately.
        with self._lock:
            queues = list(self._subscribers.values())
        for q in queues:
            try:
                q.put_nowait(": end\n\n")
            except queue.Full:
                pass
        if timeout > 0:
            time.sleep(min(0.2, timeout))
        with self._lock:
            self._subscribers.clear()
            self._stop.clear()
