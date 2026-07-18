"""Lazily-started local HTTP server exposing X-Sense cameras as MPEG-TS.

TBC's ffmpeg pipeline only ever `-i <uri>`s a camera's `stream_uri` when a
live view or recording is actually opened (on-demand, no idle-timeout - see
`app/tbc/live.py`'s LiveManager in the main repo), and `CameraModule.probe()`
runs unconditionally for every enabled camera every ~60s regardless of
whether anyone is watching. So probe() must stay cheap and must not open a
WebRTC session itself - instead it just makes sure this local server is
running and returns a URL to it; the actual (expensive) WebRTC session is
opened lazily, only when something (ffmpeg) actually connects to that URL,
and torn down again when that connection ends. This mirrors TBC's own
ffmpeg start/stop lifecycle exactly, with zero coordination needed between
this plugin and the main app.

Bound to 127.0.0.1 only, same as the main repo's Go2rtcManager
(app/tbc/go2rtc.py) - never exposed outside the host.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

try:
    from .webrtc_bridge import XSenseBridgeError, XSenseWebRTCBridge
    from .webrtc_signal import XSenseWebRTCTicket
    from .xsense_api import XSenseClient, XSenseError
except ImportError:  # pragma: no cover - exercised only by standalone test runs
    from webrtc_bridge import XSenseBridgeError, XSenseWebRTCBridge
    from webrtc_signal import XSenseWebRTCTicket
    from xsense_api import XSenseClient, XSenseError

LOGGER = logging.getLogger(__name__)

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 18734

_credentials: dict[str, tuple[str, str]] = {}
_runner: web.AppRunner | None = None
_start_lock = asyncio.Lock()


def register_credentials(serial: str, username: str, password: str) -> None:
    """Record the account to use for `serial`'s next bridged live view.

    Called from probe() every poll cycle - always reflects the camera's
    current configured credentials by the time a live view actually
    connects, without the bridge server needing its own DB access.
    """
    _credentials[serial] = (username, password)


def stream_url(serial: str) -> str:
    return f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/live/{serial}.ts"


async def ensure_started() -> None:
    """Start the local bridge server if it isn't already running (idempotent)."""
    global _runner
    if _runner is not None:
        return
    async with _start_lock:
        if _runner is not None:
            return
        app = web.Application()
        app.router.add_get("/live/{serial}.ts", _handle_live)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, BRIDGE_HOST, BRIDGE_PORT)
        await site.start()
        _runner = runner
        LOGGER.info("X-Sense WebRTC bridge listening on %s:%s", BRIDGE_HOST, BRIDGE_PORT)


async def _handle_live(request: web.Request) -> web.StreamResponse:
    serial = request.match_info["serial"]
    credentials = _credentials.get(serial)
    if credentials is None:
        return web.Response(status=404, text="Unknown X-Sense camera serial number")
    username, password = credentials

    client = XSenseClient(username, password)
    async with aiohttp.ClientSession() as http_session:
        try:
            ticket = await _fetch_ticket(client, serial)
            bridge = XSenseWebRTCBridge(http_session=http_session, ticket=ticket)
            await bridge.start()
        except XSenseBridgeError as exc:
            LOGGER.warning("X-Sense WebRTC bridge failed for %s: %s", serial, exc)
            return web.Response(status=502, text=str(exc))

        response = web.StreamResponse(status=200, headers={"Content-Type": "video/mp2t"})
        await response.prepare(request)
        try:
            async for chunk in bridge.iter_mpegts_chunks():
                await response.write(chunk)
        except (ConnectionResetError, ConnectionError):
            pass
        finally:
            await bridge.close()
        return response


async def _fetch_ticket(client: XSenseClient, serial: str) -> XSenseWebRTCTicket:
    try:
        await client.login()
        ticket_data = await client.get_webrtc_ticket(serial)
    except XSenseError as exc:
        raise XSenseBridgeError(str(exc)) from exc
    return XSenseWebRTCTicket.from_api(serial, ticket_data)
