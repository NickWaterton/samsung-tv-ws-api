"""Tests for IP Control and power state derivation.

Fixtures were recorded from a 2021 and a 2022 Frame TV in each state.
"""

import json
from typing import Any, Dict
from unittest.mock import patch

import pytest

from samsungframetv import ipcontrol
from samsungframetv.ipcontrol import (
    POWER_ART,
    POWER_OFF,
    POWER_ON,
    POWER_STANDBY,
    POWER_TV,
    SamsungTVAsyncIPControl,
    SamsungTVIPControl,
    derive_power_state,
)

with open("tests/fixtures/ipcontrol_frame_2021_2022.json") as file:
    FIXTURES: Dict[str, Dict[str, Any]] = json.load(file)


def fake_post(scenario: str):
    """Answer IP Control requests from a recorded scenario."""

    def _post(self, payload: bytes, timeout: float) -> Dict[str, Any]:
        method = json.loads(payload)["method"]
        reply = FIXTURES[scenario][method]
        if isinstance(reply, str):
            if "refused" in reply:
                raise ipcontrol.IPControlRefusedError(reply)
            raise ipcontrol.IPControlUnreachableError(reply)
        return reply

    return _post


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("2021_on", POWER_TV),
        ("2021_art", POWER_ART),
        # recorded: powerControl timed out, later calls were refused. A timeout
        # reads as off; get_power_state() on the art classes then asks REST.
        ("2021_standby", POWER_OFF),
        ("2022_on", POWER_TV),
        ("2022_art", POWER_ART),
        ("2022_standby", POWER_STANDBY),
    ],
)
def test_ipcontrol_power_state(scenario: str, expected: str) -> None:
    with patch.object(SamsungTVIPControl, "_post", fake_post(scenario)):
        tv = SamsungTVIPControl("127.0.0.1", token="token")
        assert tv.get_power_state() == expected


def test_ipcontrol_2022_standby_art_flag_is_stale() -> None:
    """The art flag stays on in standby, which is why power is checked first."""
    with patch.object(SamsungTVIPControl, "_post", fake_post("2022_standby")):
        tv = SamsungTVIPControl("127.0.0.1", token="token")
        assert tv.get_artmode() == "on"
        assert tv.get_power() == "powerOff"


def test_ipcontrol_unreachable_is_off() -> None:
    def _post(self, payload: bytes, timeout: float) -> Dict[str, Any]:
        raise ipcontrol.IPControlUnreachableError("timed out")

    with patch.object(SamsungTVIPControl, "_post", _post):
        assert SamsungTVIPControl("127.0.0.1", token="token").get_power_state() == POWER_OFF


def test_ipcontrol_method_not_found() -> None:
    with patch.object(SamsungTVIPControl, "_post", fake_post("2022_art")):
        tv = SamsungTVIPControl("127.0.0.1", token="token")
        with pytest.raises(ipcontrol.IPControlUnsupportedError):
            tv.get_input_source()


def test_ipcontrol_states_and_input() -> None:
    with patch.object(SamsungTVIPControl, "_post", fake_post("2022_on")):
        tv = SamsungTVIPControl("127.0.0.1", token="token")
        assert tv.get_input_source() == "HDMI1"
        assert tv.get_tv_states()["pictureMode"] == "Movie"
        assert tv.get_volume() == 21
        assert tv.get_mute() is False


def test_ipcontrol_needs_token() -> None:
    with pytest.raises(ipcontrol.IPControlAuthError):
        SamsungTVIPControl("127.0.0.1").get_power()


def test_ipcontrol_stale_token() -> None:
    def _post(self, payload: bytes, timeout: float) -> Dict[str, Any]:
        return {"code": -32700, "message": "Parse error"}

    with patch.object(SamsungTVIPControl, "_post", _post):
        with pytest.raises(ipcontrol.IPControlAuthError):
            SamsungTVIPControl("127.0.0.1", token="old").get_power()


def test_ipcontrol_pair_saves_token(tmp_path) -> None:
    token_file = tmp_path / "token.txt"

    def _post(self, payload: bytes, timeout: float) -> Dict[str, Any]:
        body = json.loads(payload)
        assert body["method"] == "createAccessToken"
        assert "params" not in body
        return {"jsonrpc": "2.0", "id": "1", "result": {"AccessToken": "abc123"}}

    with patch.object(SamsungTVIPControl, "_post", _post):
        tv = SamsungTVIPControl("127.0.0.1", token_file=str(token_file))
        assert tv.pair() == "abc123"
    assert token_file.read_text() == "abc123"
    assert SamsungTVIPControl("127.0.0.1", token_file=str(token_file)).token == "abc123"


