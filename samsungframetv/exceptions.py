"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Copyright (C) 2019 DSR! <xchwarze@gmail.com>

SPDX-License-Identifier: LGPL-3.0
"""

import asyncio


class ConnectionFailure(Exception):
    """Error during connection."""

    pass


class UnauthorizedError(ConnectionFailure):
    """Error from ms.channel.unauthorized event."""

    pass


class ChannelTimeoutError(ConnectionFailure):
    """ms.channel.timeOut: the TV did not accept the connection.

    Usually the token is missing or belongs to another TV, or Allow was not pressed on the TV.
    Delete the token file and connect again to pair.
    """

    pass


class ResponseError(Exception):
    """Error in response."""

    pass


class ThumbnailUnavailableError(ResponseError, asyncio.IncompleteReadError):
    """The TV closed the transfer socket without sending a thumbnail.

    Seen for some Art Store items (SAM-...): the same item fails every time on one TV
    and can work on another. Also an asyncio.IncompleteReadError, which is what
    get_thumbnail() raised for these before.
    """

    def __init__(self, content_id: object = None) -> None:
        asyncio.IncompleteReadError.__init__(self, b"", 4)
        self.content_id = content_id
        self.args = ("TV sent no thumbnail for {}".format(content_id),)

    def __str__(self) -> str:
        return str(self.args[0])


class HttpApiError(Exception):
    """Error using HTTP API."""

    pass


class MessageError(Exception):
    """Error from ms.error event."""

    pass
    
class TimeoutError(Exception):
    """Websocket time out."""

    pass


class ArtRequestTimeoutError(TimeoutError, AssertionError):
    """The TV did not answer an art request in time.

    Also an AssertionError, because art calls that got no answer have always raised one.
    """

    pass


class ArtServiceUnavailableError(ArtRequestTimeoutError):
    """The art service stopped answering, requests fail fast until it recovers.

    Also an AssertionError, because art calls that got no answer have always raised one.
    """

    pass
