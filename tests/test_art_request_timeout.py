"""An art request that gets no answer raises ArtRequestTimeoutError (it used to be a bare assert)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from samsungframetv import exceptions
from samsungframetv.art import SamsungTVArt
from samsungframetv.async_art import SamsungTVAsyncArt


@pytest.mark.asyncio
async def test_async_no_answer() -> None:
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv.start_listening = AsyncMock(return_value=True)
    tv.send_command = AsyncMock()
    tv.wait_for_response = AsyncMock(return_value=None)
    with pytest.raises(exceptions.ArtRequestTimeoutError) as raised:
        await tv.get_current()
    assert "get_current_artwork" in str(raised.value)
    # still caught by code written for the old bare assert
    assert isinstance(raised.value, AssertionError)
    assert isinstance(raised.value, exceptions.TimeoutError)


def test_sync_no_answer() -> None:
    tv = SamsungTVArt("127.0.0.1", port=8002, token="token")
    tv.send_command = Mock()
    tv.wait_for_response = Mock(return_value={})
    with pytest.raises(exceptions.ArtRequestTimeoutError, match="get_device_info"):
        tv.get_device_info()


def test_service_unavailable_is_a_request_timeout() -> None:
    assert issubclass(exceptions.ArtServiceUnavailableError, exceptions.ArtRequestTimeoutError)
