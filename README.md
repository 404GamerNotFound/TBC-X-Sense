# TBC-X-Sense

Two [TBC-camera-manager](https://github.com/404GamerNotFound/TBC-camera-manager) plugins for
X-Sense cameras (models **SSC0A**/**SSC0B**), installed from this one repository via two
separate "standard sources" (`Admin -> External sources`), each pointing at a different
subdirectory:

- **`cloud/`** - a *cloud provider* plugin. Logs into an X-Sense account and lists its cameras
  (serial number, name, model) for inventory. It does **not** offer a one-click "Add as camera"
  button - see "Why no automatic import?" below.
- **`camera/`** - a *camera* plugin that does the actual streaming. Add a camera manually
  (`+ Camera`), pick the **X-Sense** module, and fill in:
  - **Username** / **Password**: your X-Sense account email and password (not a camera-specific
    login - X-Sense cameras don't have one).
  - **X-Sense serial number** (the field TBC normally labels "Host / IP" - X-Sense cameras have
    no local IP address, everything goes through X-Sense's cloud, so this field holds the
    camera's serial number instead; the "Discover devices" flow via the cloud plugin lists it
    for you).
  - The ONVIF/HTTP/RTSP port fields are unused by this module - leave them at their defaults.

## Important: this is unofficial and reverse-engineered

**X-Sense has no public developer API** (confirmed directly by X-Sense's own team on the
[Home Assistant community forum](https://community.home-assistant.io/t/x-sense-security-is-it-possible-to-create-an-integration/534119)).
Everything this plugin does is reverse-engineered from the X-Sense Android app, including a
fixed device-identity block the app itself sends with every camera request. This means it can
break without warning whenever X-Sense changes their backend or app.

**X-Sense cameras have no pullable stream URL at all - live view is real WebRTC** (SDP
offer/answer, trickled ICE, a TURN relay from a per-session ticket), confirmed by reading the
Home Assistant reference implementation's actual source (it hands the session straight to the
browser's native WebRTC stack). TBC's `LiveManager`/ffmpeg pipeline expects a plain URL it can
`-i`, not a WebRTC session, so `camera/` runs its own small local bridge
(`webrtc_signal.py` + `webrtc_bridge.py` + `bridge_server.py`, using
[aiortc](https://github.com/aiortc/aiortc)):

- `probe()` stays cheap - it only logs in and makes sure the local bridge server is running.
- The bridge only opens a real WebRTC session to the camera **when something actually connects**
  to pull the stream (a live view being opened, or a recording starting) - mirroring TBC's own
  on-demand ffmpeg start/stop, so an idle, unwatched camera holds no permanent connection.
- Once connected, it decodes the WebRTC video, re-encodes it to H264, and serves it as plain
  MPEG-TS over local HTTP (`127.0.0.1` only, never exposed outside the host) - from TBC's point
  of view this is just an ordinary `stream_uri`.

**This WebRTC signaling protocol has not been verified against a live X-Sense account** - it was
ported from the reference implementation's source, not tested end-to-end (no X-Sense account was
available while building it). Treat the first real-world use as a beta test: if live view fails,
please open an issue with what you saw (the plugin logs each stage of the WebRTC negotiation).

If you'd rather not depend on reverse-engineered credentials at all, don't install this plugin.

## Why no automatic import?

Every other TBC cloud plugin (UniFi Protect, Eufy) hands over a persistent RTSP address that
TBC can store once and reuse forever. X-Sense cameras have no persistent stream address at all -
live view is a fresh WebRTC negotiation every time. Rather than pretend there's a simple address
to import, the cloud plugin here only lists devices (mirroring exactly how the
[`TBC-ewelink`](https://github.com/404GamerNotFound/TBC-ewelink) cloud plugin already handles
vendors with no persistent stream URL - see `docs/cloud-accounts.md` in the main repo). Use the
listed serial number to add the camera
manually with the `camera/` module instead, which handles the WebRTC bridging on an ongoing
basis (see above).

## Attribution

The protocol details here (auth flow, endpoint names, request signing, and the WebRTC
signaling exchange) were verified against the actual source of two community projects, not
guessed:

- [`theosnel/python-xsense`](https://github.com/theosnel/python-xsense) (MIT) - covers X-Sense
  sensors/alarms; has no camera support at all.
- [`Jarnsen/ha-xsense-component_test`](https://github.com/Jarnsen/ha-xsense-component_test)
  (Apache-2.0) - a community fork that adds camera support (SSC0A/SSC0B) on top of the above,
  including its own WebRTC signaling relay and a compiled Go WebRTC helper for SD-card playback.
  This plugin's `xsense_api.py` and `webrtc_signal.py` are small, from-scratch, live-view-only
  clients informed by reading that fork's source (its message envelope format, ticket fields,
  and offer-codec filtering), not a copy of it. Out of scope here and not included: SD-card
  playback, AI-notification history, sensor/alarm entities, MQTT, and the compiled Go helper -
  this plugin uses [aiortc](https://github.com/aiortc/aiortc) (a pure-Python WebRTC stack)
  instead, re-encoding locally rather than terminating WebRTC via a native helper binary.

Thank you to both projects' authors for the reverse-engineering work this depends on.

## Development

Each subdirectory (`camera/`, `cloud/`) is a fully independent, installable plugin with its own
`manifest.json`, `tests/`, and a copy of `xsense_api.py` (duplicated rather than shared, since
each subdirectory is packaged and installed on its own). `camera/` additionally needs `aiortc`
(and its transitive `av`/`aioice`/`pylibsrtp` dependencies - all ship prebuilt wheels, no
compiler needed) installed to run its tests, matching what TBC-camera-manager's own
`requirements.txt` installs in production. Run each plugin's tests directly:

```
pip install aiortc==1.15.0   # only needed for camera/'s tests
cd camera && python3 -m pytest tests/
cd cloud && python3 -m pytest tests/
```

Tests mock all network calls - no real X-Sense account is needed to run them. One test
(`test_webrtc_bridge.py`) does exercise real aiortc SDP-offer generation, but that's local
codec-capability logic only, not network I/O.
