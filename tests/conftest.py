"""Tests for remote module."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio
from websockets.asyncio.client import ClientConnection


@pytest.fixture(autouse=True)
def override_time_sleep():
    """Ignore time sleep in tests."""
    with patch("samsungframetv.connection.time.sleep"), patch(
        "samsungframetv.remote.time.sleep"
    ):
        yield


@pytest.fixture(name="connection")
def get_connection():
    """Open a websocket connection."""
    connection = Mock()
    with patch(
        "samsungframetv.connection.websocket.create_connection"
    ) as connection_class:
        connection_class.return_value = connection
        yield connection


@pytest.fixture(autouse=True)
def override_asyncio_sleep():
    """Ignore asyncio sleep in tests."""
    with patch("samsungframetv.async_connection.asyncio.sleep", new=AsyncMock()):
        yield


@pytest.fixture(name="async_connection")
def get_async_connection():
    """Open a websockets connection."""
    connection = Mock(ClientConnection)
    with patch(
        "samsungframetv.async_connection.connect", new=AsyncMock(return_value=connection)
    ):
        yield connection


@pytest_asyncio.fixture(name="aiointercept_mock")
async def mock_aiointercept():
    # aiointercept needs Python 3.11+, tests using it are skipped on 3.10
    aiointercept = pytest.importorskip("aiointercept").aiointercept
    async with aiointercept(mock_external_urls=True) as m:
        yield m
