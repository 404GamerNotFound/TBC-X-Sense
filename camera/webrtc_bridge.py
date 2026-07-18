"""Bridges one X-Sense camera's WebRTC live view to MPEG-TS bytes.

X-Sense cameras only accept H264 video (confirmed against the reference
implementation's offer-payload filtering) - a single recvonly, video-only
transceiver with H264 codec preference is enough. TBC's own ffmpeg pipeline
already drops audio (`-an` in live.py), so no audio transceiver is
negotiated at all.

aiortc decodes incoming RTP into `av.VideoFrame`s internally; this module
re-encodes those frames to H264 and muxes them into MPEG-TS so plain HTTP
(and TBC's existing ffmpeg `-i <uri>` pipeline) can consume the result -
verified against aiortc 1.15.0's actual API (RTCPeerConnection gathers all
local ICE candidates synchronously inside setLocalDescription(), so no
"icecandidate" event/trickle is needed for the local side; only remote
candidates from the camera need addIceCandidate() after the fact).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from fractions import Fraction
from typing import Any

import aiohttp
import av
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import MediaStreamError
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.sdp import candidate_from_sdp

try:
    from .webrtc_signal import XSenseSignalError, XSenseWebRTCSignalSession, XSenseWebRTCTicket
except ImportError:  # pragma: no cover - exercised only by standalone test runs
    from webrtc_signal import XSenseSignalError, XSenseWebRTCSignalSession, XSenseWebRTCTicket

LOGGER = logging.getLogger(__name__)

_TRACK_TIMEOUT_SECONDS = 15
_CANDIDATE_LINE = re.compile(r"^a=candidate:(?P<value>.+)$", re.MULTILINE)
_ENCODE_FRAME_RATE = 25
_TIME_BASE = Fraction(1, _ENCODE_FRAME_RATE)


class XSenseBridgeError(RuntimeError):
    """Raised when the WebRTC bridge to an X-Sense camera fails to start."""


def _ice_servers(ticket: XSenseWebRTCTicket) -> list[RTCIceServer]:
    servers: list[RTCIceServer] = []
    for entry in ticket.ice_servers:
        urls = entry.get("urls") or entry.get("url")
        if not urls:
            continue
        kwargs: dict[str, Any] = {"urls": urls}
        if entry.get("username"):
            kwargs["username"] = entry["username"]
        credential = entry.get("credential") or entry.get("password")
        if credential:
            kwargs["credential"] = credential
        servers.append(RTCIceServer(**kwargs))
    return servers


def _h264_video_codecs() -> list[Any]:
    capabilities = RTCRtpSender.getCapabilities("video")
    return [codec for codec in capabilities.codecs if codec.mimeType == "video/H264"]


def _local_candidates(offer_sdp: str) -> list[tuple[str, int]]:
    """Return (candidate, media-line-index) pairs embedded in a local offer.

    aiortc gathers ICE synchronously before setLocalDescription() returns,
    so every local candidate is already in the SDP text by the time this
    runs - nothing here waits on network I/O.
    """
    candidates: list[tuple[str, int]] = []
    media_line_index = -1
    for line in offer_sdp.splitlines():
        if line.startswith("m="):
            media_line_index += 1
        match = _CANDIDATE_LINE.match(line)
        if match and media_line_index >= 0:
            candidates.append((match.group("value"), media_line_index))
    return candidates


class XSenseWebRTCBridge:
    """One live-view session: WebRTC negotiation plus H264/MPEG-TS re-encoding."""

    def __init__(self, *, http_session: aiohttp.ClientSession, ticket: XSenseWebRTCTicket) -> None:
        self._http_session = http_session
        self._ticket = ticket
        self._pc: RTCPeerConnection | None = None
        self._signal: XSenseWebRTCSignalSession | None = None
        self._forward_task: asyncio.Task | None = None
        self._track: Any = None

    async def start(self) -> None:
        self._pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=_ice_servers(self._ticket)))
        track_future: asyncio.Future = asyncio.get_event_loop().create_future()

        @self._pc.on("track")
        def _on_track(track: Any) -> None:
            if track.kind == "video" and not track_future.done():
                track_future.set_result(track)

        transceiver = self._pc.addTransceiver("video", direction="recvonly")
        h264_codecs = _h264_video_codecs()
        if h264_codecs:
            transceiver.setCodecPreferences(h264_codecs)

        self._signal = XSenseWebRTCSignalSession(session=self._http_session, ticket=self._ticket)
        try:
            await self._signal.connect()

            offer = await self._pc.createOffer()
            await self._pc.setLocalDescription(offer)
            local_sdp = self._pc.localDescription.sdp

            answer_sdp = await self._signal.negotiate(local_sdp)
            await self._pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))

            for candidate_value, media_line_index in _local_candidates(local_sdp):
                await self._signal.send_candidate(candidate_value, None, media_line_index)

            self._forward_task = asyncio.create_task(self._forward_remote_candidates())

            self._track = await asyncio.wait_for(track_future, timeout=_TRACK_TIMEOUT_SECONDS)
        except (XSenseSignalError, asyncio.TimeoutError) as exc:
            await self.close()
            raise XSenseBridgeError(f"Could not start X-Sense WebRTC live view: {exc}") from exc
        except Exception:
            await self.close()
            raise

    async def iter_mpegts_chunks(self) -> AsyncIterator[bytes]:
        """Yield MPEG-TS bytes for as long as the camera keeps sending video."""
        if self._track is None:
            raise XSenseBridgeError("X-Sense WebRTC bridge was not started")
        sink = _ChunkSink()
        container = av.open(sink, mode="w", format="mpegts")
        stream = None
        frame_index = 0
        try:
            while True:
                try:
                    frame = await self._track.recv()
                except MediaStreamError:
                    return
                if stream is None:
                    stream = container.add_stream("h264", rate=_ENCODE_FRAME_RATE)
                    stream.width = frame.width
                    stream.height = frame.height
                    stream.pix_fmt = "yuv420p"
                    stream.codec_context.options = {"preset": "ultrafast", "tune": "zerolatency"}
                frame.pts = frame_index
                frame.time_base = _TIME_BASE
                frame_index += 1
                for packet in stream.encode(frame):
                    container.mux(packet)
                chunk = sink.pop()
                if chunk:
                    yield chunk
        finally:
            container.close()

    async def close(self) -> None:
        if self._forward_task is not None:
            self._forward_task.cancel()
            self._forward_task = None
        if self._signal is not None:
            await self._signal.close()
            self._signal = None
        if self._pc is not None:
            await self._pc.close()
            self._pc = None

    async def _forward_remote_candidates(self) -> None:
        assert self._signal is not None
        assert self._pc is not None
        async for candidate in self._signal.remote_candidates():
            raw = str(candidate.get("candidate") or "")
            if raw.startswith("candidate:"):
                raw = raw[len("candidate:"):]
            if not raw:
                continue
            try:
                ice_candidate = candidate_from_sdp(raw)
            except ValueError:
                LOGGER.debug("Ignoring unparsable X-Sense ICE candidate: %s", raw)
                continue
            ice_candidate.sdpMid = candidate.get("sdpMid")
            ice_candidate.sdpMLineIndex = candidate.get("sdpMLineIndex")
            # The bridge may have been closed (pc already shut down) between
            # a candidate being queued and being processed here - harmless.
            with suppress(ConnectionError, InvalidStateError):
                await self._pc.addIceCandidate(ice_candidate)


class _ChunkSink:
    """A minimal writable object PyAV can mux into, collecting bytes for draining."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def pop(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data
