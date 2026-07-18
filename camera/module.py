from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from tbc_camera_api import CameraCapability, CameraModule, CameraSnapshot

try:
    from .xsense_api import XSenseClient, XSenseError
except ImportError:  # pragma: no cover - exercised only by standalone test runs
    from xsense_api import XSenseClient, XSenseError

_STREAMABLE_SCHEMES = {"rtsp", "rtsps", "rtmp"}


class XSenseCameraModule(CameraModule):
    """X-Sense camera (SSC0A/SSC0B) via the unofficial X-Sense cloud live-view API.

    `camera["username"]`/`camera["password"]` hold the X-Sense account
    email/password; `camera["host"]` holds the camera's X-Sense serial
    number (there is no local IP - see this plugin's README.md for why the
    generic "Host / IP" field is repurposed this way, and
    docs/camera-modules.md's identifier_label section in the main TBC repo).
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
            live_url = await client.get_live_stream_url(serial)
        except XSenseError as exc:
            return CameraSnapshot(status="error", message=str(exc))

        if not live_url or urlsplit(live_url).scheme.lower() not in _STREAMABLE_SCHEMES:
            return CameraSnapshot(
                status="error",
                message=(
                    "This X-Sense camera only offers a WebRTC live view, which TBC cannot play "
                    "(only RTSP/RTSPS/RTMP streams are supported)."
                ),
            )

        return CameraSnapshot(
            status="ok",
            message="Connected to X-Sense",
            manufacturer="X-Sense",
            serial=serial,
            stream_uri=live_url,
        )
