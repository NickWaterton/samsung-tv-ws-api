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
import asyncio
import time
import aiohttp
from typing import Any, Dict, List, Optional, Union, Callable, Awaitable, TypeVar
from urllib.parse import urlparse
import uuid

from . import exceptions, helper
from .command import SamsungTVCommand
from .async_connection import SamsungTVWSAsyncConnection, DEFAULT_OPEN_TIMEOUT
from .remote import SamsungTVWS
from .event import D2D_SERVICE_MESSAGE_EVENT, MS_CHANNEL_READY_EVENT
from .async_rest import SamsungTVAsyncRest
from .helper import get_ssl_context, is_true
from . import ipcontrol

_LOGGING = logging.getLogger(__name__)
_T = TypeVar("_T")

ART_ENDPOINT = "com.samsung.art-app"

# The art service can stop answering (seen on 2022 Frames after watching TV) while the
# websocket stays open. After this many timeouts in a row the connection is closed and
# requests fail fast for a cooldown period, which doubles each time the TV still does not answer.
ART_FAILURE_THRESHOLD = 3
ART_COOLDOWN_START = 30
ART_COOLDOWN_MAX = 240
# seconds to wait for a reply. A TV with another client on the art channel (eg openHAB or
# Home Assistant) can take more than 2s to answer content requests
ART_RESPONSE_TIMEOUT = 5
# an upload that timed out can still be added by the TV later. Its image_added is matched to it, not
# to the next upload, for this many seconds (issues #10 and #27)
UPLOAD_LATE_REPLY_WINDOW = 300


class _UploadWaiter:
    """an upload waiting for the TV's image_added event"""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.abandoned_at: Optional[float] = None


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


