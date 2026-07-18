import base64
import json
import unittest

from webrtc_signal import (
    XSenseWebRTCTicket,
    make_ice_candidate_message,
    make_sdp_offer_message,
    parse_signal_message,
)


def _ticket(**overrides):
    data = {
        "signalServer": "signal.x-sense-iot.com",
        "groupId": "group-1",
        "role": "viewer",
        "id": "client-1",
        "traceId": "trace-1",
        "sign": "signature",
        "time": 1700000000,
        "iceServer": [{"urls": "stun:stun.example.com:19302"}],
    }
    data.update(overrides)
    return XSenseWebRTCTicket.from_api("SERIAL123", data)


class XSenseWebRTCTicketTests(unittest.TestCase):
    def test_signal_url_is_signed_and_scoped_to_the_ticket(self):
        ticket = _ticket()

        url = ticket.signal_url()

        self.assertTrue(url.startswith("wss://signal.x-sense-iot.com/group-1/viewer/client-1?"))
        self.assertIn("traceId=trace-1", url)
        self.assertIn("time=1700000000", url)
        self.assertIn("sign=signature", url)

    def test_signal_server_without_scheme_defaults_to_wss(self):
        ticket = _ticket(signalServer="plain.example.com")

        self.assertTrue(ticket.signal_url().startswith("wss://plain.example.com/"))

    def test_missing_field_raises_signal_error(self):
        with self.assertRaises(Exception):
            XSenseWebRTCTicket.from_api("SERIAL123", {"signalServer": "x"})


class MessageEnvelopeRoundTripTests(unittest.TestCase):
    def test_sdp_offer_envelope_round_trips_through_parse_signal_message(self):
        ticket = _ticket()

        message = make_sdp_offer_message(offer_sdp="v=0\r\n...", ticket=ticket, session_id="session-1")
        envelope = json.loads(message)

        self.assertEqual(envelope["messageType"], "SDP_OFFER")
        self.assertEqual(envelope["recipientClientId"], "SERIAL123")
        self.assertEqual(envelope["senderClientId"], "client-1")
        payload = json.loads(base64.b64decode(envelope["messagePayload"]))
        self.assertEqual(payload, {"type": "offer", "sdp": "v=0\r\n..."})

    def test_ice_candidate_envelope_round_trips_through_parse_signal_message(self):
        ticket = _ticket()

        message = make_ice_candidate_message(
            candidate="candidate:1 1 UDP 12345 1.2.3.4 5000 typ host",
            sdp_mid="0",
            sdp_m_line_index=0,
            ticket=ticket,
            session_id="session-1",
        )

        event, payload = parse_signal_message(message)

        self.assertEqual(event, "ICE_CANDIDATE")
        self.assertEqual(payload["candidate"], "candidate:1 1 UDP 12345 1.2.3.4 5000 typ host")
        self.assertEqual(payload["sdpMid"], "0")
        self.assertEqual(payload["sdpMLineIndex"], 0)


class ParseSignalMessageTests(unittest.TestCase):
    def test_sdp_answer_message_is_decoded(self):
        payload = base64.b64encode(json.dumps({"type": "answer", "sdp": "v=0\r\n..."}).encode()).decode()
        raw = json.dumps({"messageType": "SDP_ANSWER", "messagePayload": payload})

        event, decoded_payload = parse_signal_message(raw)

        self.assertEqual(event, "SDP_ANSWER")
        self.assertEqual(decoded_payload["sdp"], "v=0\r\n...")

    def test_peer_in_message_without_encoded_payload_is_still_parsed(self):
        raw = json.dumps({"messageType": "PEER_IN", "senderClientId": "SERIAL123"})

        event, payload = parse_signal_message(raw)

        self.assertEqual(event, "PEER_IN")

    def test_non_json_message_returns_none_event(self):
        event, payload = parse_signal_message("not json")

        self.assertIsNone(event)
        self.assertEqual(payload, "not json")

    def test_bytes_input_is_decoded_before_parsing(self):
        raw = json.dumps({"messageType": "PEER_IN"}).encode()

        event, _payload = parse_signal_message(raw)

        self.assertEqual(event, "PEER_IN")


if __name__ == "__main__":
    unittest.main()
