import asyncio
import contextlib
import logging

import aiohttp

from samsungframetv.async_rest import SamsungTVAsyncRest
from samsungframetv.exceptions import HttpApiError

logging.basicConfig(level=logging.DEBUG)

host = "1.2.3.4"
port = 8002


async def main():
    async with aiohttp.ClientSession() as session:
        with contextlib.suppress(HttpApiError):
            rest_api = SamsungTVAsyncRest(host=host, port=port, session=session)
            logging.info(await rest_api.rest_device_info())


loop = asyncio.get_event_loop()
loop.run_until_complete(main())
