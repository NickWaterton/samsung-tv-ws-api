"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Copyright (C) 2019 DSR! <xchwarze@gmail.com>
Copyright (C) 2021 Matthew Garrett <mjg59@srcf.ucam.org>
Copyright (C) 2024,2025 Nick Waterton <n.waterton@outlook.com>

SPDX-License-Identifier: LGPL-3.0
"""

import contextlib
from datetime import datetime
import json
import logging
import random
from pathlib import Path
import socket
import time
from typing import Any, Dict, List, Optional, Union, TypeVar
from urllib.parse import urlparse
import uuid

import requests
import websocket

from . import exceptions, helper, ipcontrol
from .command import SamsungTVCommand
from .connection import SamsungTVWSConnection
from .event import D2D_SERVICE_MESSAGE_EVENT, MS_CHANNEL_READY_EVENT
from .rest import SamsungTVRest
from .helper import get_ssl_context, is_true

_LOGGING = logging.getLogger(__name__)
_T = TypeVar("_T")

ART_ENDPOINT = "com.samsung.art-app"


class ArtChannelEmitCommand(SamsungTVCommand):
    def __init__(self, params: Dict[str, Any]) -> None:
        super().__init__("ms.channel.emit", params)

    @staticmethod
    def art_app_request(data: Dict[str, Any]) -> "ArtChannelEmitCommand":
        return ArtChannelEmitCommand(
            {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(data),
            }
        )


class SamsungTVArt(SamsungTVWSConnection):
    def __init__(
        self,
        host,
        token=None,
        token_file=None,
        port=8001,
        timeout=None,
        key_press_delay=1,
        name="SamsungTvRemote",
    ):
        super().__init__(
            host,
            endpoint=ART_ENDPOINT,
            token=token,
            token_file=token_file,
            port=port,
            timeout=timeout,
            key_press_delay=key_press_delay,
            name=name,
        )
        self.art_uuid = str(uuid.uuid4())
        self._rest_api: Optional[SamsungTVRest] = None
        self.pending_requests = None
        self._slideshow_name: Optional[str] = None   # auto_rotation or slideshow, see _slideshow_request

    def open(self) -> websocket.WebSocket:
        super().open()

        # Override base class to wait for MS_CHANNEL_READY_EVENT
        assert self.connection
        data = self.connection.recv()
        response = helper.process_api_response(data)
        event = response.get("event", "*")
        self._websocket_event(event, response)

        if event != MS_CHANNEL_READY_EVENT:
            self.close()
            raise exceptions.ConnectionFailure(response)

        return self.connection

    def get_uuid(self):
        self.art_uuid = str(uuid.uuid4())
        return self.art_uuid

    def get_websocket_message(self):
        try:
            assert self.connection
            raw_data = self.connection.recv()
            response = helper.process_api_response(raw_data)
            event = response.get("event", "*")
            self._websocket_event(event, response)
            _LOGGING.debug('event: {}'.format(event))
            if event == D2D_SERVICE_MESSAGE_EVENT:
                data = json.loads(response["data"])
                if response.get("binary"):
                    data["binary"] = response["binary"]    # older art API's send files inline
                return data
        except websocket.WebSocketTimeoutException as e:
            raise exceptions.TimeoutError('Websocket Time out: {}'.format(e)) from e
        return {}

    def wait_for_response(self, wait_for_event, request_uuid=None, or_no_id=False):
        """or_no_id also accepts a message without an id, eg image_added from art API 4.3.4.0"""
        while True:
            data = self.get_websocket_message()
            message_uuid = data.get('request_id', data.get('id'))
            _LOGGING.debug('request_uuid: {}, message uuid: {}'.format(request_uuid, message_uuid))
            if message_uuid == request_uuid or (or_no_id and message_uuid is None):
                sub_event = data.get("event", "*")
                _LOGGING.debug('sub_event: {}, wait_for_event: {}'.format(sub_event, wait_for_event))
                if sub_event == "error":
                    raise exceptions.ResponseError(
                        f"{json.loads(data['request_data'])['request']} request failed "
                        f"with error number {data['error_code']}"
                    )
                # Check sub event, return if found or not defined
                if not wait_for_event or sub_event == wait_for_event:
                    return data
        return None

    def _check_reply(self, data: Optional[_T]) -> _T:
        """raise ArtRequestTimeoutError when an art request got no answer (used to be a bare assert)"""
        if not data:
            raise exceptions.ArtRequestTimeoutError(
                "{} did not answer the art request {}".format(self.host, getattr(self, "_last_art_request", None))
            )
        return data

    def _send_art_request(
        self,
        request_data: Dict[str, Any],
        wait_for_event: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not request_data.get("id"):
            request_data["id"] = self.get_uuid()            #old api
        request_data["request_id"] = request_data["id"]     #new api  
        self._last_art_request = request_data.get("request")
        # no key press delay, the reply is what we wait for
        self.send_command(ArtChannelEmitCommand.art_app_request(request_data), key_press_delay=0)
        return self.wait_for_response(wait_for_event, request_data["id"])

    def _get_rest_api(self) -> SamsungTVRest:
        if self._rest_api is None:
            self._rest_api = SamsungTVRest(self.host, self.port, self.timeout)
        return self._rest_api

    def supported(self) -> bool:
        data = self._get_rest_api().rest_device_info()
        if data.get("device", {}).get("FrameTVSupport") == "true":
            return True
        # some TV's with art mode (eg S95H) report FrameTVSupport false, so ask the art channel
        if data.get("device", {}).get("PowerState", "off") == "on":
            return self._try_get_artmode() is not None
        return False

    def _try_get_artmode(self) -> Optional[str]:
        """get_artmode(), returning None instead of raising if the art channel does not answer"""
        try:
            return self.get_artmode()
        except Exception as e:
            _LOGGING.debug("art channel did not answer: %s", e)
            self.close()
        return None

    def get_power_state(self, ip_control: Optional[ipcontrol.SamsungTVIPControl] = None) -> str:
        """
        returns one of 'off', 'standby', 'tv', 'art' or 'on' (on, but art mode unknown)
        ip_control: optional paired SamsungTVIPControl, the most reliable source
        NOTE: set a timeout on this object, or the art channel can block when the TV is in standby
        """
        ip_state = None
        if ip_control is not None:
            try:
                ip_state = ip_control.get_power_state()
                if ip_state != ipcontrol.POWER_OFF:
                    return ip_state
            except ipcontrol.IPControlError as e:
                _LOGGING.debug("IP Control failed: %s", e)
        try:
            rest_info = self._get_rest_api().rest_device_info()
        except exceptions.HttpApiError:
            rest_info = None
        artmode = None
        if rest_info and rest_info.get("device", {}).get("PowerState", "on") in ("on", "standby"):
            artmode = self._try_get_artmode()
        return ipcontrol.derive_power_state(rest_info, artmode, ip_state)

    def _open_d2d_socket(self, conn_info, secured=None):
        """connect to the socket the TV named in conn_info for a thumbnail or upload transfer"""
        if secured is None:
            secured = conn_info.get("secured", False)
        art_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if secured:
            time.sleep(helper.D2D_SECURED_CONNECT_DELAY)
            art_socket = get_ssl_context().wrap_socket(art_socket)
        art_socket.connect((conn_info["ip"], int(conn_info["port"])))
        return art_socket

    def _legacy_api(self) -> bool:
        """2017/2018 Frames (art API below 2) upload and send thumbnails in one websocket message"""
        if getattr(self, "_api_version", None) is None:
            try:
                self._api_version = self.get_api_version()
            except Exception as e:
                _LOGGING.debug("could not get art api version: %s", e)
                return False
        return helper.is_legacy_art_api(self._api_version)

    def get_api_version(self):
        try:
            data = self._send_art_request(
                #new api, throws ResponseError on old tv's
                {"request": "api_version"}
            )
        except exceptions.ResponseError:
            data = self._send_art_request(
                #old api produces no response on new TV's
                {"request": "get_api_version"}
            )
        data = self._check_reply(data)
        self._api_version = data["version"]
        return data["version"]

    def get_device_info(self):
        data = self._send_art_request(
            {"request": "get_device_info"}
        )
        data = self._check_reply(data)
        return data

    def available(self, category=None):
        data = self._send_art_request(
            {"request": "get_content_list", "category": category}
        )
        data = self._check_reply(data)
        return [ v for v in json.loads(data["content_list"]) if v["category_id"] == category] if category else json.loads(data["content_list"])

    def get_current(self):
        data = self._send_art_request(
            {"request": "get_current_artwork"}
        )
        data = self._check_reply(data)
        return data

    def set_favourite(self, content_id, status="on"):
        status = "on" if is_true(status) else "off"
        data = self._send_art_request(
            {   "request": "change_favorite",
                "content_id": content_id,
                "status": status},
            wait_for_event="favorite_changed"
        )
        data = self._check_reply(data)
        return data

    def _artmode_settings_one_by_one(self, setting=''):
        """the get_artmode_settings list, from the requests art API 2.03 has for each setting"""
        items = []
        for item in helper.ART_SETTINGS_REQUESTED_ONE_BY_ONE:
            if setting and item != setting:
                continue
            try:
                data = self._send_art_request({"request": "get_{}".format(item)})
            except exceptions.ResponseError as e:
                _LOGGING.debug("get_%s: %s", item, e)
                continue
            if data:
                # brightness_sensor_setting sends its value as status
                values = dict(data, value=data["status"]) if "value" not in data and "status" in data else data
                items.append(dict({"item": item}, **{k: values[k] for k in ("value", "min", "max", "valid_values") if k in values}))
        return items

    def get_artmode_settings(self, setting=''):
        '''
        setting can be any of 'brightness', 'color_temperature', 'motion_sensitivity',
        'motion_timer', or 'brightness_sensor_setting'
        art API 4.3.4.0 (2022) added get_artmode_settings, earlier versions (2.03) return error -9, so the
        list is built from get_brightness and get_color_temperature, which they answer
        '''
        self._legacy_api()   # reads the art api version
        if not helper.has_artmode_settings(getattr(self, "_api_version", None)):
            items = self._artmode_settings_one_by_one(setting)
            items = self._check_reply(items)
            return next(iter(item for item in items if item['item'] == setting), items)
        try:
            data = self._send_art_request(
                {"request": "get_artmode_settings"}
            )
        except exceptions.ResponseError:
            items = self._artmode_settings_one_by_one(setting)
            if not items:
                raise
            return next(iter(item for item in items if item['item'] == setting), items)
        data = self._check_reply(data)
        if 'data' in data.keys():
            data = json.loads(data["data"])
            return next(iter(item for item in data if item['item'] == setting), data)
        return data

    def _slideshow_request(self, action, fields=None):
        """
        get or set the slideshow. Art API 2.03 (2021) calls it auto_rotation and 4.3.4.0 (2022) renamed it
        slideshow, each TV fails the other name (error -9 or no answer), so send the name that works
        """
        if self._slideshow_name is None:
            self._legacy_api()   # reads the art api version
            self._slideshow_name = helper.slideshow_name(getattr(self, "_api_version", None))
        error = None
        for name in (self._slideshow_name, "slideshow" if self._slideshow_name == "auto_rotation" else "auto_rotation"):
            try:
                data = self._send_art_request(dict({"request": "{}_{}_status".format(action, name)}, **(fields or {})))
            except exceptions.ResponseError as e:
                data, error = None, e
            if data:
                self._slideshow_name = name
                return data
        if error:
            raise error
        raise exceptions.ArtRequestTimeoutError("{} did not answer {}_slideshow_status".format(self.host, action))

    @staticmethod
    def _slideshow_fields(duration, type, category):
        return {"value": str(duration) if duration > 0 else "off", "category_id": "MY-C000{}".format(category),
                "type": "shuffleslideshow" if type else "slideshow"}

    def get_auto_rotation_status(self):
        return self._slideshow_request("get")

    def set_auto_rotation_status(self, duration=0, type=True, category=2):
        '''
        duration is "off" or "number" where number is duration in minutes. set 0 for 'off'
        slide show type can be "slideshow" or "shuffleslideshow", set True for shuffleslideshow
        category is 'MY-C0004' or 'MY-C0002' where 4 is favourites, 2 is my pictures, and 8 is store
        '''
        return self._slideshow_request("set", self._slideshow_fields(duration, type, category))

    def get_slideshow_status(self):
        return self._slideshow_request("get")

    def set_slideshow_status(self, duration=0, type=True, category=2):
        '''
        duration is "off" or "number" where number is duration in minutes. set 0 for 'off'
        slide show type can be "slideshow" or "shuffleslideshow", set True for shuffleslideshow
        category is 'MY-C0004' or 'MY-C0002' where 4 is favourites, 2 is my pictures, and 8 is store
        '''
        return self._slideshow_request("set", self._slideshow_fields(duration, type, category))

    def get_brightness(self):
        # art API 4.3.4.0 (2022) only answers get_artmode_settings, earlier versions return error -9 for it
        self._legacy_api()   # reads the art api version
        data = None
        if helper.has_artmode_settings(getattr(self, "_api_version", None)):
            with contextlib.suppress(exceptions.ResponseError):
                data = self.get_artmode_settings('brightness')
        if not data:
            data = self._send_art_request(
                {"request": "get_brightness"}
            )
        data = self._check_reply(data)
        return data['value']

    def set_brightness(self, value):
        # "0" to "10"
        data = self._send_art_request(
            {"request": "set_brightness", "value": value}
        )
        data = self._check_reply(data)
        return data

    def get_color_temperature(self):
        # art API 4.3.4.0 (2022) only answers get_artmode_settings, earlier versions return error -9 for it
        self._legacy_api()   # reads the art api version
        data = None
        if helper.has_artmode_settings(getattr(self, "_api_version", None)):
            with contextlib.suppress(exceptions.ResponseError):
                data = self.get_artmode_settings('color_temperature')
        if not data:
            data = self._send_art_request(
                {"request": "get_color_temperature"}
            )
        data = self._check_reply(data)
        return data['value']

    def set_color_temperature(self, value):
        # "-5" to "5"
        data = self._send_art_request(
            {"request": "set_color_temperature", "value": value}
        )
        data = self._check_reply(data)
        return data

    def set_brightness_sensor_setting(self, value):
        value = "on" if is_true(value) else "off"
        data = self._send_art_request(
            {"request": "set_brightness_sensor_setting", "value": value}
        )
        data = self._check_reply(data)
        return data

    def set_motion_timer(self, value):
        # "off", "5", "15", "30", "60", "120","240" 
        data = self._send_art_request(
            {"request": "set_motion_timer", "value": value}
        )
        data = self._check_reply(data)
        return data

    def set_motion_sensitivity(self, value):
        # "1" to "3"
        data = self._send_art_request(
            {"request": "set_motion_sensitivity", "value": value}
        )
        data = self._check_reply(data)
        return data

    @staticmethod
    def _read_thumbnail_header(art_socket, content_id, eof_ok=False):
        header_len = art_socket.recv(4)
        if not header_len:
            art_socket.close()
            if eof_ok:
                return None
            # the TV closed the socket without sending anything, seen for some Art Store items
            raise exceptions.ThumbnailUnavailableError(content_id)
        return json.loads(art_socket.recv(int.from_bytes(header_len, "big")))

    def get_thumbnail_list(self, content_id_list=[]):
        if isinstance(content_id_list, str):
            content_id_list=[content_id_list]
        requested = list(content_id_list)
        data = self._send_art_request(
            {
                "request": "get_thumbnail_list",
                "content_id_list": [{"content_id": id} for id in requested],
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self.get_uuid(),
                },
            }
        )
        data = self._check_reply(data)
        conn_info = json.loads(data["conn_info"])
        art_socket = self._open_d2d_socket(conn_info)
        total_num_thumbnails = 1
        current_thumb = -1
        thumbnail_data_dict: Dict[str, bytearray] = {}
        while current_thumb+1 < total_num_thumbnails:
            # the TV leaves out items it has no thumbnail for and closes the socket early
            header = self._read_thumbnail_header(art_socket, requested, eof_ok=bool(thumbnail_data_dict))
            if header is None:
                break
            thumbnail_data_len = int(header["fileLength"])
            current_thumb = int(header["num"])
            total_num_thumbnails = int(header["total"])
            filename = "{}.{}".format(header["fileID"], header["fileType"])
            thumbnail_data = bytearray()
            while len(thumbnail_data) < thumbnail_data_len:
                packet = art_socket.recv(thumbnail_data_len - len(thumbnail_data))
                thumbnail_data.extend(packet)
            thumbnail_data_dict[filename]=thumbnail_data
        helper.log_missing_thumbnails(self.host, requested, thumbnail_data_dict)
        return thumbnail_data_dict

    def get_thumbnail(self, content_id_list=[], as_dict=False):
        if isinstance(content_id_list, str):
            content_id_list=[content_id_list]
        self._legacy_api()   # reads the art api version
        if helper.uses_thumbnail_list(getattr(self, "_api_version", None)):
            thumbnails = self.get_thumbnail_list(content_id_list)
            return thumbnails if as_dict else list(thumbnails.values()) if len(content_id_list) > 1 else next(iter(thumbnails.values()), None)
        thumbnail_data_dict = {}
        thumbnail_data = None
        for content_id in content_id_list:
            data = self._send_art_request(
                {
                    "request": "get_thumbnail",
                    "content_id": content_id,
                    "conn_info": {
                        "d2d_mode": "socket",
                        "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                        "id": self.get_uuid(),
                    },
                }
            )
            data = self._check_reply(data)
            if data.get("binary"):
                # older art API's send the thumbnail inline
                thumbnail_data = bytearray(data["binary"])
                thumbnail_data_dict[helper.thumbnail_name(content_id, thumbnail_data)] = thumbnail_data
                continue
            conn_info = json.loads(data["conn_info"])

            art_socket = self._open_d2d_socket(conn_info, secured=False)
            try:
                header = self._read_thumbnail_header(art_socket, content_id)
            except exceptions.ThumbnailUnavailableError:
                if len(content_id_list) == 1:
                    raise
                continue    # keep the others

            thumbnail_data_len = int(header["fileLength"])
            thumbnail_data = bytearray()
            while len(thumbnail_data) < thumbnail_data_len:
                packet = art_socket.recv(thumbnail_data_len - len(thumbnail_data))
                thumbnail_data.extend(packet)
            filename = "{}.{}".format(header["fileID"], header["fileType"])
            thumbnail_data_dict[filename] = thumbnail_data

        if content_id_list and not thumbnail_data_dict:
            raise exceptions.ThumbnailUnavailableError(content_id_list)
        helper.log_missing_thumbnails(self.host, content_id_list, thumbnail_data_dict)
        return thumbnail_data_dict if as_dict else list(thumbnail_data_dict.values()) if len(content_id_list) > 1 else thumbnail_data

    def upload(self, file, matte="shadowbox_polar", portrait_matte="shadowbox_polar", file_type="png", date=None, CHUNK_SIZE=64*1024):
        '''
        NOTE: both id's and request_id have to be the same
        
        Handle uploading images from different source types.
        An image can be one of:
            a) a file path to an image on the local disk
            b) a url for an image on the web
            c) a series of bytes being directly passed to this method
        Both a) and b) will be passed as strings, so we use urlparse to see if it has a scheme,
        which makes it a URL.
        High quality images will be large, and reading them into memory can be inefficient, so we
        define several methods above to yield their contents in CHUNK_SIZE pieces.
        '''
        def _bytes_chunker(data):
            '''
            Return the bytes in CHUNK_SIZE pieces
            '''
            for pos in range(0, len(data)+1, CHUNK_SIZE):
                yield data[pos: pos+CHUNK_SIZE]

        def _stream_chunker(url):
            '''
            Stream the image, yielding CHUNK_SIZE pieces
            '''
            with requests.get(url, stream=True) as response:
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:  # filter out keep-alive chunks
                        yield chunk

        def _file_chunker(pth):
            '''
            Read the image file, yielding CHUNK_SIZE pieces
            '''
            with open(pth, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break  # End of file
                    yield chunk
 
        if isinstance(file, str):
            pth = Path(file)
            file_name = pth.stem
            file_type = pth.suffix[1:]

            # Check if the string is a URL for a remote image file
            url_parts = urlparse(file)
            if url_parts.scheme in ("http", "https"):   # a windows path such as D:\art.jpg has scheme "d"
                resp = requests.head(file)
                file_size = int(resp.headers.get("Content-Length", 0))
                chunker = _stream_chunker
            else:
                # It must be a file path
                file_size = pth.stat().st_size
                chunker = _file_chunker
        else:
            # Received the raw bytes for an image. `file_type` must be passed if it is not `.png`
            file_size = len(file)
            chunker = _bytes_chunker

        file_type = file_type.lower()
        if file_type == "jpeg":
            file_type = "jpg"

        if date is None:
            date = datetime.now().strftime("%Y:%m:%d %H:%M:%S")

        if self._legacy_api():
            return self._upload_binary(b"".join(chunker(file)), matte, file_type)

        upload_id = self.get_uuid()
        data = self._send_art_request(
            {
                "request": "send_image",
                "file_type": file_type,
                "request_id" : upload_id,
                "id": self.art_uuid,
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self.art_uuid,
                },
                "image_date": date,
                "matte_id": matte or "none",
                "portrait_matte_id": portrait_matte or "none",
                "file_size": file_size,
            },
            wait_for_event="ready_to_use"
        )
        data = self._check_reply(data)
        conn_info = json.loads(data["conn_info"])
        header = json.dumps(
            {
                "num": 0,
                "total": 1,
                "fileLength": file_size,
                "fileName": "dummy",
                "fileType": file_type,
                "secKey": conn_info["key"],
                "version": "0.0.1",
            }
        )

        art_socket = self._open_d2d_socket(conn_info)
        art_socket.send(len(header).to_bytes(4, "big"))
        art_socket.send(header.encode("ascii"))
        #art_socket.send(file)
        # Send the image contents in chunks
        for chunk in chunker(file):
            art_socket.send(chunk)
        # art API 2.03 (2021) sends the upload's id with image_added, 4.3.4.0 (2022) sends none
        data = self.wait_for_response("image_added", upload_id, or_no_id=True)
        return data["content_id"] if data else None

    def _upload_binary(self, data, matte, file_type):
        """upload in one binary websocket message, as SmartThings does on older art API's (0.97, 1.07)"""
        upload_id = self.get_uuid()
        request = {
            "request": "send_image",
            "file_type": "JPEG" if file_type == "jpg" else file_type.upper(),
            "matte_id": matte or "none",
            "id": upload_id,
        }
        if self.connection is None:
            self.open()
        assert self.connection
        self.connection.send_binary(helper.binary_send_image(request, data))
        response = self.wait_for_response("image_added", upload_id)
        return response["content_id"] if response else None

    def delete(self, content_id):
        return self.delete_list([content_id])

    def delete_list(self, content_ids):
        content_id_list = []
        for item in content_ids:
            content_id_list.append({"content_id": item})

        data = self._send_art_request(
            {"request": "delete_image_list", "content_id_list": content_id_list}
        )
        data = self._check_reply(data)
        return content_id_list == json.loads(data['content_id_list'])

    def select_image(self, content_id, category=None, show=True):
        self._send_art_request(
            {
                "request": "select_image",
                "category_id": category,
                "content_id": content_id,
                "show": show,
            }
        )

    def get_artmode(self):
        data = self._send_art_request(
            {
                "request": "get_artmode_status",
            }
        )
        data = self._check_reply(data)
        return data["value"]

    def set_artmode(self, mode):
        mode = "on" if is_true(mode) else "off"
        self._send_art_request(
            {
                "request": "set_artmode_status",
                "value": mode,
            }
        )

    def get_rotation(self):
        data = self._send_art_request(
            {"request": "get_current_rotation"}
        )
        data = self._check_reply(data)
        return data.get("current_rotation_status",0)

    def get_photo_filter_list(self):
        data = self._send_art_request(
            {"request": "get_photo_filter_list"}
        )
        data = self._check_reply(data)
        return json.loads(data["filter_list"])

    def set_photo_filter(self, content_id, filter_id):
        self._send_art_request(
            {
                "request": "set_photo_filter",
                "content_id": content_id,
                "filter_id": filter_id,
            }
        )

    def get_matte_list(self, include_colour=False):
        data = self._send_art_request(
            {"request": "get_matte_list"}
        )
        data = self._check_reply(data)
        return helper.parse_matte_list(data, include_colour)

    def change_matte(self, content_id, matte_id=None, portrait_matte=None):
        '''
        matte is name_color eg flexible_polar or none
        NOTE: Not all mattes can be set for all image sizes!
        '''
        art_request = {
                        "request": "change_matte",
                        "content_id": content_id,
                        "matte_id": matte_id or 'none',
                      }
        if portrait_matte:
            art_request["portrait_matte_id"] = portrait_matte
        self._send_art_request(art_request)
