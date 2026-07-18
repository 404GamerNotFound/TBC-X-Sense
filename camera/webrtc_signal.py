"""X-Sense ADDX WebRTC signaling - live view only.

X-Sense cameras (SSC0A/SSC0B) have no pullable stream URL; live view is real
WebRTC, negotiated over a proprietary WebSocket signaling protocol. This is a
deliberately scoped-down port (not a copy) of the live-view path in the
Apache-2.0-licensed https://github.com/Jarnsen/ha-xsense-component_test,
informed by (not copy-pasted from) its `webrtc_signal.py`. Out of scope here:
SD-card playback, data-channel commands, MQTT - none of that is needed for a
live camera feed. See this plugin's README.md for full attribution.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

SIGNAL_MODE = "vicoo"
SIGNAL_VIEWER_TYPE = "a4x_sdk"
_SIGNAL_NAME = "tbc-camera-manager"
ANSWER_TIMEOUT_SECONDS = 40


class XSenseSignalError(RuntimeError):
    """Raised when the X-Sense WebRTC signaling exchange fails."""


@dataclass(frozen=True)
class XSenseWebRTCTicket:
    """Parsed `/device/getWebrtcTicket` response - one ticket per live-view session."""

    serial_number: str
    signal_server: str
    group_id: str
    role: str
    client_id: str
    trace_id: str
    sign: str
    time: int
    ice_servers: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_api(cls, serial_number: str, data: dict[str, Any]) -> "XSenseWebRTCTicket":
        try:
            return cls(
                serial_number=serial_number,
                signal_server=str(data["signalServer"]),
                group_id=str(data["groupId"]),
                role=str(data["role"]),
                client_id=str(data["id"]),
                trace_id=str(data["traceId"]),
                sign=str(data["sign"]),
                time=int(data["time"]),
                ice_servers=list(data.get("iceServer") or []),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise XSenseSignalError(f"X-Sense returned an incomplete WebRTC ticket: {exc}") from exc

    @property
    def session_id(self) -> str:
        return f"tbc-{self.client_id}-{int(time.time() * 1000)}"

    def signal_url(self) -> str:
        """Return the signed WebSocket URL for this ticket's signal server."""
        parsed = urlparse(self.signal_server)
        if not parsed.scheme:
            parsed = urlparse(f"wss://{self.signal_server}")
        scheme = "wss" if parsed.scheme in {"http", "https"} else parsed.scheme
        path = f"/{self.group_id}/{self.role}/{self.client_id}"
        query = f"traceId={self.trace_id}&time={self.time}&sign={self.sign}&name={_SIGNAL_NAME}"
        return urlunparse((scheme, parsed.netloc, path, "", query, ""))


