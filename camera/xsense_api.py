"""Minimal X-Sense cloud API client - auth, camera discovery, and live-stream URLs only.

X-Sense has no public developer API (confirmed by X-Sense's own team on the
Home Assistant community forum). Everything here is reverse-engineered from
the Android app. This is a deliberately small, from-scratch port of just the
calls TBC needs, informed by (not copy-pasted from) the Apache-2.0-licensed
community fork https://github.com/Jarnsen/ha-xsense-component_test, itself
built on top of the MIT-licensed https://github.com/theosnel/python-xsense
(which covers sensors/alarms but has no camera support at all). Every
constant and endpoint below was verified against that fork's actual source,
not guessed. See this plugin's README.md for the full attribution.

No sensor/alarm/AI-history/SD-card-playback/MQTT support - out of scope for
a camera-manager plugin.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import aiohttp
import boto3
from botocore.config import Config
from pycognito import AWSSRP

API_BASE = "https://api.x-sense-iot.com"
IPC_BASE = "https://ipc.x-sense-iot.com"
ADDX_BASE_BY_NODE = {
    "CN": "https://api.addx.live",
    "EU": "https://api-eu.vicohome.io",
    "US": "https://api-us.vicohome.io",
}
APP_CODE = "1400"
CLIENT_TYPE = "2"
APP_VERSION = "v1.40.0_20260612"
CAMERA_MODELS = {"SSC0A", "SSC0B"}

# Fixed device-identity block the X-Sense Android app ("VicoHome") sends with
# every camera-cloud (ADDX) request - required by the protocol itself, not a
# per-install secret.
ADDX_APP = {
    "appName": "VicoHome",
    "appType": "Android",
    "bundle": "com.ai.vicoo",
    "channelId": 1000,
    "countlyId": "b940908f19b8e858",
    "tenantId": "guard",
    "version": 200700500,
    "versionName": "2.7.5",
}

_COGNITO_CLIENT_CONFIG = Config(
    connect_timeout=15,
    read_timeout=15,
    retries={"total_max_attempts": 4, "mode": "standard"},
)


class XSenseError(RuntimeError):
    """Raised on any X-Sense login or API failure."""


@dataclass(frozen=True)
class XSenseCamera:
    serial: str
    name: str
    model: str


def _mac_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class XSenseClient:
    """A logged-in X-Sense session - camera discovery and live-stream URLs only."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._client_id: str | None = None
        self._client_secret: bytes | None = None
        self._region: str | None = None
        self._user_pool_id: str | None = None
        self._access_token: str | None = None
        self._node_type: str | None = None
        self._addx_session: dict[str, Any] | None = None

    async def login(self) -> None:
        await self._get_client_info()
        await asyncio.to_thread(self._cognito_login)

    async def list_cameras(self) -> list[XSenseCamera]:
        houses = await self._api_call("102007", utctimestamp="0")
        cameras: list[XSenseCamera] = []
        for house in houses or []:
            house_id = house.get("houseId")
            if not house_id:
                continue
            if self._node_type is None:
                self._node_type = _ipc_node_type(house.get("mqttRegion"))
            stations = await self._api_call("103007", houseId=house_id, utctimestamp="0")
            for entry in (stations or {}).get("cameras") or []:
                model = str(entry.get("category") or "")
                serial = entry.get("ipcSn")
                if model not in CAMERA_MODELS or not serial:
                    continue
                cameras.append(XSenseCamera(serial=serial, name=entry.get("ipcName") or serial, model=model))
        return cameras

    async def get_webrtc_ticket(self, serial: str) -> dict[str, Any]:
        """Return a fresh WebRTC signaling ticket for a camera's live view.

        X-Sense cameras have no pullable stream URL at all (confirmed against
        the reference implementation) - live view is real WebRTC, and this
        ticket (signal server, ICE servers, a signed session identity) is
        what a caller needs to open that WebRTC session. See webrtc_signal.py
        for how it's used. Each ticket is meant for one live-view session,
        not cached/reused across sessions.
        """
        data = await self._addx_call("/device/getWebrtcTicket", serialNumber=serial)
        if not isinstance(data, dict):
            raise XSenseError("X-Sense did not return a WebRTC ticket")
        return data

    # -- internals --

    async def _get_client_info(self) -> None:
        data = await self._api_call("101001", unauth=True)
        self._client_id = data["clientId"]
        self._client_secret = self._decode_secret(data["clientSecret"])
        self._region = data["cgtRegion"]
        self._user_pool_id = data["userPoolId"]

    def _decode_secret(self, encoded: str) -> bytes:
        value = base64.b64decode(encoded)
        prefix_len = len(APP_CODE)
        return value[prefix_len:-1]

    def _cognito_login(self) -> None:
        session = boto3.Session()
        cognito = session.client("cognito-idp", region_name=self._region, config=_COGNITO_CLIENT_CONFIG)
        srp = AWSSRP(
            username=self._username,
            password=self._password,
            pool_id=self._user_pool_id,
            client_id=self._client_id,
            client=cognito,
        )
        auth_params = srp.get_auth_params()
        if self._client_secret:
            auth_params["SECRET_HASH"] = self._secret_hash(self._username + self._client_id)
        try:
            response = cognito.initiate_auth(
                ClientId=self._client_id, AuthFlow="USER_SRP_AUTH", AuthParameters=auth_params
            )
        except Exception as exc:
            raise XSenseError(f"X-Sense login failed: {exc}") from exc

        user_id = response["ChallengeParameters"]["USERNAME"]
        challenge_response = srp.process_challenge(response["ChallengeParameters"], auth_params)
        if self._client_secret:
            challenge_response["SECRET_HASH"] = self._secret_hash(user_id + self._client_id)

        try:
            response = cognito.respond_to_auth_challenge(
                ClientId=self._client_id,
                ChallengeName="PASSWORD_VERIFIER",
                ChallengeResponses=challenge_response,
            )
        except Exception as exc:
            raise XSenseError(f"X-Sense login failed: {exc}") from exc
        self._access_token = response["AuthenticationResult"]["AccessToken"]

    def _secret_hash(self, data: str) -> str:
        return base64.b64encode(
            hmac.new(self._client_secret, data.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode()

    def _calculate_mac(self, data: dict[str, Any]) -> str:
        values = [_mac_scalar(value) for value in data.values()]
        mac_data = "".join(values).encode("utf-8") + (self._client_secret or b"")
        return hashlib.md5(mac_data).hexdigest()

    async def _api_call(self, code: str, *, unauth: bool = False, **kwargs: Any) -> Any:
        data = dict(kwargs)
        if unauth:
            headers = None
            mac = "abcdefg"
        else:
            headers = {"Authorization": self._access_token or ""}
            mac = self._calculate_mac(data)
        body = {
            **data,
            "clientType": CLIENT_TYPE,
            "mac": mac,
            "appVersion": APP_VERSION,
            "bizCode": code,
            "appCode": APP_CODE,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{API_BASE}/app", json=body, headers=headers) as response:
                result = await response.json()
                status = response.status
        if status >= 400:
            raise XSenseError(f"X-Sense API {code} failed: HTTP {status}/{result.get('message', 'unknown error')}")
        if "reCode" not in result:
            raise XSenseError(f"X-Sense API {code} returned an unexpected response")
        if result["reCode"] != 200:
            raise XSenseError(
                f"X-Sense API {code} failed: {result.get('errCode', 0)}/{result['reCode']} {result.get('reMsg')}"
            )
        return result["reData"]

    async def _register_ipc(self) -> dict[str, Any]:
        node_type = self._node_type or "US"
        data = {"userName": self._username, "nodeType": node_type, "language": "en"}
        mac = self._calculate_mac(data)
        body = {
            **data,
            "clientType": CLIENT_TYPE,
            "mac": mac,
            "appVersion": APP_VERSION,
            "bizCode": "C10101",
            "appCode": APP_CODE,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{IPC_BASE}/ipc", json=body, headers={"Authorization": self._access_token or ""}
            ) as response:
                result = await response.json()
                status = response.status
        if status >= 400 or str(result.get("reCode")) != "200":
            raise XSenseError(f"X-Sense IPC registration failed: {result.get('reMsg', 'unknown error')}")
        return result["reData"]

    async def _addx_call(self, endpoint: str, *, _retry: bool = True, **kwargs: Any) -> Any:
        if self._addx_session is None:
            self._addx_session = await self._register_ipc()
        session_info = self._addx_session
        base_url = ADDX_BASE_BY_NODE.get(session_info.get("nodeType"))
        if base_url is None:
            raise XSenseError(f"X-Sense returned an unknown region: {session_info.get('nodeType')}")
        body = {
            **kwargs,
            "countryNo": session_info["countryNo"],
            "language": session_info["language"],
            "app": ADDX_APP,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}{endpoint}",
                json=body,
                headers={"Authorization": session_info["token"], "Content-Type": "application/json"},
            ) as response:
                result = await response.json()
                status = response.status
        if status in (401, 403) and _retry:
            self._addx_session = None
            return await self._addx_call(endpoint, _retry=False, **kwargs)
        if status >= 400:
            raise XSenseError(f"X-Sense camera API {endpoint} failed: HTTP {status}/{result.get('msg', 'unknown error')}")
        if result.get("result") not in (0, None):
            if result.get("result") == -1024 and _retry:
                self._addx_session = None
                return await self._addx_call(endpoint, _retry=False, **kwargs)
            raise XSenseError(f"X-Sense camera API {endpoint} failed: {result.get('result')}/{result.get('msg')}")
        return result.get("data")


def _ipc_node_type(mqtt_region: str | None) -> str:
    if not mqtt_region or len(mqtt_region) <= 2:
        return "US"
    node_type = mqtt_region[:2].upper()
    return node_type if node_type in ADDX_BASE_BY_NODE else "US"
