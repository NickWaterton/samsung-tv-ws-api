"""Tests for older art API's (2017/2018 Frames), matte list formats, ipv6 and pairing errors.

Binary upload and inline thumbnails follow upstream xchwarze/samsung-tv-ws-api (art API 0.97)
and JustifyTechExpenses/frame-daily-plan (2017 Frame, art API 1.07).
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from samsungframetv import exceptions, helper
from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt
from samsungframetv.async_remote import SamsungTVWSAsyncRemote
from samsungframetv.connection import SamsungTVWSBaseConnection
from samsungframetv.remote import SamsungTVWS

from .const import MS_CHANNEL_CONNECT_SAMPLE, MS_CHANNEL_READY_SAMPLE
from .test_art import REQUEST_UUID, d2d_reply

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00{not json}\xff\xd9"
MS_CHANNEL_TIMEOUT_SAMPLE = '{"event": "ms.channel.timeOut"}'

# 2018 UN55LS03NA reply from issue #35 (shortened)
MATTE_TYPES_2018 = {
    "matte_types": [
        {"matte_id": "none"},
        {"matte_id": "modern_black"},
        {"matte_id": "modern_polar"},
        {"matte_id": "shadowbox_black"},
        {"matte_id": "shadowbox_polar"},
    ]
}
MATTE_LIST_NEW = {
    "matte_type_list": json.dumps([{"matte_type": "none"}, {"matte_type": "shadowbox"}]),
    "matte_color_list": json.dumps([{"color": "polar"}]),
}


def inline_thumbnail_frame(request_uuid: str = REQUEST_UUID) -> bytes:
    """d2d message followed by the thumbnail in the same binary frame"""
    return d2d_reply(request_uuid, event="thumbnail").encode() + b"\n" + JPEG


def test_process_api_response_binary_tail() -> None:
    frame = helper.process_api_response(inline_thumbnail_frame())
    assert frame["event"] == "d2d_service_message"
    assert frame["binary"] == JPEG
    assert json.loads(frame["data"])["event"] == "thumbnail"


def test_process_api_response_braces_in_strings() -> None:
    header = json.dumps({"event": "x", "data": "a } b { c"}).encode()
    frame = helper.process_api_response(b"\x00\x2a" + header + JPEG)
    assert frame["data"] == "a } b { c"
    assert frame["binary"] == JPEG


def test_process_api_response_plain_json_and_garbage() -> None:
    assert helper.process_api_response(b'{"event": "x"}') == {"event": "x"}
    assert "binary" not in helper.process_api_response('{"event": "x"}')
    with pytest.raises(exceptions.ResponseError):
        helper.process_api_response(b"\xff\xd8 no json here")


def test_binary_send_image_layout() -> None:
    payload = helper.binary_send_image({"request": "send_image", "id": "abc"}, JPEG)
    header_len = int.from_bytes(payload[:2], "big")
    header = json.loads(payload[2 : 2 + header_len])
    assert header["method"] == "ms.channel.emit"
    assert header["params"]["event"] == "art_app_request"
    assert json.loads(header["params"]["data"]) == {"request": "send_image", "id": "abc"}
    assert payload[2 + header_len :] == JPEG


@pytest.mark.parametrize(
    "version,legacy",
    [("0.97", True), ("1.07", True), ("2.03", False), ("4.3.4.0", False), (None, False), ("", False)],
)
def test_is_legacy_art_api(version, legacy) -> None:
    assert helper.is_legacy_art_api(version) is legacy


def test_parse_matte_list_2018_format() -> None:
    """issue #35: 2018 Frames send combined matte ids"""
    types, colours = helper.parse_matte_list(MATTE_TYPES_2018, include_colour=True)
    assert types == [{"matte_type": "none"}, {"matte_type": "modern"}, {"matte_type": "shadowbox"}]
    assert colours == [{"color": "black"}, {"color": "polar"}]
    assert helper.parse_matte_list(MATTE_TYPES_2018) == types


def test_parse_matte_list_new_format() -> None:
    assert helper.parse_matte_list(MATTE_LIST_NEW) == [{"matte_type": "none"}, {"matte_type": "shadowbox"}]
    assert helper.parse_matte_list(MATTE_LIST_NEW, True)[1] == [{"color": "polar"}]
    assert helper.parse_matte_list({"matte_type_list": "[]"}, True) == ([], [])


def test_ipv6_urls() -> None:
    tv = SamsungTVWSBaseConnection("fe80::1", endpoint="x", port=8002, token="abc")
    assert tv._format_websocket_url("x").startswith("wss://[fe80::1]:8002/api/v2/channels/x?")
    assert tv._format_rest_url("") == "https://[fe80::1]:8002/api/v2/"
    ipv4 = SamsungTVWSBaseConnection("192.168.1.2", endpoint="x")
    assert ipv4._format_rest_url("") == "http://192.168.1.2:8001/api/v2/"


def test_no_token_none_in_url() -> None:
    tv = SamsungTVWSBaseConnection("192.168.1.2", endpoint="x", port=8002)
    assert "token" not in tv._format_websocket_url("x")
    tv.token = "abc"
    assert tv._format_websocket_url("x").endswith("&token=abc")


