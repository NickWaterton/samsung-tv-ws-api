"""Some Art Store items get no thumbnail: the TV closes the transfer socket without sending anything."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from samsungframetv import exceptions
from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt

REPLY = {"event": "thumbnail", "conn_info": json.dumps({"ip": "127.0.0.1", "port": "56680", "secured": False})}


def async_tv(api_version: str) -> SamsungTVAsyncArt:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv._api_version = api_version
    tv._legacy_api = AsyncMock(return_value=False)
    tv._send_art_request = AsyncMock(return_value=REPLY)
    return tv


def closed_reader() -> Mock:
    reader = Mock()
    reader.readexactly = AsyncMock(side_effect=asyncio.IncompleteReadError(b"", 4))
    return reader


@pytest.mark.asyncio
@pytest.mark.parametrize("api_version", ["2.03", "4.3.4.0"])
async def test_async_no_thumbnail(api_version: str) -> None:
    tv = async_tv(api_version)
    writer = Mock()
    tv._open_d2d_connection = AsyncMock(return_value=(closed_reader(), writer))
    with pytest.raises(exceptions.ThumbnailUnavailableError) as raised:
        await tv.get_thumbnail("SAM-S10000497")
    assert "SAM-S10000497" in str(raised.value)
    # still caught by code written for the old error
    assert isinstance(raised.value, asyncio.IncompleteReadError)
    assert isinstance(raised.value, exceptions.ResponseError)
    writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_async_partial_header_is_not_hidden() -> None:
    tv = async_tv("2.03")
    reader = Mock()
    reader.readexactly = AsyncMock(side_effect=asyncio.IncompleteReadError(b"\x00\x00", 4))
    tv._open_d2d_connection = AsyncMock(return_value=(reader, Mock()))
    with pytest.raises(asyncio.IncompleteReadError) as raised:
        await tv.get_thumbnail("SAM-S10000497")
    assert not isinstance(raised.value, exceptions.ThumbnailUnavailableError)


def test_sync_no_thumbnail() -> None:
    tv = SamsungTVArt("127.0.0.1", port=8002, token="token")
    tv._legacy_api = Mock(return_value=False)
    tv._api_version = "2.03"
    tv._send_art_request = Mock(return_value=REPLY)
    art_socket = Mock()
    art_socket.recv.return_value = b""
    tv._open_d2d_socket = Mock(return_value=art_socket)
    with pytest.raises(exceptions.ThumbnailUnavailableError):
        tv.get_thumbnail("SAM-S10000497")
    art_socket.close.assert_called_once()
