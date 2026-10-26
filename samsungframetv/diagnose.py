"""
Record what a TV reports, so owners of models we can't test can send it in an issue.

usage: python -m samsungframetv.diagnose 192.168.1.20 [--ipcontrol] [--upload IMAGE] [-o report.json]

Only reads from the TV: it does not upload, delete or change any setting, unless --upload is given,
which uploads one image and deletes it again. The report has the
REST device info, the art API version and the raw reply to each art request. MAC addresses,
ip addresses, device ids and tokens are removed before it is saved.
"""

import argparse
import asyncio
import datetime
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import exceptions, ipcontrol
from .async_art import SamsungTVAsyncArt
from .version import __version__

_LOGGING = logging.getLogger(__name__)

REPORT_FORMAT = 1

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_MAC = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")
# private and link local addresses only, so versions such as 4.3.4.0 are kept
_IPV4 = re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|169\.254)\.\d{1,3}\.\d{1,3}\b")
# values that identify the TV or its owner, whatever they look like
_PRIVATE_KEYS = {"duid", "id", "udn", "wifiMac", "ip", "token", "deviceName", "name", "uri", "request_id", "connection_id",
                 "target_client_id"}


def redact(value: Any, uuids: Optional[Dict[str, str]] = None) -> Any:
    """
    copy of value with ids, MAC and ip addresses and tokens replaced. Each uuid becomes <uuid-N>, the
    same N each time it appears in one report, so replies can still be matched to their requests
    """
    uuids = {} if uuids is None else uuids

    def uuid_name(match: "re.Match[str]") -> str:
        return uuids.setdefault(match.group(0).lower(), "<uuid-{}>".format(len(uuids) + 1))

    if isinstance(value, dict):
        return {k: (_UUID.sub(uuid_name, v) if isinstance(v, str) and _UUID.fullmatch(v) else "<redacted>")
                if k in _PRIVATE_KEYS and v not in (None, "") else redact(v, uuids) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, uuids) for v in value]
    if isinstance(value, str):
        # some replies hold json in a string
        if value[:1] in "{[":
            try:
                return json.dumps(redact(json.loads(value), uuids))
            except ValueError:
                pass
        value = _UUID.sub(uuid_name, value)
        value = _MAC.sub("<mac>", value)
        return _IPV4.sub("<ip>", value)
    return value


RAW_CONTENT_LIST_ITEMS = 5


def _summarise_binary(response: Any) -> Any:
    if isinstance(response, dict) and isinstance(response.get("binary"), (bytes, bytearray)):
        response = dict(response, binary="<{} bytes>".format(len(response["binary"])))
    return response


def _shorten_content_list(response: Any) -> Any:
    """the content list can hold hundreds of items, a few show the format"""
    if isinstance(response, dict) and isinstance(response.get("content_list"), str):
        try:
            items = json.loads(response["content_list"])
            response = dict(response, content_list=json.dumps(items[:RAW_CONTENT_LIST_ITEMS]), content_list_count=len(items))
        except ValueError:
            pass
    return response


