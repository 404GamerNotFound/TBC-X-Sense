"""Stub tbc_camera_api so this plugin's tests run standalone (no TBC-camera-manager checkout needed).

module.py imports CameraCapability/CameraModule/CameraSnapshot from
tbc_camera_api at module scope - inside the real TBC process that facade is
installed by camera_modules/packages.py before a plugin is ever imported,
but a plugin's own standalone test run never goes through that loader, so
this fake stands in for it.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from enum import Enum


def _install_fake_tbc_camera_api() -> None:
    if "tbc_camera_api" in sys.modules:
        return

    class _FakeCameraCapability(str, Enum):
        LIVE = "live"
        RECORDING = "recording"
        DETECTIONS = "detections"
        CHANNELS = "channels"
        ARCHIVE = "archive"
        CONTROL = "control"
        FIRMWARE = "firmware"

    class _FakeCameraModule:
        key = ""
        label = ""
        description = ""
        default_onvif_port = 8000
        default_http_port = 80
        default_rtsp_port = 554
        supports_manual_stream_uri = False
        requires_manual_stream_uri = False
        requires_credentials = True
        capabilities: frozenset = frozenset()
        identifier_label = None

        def supports(self, capability):
            return capability in self.capabilities

        async def probe(self, camera):
            raise NotImplementedError

    @dataclass
    class _FakeCameraSnapshot:
        status: str
        message: str
        manufacturer: str | None = None
        model: str | None = None
        firmware: str | None = None
        serial: str | None = None
        stream_uri: str | None = None
        detections: list = field(default_factory=list)
        channels: list = field(default_factory=list)
        metrics: dict = field(default_factory=dict)

    api = types.ModuleType("tbc_camera_api")
    api.CameraCapability = _FakeCameraCapability
    api.CameraModule = _FakeCameraModule
    api.CameraSnapshot = _FakeCameraSnapshot
    sys.modules["tbc_camera_api"] = api


_install_fake_tbc_camera_api()
