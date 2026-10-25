"""Tests for art module."""

import json
from unittest.mock import Mock, patch

import pytest

from samsungframetv import exceptions
from samsungframetv.art import SamsungTVArt
from samsungframetv.remote import SamsungTVWS

from .const import (
    D2D_SERVICE_MESSAGE_AVAILABLE_SAMPLE,
    D2D_SERVICE_MESSAGE_SEND_IMAGE_ERROR,
    MS_CHANNEL_CONNECT_SAMPLE,
    MS_CHANNEL_DISCONNECT_SAMPLE,
    MS_CHANNEL_READY_SAMPLE,
)

REQUEST_UUID = "07e72228-7110-4655-aaa6-d81b5188c219"

SET_ARTMODE_PAYLOAD = (
    '{"method": "ms.channel.emit", "params": {"event": "art_app_request", "to": "host", "data": "{\\"request\\": \\"set_artmode_status\\", \\"value\\": \\"on\\", \\"id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\", \\"request_id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\"}"}}'
)
AVAILABLE_PAYLOAD = (
    '{"method": "ms.channel.emit", "params": {"event": "art_app_request", "to": "host", "data": "{\\"request\\": \\"get_content_list\\", \\"category\\": null, \\"id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\", \\"request_id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\"}"}}'
)
CHANGE_MATTE_PAYLOAD = (
    '{"method": "ms.channel.emit", "params": {"event": "art_app_request", "to": "host", "data": "{\\"request\\": \\"change_matte\\", \\"content_id\\": \\"test\\", \\"matte_id\\": \\"none\\", \\"id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\", \\"request_id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\"}"}}'
)
SEND_IMAGE_PAYLOAD = (
    '{"method": "ms.channel.emit", "params": {"event": "art_app_request", "to": "host", "data": "{\\"request\\": \\"send_image\\", \\"file_type\\": \\"png\\", \\"request_id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\", \\"id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\", \\"conn_info\\": {\\"d2d_mode\\": \\"socket\\", \\"connection_id\\": 4091151321, \\"id\\": \\"07e72228-7110-4655-aaa6-d81b5188c219\\"}, \\"image_date\\": \\"2023:05:02 15:06:39\\", \\"matte_id\\": \\"none\\", \\"portrait_matte_id\\": \\"shadowbox_polar\\", \\"file_size\\": 0}"}}'
)


def d2d_reply(request_uuid: str = REQUEST_UUID, sample: str = "", **data) -> str:
    """Build a d2d_service_message reply correlated with request_uuid.

    The art requests wait for a reply carrying their id, so fixtures recorded
    with another id are rewritten to use request_uuid.
    """
    if sample:
        message = json.loads(sample)
        inner = json.loads(message["data"])
        inner["id"] = request_uuid
        message["data"] = json.dumps(inner)
        return json.dumps(message)
    data.setdefault("event", "*")
    data["id"] = request_uuid
    return json.dumps({"data": json.dumps(data), "event": "d2d_service_message"})


def test_create_connection_from_remote() -> None:
    connection = Mock()
    with patch(
        "samsungframetv.connection.websocket.create_connection"
    ) as connection_class, patch(
        "samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID
    ):
        connection_class.return_value = connection
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            d2d_reply(),
        ]

        tv_art = SamsungTVWS("127.0.0.1").art()
        tv_art.set_artmode("on")

        connection_class.assert_called_once_with(
            "ws://127.0.0.1:8001/api/v2/channels/com.samsung.art-app?name=U2Ftc3VuZ1R2UmVtb3Rl",
            5,
            sslopt={},
            connection="Connection: Upgrade",
        )


def test_create_connection_direct() -> None:
    connection = Mock()
    with patch(
        "samsungframetv.connection.websocket.create_connection"
    ) as connection_class, patch(
        "samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID
    ):
        connection_class.return_value = connection
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            d2d_reply(),
        ]

        tv_art = SamsungTVArt("127.0.0.1")
        tv_art.set_artmode("on")

        connection_class.assert_called_once_with(
            "ws://127.0.0.1:8001/api/v2/channels/com.samsung.art-app?name=U2Ftc3VuZ1R2UmVtb3Rl",
            None,
            sslopt={},
            connection="Connection: Upgrade",
        )


def test_set_artmode(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    with patch("samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID):
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            d2d_reply(),
        ]
        tv_art = SamsungTVArt("127.0.0.1")
        tv_art.set_artmode("on")

        connection.send.assert_called_once_with(SET_ARTMODE_PAYLOAD)


def test_set_available(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    with patch("samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID):
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            MS_CHANNEL_DISCONNECT_SAMPLE,
            d2d_reply(sample=D2D_SERVICE_MESSAGE_AVAILABLE_SAMPLE),
        ]
        tv_art = SamsungTVArt("127.0.0.1")
        content = tv_art.available()

        assert content[0]["content_id"] == "MY_F0011"
        connection.send.assert_called_once_with(AVAILABLE_PAYLOAD)


def test_change_matte(connection: Mock) -> None:
    with patch("samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID):
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            d2d_reply(event="matte_changed"),
        ]
        tv_art = SamsungTVArt("127.0.0.1")
        tv_art.change_matte("test", "none")

        connection.send.assert_called_once_with(CHANGE_MATTE_PAYLOAD)


def test_send_image_failure(connection: Mock) -> None:
    """Ensure send_image failure raises error and doesn't hang indefinitely."""
    with patch("samsungframetv.art.uuid.uuid4", return_value=REQUEST_UUID), patch(
        "samsungframetv.art.random.randrange", return_value=4091151321
    ):
        connection.recv.side_effect = [
            MS_CHANNEL_CONNECT_SAMPLE,
            MS_CHANNEL_READY_SAMPLE,
            d2d_reply(sample=D2D_SERVICE_MESSAGE_SEND_IMAGE_ERROR),
        ]
        tv_art = SamsungTVArt("127.0.0.1")
        tv_art._api_version = "4.3.4.0"     # D2D socket upload, skip the api_version request

        with pytest.raises(
            exceptions.ResponseError,
            match="send_image request failed with error number -1",
        ):
            tv_art.upload(
                b"", file_type="png", matte="none", date="2023:05:02 15:06:39"
            )

        connection.send.assert_called_once_with(SEND_IMAGE_PAYLOAD)
