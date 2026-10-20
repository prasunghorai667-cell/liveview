#!/usr/bin/env python3
"""
Live Location Sharing Server
============================
A zero-dependency Python server that lets a family member share their
live location with you through a simple link.

How it works
------------
1.  Open  http://<your-ip>:8000/            in YOUR browser.
    Click  "Create a Sharing Link".
2.  Copy the generated link and send it to your family member.
3.  Your family member opens the link, taps the big green button to
    allow location, then taps  "SEND my location".
4.  You open the SAME link in your browser and tap  "GET location".
    Their position appears on Google Maps and keeps refreshing until
    they tap  "STOP".

Run
---
    python3 server.py
Then open http://localhost:8000  (or http://<your-LAN-IP>:8000 from
another phone on the same Wi-Fi).

IMPORTANT – HTTPS
----------------
Browsers only allow the Geolocation API over HTTPS (or localhost).
For phones on your LAN you have two easy options:

  A)  Use ngrok  (free, recommended)
      �ngrok http 8000
      …it prints a public https:// URL.  Share THAT with your family.

  B)  Run a tiny HTTPS proxy in front of this server (see below).

For localhost testing everything works over plain http.
"""

import json
import os
import queue
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# A room is deleted if nobody has sent an update for this many seconds
ROOM_TTL = 30 * 60          # 30 minutes

# ---------------------------------------------------------------------------
# Room registry  (shared state, guarded by a lock)
# ---------------------------------------------------------------------------

_rooms: dict[str, dict] = {}
_lock = threading.Lock()


def _get_room(room_id: str) -> dict:
    """Return the room dict for *room_id*, creating it if needed."""
    with _lock:
        room = _rooms.get(room_id)
        if room is None:
            room = {
                "location": None,          # last posted location dict
                "updated_at": None,        # monotonic timestamp
                "subscribers": set(),      # set[queue.Queue]
            }
            _rooms[room_id] = room
        return room


def _touch(room: dict, location: dict) -> None:
    """Store a new location and fan-out to every SSE subscriber."""
    room["location"] = location
    room["updated_at"] = time.monotonic()
    dead = []
    for q in room["subscribers"]:
        try:
            q.put_nowait(location)
        except queue.Full:
            dead.append(q)
    for q in dead:
        room["subscribers"].discard(q)


def _cleaner() -> None:
    """Background thread – drop rooms that have gone stale."""
    while True:
        time.sleep(60)
        now = time.monotonic()
        with _lock:
            stale = [
                rid for rid, r in _rooms.items()
                if r["updated_at"] is None
                or now - r["updated_at"] > ROOM_TTL
            ]
            for rid in stale:
                room = _rooms.pop(rid, None)
                if room:
                    for q in room["subscribers"]:
                        try:
                            q.put_nowait(None)          # sentinel → close stream
                        except queue.Full:
                            pass


threading.Thread(target=_cleaner, daemon=True).start()

# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---- helpers ----------------------------------------------------------

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filepath, content_type):
        try:
            with open(filepath, "rb") as fh:
                data = fh.read()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):          # quieter logs
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- GET --------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # --- static files --------------------------------------------------
        if path in ("/", "/index.html"):
            return self._send_file(os.path.join(STATIC_DIR, "index.html"),
                                   "text/html; charset=utf-8")
        if path == "/style.css":
            return self._send_file(os.path.join(STATIC_DIR, "style.css"),
                                   "text/css; charset=utf-8")
        if path == "/app.js":
            return self._send_file(os.path.join(STATIC_DIR, "app.js"),
                                   "application/javascript; charset=utf-8")
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # --- room page -----------------------------------------------------
        #   /r/<ROOM_ID>   → serve the same index.html (client-side routes)
        if path.startswith("/r/"):
            return self._send_file(os.path.join(STATIC_DIR, "index.html"),
                                   "text/html; charset=utf-8")

        # --- API: create a new room ----------------------------------------
        if path == "/api/room":
            room_id = secrets.token_urlsafe(6)   # short, shareable
            _get_room(room_id)
            return self._send_json({"room": room_id}, 201)

        # --- API: fetch last known location --------------------------------
        if path.startswith("/api/room/") and path.endswith("/location"):
            room_id = path[len("/api/room/"):-len("/location")]
            room = _get_room(room_id)
            return self._send_json({
                "location": room["location"],
                "viewers": len(room["subscribers"]),
            })

        # --- API: SSE stream ------------------------------------------------
        if path.startswith("/api/room/") and path.endswith("/stream"):
            room_id = path[len("/api/room/"):-len("/stream")]
            return self._sse_stream(room_id)

        self._send_json({"error": "not found"}, 404)

    # ---- POST -------------------------------------------------------------

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # --- API: create a new room (also accepted via POST) ---------------
        if path == "/api/room":
            room_id = secrets.token_urlsafe(6)
            _get_room(room_id)
            return self._send_json({"room": room_id}, 201)

        if path.startswith("/api/room/") and path.endswith("/location"):
            room_id = path[len("/api/room/"):-len("/location")]
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw)
                lat = float(payload["lat"])
                lng = float(payload["lng"])
            except (ValueError, KeyError, json.JSONDecodeError):
                return self._send_json({"error": "invalid payload"}, 400)

            location = {
                "lat": lat,
                "lng": lng,
                "accuracy": payload.get("accuracy"),
                "timestamp": payload.get("timestamp", time.time() * 1000),
            }
            room = _get_room(room_id)
            _touch(room, location)
            return self._send_json({"ok": True})

        self._send_json({"error": "not found"}, 404)

    # ---- SSE --------------------------------------------------------------

    def _sse_stream(self, room_id: str):
        room = _get_room(room_id)
        q: queue.Queue = queue.Queue(maxsize=32)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")   # nginx-friendly
        self.end_headers()

        with _lock:
            room["subscribers"].add(q)

        try:
            # Send the last known location immediately (if any)
            if room["location"] is not None:
                data = json.dumps(room["location"])
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()

            while True:
                try:
                    item = q.get(timeout=25)
                except queue.Empty:
                    # heartbeat comment keeps proxies from killing the conn
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue

                if item is None:               # room cleaned up → close
                    break
                data = json.dumps(item)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _lock:
                room["subscribers"].discard(q)
            try:
                self.wfile.flush()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True

    # Figure out LAN IPs so the user can open the link from another phone
    import socket
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    if not ips or ips[0].startswith("127."):
        ips = ["localhost"]

    print("=" * 60)
    print("  LIVE LOCATION SHARING")
    print("=" * 60)
    print(f"  Open in THIS computer :  http://localhost:{PORT}")
    for ip in ips:
        print(f"  Open on another phone :  http://{ip}:{PORT}")
    print()
    print("  NOTE: phones need HTTPS for location to work.")
    print("  Easiest fix – run:  ngrok http", PORT)
    print("  …then share the https URL it prints.")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
        server.server_close()


if __name__ == "__main__":
    main()