class _Recorder:
    """wraps the art channel and REST calls of a SamsungTVAsyncArt to keep the raw replies"""

    def __init__(self, tv: SamsungTVAsyncArt) -> None:
        self.raw: List[Dict[str, Any]] = []
        self.rest: Optional[Dict[str, Any]] = None
        send = tv._send_art_request
        device_info = tv._get_device_info

        async def recorded_send(request_data: Dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            entry: Dict[str, Any] = {"request": dict(request_data)}
            started = time.monotonic()
            try:
                response = await send(request_data, *args, **kwargs)
                entry["response"] = _shorten_content_list(_summarise_binary(response))
                return response
            except Exception as e:
                entry["error"] = repr(e)
                raise
            finally:
                entry["seconds"] = round(time.monotonic() - started, 2)
                self.raw.append(entry)

        async def recorded_device_info() -> Any:
            info = await device_info()
            if self.rest is None and info:
                self.rest = info
            return info

        wait_for_response = tv.wait_for_response

        async def recorded_wait(request_uuid: str, *args: Any, **kwargs: Any) -> Any:
            # events that answer a request sent earlier, eg image_added after an upload
            entry: Dict[str, Any] = {"wait_for": request_uuid}
            started = time.monotonic()
            try:
                response = await wait_for_response(request_uuid, *args, **kwargs)
                entry["response"] = _summarise_binary(response)
                return response
            except Exception as e:
                entry["error"] = repr(e)
                raise
            finally:
                entry["seconds"] = round(time.monotonic() - started, 2)
                self.raw.append(entry)

        tv._send_art_request = recorded_send  # type: ignore[method-assign]
        tv._get_device_info = recorded_device_info  # type: ignore[method-assign]
        tv.wait_for_response = recorded_wait  # type: ignore[method-assign]


def thumbnail_content_id(current: Any, content: Any) -> Optional[str]:
    """Pick an item for the thumbnail test.

    The 2021 Frame (art API 2.03) closes the transfer socket without sending
    anything for Art Store items (SAM-...), so a user photo (MY_...) is
    tried first: the current one, else the first one in the content list.
    """
    current_id = current.get("content_id") if isinstance(current, dict) else None
    if current_id and str(current_id).startswith("MY_"):
        return str(current_id)
    if isinstance(content, list):
        for item in content:
            item_id = item.get("content_id") if isinstance(item, dict) else None
            if item_id and str(item_id).startswith("MY_"):
                return str(item_id)
    return str(current_id) if current_id else None


async def _step(results: Dict[str, Any], name: str, call: Callable[[], Awaitable[Any]], timeout: float) -> Any:
    started = time.monotonic()
    try:
        value = await asyncio.wait_for(call(), timeout)
        results[name] = {"ok": True, "result": _shorten_content_list(_summarise_binary(value))}
        return value
    except Exception as e:
        results[name] = {"ok": False, "error": repr(e)}
        _LOGGING.debug("%s failed: %r", name, e)
        return None
    finally:
        results[name]["seconds"] = round(time.monotonic() - started, 2)


async def run_diagnostics(
    tv: SamsungTVAsyncArt,
    ip_control: Optional[ipcontrol.SamsungTVAsyncIPControl] = None,
    timeout: float = 15,
    upload_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    run the read only checks against tv, returns the redacted report. With upload_file, also upload
    that image and delete it again, to record how the TV answers an upload
    """
    recorder = _Recorder(tv)
    steps: Dict[str, Any] = {}

    async def step(name: str, call: Callable[[], Awaitable[Any]]) -> Any:
        return await _step(steps, name, call, timeout)

    await step("rest_device_info", tv._get_device_info)
    await step("supported", tv.supported)
    await step("get_api_version", tv.get_api_version)
    await step("legacy_art_api", tv._legacy_api)
    await step("get_device_info", tv.get_device_info)
    await step("get_artmode", tv.get_artmode)
    await step("in_artmode", tv.in_artmode)
    await step("get_power_state", lambda: tv.get_power_state(ip_control))
    current = await step("get_current", tv.get_current)
    content = await step("available", tv.available)
    if isinstance(content, list):
        # the list can be long, the categories and a sample are enough
        categories: Dict[str, int] = {}
        for item in content:
            categories[item.get("category_id", "?")] = categories.get(item.get("category_id", "?"), 0) + 1
        steps["available"]["result"] = {"count": len(content), "categories": categories, "sample": content[:3]}
    await step("get_matte_list", lambda: tv.get_matte_list(include_colour=True))
    await step("get_photo_filter_list", tv.get_photo_filter_list)
    await step("get_slideshow_status", tv.get_slideshow_status)
    await step("get_auto_rotation_status", tv.get_auto_rotation_status)
    await step("get_artmode_settings", tv.get_artmode_settings)
    await step("get_brightness", tv.get_brightness)
    content_id = thumbnail_content_id(current, content)
    if content_id:
        thumbnail = await step("get_thumbnail", lambda: tv.get_thumbnail(content_id))
        if isinstance(thumbnail, (bytes, bytearray)):
            steps["get_thumbnail"]["result"] = {"bytes": len(thumbnail), "jpeg": thumbnail[:2] == b"\xff\xd8", "png": thumbnail[:4] == b"\x89PNG"}
    if ip_control is not None:
        await step("ipcontrol_tv_states", lambda: ip_control.async_call("get_tv_states"))
    if upload_file:
        file_type = "png" if upload_file.lower().endswith(".png") else "jpg"
        image = Path(upload_file).read_bytes()
        uploaded = await step("upload", lambda: tv.upload(image, matte="none", portrait_matte="none", file_type=file_type))
        if uploaded:
            await step("delete_uploaded", lambda: tv.delete(uploaded))

    rest = recorder.rest or {}
    device = rest.get("device", {})
    report = {
        "report_format": REPORT_FORMAT,
        "library_version": __version__,
        "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": device.get("model"),
        "model_name": device.get("modelName"),
        "model_year": ipcontrol.model_year(rest) if rest else None,
        "art_api_version": getattr(tv, "_api_version", None),
        "rest": rest,
        "steps": steps,
        "raw": recorder.raw,
    }
    # as it will be saved, eg tuples become lists
    return json.loads(json.dumps(redact(report), default=repr))


def _print_summary(report: Dict[str, Any]) -> None:
    print("model {} ({}), art api {}".format(report["model_name"], report["model"], report["art_api_version"]))
    if not report["rest"]:
        print("  the TV did not answer on port 8002, check it is on and the ip address is right")
    for name, step in report["steps"].items():
        if step["ok"]:
            result = json.dumps(step["result"])
            print("  ok    {:26} {}".format(name, result if len(result) < 70 else result[:67] + "..."))
        else:
            print("  FAIL  {:26} {}".format(name, step["error"]))


async def _main(args: argparse.Namespace) -> None:
    ip_control = None
    if args.ipcontrol:
        ip_control = ipcontrol.SamsungTVAsyncIPControl(args.ip, token_file="ipcontrol_token_{}.txt".format(args.ip))
        if not ip_control.token:
            print("pairing IP Control, press Allow on the TV")
            await ip_control.async_pair()
    tv = SamsungTVAsyncArt(args.ip, port=8002, token_file=args.token_file, timeout=args.timeout)
    try:
        report = await run_diagnostics(tv, ip_control, timeout=args.timeout, upload_file=args.upload)
    finally:
        try:
            await tv.close()
        except exceptions.ConnectionFailure:
            pass
    _print_summary(report)
    output = args.output or "diagnose_{}.json".format(re.sub(r"\W", "_", report["model_name"] or "unknown"))
    with open(output, "w") as f:
        json.dump(report, f, indent=2)
    print("\nsaved {}. Please check it, then attach it to a GitHub issue:".format(output))
    print("https://github.com/NickWaterton/samsung-tv-ws-api/issues")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Record what a Samsung TV reports (read only), for bug reports")
    parser.add_argument("ip", help="TV ip address")
    parser.add_argument("-t", "--token_file", default=None, help="websocket token file (default token_<ip>.txt)")
    parser.add_argument("-o", "--output", default=None, help="report file (default diagnose_<model>.json)")
    parser.add_argument("--ipcontrol", action="store_true", help="also ask IP Control (port 1516)")
    parser.add_argument("--upload", metavar="IMAGE", default=None,
                        help="also upload this jpg or png and delete it again (changes the TV's art for a moment)")
    parser.add_argument("--timeout", type=float, default=15, help="seconds to wait for each check (default 15)")
    parser.add_argument("-D", "--debug", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    args.token_file = args.token_file or "token_{}.txt".format(args.ip)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
