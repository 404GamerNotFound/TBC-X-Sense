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
fixed device-identity block the app itself sends with every camera request. This means:

- It can break without warning whenever X-Sense changes their backend or app.
- X-Sense's live-view URL is a **short-lived session ticket**, not a persistent stream address.
  TBC keeps it fresh automatically (`camera/module.py`'s `probe()` re-fetches it on every
  background poll cycle, well within the ticket's few-minute lifetime), but if X-Sense changes
  how that works, live view/recording for X-Sense cameras will start failing until this plugin
  is updated.
- If a specific camera only offers a WebRTC live view (no direct RTSP/RTSPS/RTMP URL), this
  plugin cannot stream it - TBC only understands RTSP-family sources, and adding a WebRTC
  ingestion pipeline is out of scope for a single camera plugin. The camera will show a clear
  "WebRTC-only, cannot play" status instead of silently failing.

If you'd rather not depend on reverse-engineered credentials at all, don't install this plugin.

## Why no automatic import?

Every other TBC cloud plugin (UniFi Protect, Eufy) hands over a persistent RTSP address that
TBC can store once and reuse forever. X-Sense's live-view API doesn't work that way - the URL
you get back expires in a few minutes. Rather than import a camera with a stream that goes dead
almost immediately, the cloud plugin here only lists devices (mirroring exactly how the
built-in `ewelink` cloud plugin already handles vendors with no persistent stream URL - see
`docs/cloud-accounts.md` in the main repo). Use the listed serial number to add the camera
manually with the `camera/` module instead, which keeps the stream alive on an ongoing basis.

## Attribution

The protocol details here (auth flow, endpoint names, request signing, the live-stream call)
were verified against the actual source of two community projects, not guessed:

- [`theosnel/python-xsense`](https://github.com/theosnel/python-xsense) (MIT) - covers X-Sense
  sensors/alarms; has no camera support at all.
- [`Jarnsen/ha-xsense-component_test`](https://github.com/Jarnsen/ha-xsense-component_test)
  (Apache-2.0) - a community fork that adds camera support (SSC0A/SSC0B) on top of the above.
  This plugin's `xsense_api.py` is a small, from-scratch, camera-only client informed by reading
  that fork's source, not a copy of it - the great majority of that project (SD-card playback,
  AI-notification history, sensor/alarm entities, MQTT, WebRTC signaling) is out of scope here
  and isn't included.

Thank you to both projects' authors for the reverse-engineering work this depends on.

## Development

Each subdirectory (`camera/`, `cloud/`) is a fully independent, installable plugin with its own
`manifest.json`, `tests/`, and a copy of `xsense_api.py` (duplicated rather than shared, since
each subdirectory is packaged and installed on its own). Run each plugin's tests directly:

```
cd camera && python3 -m pytest tests/
cd cloud && python3 -m pytest tests/
```

Tests mock all network calls - no real X-Sense account is needed to run them.
