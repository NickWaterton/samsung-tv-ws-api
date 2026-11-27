"""set_artmode() replies as recorded from a 2021 Frame (art API 2.03) and a 2022 Frame (art API 6.0) on 2026-11-27."""

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from samsungframetv import exceptions
from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt
from samsungframetv.ipcontrol import SamsungTVAsyncIPControl

OURS = "<ours>"


def message(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"event": "d2d_service_message", "data": json.dumps(data)}


# what each TV sent after set_artmode_status, in order. OURS is replaced by the request id
REPLIES = {
    "6.0 off": [{"event": "set_artmode_status", "request_id": OURS, "status": "off"},
                {"event": "art_mode_changed", "status": "off"}],
    # art_mode_changed came before the reply
    "6.0 on": [{"event": "art_mode_changed", "status": "on"},
               {"event": "set_artmode_status", "request_id": OURS, "status": "on"}],
    # the reply to 'off' is art_mode_changed with our id
    "2.03 off": [{"id": OURS, "event": "art_mode_changed", "status": "off"},
                 {"event": "art_mode_changed", "status": "off"}],
    # 'on' never gets a reply with our id
    "2.03 on": [{"event": "art_mode_changed", "status": "on"},
                {"event": "image_selected", "content_id": "SAM-S5437", "matte_id": "none"}],
    # art API 6.0 while booting: nothing
    "6.0 booting": [],
}


def with_id(replies: List[Dict[str, Any]], request_id: str) -> List[Dict[str, Any]]:
    return [{k: request_id if v == OURS else v for k, v in reply.items()} for reply in replies]


def async_tv(replies: List[Dict[str, Any]]) -> SamsungTVAsyncArt:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv.start_listening = AsyncMock(return_value=True)  # type: ignore[method-assign]

    async def send_command(command: Any, key_press_delay: Any = None) -> None:
        request_id = json.loads(command.params["data"])["id"]

        async def answer() -> None:
            for reply in with_id(replies, request_id):
                await asyncio.sleep(0.01)
                await tv.process_event("d2d_service_message", message(reply))

        asyncio.ensure_future(answer())

    tv.send_command = send_command  # type: ignore[method-assign]
    return tv


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["6.0 off", "6.0 on", "2.03 off", "2.03 on"])
async def test_set_artmode(case: str) -> None:
    mode = case.split()[1]
    tv = async_tv(REPLIES[case])
    assert await tv.set_artmode(mode, timeout=1) is True
    assert tv.art_mode is (mode == "on")
    assert tv._art_timeouts == 0
    assert tv.pending_requests == {}
    assert tv._artmode_waiters == []


@pytest.mark.asyncio
async def test_set_artmode_ignored_while_booting() -> None:
    tv = async_tv(REPLIES["6.0 booting"])
    assert await tv.set_artmode("on", timeout=0.05) is False
    # not counted towards the art service back-off
    assert tv._art_timeouts == 0
    assert tv.pending_requests == {}


@pytest.mark.asyncio
async def test_set_artmode_ignores_the_other_mode() -> None:
    # a late art_mode_changed 'off' (6.0 sends it twice) does not confirm 'on'
    tv = async_tv([{"event": "art_mode_changed", "status": "off"}])
    assert await tv.set_artmode("on", timeout=0.05) is False


@pytest.mark.asyncio
async def test_set_artmode_ignores_get_artmode_status() -> None:
    # art API 6.0 answers get_artmode_status 'on' in standby
    tv = async_tv([{"event": "get_artmode_status", "request_id": "someone else", "value": "on"}])
    assert await tv.set_artmode("on", timeout=0.05) is False


@pytest.mark.asyncio
async def test_set_artmode_refused() -> None:
    tv = async_tv([{"event": "error", "request_id": OURS, "error_code": "-1",
                    "request_data": json.dumps({"request": "set_artmode_status"})}])
    with pytest.raises(exceptions.ResponseError):
        await tv.set_artmode("on", timeout=1)


@pytest.mark.asyncio
async def test_set_artmode_falls_back_to_ipcontrol() -> None:
    tv = async_tv(REPLIES["6.0 booting"])
    ipc = MagicMock(spec=SamsungTVAsyncIPControl)
    ipc.async_call = AsyncMock(side_effect=[None, "on"])
    assert await tv.set_artmode(True, timeout=0.05, ip_control=ipc) is True
    assert [c.args for c in ipc.async_call.call_args_list] == [("set_artmode", True), ("get_artmode",)]


def sync_tv(replies: List[Dict[str, Any]]) -> SamsungTVArt:
    tv = SamsungTVArt("127.0.0.1")
    queue: List[Dict[str, Any]] = []

    def send_command(command: Any, key_press_delay: Any = None) -> None:
        queue.extend(with_id(replies, json.loads(command.params["data"])["id"]))

    def get_websocket_message() -> Dict[str, Any]:
        if not queue:
            raise exceptions.TimeoutError("Websocket Time out")
        return queue.pop(0)

    tv.send_command = send_command  # type: ignore[method-assign]
    tv.get_websocket_message = get_websocket_message  # type: ignore[method-assign]
    return tv


@pytest.mark.parametrize("case", ["6.0 off", "6.0 on", "2.03 off", "2.03 on"])
def test_sync_set_artmode(case: str) -> None:
    assert sync_tv(REPLIES[case]).set_artmode(case.split()[1]) is True


def test_sync_set_artmode_ignored_while_booting() -> None:
    assert sync_tv(REPLIES["6.0 booting"]).set_artmode("on") is False
    assert sync_tv([{"event": "art_mode_changed", "status": "off"}]).set_artmode("on") is False
