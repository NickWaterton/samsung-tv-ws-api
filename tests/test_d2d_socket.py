"""Tests for connecting to the socket the TV opens for thumbnail and upload transfers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt

CONN_INFO = {"ip": "127.0.0.1", "port": "53092", "secured": True}


@pytest.mark.asyncio
async def test_async_secured_connect_waits() -> None:
    """a 2022 Frame abandoned get_thumbnail_list when we connected as soon as it answered"""
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    streams = (Mock(), Mock())
    with patch("samsungframetv.async_art.asyncio.open_connection", new=AsyncMock(return_value=streams)) as open_connection, \
            patch("samsungframetv.async_art.asyncio.sleep", new=AsyncMock()) as sleep:
        assert await tv._open_d2d_connection(CONN_INFO) == streams
        sleep.assert_awaited_once_with(1.0)
        assert open_connection.await_args.kwargs["ssl"] is not None

        sleep.reset_mock()
        await tv._open_d2d_connection(CONN_INFO, secured=False)
        sleep.assert_not_awaited()
        assert open_connection.await_args.kwargs["ssl"] is None


def test_sync_connect() -> None:
    tv = SamsungTVArt("127.0.0.1", port=8002, token="token")
    raw = Mock()
    with patch("samsungframetv.art.socket.socket", return_value=raw), patch("samsungframetv.art.time.sleep") as sleep:
        assert tv._open_d2d_socket(CONN_INFO, secured=False) is raw
    sleep.assert_not_called()
    raw.connect.assert_called_once_with(("127.0.0.1", 53092))