class SamsungTVAsyncArt(SamsungTVWSAsyncConnection):
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
        self._rest_api: Optional[SamsungTVAsyncRest] = None
        self.art_mode = None
        self.session = None
        self.lock = asyncio.Lock()
        self.pending_requests = {}
        self.callbacks = {}
        self._art_timeouts = 0
        self._art_cooldown = 0
        self._art_unavailable_until = 0.0
        self._thumbnail_list_only = False   # 2022+ Frames only answer get_thumbnail_list
        self._slideshow_name: Optional[str] = None   # auto_rotation or slideshow, see _slideshow_request
        self._upload_waiters: List[_UploadWaiter] = []   # in upload order, see _match_image_added
        self.get_token()
            
    def get_token(self):
        '''
        Open and close remote control websocket to get/check token
        '''
        tv = SamsungTVWS(self.host, port=self.port, token=self.token, token_file=self.token_file, timeout=self.timeout)

    async def open(self):
        await super().open()

        # Override base class to wait for MS_CHANNEL_READY_EVENT
        assert self.connection
        try:
            data = await asyncio.wait_for(self.connection.recv(), self.timeout or DEFAULT_OPEN_TIMEOUT)
        except asyncio.TimeoutError:
            await self.close()
            raise exceptions.ConnectionFailure("{} art channel did not send ms.channel.ready".format(self.host)) from None
        response = helper.process_api_response(data)
        event = response.get("event", "*")
        self._websocket_event(event, response)

        if event != MS_CHANNEL_READY_EVENT:
            await self.close()
            raise exceptions.ConnectionFailure(response)

        return self.connection

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()
   
    async def start_listening(self) -> bool:  # type: ignore[override]
        # Override base class to process events
        if not self.is_alive():
            await self.open()
        if await super().start_listening(self.process_event):
            try:
                await self.get_artmode()
            except AssertionError:
                pass
            return True
        return False
            
    def get_uuid(self):
        self.art_uuid = str(uuid.uuid4())
        return self.art_uuid
        
    async def wait_for_response(self, request_uuid, timeout=ART_RESPONSE_TIMEOUT):
        data = None
        try:
            if request_uuid not in self.pending_requests.keys():
                self.pending_requests[request_uuid] = asyncio.Future()
            response = await asyncio.wait_for(self.pending_requests[request_uuid], timeout)
            data = json.loads(response["data"])
            if response.get("binary"):
                data["binary"] = response["binary"]    # older art API's send files inline
        except asyncio.exceptions.TimeoutError:
            pass
        self.pending_requests.pop(request_uuid, None)
        if data and data.get("event", "*") == "error":
            raise exceptions.ResponseError(
                f"{json.loads(data['request_data'])['request']} request failed "
                f"with error number {data['error_code']}"
            )
        return data

    def _check_reply(self, data: Optional[_T]) -> _T:
        """raise ArtRequestTimeoutError when an art request got no answer (used to be a bare assert)"""
        if not data:
            raise exceptions.ArtRequestTimeoutError(
                "{} did not answer the art request {}".format(self.host, getattr(self, "_last_art_request", None))
            )
        return data

    async def _send_art_request(
        self,
        request_data: Dict[str, Any],
        wait_for_event: Optional[str] = None,
        timeout: float = ART_RESPONSE_TIMEOUT
    ) -> Optional[Dict[str, Any]]:
        remaining = self._art_unavailable_until - time.monotonic()
        if remaining > 0:
            raise exceptions.ArtServiceUnavailableError(
                "{} art service is not answering, retry in {:.0f}s".format(self.host, remaining)
            )
        if not request_data.get("id"):
            request_data["id"] = self.get_uuid()            #old api
        request_data["request_id"] = request_data["id"]     #new api
        self.pending_requests[wait_for_event or request_data["id"]] = asyncio.Future()
        await self.start_listening()
        # after start_listening, which sends its own get_artmode on a new connection
        self._last_art_request = request_data.get("request")
        # no key press delay, the reply is what we wait for
        await self.send_command(ArtChannelEmitCommand.art_app_request(request_data), key_press_delay=0)
        data = await self.wait_for_response(wait_for_event or request_data["id"], timeout)
        if data is None:
            await self._art_request_timed_out(request_data.get("request"))
        else:
            self._art_timeouts = self._art_cooldown = 0
        return data

    def reset_art_service(self) -> None:
        """forget past timeouts, eg when you know the TV has just been turned on"""
        self._art_timeouts = self._art_cooldown = 0
        self._art_unavailable_until = 0.0

    @property
    def art_service_available(self) -> bool:
        """False while requests are failing fast because the art service stopped answering"""
        return time.monotonic() >= self._art_unavailable_until

    async def _art_request_timed_out(self, request: Optional[str]) -> None:
        self._art_timeouts += 1
        # after a cooldown, one more timeout is enough to back off again
        threshold = 1 if self._art_cooldown else ART_FAILURE_THRESHOLD
        _LOGGING.debug("art request %s timed out (%d/%d)", request, self._art_timeouts, threshold)
        if self._art_timeouts < threshold:
            return
        self._art_timeouts = 0
        self._art_cooldown = min(self._art_cooldown * 2 or ART_COOLDOWN_START, ART_COOLDOWN_MAX)
        self._art_unavailable_until = time.monotonic() + self._art_cooldown
        _LOGGING.warning(
            "%s art service is not answering, closing the connection and pausing requests for %ds",
            self.host, self._art_cooldown,
        )
        with contextlib.suppress(Exception):
            await self.close()
        
    async def process_event(self, event=None, response=None):
        if event == D2D_SERVICE_MESSAGE_EVENT:
            self._art_timeouts = 0      # the art service is answering
            data = json.loads(response["data"])
            sub_event = data.get("event", "*")
            if 'artmode_status' in sub_event:
                # API 5.x may use 'status' instead of 'value'
                self.art_mode = data.get('value', data.get('status', 'off')) == 'on'
            elif sub_event == 'art_mode_changed':
                self.art_mode = data.get('status', 'off') == 'on'
            elif sub_event == 'go_to_standby':
                self.art_mode = False
            elif 'wakeup' in sub_event:
                asyncio.create_task(self.get_artmode())
                
            if sub_event in self.callbacks.keys():
                awaitable = self.callbacks[sub_event](event, response)
                if awaitable:
                    asyncio.create_task(awaitable)
                
            request_id = data.get('request_id', data.get('id'))
            if sub_event == "image_added" and self._match_image_added(request_id, response):
                return
            try:
                if request_id in self.pending_requests.keys():
                    self.pending_requests[request_id].set_result(response)
                elif sub_event in self.pending_requests.keys():
                    self.pending_requests[sub_event].set_result(response)
            except asyncio.exceptions.InvalidStateError:    #already completed
                pass
                
    def _match_image_added(self, request_id: Optional[str], response: Dict[str, Any]) -> bool:
        """
        give an image_added event to the upload it belongs to, True if it was used. The 2021 Frame (art
        API 2.03) sends the upload's id, the 2022 (4.3.4.0) sends none, so those go to the oldest upload,
        which is the one the TV finished first. Uploads that timed out stay in the list for a while, so
        their late event is not taken as the answer to the next upload
        """
        now = time.monotonic()
        self._upload_waiters = [
            w for w in self._upload_waiters if w.abandoned_at is None or now - w.abandoned_at < UPLOAD_LATE_REPLY_WINDOW
        ]
        if not self._upload_waiters:
            return False
        if request_id:
            waiter = next((w for w in self._upload_waiters if w.request_id == request_id), None)
            if waiter is None:
                _LOGGING.debug("image_added for an upload we are not waiting for: %s", request_id)
                return True
        else:
            waiter = self._upload_waiters[0]
        self._upload_waiters.remove(waiter)
        if waiter.abandoned_at is not None:
            _LOGGING.warning("%s added an upload %.0fs after it timed out: %s", self.host, now - waiter.abandoned_at,
                             json.loads(response["data"]).get("content_id"))
        elif not waiter.future.done():
            waiter.future.set_result(response)
        return True

    async def _wait_for_image_added(self, waiter: _UploadWaiter, timeout: float) -> Optional[Dict[str, Any]]:
        try:
            response = await asyncio.wait_for(asyncio.shield(waiter.future), timeout)
        except asyncio.TimeoutError:
            waiter.abandoned_at = time.monotonic()
            _LOGGING.warning("%s did not confirm the upload within %ss, it may still be added", self.host, timeout)
            return None
        return json.loads(response["data"])

    def set_callback(self, trigger, callback=None):
        if not callback:
            self.callbacks.pop(trigger, None)
        else:
            self.callbacks[trigger] = callback
            
    def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
            self._rest_api = None
        return self.session

    def _get_rest_api(self) -> SamsungTVAsyncRest:
        self.get_session()
        if self._rest_api is None:
            self._rest_api = SamsungTVAsyncRest(host=self.host, port=self.port, session=self.session)
        return self._rest_api
        
    async def _get_device_info(self):
        try:
            async with self.lock:
                await asyncio.sleep(0.1)    #do not hit rest api to frequently
                return await self._get_rest_api().rest_device_info()
        except Exception as e:
            pass
        return {}

    async def supported(self) -> bool:
        data = await self._get_device_info()
        if data.get("device", {}).get("FrameTVSupport") == "true":
            return True
        # some TV's with art mode (eg S95H) report FrameTVSupport false, so ask the art channel
        if data.get("device", {}).get("PowerState", "off") == "on":
            return await self._try_get_artmode() is not None
        return False

    async def _try_get_artmode(self, timeout=5) -> Optional[str]:
        """get_artmode(), returning None instead of raising if the art channel does not answer"""
        try:
            return await asyncio.wait_for(self.get_artmode(), timeout)
        except Exception as e:
            _LOGGING.debug("art channel did not answer: %s", e)
        return None

    async def get_power_state(self, ip_control: Optional[ipcontrol.SamsungTVAsyncIPControl] = None) -> str:
        """
        returns one of 'off', 'standby', 'tv', 'art' or 'on' (on, but art mode unknown)
        ip_control: optional paired SamsungTVAsyncIPControl, the most reliable source
        """
        ip_state = None
        if ip_control is not None:
            try:
                ip_state = await ip_control.async_get_power_state()
                if ip_state != ipcontrol.POWER_OFF:
                    return ip_state
            except ipcontrol.IPControlError as e:
                _LOGGING.debug("IP Control failed: %s", e)
        rest_info = await self._get_device_info() or None
        artmode = None
        if rest_info and rest_info.get("device", {}).get("PowerState", "on") in ("on", "standby"):
            artmode = await self._try_get_artmode()
        return ipcontrol.derive_power_state(rest_info, artmode, ip_state)
        
    async def on(self) -> bool:
        data = await self._get_device_info()
        return data.get("device", {}).get('PowerState', 'off') == 'on'
        
    async def is_artmode(self) -> bool:
        return await self.on() and bool(self.art_mode)
        
    async def in_artmode(self) -> bool:
        data = await self._get_device_info()
        power = data.get("device", {}).get("PowerState", "off")
        # 2025+ Frames report PowerState 'standby' while in art mode (issue #33)
        if power == "on" or (power == "standby" and ipcontrol.model_year(data) >= 25):
            return await self.get_artmode() == 'on'
        return False
        
    async def get_api_version(self):
        try:
            # new api first, it answers quickly with an error on old TV's
            data = await self._send_art_request(
                {"request": "api_version"}
            )
        except exceptions.ResponseError:
            data = None
        if not data:
            data = await self._send_art_request(
                {"request": "get_api_version"}
            )
        data = self._check_reply(data)
        self._api_version = data["version"]
        return data["version"]

    async def _open_d2d_connection(self, conn_info, secured=None):
        """connect to the socket the TV named in conn_info for a thumbnail or upload transfer"""
        if secured is None:
            secured = conn_info.get("secured", False)
        if secured:
            await asyncio.sleep(helper.D2D_SECURED_CONNECT_DELAY)
        return await asyncio.open_connection(conn_info["ip"], int(conn_info["port"]), ssl=get_ssl_context() if secured else None)

    async def _legacy_api(self) -> bool:
        """2017/2018 Frames (art API below 2) upload and send thumbnails in one websocket message"""
        if getattr(self, "_api_version", None) is None:
            try:
                await self.get_api_version()
            except Exception as e:
                _LOGGING.debug("could not get art api version: %s", e)
                return False
        return helper.is_legacy_art_api(self._api_version)

    async def get_device_info(self):
        data = await self._send_art_request(
            {"request": "get_device_info"}
        )
        data = self._check_reply(data)
        return data

    async def available(self, category=None, timeout=10):
        '''
        category is 'MY-C0004' or 'MY-C0002' where 4 is favourites, 2 is my pictures, and 8 is store
        '''
        data = await self._send_art_request(
            {"request": "get_content_list", "category": category},
            timeout=timeout
        )
        data = self._check_reply(data)
        return [ v for v in json.loads(data["content_list"]) if v["category_id"] == category] if category else json.loads(data["content_list"])

    async def get_current(self):
        data = await self._send_art_request(
            {"request": "get_current_artwork"}
        )
        data = self._check_reply(data)
        return data
        
    async def set_favourite(self, content_id, status="on"):
        status = "on" if is_true(status) else "off"
        data = await self._send_art_request(
            {   "request": "change_favorite",
                "content_id": content_id,
                "status": status},
            wait_for_event = "favorite_changed"
        )
        data = self._check_reply(data)
        return data
        
    async def _artmode_settings_one_by_one(self, setting=''):
        """the get_artmode_settings list, from the requests art API 2.03 has for each setting"""
        items = []
        for item in helper.ART_SETTINGS_REQUESTED_ONE_BY_ONE:
            if setting and item != setting:
                continue
            try:
                data = await self._send_art_request({"request": "get_{}".format(item)})
            except exceptions.ResponseError as e:
                _LOGGING.debug("get_%s: %s", item, e)
                continue
            if data:
                # brightness_sensor_setting sends its value as status
                values = dict(data, value=data["status"]) if "value" not in data and "status" in data else data
                items.append(dict({"item": item}, **{k: values[k] for k in ("value", "min", "max", "valid_values") if k in values}))
        return items

    async def get_artmode_settings(self, setting=''):
        '''
        setting can be any of 'brightness', 'color_temperature', 'motion_sensitivity',
        'motion_timer', or 'brightness_sensor_setting'
        art API 4.3.4.0 (2022) added get_artmode_settings, earlier versions (2.03) return error -9, so the
        list is built from get_brightness and get_color_temperature, which they answer
        '''
        await self._legacy_api()   # reads the art api version
        if helper.has_artmode_settings(getattr(self, "_api_version", None)):
            try:
                data = await self._send_art_request(
                    {"request": "get_artmode_settings"}
                )
                data = self._check_reply(data)
                data = json.loads(data['data'])
            except exceptions.ResponseError:
                data = await self._artmode_settings_one_by_one(setting)
                if not data:
                    raise
        else:
            data = await self._artmode_settings_one_by_one(setting)
            data = self._check_reply(data)
        return next(iter(item for item in data if item['item'] == setting), data)

    async def _slideshow_request(self, action, fields=None):
        """
        get or set the slideshow. Art API 2.03 (2021) calls it auto_rotation and 4.3.4.0 (2022) renamed it
        slideshow, each TV fails the other name (error -9 or no answer), so send the name that works
        """
        if self._slideshow_name is None:
            await self._legacy_api()   # reads the art api version
            self._slideshow_name = helper.slideshow_name(getattr(self, "_api_version", None))
        error = None
        for name in (self._slideshow_name, "slideshow" if self._slideshow_name == "auto_rotation" else "auto_rotation"):
            try:
                data = await self._send_art_request(dict({"request": "{}_{}_status".format(action, name)}, **(fields or {})))
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

    async def get_auto_rotation_status(self):
        return await self._slideshow_request("get")

    async def set_auto_rotation_status(self, duration=0, type=True, category=2):
        '''
        duration is "off" or "number" where number is duration in minutes. set 0 for 'off'
        slide show type can be "slideshow" or "shuffleslideshow", set True for shuffleslideshow
        category is 'MY-C0004' or 'MY-C0002' where 4 is favourites, 2 is my pictures, and 8 is store
        '''
        return await self._slideshow_request("set", self._slideshow_fields(duration, type, category))

    async def get_slideshow_status(self):
        return await self._slideshow_request("get")

    async def set_slideshow_status(self, duration=0, type=True, category=2):
        '''
        duration is "off" or "number" where number is duration in minutes. set 0 for 'off'
        slide show type can be "slideshow" or "shuffleslideshow", set True for shuffleslideshow
        category is 'MY-C0004' or 'MY-C0002' where 4 is favourites, 2 is my pictures, and 8 is store
        '''
        return await self._slideshow_request("set", self._slideshow_fields(duration, type, category))

    async def get_brightness(self):
        # art API 4.3.4.0 (2022) only answers get_artmode_settings, earlier versions return error -9 for it
        await self._legacy_api()   # reads the art api version
        if helper.has_artmode_settings(getattr(self, "_api_version", None)):
            with contextlib.suppress(exceptions.ResponseError):
                return await self.get_artmode_settings('brightness')
        data = await self._send_art_request(
            {"request": "get_brightness"}
        )
        data = self._check_reply(data)
        return data

    async def set_brightness(self, value):
        # "0" to "10"
        data = await self._send_art_request(
            {"request": "set_brightness", "value": value}
        )
        data = self._check_reply(data)
        return data
        
    async def get_color_temperature(self):
        # art API 4.3.4.0 (2022) only answers get_artmode_settings, earlier versions return error -9 for it
        await self._legacy_api()   # reads the art api version
        if helper.has_artmode_settings(getattr(self, "_api_version", None)):
            with contextlib.suppress(exceptions.ResponseError):
                return await self.get_artmode_settings('color_temperature')
        data = await self._send_art_request(
            {"request": "get_color_temperature"}
        )
        data = self._check_reply(data)
        return data

    async def set_color_temperature(self, value):
        # "-5" to "5"
        data = await self._send_art_request(
            {"request": "set_color_temperature", "value": value}
        )
        data = self._check_reply(data)
        return data
        
    async def set_brightness_sensor_setting(self, value):
        value = "on" if is_true(value) else "off"
        data = await self._send_art_request(
            {"request": "set_brightness_sensor_setting", "value": value}
        )
        data = self._check_reply(data)
        return data
        
    async def set_motion_timer(self, value):
        # "off", "5", "15", "30", "60", "120","240" 
        data = await self._send_art_request(
            {"request": "set_motion_timer", "value": value}
        )
        data = self._check_reply(data)
        return data
        
    async def set_motion_sensitivity(self, value):
        # "1" to "3"
        data = await self._send_art_request(
            {"request": "set_motion_sensitivity", "value": value}
        )
        data = self._check_reply(data)
        return data
 
    @staticmethod
    async def _read_thumbnail_header(reader, writer, content_id, eof_ok=False):
        try:
            header_len = int.from_bytes(await reader.readexactly(4), "big")
        except asyncio.IncompleteReadError as e:
            writer.close()
            if e.partial:
                raise
            if eof_ok:
                return None
            # the TV closed the socket without sending anything, seen for some Art Store items
            raise exceptions.ThumbnailUnavailableError(content_id) from e
        return json.loads(await reader.readexactly(header_len))

    async def get_thumbnail_list(self, content_id_list=[]):
        if isinstance(content_id_list, str):
            content_id_list=[content_id_list]
        requested = list(content_id_list)
        data = await self._send_art_request(
            {
                "request": "get_thumbnail_list",
                "content_id_list": [{"content_id": id} for id in requested],
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self.get_uuid(),
                }
            }
        )
        data = self._check_reply(data)
        conn_info = json.loads(data["conn_info"])
        reader, writer = await self._open_d2d_connection(conn_info)
        total_num_thumbnails = 1
        current_thumb = -1
        thumbnail_data_dict: Dict[str, bytes] = {}
        while current_thumb+1 < total_num_thumbnails:
            # the TV leaves out items it has no thumbnail for and closes the socket early
            header = await self._read_thumbnail_header(reader, writer, requested, eof_ok=bool(thumbnail_data_dict))
            if header is None:
                break
            thumbnail_data_len = int(header["fileLength"])
            current_thumb = int(header["num"])
            total_num_thumbnails = int(header["total"])
            filename = "{}.{}".format(header["fileID"], header["fileType"])
            thumbnail_data_dict[filename] = await reader.readexactly(thumbnail_data_len)
        writer.close()
        helper.log_missing_thumbnails(self.host, requested, thumbnail_data_dict)
        return thumbnail_data_dict

    async def get_thumbnail(self, content_id_list=[], as_dict=False):
        if isinstance(content_id_list, str):
            content_id_list=[content_id_list]
        if not self._thumbnail_list_only:
            await self._legacy_api()   # reads the art api version
            self._thumbnail_list_only = helper.uses_thumbnail_list(getattr(self, "_api_version", None))
        if self._thumbnail_list_only:
            thumbnails = await self.get_thumbnail_list(content_id_list)
            return thumbnails if as_dict else list(thumbnails.values()) if len(content_id_list) > 1 else next(iter(thumbnails.values()), None)
        thumbnail_data_dict: Dict[str, bytes] = {}
        thumbnail_data = None
        for content_id in content_id_list:
            data = await self._send_art_request(
                {
                    "request": "get_thumbnail",
                    "content_id": content_id,
                    "conn_info": {
                        "d2d_mode": "socket",
                        "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                        "id": self.get_uuid()
                    }
                }
            )
            if data is None and not thumbnail_data_dict and not await self._legacy_api():
                # newer TV's (seen on a 2022 Frame) don't answer get_thumbnail at all
                _LOGGING.info("%s did not answer get_thumbnail, using get_thumbnail_list", self.host)
                self._thumbnail_list_only = True
                return await self.get_thumbnail(content_id_list, as_dict)
            data = self._check_reply(data)
            if data.get("binary"):
                # older art API's send the thumbnail inline
                thumbnail_data = bytes(data["binary"])
                thumbnail_data_dict[helper.thumbnail_name(content_id, thumbnail_data)] = thumbnail_data
                continue
            conn_info = json.loads(data["conn_info"])
            reader, writer = await self._open_d2d_connection(conn_info, secured=False)
            try:
                header = await self._read_thumbnail_header(reader, writer, content_id)
            except exceptions.ThumbnailUnavailableError:
                if len(content_id_list) == 1:
                    raise
                continue    # keep the others
            thumbnail_data_len = int(header["fileLength"])
            thumbnail_data = await reader.readexactly(thumbnail_data_len)
            writer.close()
            filename = "{}.{}".format(header["fileID"], header["fileType"])
            thumbnail_data_dict[filename] = thumbnail_data
        if content_id_list and not thumbnail_data_dict:
            raise exceptions.ThumbnailUnavailableError(content_id_list)
        helper.log_missing_thumbnails(self.host, content_id_list, thumbnail_data_dict)
        return thumbnail_data_dict if as_dict else list(thumbnail_data_dict.values()) if len(content_id_list) > 1 else thumbnail_data

    async def upload(self, file, matte="shadowbox_polar", portrait_matte="shadowbox_polar", file_type="png", date=None, timeout=30, CHUNK_SIZE=64*1024):
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
        async def _bytes_chunker(data):
            '''
            Return the bytes in CHUNK_SIZE pieces
            '''
            for pos in range(0, len(data)+1, CHUNK_SIZE):
                yield data[pos: pos+CHUNK_SIZE]

        async def _stream_chunker(url):
            '''
            Stream the image, yielding CHUNK_SIZE pieces
            '''
            async with aiohttp.ClientSession().get(url) as response:
                response.raise_for_status()  # Raise an exception for bad status codes
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    yield chunk

        async def _file_chunker(pth):
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
                async with aiohttp.ClientSession().head(file) as resp:
                    resp.raise_for_status()  # Raise an exception for bad status codes
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

        if await self._legacy_api():
            image = b"".join([chunk async for chunk in chunker(file)])
            return await self._upload_binary(image, matte, file_type, timeout)

        data = await self._send_art_request(
            {
                "request": "send_image",
                "file_type": file_type,
                "request_id" : self.get_uuid(),
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
            }
        )
        data = self._check_reply(data)
        waiter = _UploadWaiter(self.art_uuid)
        self._upload_waiters.append(waiter)
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

        try:
            reader, writer = await self._open_d2d_connection(conn_info)
        except Exception:
            self._upload_waiters.remove(waiter)
            raise
        writer.write(len(header).to_bytes(4, "big"))
        writer.write(header.encode("ascii"))
        #writer.write(file)
        # Send the image contents in chunks
        async for chunk in chunker(file):
            writer.write(chunk)
            await writer.drain()
        writer.close()
        data = await self._wait_for_image_added(waiter, timeout)
        return data["content_id"] if data else None

    async def _upload_binary(self, data, matte, file_type, timeout=10):
        """upload in one binary websocket message, as SmartThings does on older art API's (0.97, 1.07)"""
        request = {
            "request": "send_image",
            "file_type": "JPEG" if file_type == "jpg" else file_type.upper(),
            "matte_id": matte or "none",
            "id": self.get_uuid(),
        }
        self.pending_requests["image_added"] = asyncio.Future()
        await self.start_listening()
        assert self.connection
        await self.connection.send(helper.binary_send_image(request, data))
        response = await self.wait_for_response("image_added", timeout=timeout)
        return response["content_id"] if response else None

    async def delete(self, content_id):
        await self.delete_list([content_id])

    async def delete_list(self, content_ids):
        content_id_list = [{"content_id": item} for item in content_ids]
        await self._send_art_request(
            {   "request": "delete_image_list",
                "content_id_list": content_id_list
            }
        )

    async def select_image(self, content_id, category=None, show=True):
        await self._send_art_request(
            {
                "request": "select_image",
                "category_id": category,
                "content_id": content_id,
                "show": show,
            }
        )

    async def get_artmode(self):
        data = await self._send_art_request(
            {
                "request": "get_artmode_status",
            }
        )
        data = self._check_reply(data)
        return data["value"]

    async def set_artmode(self, mode):
        mode = "on" if is_true(mode) else "off"
        await self._send_art_request(
            {
                "request": "set_artmode_status",
                "value": mode,
            }
        )
        
    async def get_rotation(self):
        data = await self._send_art_request(
            {"request": "get_current_rotation"}
        )
        data = self._check_reply(data)
        return data.get("current_rotation_status",0)

    async def get_photo_filter_list(self):
        data = await self._send_art_request(
            {"request": "get_photo_filter_list"}
        )
        data = self._check_reply(data)
        return json.loads(data["filter_list"])

    async def set_photo_filter(self, content_id, filter_id):
        await self._send_art_request(
            {
                "request": "set_photo_filter",
                "content_id": content_id,
                "filter_id": filter_id,
            }
        )

    async def get_matte_list(self, include_colour=False):
        data = await self._send_art_request(
            {"request": "get_matte_list"}
        )
        data = self._check_reply(data)
        return helper.parse_matte_list(data, include_colour)

    async def change_matte(self, content_id, matte_id=None, portrait_matte=None):
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
        await self._send_art_request(art_request)
