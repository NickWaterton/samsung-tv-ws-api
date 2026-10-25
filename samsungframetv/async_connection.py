"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Copyright (C) 2019 DSR! <xchwarze@gmail.com>

SPDX-License-Identifier: LGPL-3.0
"""

import asyncio
import contextlib
import json
import logging
from types import TracebackType
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Union

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

from . import connection, exceptions, helper
from .command import SamsungTVCommand, SamsungTVSleepCommand
from .event import (
    IGNORE_EVENTS_AT_STARTUP,
    MS_CHANNEL_CONNECT_EVENT,
    MS_CHANNEL_UNAUTHORIZED,
    MS_CHANNEL_TIMEOUT
)
from .helper import get_ssl_context

_LOGGING = logging.getLogger(__name__)

DEFAULT_OPEN_TIMEOUT = connection.DEFAULT_OPEN_TIMEOUT
PAIRING_TIMEOUT = connection.PAIRING_TIMEOUT


class SamsungTVWSAsyncConnection(connection.SamsungTVWSBaseConnection):
    connection: Optional[ClientConnection]
    _recv_loop: Optional["asyncio.Task[None]"]

    async def __aenter__(self) -> "SamsungTVWSAsyncConnection":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    async def open(self) -> ClientConnection:
        if self.is_alive():
            # someone else already created a new connection
            return self.connection

        url = self._format_websocket_url(self.endpoint)

        _LOGGING.debug("WS url %s", url)
        connect_kwargs: Dict[str, Any] = {}
        if self._is_ssl_connection():
            connect_kwargs["ssl"] = get_ssl_context()
        open_timeout = self.timeout or DEFAULT_OPEN_TIMEOUT
        connection = await connect(url, open_timeout=open_timeout, **connect_kwargs)

        # a TV can accept the connection and then never answer, so bound the wait
        startup_timeout = open_timeout if self._get_token() else max(open_timeout, PAIRING_TIMEOUT)
        try:
            response = await asyncio.wait_for(self._wait_for_connect_event(connection), startup_timeout)
        except asyncio.TimeoutError:
            await connection.close()
            raise exceptions.ConnectionFailure(
                "{} accepted the connection but did not answer within {}s".format(self.host, startup_timeout)
            ) from None
        event = response.get("event", "*")

        if event == MS_CHANNEL_UNAUTHORIZED:
            await connection.close()
            raise exceptions.UnauthorizedError(response)

        if event == MS_CHANNEL_TIMEOUT:
            await connection.close()
            _LOGGING.warning(
                "%s did not accept the connection (ms.channel.timeOut): token missing or for another TV, "
                "or Allow not pressed on the TV", self.host
            )
            raise exceptions.ChannelTimeoutError(response)

        if event != MS_CHANNEL_CONNECT_EVENT:
            # Unexpected event received during connection routine
            await connection.close()
            raise exceptions.ConnectionFailure(response)

        self._check_for_token(response)

        self.connection = connection
        return connection

    async def _wait_for_connect_event(self, connection: ClientConnection) -> Dict[str, Any]:
        """skip the events some TV's send first, return the first one that matters"""
        while True:
            data = await connection.recv()
            response = helper.process_api_response(data)
            event = response.get("event", "*")
            assert event
            self._websocket_event(event, response)
            if event not in IGNORE_EVENTS_AT_STARTUP:
                return response

    async def start_listening(
        self, callback: Optional[Callable[[str, Any], Optional[Awaitable[None]]]] = None
    ) -> bool:
        """Open, and start listening, returns False if already listening."""
        self._listening = True
        self._listen_callback = callback
        if not self._recv_loop:
            if not self.is_alive():
                self.connection = await self.open()

            self._recv_loop = asyncio.ensure_future(
                self._do_start_listening(callback, self.connection)
            )
            return True
        return False

    async def _do_start_listening(
        self,
        callback: Optional[Callable[[str, Any], Optional[Awaitable[None]]]],
        connection: ClientConnection,
    ) -> None:
        """Do start listening."""
        _LOGGING.debug("Listening Connection Started")
        try:
            while True:
                data = await connection.recv()
                # a bad message or a failing callback must not stop the listener,
                # or every later request would time out
                try:
                    response = helper.process_api_response(data)
                    event = response.get("event", "*")
                    self._websocket_event(event, response)
                    if callback:
                        awaitable = callback(event, response)
                        if awaitable:
                            await awaitable
                except Exception:
                    _LOGGING.exception("Error processing message: %s", data)
        except ConnectionClosed as e:
            _LOGGING.debug("Connection closed by TV: %s", e)
        except Exception as e:
            _LOGGING.warning("Listening stopped: %s", e)
        finally:
            _LOGGING.debug("Listening Connection closed")
            if self._recv_loop is asyncio.current_task():
                self._recv_loop = None

    async def close(self) -> None:
        self._listening = False
        if self.is_alive():
            assert self.connection
            await self.connection.close()
        if self._recv_loop and self._recv_loop is not asyncio.current_task():
            await self._recv_loop
        self._recv_loop = None

        self.connection = None
        _LOGGING.debug("Connection closed.")

    async def pair(self, timeout: float = PAIRING_TIMEOUT, force: bool = False) -> Optional[str]:
        """
        Get a token (port 8002) by opening the remote control channel, which shows the Allow prompt
        on every model. Returns the token, which is also saved to token_file.
        If there is a token already, it is checked first, and only asked for again if the TV
        no longer accepts it (eg after a long power off). force: always ask again.
        """
        token = self._get_token()
        if token and not force:
            try:
                return self._update_token(await self._open_pairing_channel(token, self.timeout or DEFAULT_OPEN_TIMEOUT)) or token
            except exceptions.ConnectionFailure as e:
                _LOGGING.info("%s did not accept the token (%r), asking for a new one", self.host, e)
        _LOGGING.warning("%s: press Allow on the TV within %ds", self.host, timeout)
        return self._update_token(await self._open_pairing_channel(None, timeout))

    async def _open_pairing_channel(self, token: Optional[str], timeout: float) -> Optional[str]:
        remote = SamsungTVWSAsyncConnection(
            self.host, endpoint=connection.PAIRING_ENDPOINT, token=token, port=self.port, timeout=timeout, name=self.name
        )
        try:
            await remote.open()
        finally:
            await remote.close()
        return remote.token

    def _update_token(self, token: Optional[str]) -> Optional[str]:
        token = str(token) if token else None
        if token and token != self._get_token():
            self._set_token(token)
        return token

    async def _reconnect(self) -> None:
        """drop a dead connection, open a new one, and resume listening if we were"""
        listening, callback = self._listening, self._listen_callback
        with contextlib.suppress(Exception):
            await self.close()
        self.connection = await self.open()
        if listening:
            await SamsungTVWSAsyncConnection.start_listening(self, callback)

    async def send_commands(
        self,
        commands: Sequence[Union[SamsungTVCommand, Dict[str, Any]]],
        key_press_delay: Optional[float] = None,
    ) -> None:
        if not self.is_alive():
            self.connection = await self.open()

        delay = self.key_press_delay if key_press_delay is None else key_press_delay

        for command in commands:
            try:
                await self._send_command(self.connection, command, delay)
            except ConnectionClosed as e:
                # TV's drop the connection (code 1006) shortly after standby, reconnect once (issue #16)
                _LOGGING.debug("Connection lost (%s), reconnecting", e)
                await self._reconnect()
                await self._send_command(self.connection, command, delay)

    async def send_command(
        self,
        command: Union[List[SamsungTVCommand], SamsungTVCommand, Dict[str, Any]],
        key_press_delay: Optional[float] = None,
    ) -> None:
        if isinstance(command, list):
            _LOGGING.warning(
                "Using send_command to send multiple commands is deprecated, "
                "please use send_commands."
            )
            await self.send_commands(command, key_press_delay)
            return

        await self.send_commands([command], key_press_delay)

    @staticmethod
    async def _send_command(
        connection: ClientConnection,
        command: Union[SamsungTVCommand, Dict[str, Any]],
        delay: float,
    ) -> None:
        if isinstance(command, SamsungTVSleepCommand):
            await asyncio.sleep(command.delay)
            return

        if isinstance(command, SamsungTVCommand):
            payload = command.get_payload()
        else:
            payload = json.dumps(command)
        _LOGGING.debug("SamsungTVWS websocket command: %s", payload)
        await connection.send(payload)

        await asyncio.sleep(delay)

    def is_alive(self) -> bool:
        return self.connection is not None and self.connection.state is not State.CLOSED
