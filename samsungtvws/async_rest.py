"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Copyright (C) 2019 Xchwarze

    This library is free software; you can redistribute it and/or
    modify it under the terms of the GNU Lesser General Public
    License as published by the Free Software Foundation; either
    version 2.1 of the License, or (at your option) any later version.

    This library is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
    Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public
    License along with this library; if not, write to the Free Software
    Foundation, Inc., 51 Franklin Street, Fifth Floor,
    Boston, MA  02110-1335  USA

"""
import logging

import aiohttp

from . import connection, exceptions, helper

_LOGGING = logging.getLogger(__name__)


class SamsungTVAsyncRest(connection.SamsungTVWSBaseConnection):
    def __init__(
        self,
        host,
        *,
        session: aiohttp.ClientSession,
        port=8001,
        timeout=None,
    ):
        super().__init__(
            host,
            endpoint=None,
            port=port,
            timeout=timeout,
        )
        self.session = session

    async def _rest_request(self, target, method="GET"):
        url = self._format_rest_url(target)
        try:
            if method == "POST":
                future = self.session.post(url, timeout=self.timeout, verify_ssl=False)
            elif method == "PUT":
                future = self.session.put(url, timeout=self.timeout, verify_ssl=False)
            elif method == "DELETE":
                future = self.session.delete(
                    url, timeout=self.timeout, verify_ssl=False
                )
            else:
                future = self.session.get(url, timeout=self.timeout, verify_ssl=False)
            async with future as resp:
                return helper.process_api_response(await resp.text())
        except aiohttp.ClientConnectionError:
            raise exceptions.HttpApiError(
                "TV unreachable or feature not supported on this model."
            )

    async def rest_device_info(self):
        _LOGGING.debug("Get device info via rest api")
        return await self._rest_request("")

    async def rest_app_status(self, app_id):
        _LOGGING.debug("Get app %s status via rest api", app_id)
        return await self._rest_request("applications/" + app_id)

    async def rest_app_run(self, app_id):
        _LOGGING.debug("Run app %s via rest api", app_id)
        return await self._rest_request("applications/" + app_id, "POST")

    async def rest_app_close(self, app_id):
        _LOGGING.debug("Close app %s via rest api", app_id)
        return await self._rest_request("applications/" + app_id, "DELETE")

    async def rest_app_install(self, app_id):
        _LOGGING.debug("Install app %s via rest api", app_id)
        return await self._rest_request("applications/" + app_id, "PUT")
