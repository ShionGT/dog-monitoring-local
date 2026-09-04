"""Live video and status streams.

Two routes:

* ``/live.mjpeg`` -- a multipart/x-mixed-replace MJPEG stream built from the
shared frame source (spec section 7: one camera pipeline feeds many
viewers, not one pipeline per viewer).
* ``/events`` -- a Server-Sent Events (SSE) stream that pushes state changes
to the UI in real time (spec section 10).

Both are gated by the monitoring state (spec section 13): the live video
feed is only served while the state is RED. In GREEN the camera is off; in
YELLOW the camera is active locally but the feed is not exposed to remote
viewers.

Implementation note
-------------------
Streaming generators run after the Flask request context is torn down, so
they must not reach into flask.g. Every component they need (state machine,
camera, viewer registry, SSE bus) is captured in the view function while g is
still valid and passed into the generator.
"""
from __future__ import annotations

import time
import uuid
from queue import Empty, Queue

from flask import Blueprint, Response, g, session

from ..logging import getLogger
from ..state.monitoring_state import MonitoringState

log = getLogger("dog_monitoring.routes.stream")

bp = Blueprint("stream", __name__)

_BOUNDARY = b"--dogmon-frame"
_CONTENT_TYPE = "multipart/x-mixed-replace; boundary=" + _BOUNDARY.decode()
_STREAM_FPS = 15.0
_HEARTBEAT_INTERVAL = 2.0


def _encode_mjpeg_frame(frame: bytes) -> bytes:
    """Wrap a raw JPEG in one multipart/x-mixed-replace frame."""
    return (
        b"--"
        + _BOUNDARY
        + b"\r\n"
        + b"Content-Type: image/jpeg\r\n"
        + b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
        + frame
        + b"\r\n"
    )


def _mjpeg_gen(viewer_id, state_machine, camera, viewers):
    """Yield MJPEG frames, ending if the state leaves RED."""
    last_beat = time.time()
    try:
        while True:
            if state_machine.state != MonitoringState.RED:
                log.info(
                    "Live stream ending for viewer %r (state=%s)",
                    viewer_id,
                    state_machine.state.name,
                )
                break

            if time.time() - last_beat >= _HEARTBEAT_INTERVAL:
                viewers.heartbeat(viewer_id)
                last_beat = time.time()

            try:
                frame = camera.capture_jpeg()
            except Exception as exc:
                log.warning("capture_jpeg failed: %s", exc)
                time.sleep(0.5)
                continue

            if not frame:
                time.sleep(0.2)
                continue

            yield _encode_mjpeg_frame(frame)

            time.sleep(1.0 / _STREAM_FPS)
    finally:
        log.info("Viewer unregistered on stream end: %r", viewer_id)
        try:
            viewers.unregister(viewer_id)
        except Exception:
            pass


@bp.get("/live.mjpeg")
def live_mjpeg():
    """Serve the live MJPEG feed (only while the state is RED)."""
    state_machine = g.state_machine
    camera = g.camera
    viewers = g.viewers

    if state_machine.state != MonitoringState.RED:
        log.info(
            "live.mjpeg refused: state is %s (need RED)",
            state_machine.state.name,
        )
        return (
            "Live video is only available in the RED state. "
            "Current state: %s." % state_machine.state.name,
            403,
        )

    if viewers.is_full():
        return "Too many concurrent viewers. Please try again shortly.", 503

    viewer_id = "%s-%s" % (session.get("_sid", "anon"), uuid.uuid4().hex[:8])
    viewers.register(viewer_id)

    return Response(
        _mjpeg_gen(viewer_id, state_machine, camera, viewers),
        headers={
            # Set the Content-Type ONLY here, WITH the boundary — browsers
            # cannot parse multipart/x-mixed-replace without it (the <img>
            # renders nothing and the connection is dropped). Do NOT also
            # pass mimetype= to Response, or Flask's default would win and
            # strip the boundary.
            "Content-Type": _CONTENT_TYPE,
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@bp.get("/events")
def events():
    """Server-Sent Events stream that pushes state changes to the UI."""
    bus = g.bus
    sub_id = bus.subscribe()

    def gen():
        yield 'event: connected\ndata: {"ok": true}\n\n'
        q = bus._subscribers[sub_id]
        try:
            while True:
                try:
                    frame = q.get(timeout=1.5)
                    yield frame
                except (Empty, TimeoutError):
                    yield ": heartbeat\n\n"
        finally:
            bus.unsubscribe(sub_id)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
