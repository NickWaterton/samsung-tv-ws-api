"""Tests for timeouts, listener recovery, reconnect and the art service circuit breaker."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from websockets.exceptions import ConnectionClosedError

from samsungframetv import async_art, exceptions
from samsungframetv.async_art import SamsungTVAsyncArt
from samsungframetv.async_remote import SamsungTVWSAsyncRemote
from samsungframetv.remote import SendRemoteKey

from .const import MS_CHANNEL_CONNECT_SAMPLE, MS_ERROR_SAMPLE


async def never_answer() -> str:
    await asyncio.Event().wait()
    return ""


@pytest.mark.asyncio
async def test_open_times_out_when_tv_never_answers(async_connection: Mock) -> None:
    async_connection.recv = AsyncMock(side_effect=never_answer)
    async_connection.close = AsyncMock()
    tv = SamsungTVWSAsyncRemote("127.0.0.1", token="token", timeout=0.05)
    with pytest.raises(exceptions.ConnectionFailure, match="did not answer"):
        await tv.open()
    async_connection.close.assert_awaited_once()
    assert tv.connection is None


@pytest.mark.asyncio
async def test_open_uses_default_timeout() -> None:
    connection = Mock()
    connection.recv = AsyncMock(side_effect=[MS_CHANNEL_CONNECT_SAMPLE])
    with patch("samsungframetv.async_connection.connect", new=AsyncMock(return_value=connection)) as connect:
        await SamsungTVWSAsyncRemote("127.0.0.1").open()
    assert connect.call_args.kwargs["open_timeout"] == 10


@pytest.mark.asyncio
async def test_listener_survives_failing_callback(async_connection: Mock) -> None:
    async_connection.recv = AsyncMock(
        side_effect=[MS_CHANNEL_CONNECT_SAMPLE, MS_ERROR_SAMPLE, MS_ERROR_SAMPLE, ConnectionClosedError(None, None)]
    )
    calls = []

    def callback(event, response):
        calls.append(event)
        raise ValueError("bad callback")

    tv = SamsungTVWSAsyncRemote("127.0.0.1")
    await tv.start_listening(callback)
    task = tv._recv_loop
    await task
    assert calls == ["ms.error", "ms.error"]
    assert tv._recv_loop is None


@pytest.mark.asyncio
async def test_send_reconnects_after_connection_lost(async_connection: Mock) -> None:
    """TV's drop the connection soon after standby (issue #16)"""
    async_connection.recv = AsyncMock(side_effect=[MS_CHANNEL_CONNECT_SAMPLE, MS_CHANNEL_CONNECT_SAMPLE])
    async_connection.close = AsyncMock()
    async_connection.send = AsyncMock(side_effect=[ConnectionClosedError(None, None), None])
    tv = SamsungTVWSAsyncRemote("127.0.0.1")
    await tv.send_command(SendRemoteKey.click("KEY_POWER"))
    assert async_connection.send.await_count == 2
    assert async_connection.send.await_args_list[0] == async_connection.send.await_args_list[1]


@pytest.mark.asyncio
async def test_reconnect_resumes_listening(async_connection: Mock) -> None:
    async_connection.close = AsyncMock()
    tv = SamsungTVWSAsyncRemote("127.0.0.1")
    tv.open = AsyncMock(return_value=async_connection)
    callback = Mock()
    with patch.object(tv, "_do_start_listening", new=AsyncMock()) as listen:
        await tv.start_listening(callback)
        await tv._recv_loop
        await tv._reconnect()
        await tv._recv_loop
    assert listen.await_count == 2
    assert listen.await_args.args[0] is callback


def art_tv() -> SamsungTVAsyncArt:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv.start_listening = AsyncMock()
    tv.send_command = AsyncMock()
    tv.close = AsyncMock()
    return tv


@pytest.mark.asyncio
async def test_art_breaker_trips_after_repeated_timeouts() -> None:
    tv = art_tv()
    tv.wait_for_response = AsyncMock(return_value=None)
    for _ in range(async_art.ART_FAILURE_THRESHOLD):
        assert await tv._send_art_request({"request": "get_artmode_status"}) is None
    tv.close.assert_awaited_once()
    assert not tv.art_service_available

    # fails fast, still an AssertionError for existing callers
    with pytest.raises(AssertionError):
        await tv._send_art_request({"request": "get_artmode_status"})
    with pytest.raises(exceptions.ArtServiceUnavailableError):
        await tv.get_artmode()
    assert tv.wait_for_response.await_count == async_art.ART_FAILURE_THRESHOLD


@pytest.mark.asyncio
async def test_art_breaker_backs_off_then_recovers() -> None:
    tv = art_tv()
    tv.wait_for_response = AsyncMock(return_value=None)
    now = [1000.0]
    with patch("samsungframetv.async_art.time.monotonic", side_effect=lambda: now[0]):
        for _ in range(async_art.ART_FAILURE_THRESHOLD):
            await tv._send_art_request({"request": "get_artmode_status"})
        assert tv._art_cooldown == async_art.ART_COOLDOWN_START

        # after the cooldown one timeout is enough to back off again, for twice as long
        now[0] += async_art.ART_COOLDOWN_START
        assert tv.art_service_available
        await tv._send_art_request({"request": "get_artmode_status"})
        assert tv._art_cooldown == async_art.ART_COOLDOWN_START * 2
        assert not tv.art_service_available

        # an answer resets everything
        now[0] += tv._art_cooldown
        tv.wait_for_response = AsyncMock(return_value={"value": "on"})
        assert await tv._send_art_request({"request": "get_artmode_status"}) == {"value": "on"}
        assert tv._art_cooldown == 0 and tv._art_timeouts == 0


@pytest.mark.asyncio
async def test_art_breaker_cooldown_is_capped() -> None:
    tv = art_tv()
    tv.wait_for_response = AsyncMock(return_value=None)
    for _ in range(10):
        tv._art_unavailable_until = 0.0
        await tv._send_art_request({"request": "get_artmode_status"})
    assert tv._art_cooldown == async_art.ART_COOLDOWN_MAX


@pytest.mark.asyncio
async def test_art_reset_service() -> None:
    tv = art_tv()
    tv.wait_for_response = AsyncMock(return_value=None)
    for _ in range(async_art.ART_FAILURE_THRESHOLD):
        await tv._send_art_request({"request": "get_artmode_status"})
    tv.reset_art_service()
    assert tv.art_service_available
    assert tv._art_cooldown == 0


@pytest.mark.asyncio
async def test_art_open_times_out_waiting_for_ready(async_connection: Mock) -> None:
    replies = iter([MS_CHANNEL_CONNECT_SAMPLE])

    async def recv() -> str:
        return next(replies, None) or await never_answer()

    async_connection.recv = AsyncMock(side_effect=recv)
    async_connection.close = AsyncMock()
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1", token="token", timeout=0.05)
    with pytest.raises(exceptions.ConnectionFailure, match="ms.channel.ready"):
        await tv.open()
