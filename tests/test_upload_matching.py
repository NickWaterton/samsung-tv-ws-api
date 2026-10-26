"""Tests for matching the TV's image_added event to its upload (issues #10 and #27)."""

import json
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

from samsungframetv import async_art
from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt


def image_added(content_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {"event": "image_added", "content_id": content_id}
    if request_id:
        data["id"] = request_id     # as the 2021 Frame (art API 2.03) sends it, the 2022 sends no id
    return {"event": "d2d_service_message", "data": json.dumps(data)}


def tv() -> SamsungTVAsyncArt:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        return SamsungTVAsyncArt("127.0.0.1")


@pytest.mark.asyncio
@pytest.mark.parametrize("with_id", [True, False], ids=["2021", "2022"])
async def test_late_image_added_is_not_taken_for_the_next_upload(with_id: bool) -> None:
    art = tv()
    first = async_art._UploadWaiter("first")
    art._upload_waiters.append(first)
    assert await art._wait_for_image_added(first, timeout=0.01) is None

    second = async_art._UploadWaiter("second")
    art._upload_waiters.append(second)
    await art.process_event("d2d_service_message", image_added("MY_F0001", "first" if with_id else None))
    assert not second.future.done()
    await art.process_event("d2d_service_message", image_added("MY_F0002", "second" if with_id else None))
    assert (await art._wait_for_image_added(second, timeout=1))["content_id"] == "MY_F0002"
    assert art._upload_waiters == []


@pytest.mark.asyncio
async def test_late_uploads_are_forgotten_after_a_while() -> None:
    art = tv()
    first = async_art._UploadWaiter("first")
    art._upload_waiters.append(first)
    await art._wait_for_image_added(first, timeout=0.01)
    first.abandoned_at -= async_art.UPLOAD_LATE_REPLY_WINDOW   # type: ignore[operator]
    second = async_art._UploadWaiter("second")
    art._upload_waiters.append(second)
    await art.process_event("d2d_service_message", image_added("MY_F0002"))
    assert (await art._wait_for_image_added(second, timeout=1))["content_id"] == "MY_F0002"


@pytest.mark.asyncio
async def test_image_added_for_an_unknown_upload_is_dropped() -> None:
    art = tv()
    waiter = async_art._UploadWaiter("mine")
    art._upload_waiters.append(waiter)
    await art.process_event("d2d_service_message", image_added("MY_F0001", "someone else's"))
    assert not waiter.future.done()


@pytest.mark.asyncio
async def test_image_added_without_uploads_still_reaches_pending_requests() -> None:
    """the binary upload of 2017/2018 Frames waits for image_added by name"""
    art = tv()
    art.pending_requests["image_added"] = async_art.asyncio.get_running_loop().create_future()
    await art.process_event("d2d_service_message", image_added("MY_F0001"))
    assert art.pending_requests["image_added"].done()


def test_sync_image_added_with_or_without_id() -> None:
    art = SamsungTVArt("127.0.0.1", port=8002, token="token")
    messages = [{"event": "image_added", "id": "other", "content_id": "MY_F0001"},
                {"event": "image_added", "id": "mine", "content_id": "MY_F0002"}]
    with patch.object(SamsungTVArt, "get_websocket_message", side_effect=messages):
        assert art.wait_for_response("image_added", "mine", or_no_id=True)["content_id"] == "MY_F0002"
    with patch.object(SamsungTVArt, "get_websocket_message", side_effect=[{"event": "image_added", "content_id": "MY_F0003"}]):
        assert art.wait_for_response("image_added", "mine", or_no_id=True)["content_id"] == "MY_F0003"
