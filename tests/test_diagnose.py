"""Tests for the diagnose report, and replay of reports recorded on real TV's (tests/fixtures/devices)."""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from samsungframetv import diagnose, exceptions
from samsungframetv.async_art import SamsungTVAsyncArt

DEVICES = sorted((Path(__file__).parent / "fixtures" / "devices").glob("*.json"))
JPEG = b"\xff\xd8\xff\xe0" + bytes(100)

REST_2018 = {
    "device": {
        "FrameTVSupport": "true",
        "PowerState": "on",
        "model": "18_KANTM2_FRAME",
        "modelName": "UN55LS03NA",
        "duid": "uuid:c55ae696-726b-486d-bcbb-07e64d975206",
        "wifiMac": "4c:57:39:b0:43:7c",
        "ip": "192.168.1.166",
        "name": "Nick's Frame",
    },
    "isSupport": '{"FrameTVSupport":"true"}\n',
    "uri": "https://192.168.1.166:8002/api/v2/",
}
# replies modelled on a 2018 Frame (issue #35)
REPLIES_2018: Dict[str, Any] = {
    "api_version": exceptions.ResponseError("api_version request failed with error number -1"),
    "get_api_version": {"version": "1.07"},
    "get_device_info": {"id": "4c:57:39:b0:43:7c"},
    "get_artmode_status": {"value": "on"},
    "get_current_artwork": {"content_id": "MY_F0001", "matte_id": "none"},
    "get_content_list": {"content_list": json.dumps([{"content_id": "MY_F0001", "category_id": "MY-C0002"}])},
    "get_matte_list": {"matte_types": [{"matte_id": "none"}, {"matte_id": "modern_polar"}]},
    "get_photo_filter_list": {"filter_list": json.dumps([{"filter_id": "ink"}])},
    "get_thumbnail": {"event": "thumbnail", "binary": JPEG},
}