def test_channel_timeout_error(connection: Mock) -> None:
    connection.recv.side_effect = [MS_CHANNEL_TIMEOUT_SAMPLE]
    with patch.object(SamsungTVWS, "_get_rest_api"):
        tv = SamsungTVWS("127.0.0.1", token="wrong")
    with pytest.raises(exceptions.ChannelTimeoutError):
        tv.open()
    connection.close.assert_called_once()


@pytest.mark.asyncio
async def test_async_channel_timeout_error(async_connection: Mock) -> None:
    async_connection.recv = AsyncMock(side_effect=[MS_CHANNEL_TIMEOUT_SAMPLE])
    async_connection.close = AsyncMock()
    tv = SamsungTVWSAsyncRemote("127.0.0.1", token="wrong")
    with pytest.raises(exceptions.ConnectionFailure) as err:
        await tv.open()
    assert isinstance(err.value, exceptions.ChannelTimeoutError)
    assert err.value.args[0] == {"event": "ms.channel.timeOut"}


def test_legacy_upload_sends_one_binary_message(connection: Mock) -> None:
    with patch("samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID):
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            d2d_reply(event="image_added", content_id="MY_F0042"),
        ]
        tv_art = SamsungTVArt("127.0.0.1")
        tv_art._api_version = "1.07"
        assert tv_art.upload(JPEG, file_type="jpeg", matte="none") == "MY_F0042"

    connection.send.assert_not_called()
    payload = connection.send_binary.call_args.args[0]
    header_len = int.from_bytes(payload[:2], "big")
    request = json.loads(json.loads(payload[2 : 2 + header_len])["params"]["data"])
    assert request == {"request": "send_image", "file_type": "JPEG", "matte_id": "none", "id": REQUEST_UUID}
    assert payload[2 + header_len :] == JPEG


def test_inline_thumbnail(connection: Mock) -> None:
    with patch("samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID):
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            inline_thumbnail_frame(),
        ]
        tv_art = SamsungTVArt("127.0.0.1")
        tv_art._api_version = "1.07"
        assert tv_art.get_thumbnail("MY_F0001") == bytearray(JPEG)


def test_matte_list_2018(connection: Mock) -> None:
    with patch("samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID):
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            d2d_reply(event="matte_list", **MATTE_TYPES_2018),
        ]
        types, colours = SamsungTVArt("127.0.0.1").get_matte_list(include_colour=True)
    assert {"matte_type": "shadowbox"} in types
    assert {"color": "polar"} in colours


def async_art_tv() -> SamsungTVAsyncArt:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv.start_listening = AsyncMock()
    return tv


@pytest.mark.asyncio
async def test_async_legacy_upload() -> None:
    tv = async_art_tv()
    tv._api_version = "0.97"

    async def send(payload):
        # the TV answers with image_added
        tv.pending_requests["image_added"].set_result(
            {"data": json.dumps({"event": "image_added", "content_id": "MY_F0042"})}
        )

    tv.connection = Mock(send=AsyncMock(side_effect=send))
    assert await tv.upload(JPEG, file_type="jpg", matte="none") == "MY_F0042"
    payload = tv.connection.send.await_args.args[0]
    assert payload.endswith(JPEG)


@pytest.mark.asyncio
async def test_async_inline_thumbnail() -> None:
    tv = async_art_tv()
    tv._send_art_request = AsyncMock(return_value={"event": "thumbnail", "binary": JPEG})
    assert await tv.get_thumbnail("MY_F0001") == JPEG
    assert await tv.get_thumbnail(["MY_F0001"], as_dict=True) == {"MY_F0001.jpg": JPEG}


@pytest.mark.asyncio
async def test_async_wait_for_response_keeps_binary() -> None:
    tv = async_art_tv()
    tv.pending_requests["abc"] = fut = __import__("asyncio").get_running_loop().create_future()
    fut.set_result({"data": json.dumps({"event": "thumbnail"}), "binary": JPEG})
    assert (await tv.wait_for_response("abc"))["binary"] == JPEG


@pytest.mark.asyncio
async def test_async_api_version_asks_new_api_first() -> None:
    tv = async_art_tv()
    tv._send_art_request = AsyncMock(return_value={"version": "4.3.4.0"})
    assert await tv.get_api_version() == "4.3.4.0"
    assert tv._send_art_request.await_args_list[0].args[0] == {"request": "api_version"}
    assert tv._send_art_request.await_count == 1

    # old TV's answer the new request with an error
    tv._send_art_request = AsyncMock(side_effect=[exceptions.ResponseError("old"), {"version": "1.07"}])
    assert await tv.get_api_version() == "1.07"
    assert await tv._legacy_api() is True


@pytest.mark.asyncio
async def test_async_matte_list_2018() -> None:
    tv = async_art_tv()
    tv._send_art_request = AsyncMock(return_value=MATTE_TYPES_2018)
    types = await tv.get_matte_list()
    assert {"matte_type": "modern"} in types
