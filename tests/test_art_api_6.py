"""2022 Frame on art API 6.0 (firmware from 2026-11): no go_to_standby event, and art mode 'on' in standby."""

from unittest.mock import AsyncMock, patch

import pytest

from samsungframetv.async_art import SamsungTVAsyncArt
from samsungframetv.ipcontrol import POWER_ART, POWER_STANDBY, POWER_TV, SamsungTVAsyncIPControl, SamsungTVIPControl

from .test_ipcontrol import fake_post, rest


def art_tv(device_info, artmode: str) -> SamsungTVAsyncArt:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv._get_device_info = AsyncMock(return_value=device_info)

    async def get_artmode() -> str:
        # like the reply going through process_event
        tv.art_mode = artmode == "on"
        return artmode

    tv.get_artmode = AsyncMock(side_effect=get_artmode)
    return tv


@pytest.mark.asyncio
async def test_power_state_clears_art_mode_in_standby() -> None:
    tv = art_tv(rest("standby"), "on")
    tv.art_mode = True      # last heard before standby, no go_to_standby event followed
    assert await tv.get_power_state() == POWER_STANDBY
    assert tv.art_mode is False


@pytest.mark.asyncio
async def test_power_state_sets_art_mode() -> None:
    tv = art_tv(rest("on"), "on")
    assert await tv.get_power_state() == POWER_ART
    assert tv.art_mode is True
    tv = art_tv(rest("on"), "off")
    tv.art_mode = True
    assert await tv.get_power_state() == POWER_TV
    assert tv.art_mode is False


@pytest.mark.asyncio
async def test_in_artmode_clears_art_mode_in_standby() -> None:
    tv = art_tv(rest("standby"), "on")
    tv.art_mode = True
    assert await tv.in_artmode() is False
    assert tv.art_mode is False


@pytest.mark.asyncio
async def test_in_artmode_uses_ipcontrol() -> None:
    # REST says on and the art channel says on, IP Control knows the TV is in standby
    tv = art_tv(rest("on"), "on")
    with patch.object(SamsungTVIPControl, "_post", fake_post("2022_standby")):
        ipc = SamsungTVAsyncIPControl("127.0.0.1", token="token")
        assert await tv.in_artmode(ipc) is False
    assert tv.art_mode is False
    with patch.object(SamsungTVIPControl, "_post", fake_post("2022_art")):
        ipc = SamsungTVAsyncIPControl("127.0.0.1", token="token")
        assert await tv.in_artmode(ipc) is True
    assert tv.art_mode is True


@pytest.mark.asyncio
async def test_close_forgets_art_mode() -> None:
    tv = art_tv(rest("on"), "on")
    tv.art_mode = True
    await tv.close()
    assert tv.art_mode is None
