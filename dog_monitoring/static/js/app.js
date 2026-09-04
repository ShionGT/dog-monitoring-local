/* Dog Monitor – frontend app.js
 * Sends state-change POST requests to the API, handles SSE updates,
 * and renders toasts and UI updates.
 *
 * Shape contract (mirrors the server's StateMachine.snapshot()):
 *   { state: "RED"|"YELLOW"|"GREEN", label, description,
 *     last_change: { old, new, timestamp, source } }
 */
(function () {
    "use strict";

    var csrf = window.__CSRF__ || "";
    var currentState = null;

    var STATE_TEXT = {
        RED: "Live streaming",
        YELLOW: "Private monitoring",
        GREEN: "Camera off"
    };

    // -- Helper: flash a toast notification -------------------------------------------
    function toast(msg, kind) {
        var t = document.createElement("div");
        t.className = "toast toast--" + (kind || "green");
        t.textContent = msg;
        document.body.appendChild(t);
        requestAnimationFrame(function () { t.classList.add("toast--show"); });
        setTimeout(function () {
            t.classList.remove("toast--show");
            setTimeout(function () { t.remove(); }, 400);
        }, 4000);
    }

    // -- Set a given state via the API -------------------------------------------------
    function setState(target) {
        var payload = new FormData();
        payload.set("state", target);
        payload.set("csrf_token", csrf);

        fetch("/api/state", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams(payload).toString(),
        })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
            if (res.ok) {
                onStateChanged(res.d.state, "web");
                toast("State set to " + res.d.state.state, res.d.state.state.toLowerCase());
            } else {
                toast(res.d.error || "Failed to set state", "red");
            }
        })
        .catch(function (e) { toast("Network error: " + e.message, "red"); });
    }

    // -- Render the feed area for a state ----------------------------------------------
    function renderFeed(state, snap) {
        var frame = document.querySelector(".feed__frame");
        if (!frame) return;

        if (state === "RED") {
            // Idempotent: if a live <img> is already streaming, keep it.
            // Re-creating it on every SSE echo would abort the MJPEG
            // connection, unregister the viewer, and make the server demote
            // RED -> YELLOW moments after the user clicked RED.
            var existing = frame.querySelector(".feed__video");
            if (existing && existing.src.indexOf("/live.mjpeg") !== -1) return;
            frame.innerHTML =
                '<img class="feed__video" src="/live.mjpeg?_=' +
                Date.now() + '" alt="Live camera feed" />';
        } else if (state === "YELLOW") {
            frame.innerHTML =
                '<div class="feed__placeholder">' +
                '  <p class="feed__msg">🔒 Private monitoring</p>' +
                '  <p class="feed__sub">Camera is active locally, but the live feed ' +
                'is not shown to remote viewers.</p>' +
                '  <p class="feed__hint">Set the state to <strong>LIVE</strong> to view the feed.</p>' +
                "</div>";
        } else {
            frame.innerHTML =
                '<div class="feed__placeholder">' +
                '  <p class="feed__msg">🟢 Camera Off</p>' +
                '  <p class="feed__sub">The camera is stopped. No frames are being ' +
                'captured and no feed is available.</p>' +
                '  <p class="feed__hint">Set the state to <strong>PRIVATE</strong> ' +
                'or <strong>LIVE</strong> to start the camera.</p>' +
                "</div>";
        }
    }

    // -- Apply a state change to the UI -------------------------------------------------
    function onStateChanged(snap, source) {
        if (!snap) return;
        var state = snap.state || (typeof snap === "string" ? snap : null);
        if (!state) return;
        if (state === currentState) return; // no-op: skip duplicate renders
        currentState = state;

        // Update the badge
        var badge = document.querySelector(".badge");
        if (badge) {
            badge.className = "badge badge--" + state.toLowerCase();
            badge.innerHTML =
                '<span class="badge__dot"></span>' +
                (snap.label || STATE_TEXT[state] || state);
        }

        // Update the feed frame + content
        var feed = document.querySelector(".feed__frame");
        if (feed) feed.setAttribute("data-state", state);
        renderFeed(state, snap);

        // Update the state list active class
        document.querySelectorAll(".state-item").forEach(function (el) {
            el.classList.toggle("state-item--active",
                el.getAttribute("data-state") === state);
        });

        // Update the feed status line
        var feedEl = document.getElementById("feedState");
        if (feedEl) feedEl.textContent = STATE_TEXT[state] || state;

        // Update info: source, last change, description
        var srcEl = document.getElementById("lastSource");
        if (srcEl) {
            srcEl.textContent = source || (snap.last_change && snap.last_change.source) || "—";
        }
        var lcEl = document.getElementById("lastChange");
        if (lcEl) {
            var ts = snap.last_change && snap.last_change.timestamp;
            lcEl.textContent = ts
                ? new Date(ts * 1000).toLocaleTimeString()
                : "—";
        }
        var descEl = document.getElementById("stateDesc");
        if (descEl) descEl.textContent = snap.description || "";
    }

    // -- Connect the SSE stream for automatic state updates -----------------------------------
    function connectSSE() {
        try {
            var es = new EventSource("/events");
            es.addEventListener("connected", function (e) {
                console.log("[SSE] Connected");
            });
            es.addEventListener("state_change", function (e) {
                var data;
                try { data = JSON.parse(e.data); } catch (_) { return; }
                onStateChanged(data, data.source || "server");
            });
            es.onerror = function (e) {
                console.warn("[SSE] Disconnected, auto-reconnecting...");
            };
        } catch (err) {
            console.error("EventSource unavailable:", err);
        }
    }

    // -- Wire up the three control buttons ---------------------------------------------------
    document.addEventListener("DOMContentLoaded", function () {
        var buttons = document.querySelectorAll(".btn[data-target]");
        buttons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var target = btn.getAttribute("data-target");
                if (target) setState(target);
            });
        });

        // Initial state from the server-rendered page
        var frame = document.querySelector(".feed__frame");
        if (frame) {
            currentState = frame.getAttribute("data-state") || currentState;
        }
        connectSSE();
    });

    // Expose for debug / console
    window.DogMonitor = { setState: setState, onStateChanged: onStateChanged };
})();
