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


def stream(*files: tuple) -> bytes:
    """the transfer socket's bytes: a length, a JSON header and the file, for each (num, total, id, data)"""
    out = b""
    for num, total, content_id, data in files:
        header = json.dumps({"num": num, "total": total, "fileID": content_id, "fileType": "jpg", "fileLength": len(data)}).encode()
        out += len(header).to_bytes(4, "big") + header + data
    return out


class FakeReader:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def readexactly(self, n: int) -> bytes:
        if len(self.data) < n:
            partial, self.data = self.data, b""
            raise asyncio.IncompleteReadError(partial, n)
        out, self.data = self.data[:n], self.data[n:]
        return out


class FakeSocket:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.close = Mock()

    def recv(self, n: int) -> bytes:
        out, self.data = self.data[:n], self.data[n:]
        return out


GOOD = b"\xff\xd8good"


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [
    [(0, 2, "SAM-S0335", GOOD)],    # bad item last: the TV closes the socket after the good one
    [(1, 2, "SAM-S0335", GOOD)],    # bad item first: the TV skips it, num follows the request order
])
async def test_async_list_skips_missing(files: list) -> None:
    tv = async_tv("4.3.4.0")
    tv._open_d2d_connection = AsyncMock(return_value=(FakeReader(stream(*files)), Mock()))
    assert await tv.get_thumbnail_list(["SAM-S0335", "SAM-S10000718"]) == {"SAM-S0335.jpg": GOOD}
    tv._open_d2d_connection = AsyncMock(return_value=(FakeReader(stream(*files)), Mock()))
    assert await tv.get_thumbnail(["SAM-S0335", "SAM-S10000718"]) == [GOOD]


@pytest.mark.asyncio
async def test_async_list_all_missing() -> None:
    tv = async_tv("4.3.4.0")
    tv._open_d2d_connection = AsyncMock(return_value=(FakeReader(b""), Mock()))
    with pytest.raises(exceptions.ThumbnailUnavailableError):
        await tv.get_thumbnail(["SAM-S2753", "SAM-S10000718"])


@pytest.mark.asyncio
async def test_async_one_by_one_keeps_the_others() -> None:
    """2.03 sends each thumbnail on its own socket"""
    tv = async_tv("2.03")
    tv._open_d2d_connection = AsyncMock(side_effect=[
        (FakeReader(stream((0, 1, "MY_F0020", GOOD))), Mock()),
        (FakeReader(b""), Mock()),
    ])
    assert await tv.get_thumbnail(["MY_F0020", "SAM-S10000497"], as_dict=True) == {"MY_F0020.jpg": GOOD}


def test_sync_list_skips_missing() -> None:
    tv = SamsungTVArt("127.0.0.1", port=8002, token="token")
    tv._send_art_request = Mock(return_value=REPLY)
    tv._open_d2d_socket = Mock(return_value=FakeSocket(stream((1, 2, "SAM-S0335", GOOD))))
    assert tv.get_thumbnail_list(["SAM-S10000718", "SAM-S0335"]) == {"SAM-S0335.jpg": bytearray(GOOD)}
    tv._open_d2d_socket = Mock(return_value=FakeSocket(b""))
    with pytest.raises(exceptions.ThumbnailUnavailableError):
        tv.get_thumbnail_list(["SAM-S10000718"])


def test_sync_one_by_one_keeps_the_others() -> None:
    tv = SamsungTVArt("127.0.0.1", port=8002, token="token")
    tv._legacy_api = Mock(return_value=False)
    tv._api_version = "2.03"
    tv._send_art_request = Mock(return_value=REPLY)
    tv._open_d2d_socket = Mock(side_effect=[FakeSocket(stream((0, 1, "MY_F0020", GOOD))), FakeSocket(b"")])
    assert tv.get_thumbnail(["MY_F0020", "SAM-S10000497"]) == [bytearray(GOOD)]
