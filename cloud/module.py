from __future__ import annotations

from typing import Any

from tbc_cloud_api import (
    CloudAccountField,
    CloudAccountFieldType,
    CloudAccountModule,
    CloudConnectionError,
    CloudDevice,
)

try:
    from .xsense_api import XSenseClient, XSenseError
except ImportError:  # pragma: no cover - exercised only by standalone test runs
    from xsense_api import XSenseClient, XSenseError


class XSenseCloudModule(CloudAccountModule):
    """X-Sense account discovery (unofficial, reverse-engineered API).

    Lists the cameras (SSC0A/SSC0B) an X-Sense account knows about for
    inventory purposes only - there is no persistent stream URL to hand
    over (X-Sense's live-view URLs are short-lived session tickets, not a
    static address), so `CloudDevice.manual_stream_uri` is always empty and
    TBC will not offer "Add as camera" for these entries, exactly like the
    built-in `ewelink` cloud plugin already does for the same reason. Add a
    camera manually instead, using the "xsense-camera" module with the
    listed serial number - see this plugin's README.md.
    """

    account_fields = (
        CloudAccountField(
            key="email",
            label="Email address",
            field_type=CloudAccountFieldType.EMAIL,
            required=True,
            autocomplete="username",
        ),
        CloudAccountField(
            key="password",
            label="Password",
            field_type=CloudAccountFieldType.PASSWORD,
            required=True,
            autocomplete="current-password",
        ),
    )

    async def test_connection(self, account: dict[str, Any]) -> str:
        client = await self._login(account)
        cameras = await self._list_cameras(client)
        return f"Connected to X-Sense - {len(cameras)} camera(s) found"

    async def discover_devices(self, account: dict[str, Any]) -> list[CloudDevice]:
        client = await self._login(account)
        cameras = await self._list_cameras(client)
        return [
            CloudDevice(
                external_id=camera.serial,
                name=camera.name,
                model=camera.model,
                manual_stream_uri=None,
                suggested_module_key="xsense-camera",
            )
            for camera in cameras
        ]

    async def _login(self, account: dict[str, Any]) -> XSenseClient:
        email = str(account.get("email") or "").strip()
        password = str(account.get("password") or "")
        if not email or not password:
            raise CloudConnectionError("Email address and password are required")
        client = XSenseClient(email, password)
        try:
            await client.login()
        except XSenseError as exc:
            raise CloudConnectionError(str(exc)) from exc
        return client

    async def _list_cameras(self, client: XSenseClient):
        try:
            return await client.list_cameras()
        except XSenseError as exc:
            raise CloudConnectionError(str(exc)) from exc