def test_ipcontrol_sends_token() -> None:
    sent = {}

    def _post(self, payload: bytes, timeout: float) -> Dict[str, Any]:
        sent.update(json.loads(payload))
        return {"jsonrpc": "2.0", "id": "1", "result": {}}

    with patch.object(SamsungTVIPControl, "_post", _post):
        SamsungTVIPControl("127.0.0.1", token="token").set_input_source("HDMI2")
    assert sent["params"] == {"AccessToken": "token", "inputSource": "HDMI2"}


@pytest.mark.asyncio
async def test_async_ipcontrol_power_state() -> None:
    with patch.object(SamsungTVIPControl, "_post", fake_post("2022_art")):
        tv = SamsungTVAsyncIPControl("127.0.0.1", token="token")
        assert await tv.async_get_power_state() == POWER_ART


def rest(power: str, model: str = "22_PONTUSM_FTV") -> Dict[str, Any]:
    return {"device": {"PowerState": power, "model": model}}


@pytest.mark.parametrize(
    "rest_info,artmode,ip_state,expected",
    [
        # IP Control wins when it gives an answer
        (rest("on"), "off", POWER_ART, POWER_ART),
        (rest("standby"), "on", POWER_STANDBY, POWER_STANDBY),
        (None, None, POWER_OFF, POWER_OFF),
        # REST + art channel
        (None, None, None, POWER_OFF),
        (rest("on"), "on", None, POWER_ART),
        (rest("on"), "off", None, POWER_TV),
        (rest("on"), None, None, POWER_ON),
        (rest("standby"), None, None, POWER_STANDBY),
        # 2022 standby: art channel can still say on, REST standby wins
        (rest("standby"), "on", None, POWER_STANDBY),
        # 2025 reports standby while in art mode (issue #33)
        (rest("standby", "25_PTM_FTV"), "on", None, POWER_ART),
        (rest("standby", "25_PTM_FTV"), "off", None, POWER_STANDBY),
        # IP Control unreachable, but REST answers
        (rest("on"), "off", POWER_OFF, POWER_TV),
        # old TV's without PowerState
        ({"device": {}}, "on", None, POWER_ART),
    ],
)
def test_derive_power_state(rest_info, artmode, ip_state, expected) -> None:
    assert derive_power_state(rest_info, artmode, ip_state) == expected


def test_model_year() -> None:
    assert ipcontrol.model_year(rest("on", "25_PTM_FTV")) == 25
    assert ipcontrol.model_year({"device": {"model": "UE55KS7000"}}) == 0
    assert ipcontrol.model_year(None) == 0


def test_ipcontrol_refused_is_standby() -> None:
    def _post(self, payload: bytes, timeout: float) -> Dict[str, Any]:
        raise ipcontrol.IPControlRefusedError("refused")

    with patch.object(SamsungTVIPControl, "_post", _post):
        assert SamsungTVIPControl("127.0.0.1", token="token").get_power_state() == POWER_STANDBY


def async_art(device_info: Dict[str, Any], artmode: Any):
    """SamsungTVAsyncArt with REST and art channel answers patched."""
    from unittest.mock import AsyncMock

    from samsungframetv.async_art import SamsungTVAsyncArt

    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv._get_device_info = AsyncMock(return_value=device_info)
    if isinstance(artmode, Exception):
        tv.get_artmode = AsyncMock(side_effect=artmode)
    else:
        tv.get_artmode = AsyncMock(return_value=artmode)
    return tv


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "device_info,artmode,expected",
    [
        (rest("on"), "on", True),
        (rest("on"), "off", False),
        (rest("standby"), "on", False),
        (rest("standby", "25_PTM_FTV"), "on", True),
        ({}, "on", False),
    ],
)
async def test_async_in_artmode(device_info, artmode, expected) -> None:
    assert await async_art(device_info, artmode).in_artmode() is expected


@pytest.mark.asyncio
async def test_async_get_power_state() -> None:
    assert await async_art({}, "on").get_power_state() == POWER_OFF
    assert await async_art(rest("on"), "off").get_power_state() == POWER_TV
    assert await async_art(rest("on"), TimeoutError()).get_power_state() == POWER_ON
    assert await async_art(rest("standby", "25_PTM_FTV"), "on").get_power_state() == POWER_ART


@pytest.mark.asyncio
async def test_async_get_power_state_prefers_ipcontrol() -> None:
    tv = async_art(rest("on"), "off")
    with patch.object(SamsungTVIPControl, "_post", fake_post("2022_art")):
        ipc = SamsungTVAsyncIPControl("127.0.0.1", token="token")
        assert await tv.get_power_state(ipc) == POWER_ART
    tv.get_artmode.assert_not_called()


@pytest.mark.asyncio
async def test_async_supported_without_frame_flag() -> None:
    """S95H reports FrameTVSupport false but answers on the art channel (issue #34)."""
    info = {"device": {"FrameTVSupport": "false", "PowerState": "on"}}
    assert await async_art(info, "off").supported() is True
    assert await async_art(info, TimeoutError()).supported() is False
