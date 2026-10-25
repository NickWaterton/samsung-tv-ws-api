"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Samsung IP Control: JSON-RPC over HTTPS on port 1516 (1515 on pre 2020 TV's).

Enable on the TV with:
Settings -> General -> Network -> Expert Settings -> IP Remote (and Power On With Mobile)

Pairing (createAccessToken) only works while the TV is on and NOT in art mode, the
user has to press Allow on the TV. The token survives power cycles.

The TV handles one connection at a time, so calls are serialised with a lock.

Observed on 2021 and 2022 Frame TV's (October 2026):
    TV on        : powerControl=powerOn,  artModeControl=artModeOff, pictureMode=Movie
    Art Mode     : powerControl=powerOn,  artModeControl=artModeOn,  pictureMode=Ambient
    2022 standby : powerControl=powerOff, artModeControl=artModeOn,  pictureMode=Ambient (art flag is stale)
    2021 standby : port 1516 refuses connections
    2022 in art mode/standby: inputSourceControl, directVolumeControl and speakerSelectControl
                              return -32601 Method not found (they only work when watching TV)

Copyright (C) 2026 Nick Waterton <n.waterton@outlook.com>

SPDX-License-Identifier: LGPL-3.0
"""

import asyncio
import http.client
import json
import logging
import os
import ssl
import threading
from typing import Any, Dict, Optional

from . import exceptions

_LOGGING = logging.getLogger(__name__)

DEFAULT_PORT = 1516
LEGACY_PORT = 1515          # pre 2020 TV's
PAIR_TIMEOUT = 30           # time allowed to press Allow on the TV

ERROR_METHOD_NOT_FOUND = -32601
ERROR_UNAUTHORIZED = -32010
ERROR_STALE_TOKEN = -32700       # parse error, returned for a stale token

# power states returned by get_power_state()
POWER_OFF = "off"           # TV not reachable on the network
POWER_STANDBY = "standby"   # TV reachable, screen off
POWER_TV = "tv"             # TV on, watching TV/an input
POWER_ART = "art"           # TV on, in art mode
POWER_ON = "on"             # TV on, art mode could not be determined


class IPControlError(exceptions.HttpApiError):
    """Error returned by IP Control"""


class IPControlAuthError(IPControlError):
    """IP Control token missing or rejected, pair() again"""


class IPControlUnsupportedError(IPControlError):
    """Method not available on this TV, or not in the TV's current state"""


class IPControlRefusedError(IPControlError):
    """TV is on the network but refused the connection (2021 TV's in standby)"""


class IPControlUnreachableError(IPControlError):
    """TV did not answer (off, or not on the network)"""


