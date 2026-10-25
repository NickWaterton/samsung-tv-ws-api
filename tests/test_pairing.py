"""Tests for pair(), which gets a token on the remote control channel for any connection class."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt

from .const import MS_CHANNEL_CONNECT_SAMPLE

MS_CHANNEL_TIMEOUT_SAMPLE = '{"event": "ms.channel.timeOut"}'
NEW_TOKEN = str(json.loads(MS_CHANNEL_CONNECT_SAMPLE)["data"]["token"])


def opened_url(connection_class: Mock, call: int = 0) -> str:
    return connection_class.call_args_list[call].args[0]


def test_pair_without_token(connection: Mock, tmp_path: Path) -> None:
    token_file = tmp_path / "token.txt"
    connection.recv.side_effect = [MS_CHANNEL_CONNECT_SAMPLE]
    tv = SamsungTVArt("127.0.0.1", port=8002, token_file=str(token_file))
    with patch("samsungframetv.connection.websocket.create_connection", return_value=connection) as create:
        assert tv.pair(timeout=120) == NEW_TOKEN
    assert "/channels/samsung.remote.control?" in opened_url(create)
    assert "token=" not in opened_url(create)
    assert create.call_args.args[1] == 120
    assert token_file.read_text() == NEW_TOKEN


def test_pair_keeps_working_token(connection: Mock) -> None:
    connection.recv.side_effect = ['{"event": "ms.channel.connect", "data": {}}']
    tv = SamsungTVArt("127.0.0.1", port=8002, token="old")
    with patch("samsungframetv.connection.websocket.create_connection", return_value=connection) as create:
        assert tv.pair() == "old"
    assert create.call_count == 1
    assert opened_url(create).endswith("&token=old")


def test_pair_replaces_rejected_token(connection: Mock) -> None:
    """the TV forgets tokens after a long power off (issue #30)"""
    connection.recv.side_effect = [MS_CHANNEL_TIMEOUT_SAMPLE, MS_CHANNEL_CONNECT_SAMPLE]
    tv = SamsungTVArt("127.0.0.1", port=8002, token="stale")
    with patch("samsungframetv.connection.websocket.create_connection", return_value=connection) as create:
        assert tv.pair() == NEW_TOKEN
    assert create.call_count == 2
    assert "token=" not in opened_url(create, 1)
    assert tv.token == NEW_TOKEN


def test_pair_force(connection: Mock) -> None:
    connection.recv.side_effect = [MS_CHANNEL_CONNECT_SAMPLE]
    tv = SamsungTVArt("127.0.0.1", port=8002, token="old")
    with patch("samsungframetv.connection.websocket.create_connection", return_value=connection) as create:
        assert tv.pair(force=True) == NEW_TOKEN
    assert create.call_count == 1
    assert "token=" not in opened_url(create)


@pytest.mark.asyncio
async def test_async_pair_replaces_rejected_token(tmp_path: Path) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("stale")
    connection = Mock()
    connection.recv = AsyncMock(side_effect=[MS_CHANNEL_TIMEOUT_SAMPLE, MS_CHANNEL_CONNECT_SAMPLE])
    connection.close = AsyncMock()
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1", port=8002, token_file=str(token_file))
    with patch("samsungframetv.async_connection.connect", new=AsyncMock(return_value=connection)) as connect:
        assert await tv.pair(timeout=90) == NEW_TOKEN
    assert connect.await_count == 2
    assert connect.await_args_list[0].args[0].endswith("&token=stale")
    assert "/channels/samsung.remote.control?" in connect.await_args_list[1].args[0]
    assert "token=" not in connect.await_args_list[1].args[0]
    assert connect.await_args_list[1].kwargs["open_timeout"] == 90
    assert token_file.read_text() == NEW_TOKEN


@pytest.mark.asyncio
async def test_async_pair_keeps_working_token() -> None:
    connection = Mock()
    connection.recv = AsyncMock(side_effect=['{"event": "ms.channel.connect", "data": {}}'])
    connection.close = AsyncMock()
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1", port=8002, token="old")
    with patch("samsungframetv.async_connection.connect", new=AsyncMock(return_value=connection)) as connect:
        assert await tv.pair() == "old"
    assert connect.await_count == 1
