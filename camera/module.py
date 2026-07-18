from __future__ import annotations

from typing import Any

from tbc_camera_api import CameraCapability, CameraModule, CameraSnapshot

try:
    from . import bridge_server
    from .xsense_api import XSenseClient, XSenseError
except ImportError:  # pragma: no cover - exercised only by standalone test runs
    import bridge_server
    from xsense_api import XSenseClient, XSenseError


class XSenseCameraModule(CameraModule):
    """X-Sense camera (SSC0A/SSC0B) via a local WebRTC-to-MPEG-TS bridge.

    `camera["username"]`/`camera["password"]` hold the X-Sense account
    email/password; `camera["host"]` holds the camera's X-Sense serial
    number (there is no local IP - see this plugin's README.md for why the
    generic "Host / IP" field is repurposed this way, and
    docs/camera-modules.md's identifier_label section in the main TBC repo).

    X-Sense cameras have no pullable stream URL at all - live view is real
    WebRTC. probe() stays cheap (just a login check) and points TBC's ffmpeg
    pipeline at a local bridge server (bridge_server.py) that only opens the
    actual WebRTC session when something connects to pull the stream,
    mirroring TBC's own on-demand live-view lifecycle. See webrtc_bridge.py
    and webrtc_signal.py for the WebRTC/signaling implementation.
    """

    default_onvif_port = 8000
    default_http_port = 80
    default_rtsp_port = 554
    requires_credentials = True
    supports_manual_stream_uri = False
    requires_manual_stream_uri = False
    identifier_label = "X-Sense serial number"
    capabilities = frozenset({CameraCapability.LIVE, CameraCapability.RECORDING})

    async def probe(self, camera: dict[str, Any]) -> CameraSnapshot:
        username = str(camera.get("username") or "").strip()
        password = str(camera.get("password") or "")
        serial = str(camera.get("host") or "").strip()
        if not username or not password:
            return CameraSnapshot(status="error", message="X-Sense account email/password are required")
        if not serial:
            return CameraSnapshot(status="error", message="X-Sense camera serial number is required")

        client = XSenseClient(username, password)
        try:
            await client.login()
        except XSenseError as exc:
            return CameraSnapshot(status="error", message=str(exc))

        bridge_server.register_credentials(serial, username, password)
        await bridge_server.ensure_started()

        return CameraSnapshot(
            status="ok",
            message="Connected to X-Sense",
            manufacturer="X-Sense",
            serial=serial,
            stream_uri=bridge_server.stream_url(serial),
        )