class SamsungTVIPControl:
    """
    Synchronous Samsung IP Control client
    """

    def __init__(
        self,
        host: str,
        token: Optional[str] = None,
        token_file: Optional[str] = None,
        port: int = DEFAULT_PORT,
        timeout: float = 5,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.token_file = token_file
        self.token = token or self._read_token()
        self._legacy_tls = False
        self._lock = threading.Lock()

    def _read_token(self) -> Optional[str]:
        if self.token_file and os.path.isfile(self.token_file):
            with open(self.token_file) as f:
                return f.read().strip() or None
        return None

    def _write_token(self) -> None:
        if self.token_file and self.token:
            with open(self.token_file, "w") as f:
                f.write(self.token)

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE         # TV uses a self signed certificate
        if self._legacy_tls:
            ctx.set_ciphers("DEFAULT@SECLEVEL=0")   # older TV's use a small DH key
        return ctx

    def _post(self, payload: bytes, timeout: float) -> Dict[str, Any]:
        for attempt in (0, 1):
            conn = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self._ssl_context())
            try:
                conn.putrequest("POST", "/", skip_accept_encoding=True)
                conn.putheader("Accept", "application/json")
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", str(len(payload)))
                conn.endheaders()
                conn.send(payload)
                return json.loads(conn.getresponse().read().decode())
            except ssl.SSLError as e:
                if attempt == 0 and not self._legacy_tls and "dh key too small" in str(e).lower():
                    _LOGGING.debug("IP Control: DH key too small, retrying with SECLEVEL=0")
                    self._legacy_tls = True
                    continue
                raise IPControlError("TLS error talking to {}:{}: {}".format(self.host, self.port, e)) from e
            except ConnectionRefusedError as e:
                raise IPControlRefusedError("{}:{} refused the connection".format(self.host, self.port)) from e
            except (OSError, ValueError) as e:
                raise IPControlUnreachableError("{}:{} did not answer: {}".format(self.host, self.port, e)) from e
            finally:
                conn.close()
        raise IPControlError("IP Control request failed")

    def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        use_token: bool = True,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Send a JSON-RPC request, return the result dict.
        Raises IPControlError (or a subclass) on failure.
        """
        body: Dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if use_token:
            if not self.token:
                raise IPControlAuthError("No IP Control token, call pair() first")
            body["params"] = {"AccessToken": self.token, **(params or {})}
        elif params:
            body["params"] = params
        with self._lock:
            data = self._post(json.dumps(body).encode(), timeout or self.timeout)
        _LOGGING.debug("IP Control %s -> %s", method, data)
        error = data.get("error")
        if error is None and "code" in data and "result" not in data:
            error = data    # some errors are returned at the top level
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else str(error)
            if code == ERROR_METHOD_NOT_FOUND:
                raise IPControlUnsupportedError("{}: {} ({})".format(method, message, code))
            if code == ERROR_UNAUTHORIZED or (code == ERROR_STALE_TOKEN and use_token):
                raise IPControlAuthError("{}: token rejected: {} ({})".format(method, message, code))
            raise IPControlError("{}: {} ({})".format(method, message, code))
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    def pair(self) -> str:
        """
        Request a token, the user has to press Allow on the TV.
        TV must be on and not in art mode.
        """
        result = self.request("createAccessToken", use_token=False, timeout=PAIR_TIMEOUT)
        token = result.get("AccessToken")
        if not token:
            raise IPControlAuthError("No AccessToken returned: {}".format(result))
        self.token = token
        self._write_token()
        return token

    def get_power(self) -> str:
        """returns 'powerOn' or 'powerOff', art mode reports 'powerOn'"""
        return self.request("powerControl").get("power", "")

    def power_on(self) -> None:
        self.request("powerControl", {"power": "powerOn"})

    def power_off(self) -> None:
        self.request("powerControl", {"power": "powerOff"})

    def reboot(self) -> None:
        self.request("powerControl", {"power": "reboot"})

    def get_artmode(self) -> str:
        """
        returns 'on' or 'off'
        NOTE: stays 'on' when a 2022 TV is in standby, check get_power() first
        """
        return "on" if self.request("artModeControl").get("artMode") == "artModeOn" else "off"

    def set_artmode(self, on: bool) -> None:
        self.request("artModeControl", {"artMode": "artModeOn" if on else "artModeOff"})

    def get_tv_states(self) -> Dict[str, Any]:
        """speakerSelect, volume, mute, pictureSize, pictureMode, soundMode, inputSource"""
        return self.request("getTVStates")

    def get_input_source(self) -> str:
        return self.request("inputSourceControl").get("inputSource", "")

    def set_input_source(self, source: str) -> None:
        """source is eg HDMI1, only works when watching TV (not in art mode)"""
        self.request("inputSourceControl", {"inputSource": source})

    def get_volume(self) -> int:
        return self.request("directVolumeControl").get("volume", 0)

    def set_volume(self, volume: int) -> None:
        self.request("directVolumeControl", {"volume": int(volume)})

    def get_mute(self) -> bool:
        return self.request("muteControl").get("mute") == "muteOn"

    def set_mute(self, mute: bool) -> None:
        self.request("muteControl", {"mute": "muteOn" if mute else "muteOff"})

    def get_power_state(self) -> str:
        """
        returns one of POWER_OFF, POWER_STANDBY, POWER_TV, POWER_ART
        powerControl is checked first, as the art mode flag is stale in standby
        """
        try:
            power = self.get_power()
        except IPControlRefusedError:
            return POWER_STANDBY
        except IPControlUnreachableError:
            return POWER_OFF
        if power != "powerOn":
            return POWER_STANDBY
        return POWER_ART if self.get_artmode() == "on" else POWER_TV


class SamsungTVAsyncIPControl(SamsungTVIPControl):
    """
    Asyncio Samsung IP Control client, runs the blocking requests in an executor
    """

    async def async_request(self, method: str, params: Optional[Dict[str, Any]] = None, use_token: bool = True, timeout: Optional[float] = None) -> Dict[str, Any]:
        return await asyncio.get_running_loop().run_in_executor(None, lambda: self.request(method, params, use_token, timeout))

    async def async_call(self, name: str, *args: Any) -> Any:
        """run any of the synchronous methods in an executor, eg await ipc.async_call('get_power_state')"""
        return await asyncio.get_running_loop().run_in_executor(None, getattr(self, name), *args)

    async def async_pair(self) -> str:
        return await self.async_call("pair")

    async def async_get_power_state(self) -> str:
        return await self.async_call("get_power_state")


def derive_power_state(
    rest_info: Optional[Dict[str, Any]],
    artmode: Optional[str] = None,
    ip_state: Optional[str] = None,
) -> str:
    """
    Combine the signals we have into one power state.

    rest_info: REST /api/v2/ response, None if the TV did not answer
    artmode:   art websocket get_artmode() result ('on'/'off'), None if it did not answer
    ip_state:  SamsungTVIPControl.get_power_state(), None if IP Control is not used

    IP Control is the most reliable source, so it wins when available.
    REST PowerState is 'on' in art mode on 2022-2024 Frames, but 2025 Frames report
    'standby' while in art mode (issue #33), so on 2025+ the art channel is trusted.
    """
    if ip_state in (POWER_STANDBY, POWER_TV, POWER_ART):
        return ip_state
    if rest_info is None:
        return ip_state or POWER_OFF
    device = rest_info.get("device", {})
    power = device.get("PowerState", "on")      # pre 2018 TV's do not report PowerState
    if power == "on":
        if artmode == "on":
            return POWER_ART
        if artmode == "off":
            return POWER_TV
        return POWER_ON
    if power == "standby" and artmode == "on" and model_year(rest_info) >= 25:
        return POWER_ART
    return POWER_STANDBY


def model_year(rest_info: Optional[Dict[str, Any]]) -> int:
    """model year from REST info, eg model '22_PONTUSM_FTV' is 22, 0 if unknown"""
    try:
        return int((rest_info or {}).get("device", {}).get("model", "0_0").split("_")[0])
    except ValueError:
        return 0
