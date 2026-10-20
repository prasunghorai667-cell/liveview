# 📍 Live Location Sharing

A dead-simple web app that lets a non-technical family member share their
live location with you through a single link — and lets you follow them on
Google Maps. No app install, no accounts.

## How it works

1. **You** run the server and open it in your browser.
2. Click **Create a Sharing Link** and copy the link.
3. Send the link to your family member (WhatsApp, SMS, email…).
4. **They** open it and:
   - Tap **Turn ON Location** → browser asks for permission → tap **Allow**.
   - Choose **SEND** to share their location, or **GET** to view yours.
5. Their position appears on Google Maps and updates live until they tap
   **STOP Sharing** (or you tap **STOP Watching**).

## Run it

```bash
python3 server.py
```

Then open:

- On this computer: `http://localhost:8000`
- On another phone (same Wi-Fi): `http://<your-LAN-IP>:8000`

The server prints the exact URLs on startup.

## ⚠️ Important: phones need HTTPS

Browsers only allow GPS location over **HTTPS** (or `localhost`). A plain
`http://192.168.x.x` link will work for you locally, but a phone will refuse
to give its location.

### Easiest fix — ngrok (free)

```bash
# in a second terminal
ngrok http 8000
```

ngrok prints a public `https://….ngrok-free.app` URL. Share **that** URL with
your family — it works everywhere, not just on your Wi-Fi.

## Files

| File | Purpose |
|------|---------|
| `server.py` | Zero-dependency Python server + SSE real-time relay |
| `static/index.html` | UI screens (home / permission / choose / send / get) |
| `static/style.css` | Large, elderly-friendly styling |
| `static/app.js` | Geolocation + Google Maps + live-stream logic |

## Notes

- Rooms auto-expire 30 minutes after the last location update.
- The map uses the free Google Maps embed — **no API key required**.
- Everything runs over plain HTTP with Server-Sent Events; no build step.
