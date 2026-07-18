"""Stub tbc_cloud_api so this plugin's tests run standalone (no TBC-camera-manager checkout needed).

module.py imports CloudAccountModule/CloudDevice/... from tbc_cloud_api at
module scope - inside the real TBC process that facade is installed by
cloud_modules/packages.py before a plugin is ever imported, but a plugin's
own standalone test run never goes through that loader, so this fake stands
in for it.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from enum import Enum


def _install_fake_tbc_cloud_api() -> None:
    if "tbc_cloud_api" in sys.modules:
        return

    class _FakeCloudAccountFieldType(str, Enum):
        TEXT = "text"
        EMAIL = "email"
        PASSWORD = "password"
        NUMBER = "number"
        CHECKBOX = "checkbox"
        SELECT = "select"

    @dataclass(frozen=True)
    class _FakeCloudAccountField:
        key: str
        label: str
        field_type: _FakeCloudAccountFieldType = _FakeCloudAccountFieldType.TEXT
        required: bool = False
        placeholder: str = ""
        help_text: str = ""
        autocomplete: str = ""
        default: object = None
        minimum: int | None = None
        maximum: int | None = None
        full_width: bool = False
        transient: bool = False
        options: tuple = ()

    class _FakeCloudConnectionError(RuntimeError):
        pass

    @dataclass(frozen=True)
    class _FakeCloudDevice:
        external_id: str
        name: str
        model: str | None = None
        online: bool | None = None
        manual_stream_uri: str | None = None
        suggested_module_key: str = "rtsp_only"
        needs_account_credentials: bool = False

    class _FakeCloudAccountModule:
        key = ""
        label = ""
        description = ""
        account_fields: tuple = ()
        account_username_field: str | None = None
        account_password_field: str | None = None

        async def test_connection(self, account):
            raise NotImplementedError

        async def discover_devices(self, account):
            raise NotImplementedError

    api = types.ModuleType("tbc_cloud_api")
    api.CloudAccountField = _FakeCloudAccountField
    api.CloudAccountFieldType = _FakeCloudAccountFieldType
    api.CloudAccountModule = _FakeCloudAccountModule
    api.CloudConnectionError = _FakeCloudConnectionError
    api.CloudDevice = _FakeCloudDevice
    sys.modules["tbc_cloud_api"] = api


_install_fake_tbc_cloud_api()
