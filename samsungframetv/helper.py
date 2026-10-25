"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Copyright (C) 2019 DSR! <xchwarze@gmail.com>
Copyright (C) 2025 Nick Waterton <n.waterton@outlook.com>

SPDX-License-Identifier: LGPL-3.0
"""

import base64
import json
import logging
import ssl
from typing import Any, Dict, Optional, Union

from . import exceptions

_LOGGING = logging.getLogger(__name__)
_SSL_CONTEXT: Optional[ssl.SSLContext] = None

# a 2022 Frame resets a secured transfer socket, and abandons the transfer, if we connect as soon as
# it answers. Art requests used to wait 1s after sending (the key press delay), which gave it time
D2D_SECURED_CONNECT_DELAY = 1.0


def serialize_string(string: Union[str, bytes]) -> str:
    if isinstance(string, str):
        string = str.encode(string)

    return base64.b64encode(string).decode("utf-8")


def process_api_response(response: Union[str, bytes]) -> Dict[str, Any]:
    """
    Parse a websocket message. Older art API's (eg 0.97 and 1.07) send binary frames
    with a JSON header followed by the file (eg a thumbnail), the file is returned in 'binary'.
    """
    _LOGGING.debug("Processing API response: %s", response[:1000] if isinstance(response, bytes) else response)
    try:
        return json.loads(response)  # type:ignore[no-any-return]
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        if isinstance(response, (bytes, bytearray)):
            frame = _split_json_and_binary(bytes(response))
            if frame is not None:
                return frame
        raise exceptions.ResponseError(
            "Failed to parse response from TV. Maybe feature not supported on this model"
        ) from err


def _split_json_and_binary(buf: bytes) -> Optional[Dict[str, Any]]:
    """JSON header followed by binary data, None if there is no JSON header"""
    start = buf.find(b"{")
    if start < 0:
        return None
    try:
        # raw_decode stops at the end of the JSON object, and handles braces inside strings
        frame, end = json.JSONDecoder().raw_decode(buf[start:].decode("utf-8", errors="surrogateescape"))
    except json.JSONDecodeError:
        return None
    if not isinstance(frame, dict):
        return None
    # surrogateescape round trips, so this is where the JSON ended in the original bytes
    json_end = start + len(buf[start:].decode("utf-8", errors="surrogateescape")[:end].encode("utf-8", errors="surrogateescape"))
    tail = buf[json_end:]
    if tail.startswith(b"\n"):
        tail = tail[1:]
    if tail:
        frame["binary"] = tail
    _LOGGING.debug("JSON header with %d bytes of binary data", len(tail))
    return frame


def binary_send_image(request: Dict[str, Any], data: bytes) -> bytes:
    """
    One binary websocket message carrying an art request and the image, as the SmartThings
    app does for older art API's (0.97, 1.07): uint16 big endian header length, JSON header, image
    """
    header = json.dumps(
        {
            "method": "ms.channel.emit",
            "params": {"data": json.dumps(request), "to": "host", "event": "art_app_request"},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if len(header) > 0xFFFF:
        raise ValueError("Upload header too large")
    return len(header).to_bytes(2, "big") + header + bytes(data)


def is_legacy_art_api(version: Optional[str]) -> bool:
    """True for the art API on 2017/2018 Frames (eg '0.97', '1.07'), newer TV's report 2.x or 4.x"""
    try:
        return int(str(version).split(".")[0]) < 2
    except ValueError:
        return False


def slideshow_name(version: Optional[str]) -> str:
    """name of the slideshow requests: art API 2.03 (2021) calls it auto_rotation, 4.3.4.0 (2022) slideshow"""
    try:
        return "auto_rotation" if int(str(version).split(".")[0]) < 4 else "slideshow"
    except ValueError:
        return "slideshow"


def uses_thumbnail_list(version: Optional[str]) -> bool:
    """art API 4.3.4.0 (2022) replaced get_thumbnail with get_thumbnail_list"""
    try:
        return int(str(version).split(".")[0]) >= 4
    except ValueError:
        return False


def has_artmode_settings(version: Optional[str]) -> bool:
    """get_artmode_settings arrived in art API 4.3.4.0 (2022), 2.03 (2021) asks for each setting, eg get_brightness"""
    try:
        return int(str(version).split(".")[0]) >= 4
    except ValueError:
        return True


# each has its own request on art API 2.03 (seen on a 2021 Frame)
ART_SETTINGS_REQUESTED_ONE_BY_ONE = ("brightness", "color_temperature", "motion_sensitivity", "motion_timer", "brightness_sensor_setting")


def thumbnail_name(content_id: str, data: Union[bytes, bytearray]) -> str:
    """file name for an inline thumbnail, from its magic number"""
    return "{}.{}".format(content_id, "png" if bytes(data[:4]) == b"\x89PNG" else "jpg")


def parse_matte_list(data: Dict[str, Any], include_colour: bool = False) -> Any:
    """
    get_matte_list reply as matte types (and colours).
    Newer TV's send 'matte_type_list' and 'matte_color_list', 2018 Frames send 'matte_types'
    as a list of combined ids like 'shadowbox_polar' (issue #35), which are split here.
    """
    if "matte_type_list" in data:
        types = json.loads(data["matte_type_list"])
        colours = json.loads(data.get("matte_color_list", "[]"))
    else:
        mattes = data.get("matte_types", [])
        if isinstance(mattes, str):
            mattes = json.loads(mattes)
        types, colours = [], []
        for matte in mattes:
            matte_type, _, colour = matte.get("matte_id", "").partition("_")
            if matte_type and {"matte_type": matte_type} not in types:
                types.append({"matte_type": matte_type})
            if colour and {"color": colour} not in colours:
                colours.append({"color": colour})
    return (types, colours) if include_colour else types


def format_host(host: str) -> str:
    """ipv6 addresses need [] in a URL"""
    return "[{}]".format(host) if ":" in host and not host.startswith("[") else host


def get_ssl_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    if not _SSL_CONTEXT:
        _SSL_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        _SSL_CONTEXT.check_hostname = False
        _SSL_CONTEXT.verify_mode = ssl.CERT_NONE
    return _SSL_CONTEXT
    
def is_true(value):
    return value.lower() in ["on", "true"] if isinstance(value, str) else value in [True, 1]
