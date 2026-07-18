import unittest
from unittest.mock import AsyncMock, patch

from tbc_cloud_api import CloudConnectionError

from module import XSenseCloudModule
from xsense_api import XSenseCamera, XSenseError


class XSenseCloudModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_requires_email_and_password(self):
        module = XSenseCloudModule()

        with self.assertRaisesRegex(CloudConnectionError, "required"):
            await module.test_connection({})

    async def test_login_failure_is_wrapped_as_cloud_connection_error(self):
        module = XSenseCloudModule()
        fake_client = AsyncMock()
        fake_client.login.side_effect = XSenseError("wrong password")

        with patch("module.XSenseClient", return_value=fake_client):
            with self.assertRaisesRegex(CloudConnectionError, "wrong password"):
                await module.test_connection({"email": "user@example.com", "password": "secret"})

    async def test_connection_reports_camera_count(self):
        module = XSenseCloudModule()
        fake_client = AsyncMock()
        fake_client.list_cameras.return_value = [
            XSenseCamera(serial="A1", name="Front Door", model="SSC0A"),
            XSenseCamera(serial="A2", name="Backyard", model="SSC0B"),
        ]

        with patch("module.XSenseClient", return_value=fake_client):
            message = await module.test_connection({"email": "user@example.com", "password": "secret"})

        self.assertIn("2 camera(s)", message)

    async def test_discover_devices_returns_inventory_without_stream_uri(self):
        module = XSenseCloudModule()
        fake_client = AsyncMock()
        fake_client.list_cameras.return_value = [
            XSenseCamera(serial="A1", name="Front Door", model="SSC0A"),
        ]

        with patch("module.XSenseClient", return_value=fake_client):
            devices = await module.discover_devices({"email": "user@example.com", "password": "secret"})

        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.external_id, "A1")
        self.assertEqual(device.name, "Front Door")
        self.assertEqual(device.model, "SSC0A")
        self.assertIsNone(device.manual_stream_uri)
        self.assertEqual(device.suggested_module_key, "xsense-camera")

    async def test_discovery_failure_is_wrapped_as_cloud_connection_error(self):
        module = XSenseCloudModule()
        fake_client = AsyncMock()
        fake_client.list_cameras.side_effect = XSenseError("session expired")

        with patch("module.XSenseClient", return_value=fake_client):
            with self.assertRaisesRegex(CloudConnectionError, "session expired"):
                await module.discover_devices({"email": "user@example.com", "password": "secret"})


if __name__ == "__main__":
    unittest.main()
