import unittest
from unittest.mock import AsyncMock, patch

from tbc_camera_api import CameraCapability

from module import XSenseCameraModule
from xsense_api import XSenseError


class XSenseCameraModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_requires_credentials(self):
        module = XSenseCameraModule()

        snapshot = await module.probe({"host": "SERIAL123"})

        self.assertEqual(snapshot.status, "error")
        self.assertIn("email/password", snapshot.message)

    async def test_probe_requires_serial(self):
        module = XSenseCameraModule()

        snapshot = await module.probe({"username": "user@example.com", "password": "secret"})

        self.assertEqual(snapshot.status, "error")
        self.assertIn("serial number", snapshot.message)

    async def test_probe_returns_error_on_login_failure(self):
        module = XSenseCameraModule()
        fake_client = AsyncMock()
        fake_client.login.side_effect = XSenseError("bad credentials")

        with patch("module.XSenseClient", return_value=fake_client):
            snapshot = await module.probe(
                {"username": "user@example.com", "password": "secret", "host": "SERIAL123"}
            )

        self.assertEqual(snapshot.status, "error")
        self.assertIn("bad credentials", snapshot.message)

    async def test_probe_returns_error_for_webrtc_only_camera(self):
        module = XSenseCameraModule()
        fake_client = AsyncMock()
        fake_client.get_live_stream_url.return_value = None

        with patch("module.XSenseClient", return_value=fake_client):
            snapshot = await module.probe(
                {"username": "user@example.com", "password": "secret", "host": "SERIAL123"}
            )

        self.assertEqual(snapshot.status, "error")
        self.assertIn("WebRTC", snapshot.message)

    async def test_probe_returns_stream_uri_for_rtsp_camera(self):
        module = XSenseCameraModule()
        fake_client = AsyncMock()
        fake_client.get_live_stream_url.return_value = "rtsp://stream.example.com/live/SERIAL123"

        with patch("module.XSenseClient", return_value=fake_client):
            snapshot = await module.probe(
                {"username": "user@example.com", "password": "secret", "host": "SERIAL123"}
            )

        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.stream_uri, "rtsp://stream.example.com/live/SERIAL123")
        self.assertEqual(snapshot.serial, "SERIAL123")

    def test_declares_live_and_recording_capabilities(self):
        module = XSenseCameraModule()

        self.assertTrue(module.supports(CameraCapability.LIVE))
        self.assertTrue(module.supports(CameraCapability.RECORDING))

    def test_identifier_label_overrides_host_ip(self):
        module = XSenseCameraModule()

        self.assertEqual(module.identifier_label, "X-Sense serial number")


if __name__ == "__main__":
    unittest.main()
