import re
import unittest

from aiortc import RTCPeerConnection

from webrtc_bridge import _h264_video_codecs, _ice_servers, _local_candidates
from webrtc_signal import XSenseWebRTCTicket


def _ticket(ice_servers):
    data = {
        "signalServer": "signal.x-sense-iot.com",
        "groupId": "group-1",
        "role": "viewer",
        "id": "client-1",
        "traceId": "trace-1",
        "sign": "signature",
        "time": 1700000000,
        "iceServer": ice_servers,
    }
    return XSenseWebRTCTicket.from_api("SERIAL123", data)


class IceServersTests(unittest.TestCase):
    def test_urls_only_entry(self):
        servers = _ice_servers(_ticket([{"urls": "stun:stun.example.com:19302"}]))

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].urls, "stun:stun.example.com:19302")

    def test_turn_entry_with_credentials(self):
        servers = _ice_servers(
            _ticket([{"urls": "turn:turn.example.com:3478", "username": "u", "credential": "p"}])
        )

        self.assertEqual(servers[0].username, "u")
        self.assertEqual(servers[0].credential, "p")

    def test_entries_without_urls_are_skipped(self):
        servers = _ice_servers(_ticket([{"username": "u"}]))

        self.assertEqual(servers, [])


class LocalCandidatesTests(unittest.TestCase):
    def test_extracts_candidate_lines_with_their_media_line_index(self):
        sdp = (
            "v=0\r\n"
            "o=- 1 1 IN IP4 0.0.0.0\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 102\r\n"
            "a=candidate:1 1 UDP 12345 1.2.3.4 5000 typ host\r\n"
            "a=candidate:2 1 UDP 12344 1.2.3.4 5001 typ srflx\r\n"
        )

        candidates = _local_candidates(sdp)

        self.assertEqual(len(candidates), 2)
        self.assertTrue(candidates[0][0].startswith("1 1 UDP 12345"))
        self.assertEqual(candidates[0][1], 0)

    def test_no_candidates_before_first_media_line_are_ignored(self):
        sdp = "v=0\r\na=candidate:1 1 UDP 12345 1.2.3.4 5000 typ host\r\n"

        self.assertEqual(_local_candidates(sdp), [])


class H264VideoCodecsTests(unittest.TestCase):
    def test_only_h264_codecs_are_returned(self):
        codecs = _h264_video_codecs()

        self.assertTrue(codecs)
        for codec in codecs:
            self.assertEqual(codec.mimeType, "video/H264")


class RecvonlyOfferIsH264OnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_offer_video_section_only_advertises_h264_payloads(self):
        # Runs fully offline - createOffer() only needs local codec
        # capabilities, no network I/O - so this proves the actual SDP a
        # bridge session would send X-Sense only ever offers H264, which is
        # the one codec the camera side accepts (confirmed against the
        # reference implementation's offer-payload filtering).
        pc = RTCPeerConnection()
        try:
            transceiver = pc.addTransceiver("video", direction="recvonly")
            transceiver.setCodecPreferences(_h264_video_codecs())

            offer = await pc.createOffer()

            video_section = _video_media_line(offer.sdp)
            payload_types = video_section.split()[3:]
            rtpmaps = re.findall(r"a=rtpmap:(\d+) ([A-Za-z0-9]+)/", offer.sdp)
            codec_by_payload = dict(rtpmaps)
            for payload_type in payload_types:
                # rtx (payload_type has no rtpmap of its own kind here, or
                # maps to H264) is the only non-H264 entry aiortc may still
                # include, for retransmission of the H264 stream itself.
                self.assertIn(codec_by_payload.get(payload_type), ("H264", "rtx"))
        finally:
            await pc.close()


def _video_media_line(sdp: str) -> str:
    for line in sdp.splitlines():
        if line.startswith("m=video"):
            return line
    raise AssertionError("no m=video line in offer SDP")


if __name__ == "__main__":
    unittest.main()
