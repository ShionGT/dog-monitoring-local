/* Dog Monitor – frontend app.js
 * Sends state-change POST requests to the API, handles SSE updates,
 * and renders toasts and UI updates.
 */
(function () {
     "use strict";

     var csrf = window.__CSRF__ || "";
     var currentState = null;

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
                    // Update the UI to match the new state
                    onStateChanged(res.d.state, "web");
                    toast("State set to " + res.d.state.state, res.d.state.state.toLowerCase());
               } else {
                    toast(res.d.error || "Failed to set state", "red");
               }
          })
          .catch(function (e) { toast("Network error: " + e.message, "red"); });
     }

     // -- Apply a state change to the UI -------------------------------------------------
     function onStateChanged(snap, source) {
          var state = snap.state || snap;
          currentState = typeof state === "string" ? state : state.state;
          if (!currentState) return;

          // Update the badge
          var badge = document.querySelector(".badge");
          if (badge) {
               badge.className = "badge badge--" + currentState.toLowerCase();
               badge.innerHTML =
                    '<span class="badge__dot"></span>' +
                    snap.label || currentState;
          }

          // Update the feed frame data-state
          var feed = document.querySelector(".feed__frame");
          if (feed) feed.setAttribute("data-state", currentState);

          // Update the state list active class
          document.querySelectorAll(".state-item").forEach(function (el) {
               el.classList.toggle("state-item--active",
                   el.getAttribute("data-state") === currentState);
          });

          // Update the feed content
          var feedEl = document.getElementById("feedState");
          if (feedEl) feedEl.textContent = currentState;

          // Refresh the live image (only in RED state)
          if (currentState === "RED") {
               var frame = document.querySelector(".feed__frame");
               if (frame) {
                    frame.innerHTML =
                         '<img class="feed__video" src="/live.mjpeg?_=' +
                         Date.now() + '" alt="Live camera feed" />';
               }
          }

          // Update info: source and last change
          var srcEl = document.getElementById("lastSource");
          if (srcEl && source) srcEl.textContent = source;
          var lcEl = document.getElementById("lastChange");
          if (lcEl && snap.last_change) {
               lcEl.textContent = new Date(snap.last_change.timestamp * 1000).toLocaleTimeString();
           }
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
          var badge = document.querySelector(".badge");
          if (badge) {
               // Extract state from the page (already rendered by Jinja server-side)
               var stateFromBadge = "";
               if (badge.classList.contains("badge--red"))    stateFromBadge = "RED";
               else if (badge.classList.contains("badge--yellow")) stateFromBadge = "YELLOW";
               else if (badge.classList.contains("badge--green"))  stateFromBadge = "GREEN";
               if (stateFromBadge) currentState = stateFromBadge;
          }
          connectSSE();
     });

     // Expose for debug / console
     window.DogMonitor = { setState: setState, onStateChanged: onStateChanged };
})();
