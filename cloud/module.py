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

    Lists the cameras (SSC0A/SSC0B) an X-Sense account knows about. There is
    no persistent stream URL to hand over (X-Sense's live view is real
    WebRTC, negotiated fresh per session - see the "xsense-camera" plugin),
    so `CloudDevice.manual_stream_uri` is always empty, unlike vendors with a
    static RTSP address. Instead, each device sets
    `needs_account_credentials=True`: TBC's "Add as camera" then reuses this
    same account's already-authenticated email/password (via
    `account_username_field`/`account_password_field` below) plus the
    device's serial number, so no manual copy-pasting is needed.
    """

    account_username_field = "email"
    account_password_field = "password"

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
                needs_account_credentials=True,
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
