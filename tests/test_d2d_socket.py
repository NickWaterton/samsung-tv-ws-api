"""Tests for connecting to the socket the TV opens for thumbnail and upload transfers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt

CONN_INFO = {"ip": "127.0.0.1", "port": "53092", "secured": True}


@pytest.mark.asyncio
async def test_async_connect_retries_until_tv_listens() -> None:
    """a 2022 Frame reset the connection when we connected as soon as it answered get_thumbnail_list"""
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    streams = (Mock(), Mock())
    with patch("samsungframetv.async_art.asyncio.open_connection",
               new=AsyncMock(side_effect=[ConnectionResetError(), ConnectionRefusedError(), streams])) as open_connection, \
            patch("samsungframetv.helper.D2D_RETRY_DELAY", 0):
        assert await tv._open_d2d_connection(CONN_INFO) == streams
    assert open_connection.await_count == 3
    assert open_connection.await_args.kwargs["ssl"] is not None


@pytest.mark.asyncio
async def test_async_connect_gives_up() -> None:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    with patch("samsungframetv.async_art.asyncio.open_connection",
               new=AsyncMock(side_effect=ConnectionResetError())) as open_connection, \
            patch("samsungframetv.helper.D2D_RETRY_DELAY", 0):
        with pytest.raises(ConnectionResetError):
            await tv._open_d2d_connection(CONN_INFO, secured=False)
    assert open_connection.await_count == 4
    assert open_connection.await_args.kwargs["ssl"] is None


def test_sync_connect_retries_until_tv_listens() -> None:
    tv = SamsungTVArt("127.0.0.1", port=8002, token="token")
    raw = Mock()
    raw.connect.side_effect = [ConnectionResetError(), None]
    with patch("samsungframetv.art.socket.socket", return_value=raw), \
            patch("samsungframetv.helper.D2D_RETRY_DELAY", 0):
        assert tv._open_d2d_socket(CONN_INFO, secured=False) is raw
    assert raw.connect.call_count == 2
    raw.close.assert_called_once()