def art_tv(rest: Dict[str, Any], replies: Dict[str, List[Any]]) -> SamsungTVAsyncArt:
    """SamsungTVAsyncArt that answers each art request from replies, in order, repeating the last one"""
    with patch.object(SamsungTVAsyncArt, "get_token"):
        tv = SamsungTVAsyncArt("127.0.0.1")
    tv.close = AsyncMock()

    async def send(request_data: Dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        answers = replies.get(request_data["request"]) or [None]
        reply = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def device_info() -> Dict[str, Any]:
        return rest

    tv._send_art_request = send  # type: ignore[method-assign]
    tv._get_device_info = device_info  # type: ignore[method-assign]
    return tv


def test_redact() -> None:
    redacted = diagnose.redact(REST_2018)
    text = json.dumps(redacted)
    for private in ("c55ae696", "4c:57:39", "192.168.1.166", "Nick's Frame"):
        assert private not in text
    assert redacted["device"]["model"] == "18_KANTM2_FRAME"
    assert json.loads(redacted["isSupport"]) == {"FrameTVSupport": "true"}
    assert diagnose.redact({"id": "", "token": None, "n": 5}) == {"id": "", "token": None, "n": 5}
    # an art API version looks like an ip address
    assert diagnose.redact({"version": "4.3.4.0", "message": "from 10.0.0.5 and 172.20.1.2"}) == {
        "version": "4.3.4.0",
        "message": "from <ip> and <ip>",
    }


@pytest.mark.asyncio
async def test_run_diagnostics_2018() -> None:
    tv = art_tv(REST_2018, {k: [v] for k, v in REPLIES_2018.items()})
    report = await diagnose.run_diagnostics(tv, timeout=1)
    steps = report["steps"]

    assert report["model_year"] == 18
    assert report["art_api_version"] == "1.07"
    assert steps["legacy_art_api"]["result"] is True
    assert steps["supported"]["result"] is True
    assert steps["get_power_state"]["result"] == "art"
    assert steps["get_matte_list"]["result"] == [[{"matte_type": "none"}, {"matte_type": "modern"}], [{"color": "polar"}]]
    assert steps["available"]["result"]["categories"] == {"MY-C0002": 1}
    assert steps["get_thumbnail"]["result"] == {"bytes": len(JPEG), "jpeg": True, "png": False}
    # a check that fails is recorded, the rest still run
    assert not steps["get_slideshow_status"]["ok"]
    assert "c55ae696" not in json.dumps(report) and "192.168.1.166" not in json.dumps(report)
    json.dumps(report)  # the report must be saveable


@pytest.mark.asyncio
async def test_recorder_keeps_raw_replies() -> None:
    tv = art_tv(REST_2018, {k: [v] for k, v in REPLIES_2018.items()})
    recorder = diagnose._Recorder(tv)
    assert await tv.get_api_version() == "1.07"
    with pytest.raises(AssertionError):
        await tv.get_slideshow_status()
    assert [entry["request"]["request"] for entry in recorder.raw] == ["api_version", "get_api_version", "get_slideshow_status"]
    assert "ResponseError" in recorder.raw[0]["error"]
    assert recorder.raw[1]["response"] == {"version": "1.07"}
    assert recorder.raw[2]["response"] is None


def replies_from_report(report: Dict[str, Any]) -> Dict[str, List[Any]]:
    replies: Dict[str, List[Any]] = defaultdict(list)
    for entry in report["raw"]:
        response = entry.get("response")
        if "error" in entry:
            response = exceptions.ResponseError(entry["error"])
        elif isinstance(response, dict) and isinstance(response.get("binary"), str):
            response = dict(response, binary=JPEG)
        replies[entry["request"]["request"]].append(response)
    return replies


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DEVICES, ids=[p.stem for p in DEVICES])
async def test_replay_device_report(path: Path) -> None:
    """every check that worked on the real TV still works with its recorded replies"""
    recorded = json.loads(path.read_text())
    tv = art_tv(recorded["rest"], replies_from_report(recorded))
    with patch("samsungframetv.async_art.asyncio.open_connection", side_effect=OSError("no thumbnail server in tests")):
        report = await diagnose.run_diagnostics(tv, timeout=5)
    assert report["art_api_version"] == recorded["art_api_version"]
    for name, step in recorded["steps"].items():
        if name in ("get_thumbnail", "ipcontrol_tv_states") or name not in report["steps"]:
            continue
        assert report["steps"][name]["ok"] == step["ok"], name
        if step["ok"] and name not in ("available",):
            assert report["steps"][name]["result"] == step["result"], name


@pytest.mark.asyncio
async def test_thumbnail_falls_back_to_thumbnail_list() -> None:
    """a 2022 Frame (art API 4.3.4.0) never answers get_thumbnail"""
    tv = art_tv(REST_2018, {"get_thumbnail": [None]})
    tv._api_version = "4.3.4.0"
    tv.get_thumbnail_list = AsyncMock(return_value={"MY_F0001.jpg": JPEG})  # type: ignore[method-assign]
    assert await tv.get_thumbnail("MY_F0001") == JPEG
    assert await tv.get_thumbnail(["MY_F0001"], as_dict=True) == {"MY_F0001.jpg": JPEG}
    assert tv._thumbnail_list_only
    assert tv.get_thumbnail_list.await_count == 2


@pytest.mark.asyncio
async def test_raw_content_list_is_shortened() -> None:
    items = [{"content_id": "MY_F{:04}".format(n), "category_id": "MY-C0002"} for n in range(20)]
    tv = art_tv(REST_2018, {"get_content_list": [{"content_list": json.dumps(items)}]})
    recorder = diagnose._Recorder(tv)
    assert len(await tv.available()) == 20
    response = recorder.raw[0]["response"]
    assert response["content_list_count"] == 20
    assert len(json.loads(response["content_list"])) == diagnose.RAW_CONTENT_LIST_ITEMS