def _b64_json(data: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def make_sdp_offer_message(*, offer_sdp: str, ticket: XSenseWebRTCTicket, session_id: str) -> str:
    """Return the SDP_OFFER signal envelope for `offer_sdp`."""
    envelope: dict[str, Any] = {
        "messageType": "SDP_OFFER",
        "messagePayload": _b64_json({"type": "offer", "sdp": offer_sdp}),
        "mode": SIGNAL_MODE,
        "recipientClientId": ticket.serial_number,
        "senderClientId": ticket.client_id,
        "sessionId": session_id,
        "viewerType": SIGNAL_VIEWER_TYPE,
    }
    return json.dumps(envelope, separators=(",", ":"))


def make_ice_candidate_message(
    *,
    candidate: str,
    sdp_mid: str | None,
    sdp_m_line_index: int,
    ticket: XSenseWebRTCTicket,
    session_id: str,
) -> str:
    """Return the ICE_CANDIDATE signal envelope for one local ICE candidate."""
    envelope: dict[str, Any] = {
        "messageType": "ICE_CANDIDATE",
        "messagePayload": _b64_json(
            {"sdpMid": sdp_mid, "sdpMLineIndex": sdp_m_line_index, "candidate": candidate}
        ),
        "recipientClientId": ticket.serial_number,
        "senderClientId": ticket.client_id,
        "sessionId": session_id,
    }
    return json.dumps(envelope, separators=(",", ":"))


def parse_signal_message(raw: str | bytes) -> tuple[str | None, Any]:
    """Parse one incoming signal-server message into an (event, payload) pair."""
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, raw
    if not isinstance(data, dict):
        return None, data
    event = data.get("messageType") or data.get("event") or data.get("type")
    payload = data.get("messagePayload", data.get("payload", data))
    if isinstance(payload, str):
        decoded_payload = _try_decode_payload(payload)
        if decoded_payload is not None:
            payload = decoded_payload
    return event, payload


def _try_decode_payload(payload: str) -> dict[str, Any] | None:
    for candidate in (payload, _try_base64_decode(payload)):
        if candidate is None:
            continue
        try:
            decoded = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _try_base64_decode(value: str) -> str | None:
    try:
        return base64.b64decode(value).decode()
    except Exception:
        return None


class XSenseWebRTCSignalSession:
    """One live-view WebRTC signaling exchange with an X-Sense camera.

    Usage: `connect()`, then `negotiate(offer_sdp)` to get the SDP answer
    once the camera peer joins, sending local ICE candidates via
    `send_candidate()` as aiortc gathers them and consuming remote candidates
    via `remote_candidates()` - mirrors a standard trickle-ICE flow, just
    carried over this proprietary WebSocket instead of a standard signaling
    channel.
    """

    def __init__(self, *, session: aiohttp.ClientSession, ticket: XSenseWebRTCTicket) -> None:
        self._session = session
        self._ticket = ticket
        self._session_id = ticket.session_id
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._peer_ready = False
        self._offer_sent = False
        self._pending_offer_sdp: str | None = None
        self._answer: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._remote_candidates: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._closed = False
        self._read_task: asyncio.Task | None = None

    async def connect(self) -> None:
        try:
            self._ws = await self._session.ws_connect(self._ticket.signal_url())
        except aiohttp.ClientError as exc:
            raise XSenseSignalError(f"Could not connect to the X-Sense signal server: {exc}") from exc
        self._read_task = asyncio.create_task(self._read_loop())

    async def negotiate(self, offer_sdp: str) -> str:
        """Send `offer_sdp` (once the camera peer is online) and return its SDP answer."""
        self._pending_offer_sdp = offer_sdp
        if self._peer_ready:
            await self._send_offer()
        try:
            return await asyncio.wait_for(self._answer, timeout=ANSWER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise XSenseSignalError("X-Sense camera did not answer the WebRTC offer in time") from exc

    async def send_candidate(self, candidate: str, sdp_mid: str | None, sdp_m_line_index: int) -> None:
        if self._closed or self._ws is None or self._ws.closed:
            return
        await self._ws.send_str(
            make_ice_candidate_message(
                candidate=candidate,
                sdp_mid=sdp_mid,
                sdp_m_line_index=sdp_m_line_index,
                ticket=self._ticket,
                session_id=self._session_id,
            )
        )

    async def remote_candidates(self) -> AsyncIterator[dict[str, Any]]:
        """Async-iterate remote ICE candidates as the camera trickles them in."""
        while True:
            candidate = await self._remote_candidates.get()
            if candidate is None:
                return
            yield candidate

    async def close(self) -> None:
        self._closed = True
        await self._remote_candidates.put(None)
        if self._read_task is not None:
            self._read_task.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    async def _send_offer(self) -> None:
        if self._offer_sent or self._ws is None or self._pending_offer_sdp is None:
            return
        self._offer_sent = True
        await self._ws.send_str(
            make_sdp_offer_message(
                offer_sdp=self._pending_offer_sdp, ticket=self._ticket, session_id=self._session_id
            )
        )

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if message.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    continue
                event, payload = parse_signal_message(message.data)
                await self._handle_event(event, payload)
        except Exception as exc:  # noqa: BLE001 - surfaced to negotiate()'s waiter
            if not self._answer.done():
                self._answer.set_exception(XSenseSignalError(f"X-Sense signal connection failed: {exc}"))
        finally:
            if not self._answer.done():
                self._answer.set_exception(XSenseSignalError("X-Sense signal connection closed before answering"))
            await self._remote_candidates.put(None)

    async def _handle_event(self, event: str | None, payload: Any) -> None:
        if self._closed:
            return
        if event == "PEER_IN":
            self._peer_ready = True
            await self._send_offer()
            return
        if event == "SDP_ANSWER" and isinstance(payload, dict):
            answer = payload.get("sdp")
            if answer and not self._answer.done():
                self._answer.set_result(str(answer))
            return
        if event == "ICE_CANDIDATE" and isinstance(payload, dict):
            candidate = payload.get("candidate")
            if candidate:
                await self._remote_candidates.put(
                    {
                        "candidate": str(candidate),
                        "sdpMid": payload.get("sdpMid"),
                        "sdpMLineIndex": int(payload.get("sdpMLineIndex") or 0),
                    }
                )
