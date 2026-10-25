"""Tests for remote module."""

from unittest.mock import Mock, call, patch

import pytest

from samsungframetv.exceptions import ConnectionFailure, HttpApiError
from samsungframetv.remote import SamsungTVWS

from .const import (
    ED_APPS_LAUNCH_SAMPLE,
    ED_EDENTV_UPDATE_SAMPLE,
    ED_INSTALLED_APP_SAMPLE,
    MS_CHANNEL_CONNECT_SAMPLE,
    MS_ERROR_SAMPLE,
    MS_VOICEAPP_HIDE_SAMPLE,
)


def test_connect(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    connection.recv = Mock(side_effect=[MS_CHANNEL_CONNECT_SAMPLE])
    tv = SamsungTVWS("127.0.0.1")
    tv.open()
    assert tv.token == 123456789


def test_connect_with_extra_event(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    connection.recv = Mock(
        side_effect=[
            MS_VOICEAPP_HIDE_SAMPLE,
            ED_EDENTV_UPDATE_SAMPLE,
            MS_CHANNEL_CONNECT_SAMPLE,
        ]
    )
    tv = SamsungTVWS("127.0.0.1")
    tv.open()
    assert tv.token == 123456789


def test_connection_failure(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    connection.recv = Mock(side_effect=[MS_ERROR_SAMPLE])
    tv = SamsungTVWS("127.0.0.1")
    with pytest.raises(ConnectionFailure):
        tv.open()


def test_send_key(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    connection.recv.side_effect = [MS_CHANNEL_CONNECT_SAMPLE]
    tv = SamsungTVWS("127.0.0.1")
    tv.send_key("KEY_POWER")
    connection.send.assert_called_once_with(
        '{"method": "ms.remote.control", "params": {'
        '"Cmd": "Click", '
        '"DataOfCmd": "KEY_POWER", '
        '"Option": "false", '
        '"TypeOfRemote": "SendRemoteKey"'
        "}}"
    )


def test_app_list(connection: Mock) -> None:
    """Ensure valid app_list data can be parsed."""
    connection.recv.side_effect = [
        MS_CHANNEL_CONNECT_SAMPLE,
        ED_INSTALLED_APP_SAMPLE,
    ]
    tv = SamsungTVWS("127.0.0.1")
    assert tv.app_list() == [
        {
            "appId": "111299001912",
            "app_type": 2,
            "icon": "/opt/share/webappservice/apps_icon/FirstScreen/111299001912/250x250.png",
            "is_lock": 0,
            "name": "YouTube",
        },
        {
            "appId": "3201608010191",
            "app_type": 2,
            "icon": "/opt/share/webappservice/apps_icon/FirstScreen/3201608010191/250x250.png",
            "is_lock": 0,
            "name": "Deezer",
        },
    ]


def test_app_list_invalid(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    connection.recv.side_effect = [
        MS_CHANNEL_CONNECT_SAMPLE,
        ED_APPS_LAUNCH_SAMPLE,
    ]
    tv = SamsungTVWS("127.0.0.1")
    assert tv.app_list() is None
    connection.send.assert_called_once_with(
        '{"method": "ms.channel.emit", "params": {"event": "ed.installedApp.get", "to": "host"}}'
    )


def test_send_hold_key(connection: Mock) -> None:
    """Ensure simple data can be parsed."""
    connection.recv.side_effect = [MS_CHANNEL_CONNECT_SAMPLE]

    tv = SamsungTVWS("127.0.0.1")
    with patch("samsungframetv.connection.time.sleep") as patch_sleep:
        tv.hold_key("KEY_POWER", 3)

    assert patch_sleep.call_count == 3
    assert patch_sleep.call_args_list == [call(1), call(3), call(1)]


def test_create_when_tv_unreachable(connection: Mock) -> None:
    """Creating the remote must not raise when the REST api is unreachable."""
    with patch(
        "samsungframetv.rest.SamsungTVRest.get_model_year",
        side_effect=HttpApiError("TV unreachable"),
    ):
        tv = SamsungTVWS("127.0.0.1", token="123456789")
    assert tv.token == "123456789"
