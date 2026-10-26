"""2018 Frame (UN55LS03N, art API 0.97) replies from the diagnose reports on issue #35."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from samsungframetv import diagnose, exceptions, helper
from samsungframetv.async_art import SamsungTVAsyncArt

MATTE_LIST_097 = {
    "event": "matte_list",
    "matte_list": json.dumps([
        {"matte_id": "none"},
        {"matte_id": "modern_black"},
        {"matte_id": "modern_polar"},
        {"matte_id": "shadowbox_black"},
        {"matte_id": "shadowbox_polar"},
    ]),
}

REST_2018 = {"device": {"FrameTVSupport": "true", "model": "18_KANTM2_FRAME", "modelName": "UN55LS03N"}}


def test_matte_list_097() -> None:
    types, colours = helper.parse_matte_list(MATTE_LIST_097, include_colour=True)
    assert types == [{"matte_type": "none"}, {"matte_type": "modern"}, {"matte_type": "shadowbox"}]
    assert colours == [{"color": "black"}, {"color": "polar"}]


def test_rest_power_state() -> None:
    assert helper.rest_power_state(REST_2018) == "on"      # 2018 TV's don't report PowerState
    assert helper.rest_power_state({"device": {"PowerState": "standby"}}) == "standby"
    assert helper.rest_power_state({}) is None
    assert helper.rest_power_state(None) is None


@pytest.mark.asyncio
async def test_in_artmode_without_power_state() -> None:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv._get_device_info = AsyncMock(return_value=REST_2018)
    tv.get_artmode = AsyncMock(return_value="on")
    assert await tv.in_artmode()
    assert await tv.on()
    tv._get_device_info = AsyncMock(return_value={})
    assert not await tv.in_artmode()


@pytest.mark.asyncio
async def test_diagnose_marks_unknown_requests() -> None:
    async def unknown() -> None:
        raise exceptions.ResponseError("get_device_info request failed with error number -9")

    results: dict = {}
    await diagnose._step(results, "get_device_info", unknown, 1)
    assert results["get_device_info"]["unsupported"] is True
    assert results["get_device_info"]["ok"] is False
