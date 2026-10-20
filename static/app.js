/* ============================================================
   Live Location Sharing – client logic
   ============================================================ */

(() => {
  "use strict";

  // ---- DOM shorthands ------------------------------------------------------
  const $ = (id) => document.getElementById(id);

  const screens = {
    home:       $("screen-home"),
    permission: $("screen-permission"),
    choose:     $("screen-choose"),
    send:       $("screen-send"),
    get:        $("screen-get"),
  };

  function show(name) {
    Object.values(screens).forEach((s) => s.classList.add("hidden"));
    screens[name].classList.remove("hidden");
  }

  // ---- Room id from URL ----------------------------------------------------
  //   /r/<ROOM>   → we are in a shared room
  //   /           → home screen
  const pathParts = location.pathname.split("/").filter(Boolean);
  const roomId = pathParts[0] === "r" ? pathParts[1] : null;

  // ---- State ---------------------------------------------------------------
  let watchId       = null;     // navigator.geolocation.watchPosition id
  let sse           = null;     // EventSource
  let lastSendTs    = 0;
  let currentRoom   = roomId;

  // ===================================================================
  //  HOME SCREEN — create a room
  // ===================================================================
  if (!roomId) {
    show("home");

    $("btn-create").addEventListener("click", async () => {
      $("btn-create").disabled = true;
      $("btn-create").textContent = "Creating…";
      try {
        const res  = await fetch("/api/room", { method: "POST" });
        const data = await res.json();
        currentRoom = data.room;

        const url  = `${location.origin}/r/${currentRoom}`;
        $("link-text").textContent = url;
        $("btn-open-own").href = url;
        $("created-link").classList.remove("hidden");
        $("btn-create").classList.add("hidden");

        $("btn-copy").addEventListener("click", () => {
          navigator.clipboard.writeText(url)
            .then(() => { $("btn-copy").textContent = "✅ Copied!"; })
            .catch(() => { $("btn-copy").textContent = "⚠ Select & copy manually"; });
        });
      } catch (err) {
        alert("Could not create a link. Please try again.");
        $("btn-create").disabled = false;
        $("btn-create").textContent = "🔗 Create a Sharing Link";
      }
    });
    return;                       // nothing else to do on home screen
  }

  // ===================================================================
  //  ROOM SCREENS — permission → choose → send / get
  // ===================================================================

  // ---- Step 1: request location permission --------------------------
  show("permission");

  $("btn-allow").addEventListener("click", () => {
    if (!navigator.geolocation) {
      showPermissionError("This browser does not support GPS location.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      () => {                       // success
        $("permission-error").classList.add("hidden");
        show("choose");
      },
      (err) => {                    // error
        showPermissionError(describeGeoError(err));
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  });

  function showPermissionError(msg) {
    const box = $("permission-error");
    box.innerHTML = `<strong>Location is OFF.</strong><br/>${msg}`;
    box.classList.remove("hidden");
  }

  function describeGeoError(err) {
    switch (err.code) {
      case err.PERMISSION_DENIED:
        return "You blocked the permission. Open your phone&rsquo;s " +
               "<b>Settings &rarr; Apps &rarr; Browser &rarr; Permissions " +
               "&rarr; Location</b> and turn it on, then reload this page.";
      case err.POSITION_UNAVAILABLE:
        return "GPS signal not found. Go near a window or outdoors and try again.";
      case err.TIMEOUT:
        return "It took too long to find your position. Tap the button again.";
      default:
        return "Something went wrong. Tap the button again.";
    }
  }

  // ---- Step 2: choose SEND or GET ----------------------------------
  $("btn-send").addEventListener("click", startSend);
  $("btn-get").addEventListener("click",  startGet);

  // ===================================================================
  //  SEND MODE
  // ===================================================================
  function startSend() {
    show("send");

    const mapEl = $("map-send");
    let lastSrc = "";

    watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude: lat, longitude: lng, accuracy } = pos.coords;
        const timestamp = pos.timestamp;

        // throttle to 1 update / 1.5 s max
        const now = Date.now();
        if (now - lastSendTs < 1500) return;
        lastSendTs = now;

        // 1. push to server
        fetch(`/api/room/${currentRoom}/location`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lat, lng, accuracy, timestamp }),
        }).catch(() => { /* offline – will retry next tick */ });

        // 2. update map iframe (only if coords changed enough to matter)
        updateMap(mapEl, lat, lng, lastSrc, (src) => { lastSrc = src; });

        // 3. text info
        $("send-coords").textContent =
          `${lat.toFixed(6)}, ${lng.toFixed(6)}  (±${Math.round(accuracy)} m)`;
        $("send-time").textContent =
          "Updated " + new Date(timestamp).toLocaleTimeString();
      },
      (err) => {
        alert("Could not get your location: " + describeGeoError(err));
        stopSend();
      },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 20000 }
    );

    $("btn-stop-send").addEventListener("click", stopSend);
  }

  function stopSend() {
    if (watchId !== null) {
      navigator.geolocation.clearWatch(watchId);
      watchId = null;
    }
    show("choose");
  }

  // ===================================================================
  //  GET MODE  (SSE stream)
  // ===================================================================
  function startGet() {
    show("get");
    $("get-waiting").classList.remove("hidden");
    $("get-coords").textContent  = "";
    $("get-time").textContent    = "";

    const mapEl = $("map-get");
    let lastSrc = "";

    sse = new EventSource(`/api/room/${currentRoom}/stream`);

    sse.onmessage = (ev) => {
      $("get-waiting").classList.add("hidden");
      let loc;
      try { loc = JSON.parse(ev.data); } catch { return; }

      updateMap(mapEl, loc.lat, loc.lng, lastSrc, (src) => { lastSrc = src; });

      $("get-coords").textContent =
        `${loc.lat.toFixed(6)}, ${loc.lng.toFixed(6)}` +
        (loc.accuracy ? `  (±${Math.round(loc.accuracy)} m)` : "");
      $("get-time").textContent =
        "Updated " + new Date(loc.timestamp || Date.now()).toLocaleTimeString();
    };

    sse.onerror = () => {
      // EventSource auto-reconnects; show a hint if it stays down
      $("get-waiting").innerHTML =
        '<span class="spinner"></span> Reconnecting…';
    };

    $("btn-stop-get").addEventListener("click", stopGet);
  }

  function stopGet() {
    if (sse) { sse.close(); sse = null; }
    show("choose");
  }

  // ===================================================================
  //  Google Maps helper  (embed URL, no API key needed)
  // ===================================================================
  function updateMap(container, lat, lng, lastSrc, setLast) {
    // Round to ~11 cm so tiny GPS jitter does not reload the iframe
    const rLat = lat.toFixed(5);
    const rLng = lng.toFixed(5);
    const src =
      `https://www.google.com/maps?q=${rLat},${rLng}&z=16&output=embed`;

    if (src === lastSrc) return;          // no visible change → skip reload
    setLast(src);

    // First time → create iframe; later → swap src
    const existing = container.querySelector("iframe");
    if (existing) {
      existing.src = src;
    } else {
      const iframe = document.createElement("iframe");
      iframe.src = src;
      iframe.loading = "lazy";
      iframe.referrerPolicy = "no-referrer-when-downgrade";
      iframe.allowFullscreen = true;
      iframe.title = "Live location map";
      container.appendChild(iframe);
    }
  }

})();
