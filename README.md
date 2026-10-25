<p align="center">
    <img src="https://user-images.githubusercontent.com/5860071/47255992-611d9b00-d481-11e8-965d-d9816f254be2.png" width="300px" border="0" />
    <br/>
    <a href="https://pypi.org/project/samsungframetv/">
        <img src="https://img.shields.io/pypi/v/samsungframetv.svg?style=flat-square&label=PyPI" alt="Version">
    </a>
    Samsung Smart TV WS API wrapper
</p>

This project is a library for remote controlling Samsung televisions via a TCP/IP connection.

It currently supports modern (post-2016) TVs with Ethernet or Wi-Fi connectivity. They should be all models with TizenOs.

This fork is based on https://github.com/xchwarze/samsung-tv-ws-api work

Originally based on https://github.com/marysieek/samsung-tv-api work

## Install

This fork is intended to provide extended support for Frame TV's. From version 4.0.0 it is published on PyPI as `samsungframetv` (the `samsungtvws` name belongs to the original project):

```bash
pip install samsungframetv
```

With the optional extras:

```bash
pip install "samsungframetv[async,encrypted]"
```

Or install the latest code directly from git:

```bash
pip install git+https://github.com/NickWaterton/samsung-tv-ws-api.git
```

Or from a clone, in developer mode:

```bash
git clone https://github.com/NickWaterton/samsung-tv-ws-api.git
cd samsung-tv-ws-api
pip install --editable .
```

### Upgrading from samsungtvws

The package was renamed from `samsungtvws` to `samsungframetv` in version 4.0.0. The API is unchanged, so new code only needs the new import:

```python
from samsungframetv import SamsungTVWS
from samsungframetv.async_art import SamsungTVAsyncArt
```

If you have existing code (or an app you can't change) that does `import samsungtvws`, install the compatibility package as well. It maps every `samsungtvws` import onto `samsungframetv`:

```bash
pip uninstall samsungtvws
pip install samsungframetv-compat
```

Uninstall `samsungtvws` first, as both packages provide a `samsungtvws` module. The compatibility package will be kept for at least the 4.x releases.

### Install Original

The original project, which this fork is based on, is available as:

```bash
pip install "samsungtvws[async,encrypted]"
```

### Extras

`async` is required if you wish to use asynchronous I/O for all communications with the TV (`SamsungTVAsyncRest` and `SamsungTVWSAsyncRemote`)
`encrypted` is required if you wish to communicate with a TV which only support the v1 API (some J and K models) for sending commands (`SamsungTVEncryptedWSAsyncRemote` and `SamsungTVEncryptedWSAsyncAuthenticator`).

## Usage

### Basic

```python
import sys
import os
import logging
import wakeonlan

sys.path.append('../')

from samsungframetv import SamsungTVWS

# Increase debug level
logging.basicConfig(level=logging.INFO)

# Normal constructor
tv = SamsungTVWS('192.168.xxx.xxx')

# Autosave token to file
token_file = os.path.dirname(os.path.realpath(__file__)) + '/tv-token.txt'
tv = SamsungTVWS(host='192.168.xxx.xxx', port=8002, token_file=token_file)

# Toggle power
tv.shortcuts().power()

# Power On
wakeonlan.send_magic_packet('CC:6E:A4:xx:xx:xx')

# Open web in browser
tv.open_browser('https://duckduckgo.com/')

# View installed apps
apps = tv.app_list()
logging.info(apps)

# Open app (Spotify)
tv.run_app('3201606009684')

# Get app status (Spotify)
app = tv.rest_app_status('3201606009684')
logging.info(app)

# Open app (Spotify)
app = tv.rest_app_run('3201606009684')
logging.info(app)

# Close app (Spotify)
app = tv.rest_app_close('3201606009684')
logging.info(app)

# Install from official store (Spotify)
app = tv.rest_app_install('3201606009684')
logging.info(app)

# Get device info (device name, model, supported features..)
info = tv.rest_device_info()
logging.info(info)

```

### Art Mode

Art mode has been updated to support all Frame TV's including 2021/22/23 and 2024 models.
**NOTE:** The async library is more reliable/robust than the regular library, so I reccomend you use that if possible. See the `async_art` examples.

Older Frames (2017 and 2018, art API below 2, eg `0.97` or `1.07`) handle uploads and thumbnails differently. `upload()` sends the image in one binary websocket message, as the SmartThings app does, and `get_thumbnail()` reads thumbnails that arrive inline. This is chosen automatically from `get_api_version()`. `get_matte_list()` also accepts the 2018 reply format (`matte_types`). These paths are based on reports from 2017 and 2018 owners and have not been tested here, so please open an issue with debug logs if they don't work on your TV.

If the TV rejects the connection with `ms.channel.timeOut`, the library raises `ChannelTimeoutError` (a `ConnectionFailure`). This usually means the token is missing, or belongs to another TV, so use a separate token file for each TV.

If you use and like this library for art mode, [Buy me a coffee](https://paypal.me/NWaterton).

TVs that support art mode (such as The Frame) can be controlled as follows:

```python
import sys
import logging

sys.path.append('../')

from samsungframetv import SamsungTVWS

# Increase debug level
logging.basicConfig(level=logging.INFO)

# Normal constructor
tv = SamsungTVWS('192.168.xxx.xxx')

# Is art mode supported?
info = tv.art().supported()
logging.info(info)

# List the art available on the device
info = tv.art().available()
logging.info(info)

# Retrieve information about the currently selected art
info = tv.art().get_current()
logging.info(info)

# Retrieve a thumbnail for a specific piece of art. Returns a JPEG.
thumbnail = tv.art().get_thumbnail('SAM-F0206')

# Set a piece of art
tv.art().select_image('SAM-F0206')

# Set a piece of art, but don't immediately show it if not in art mode
tv.art().select_image('SAM-F0201', show=False)

# Determine whether the TV is currently in art mode
info = tv.art().get_artmode()
logging.info(info)

# Switch art mode on or off
tv.art().set_artmode(True)
tv.art().set_artmode(False)

# Upload a picture
file = open('test.png', 'rb')
data = file.read()
tv.art().upload(data)

# If uploading a JPEG
tv.art().upload(data, file_type='JPEG')

# To set the matte to modern and apricot color
tv.art().upload(data, matte='modern_apricot')

# Delete an uploaded item
tv.art().delete('MY-F0020')

# Delete multiple uploaded items
tv.art().delete_list(['MY-F0020', 'MY-F0021'])

# List available photo filters
info = tv.art().get_photo_filter_list()
logging.info(info)

# Apply a filter to a specific piece of art
tv.art().set_photo_filter('SAM-F0206', 'ink')
```

### Power State and IP Control

`get_power_state()` returns one of `off`, `standby`, `tv`, `art`, or `on` (on, but art mode could not be determined). It combines the REST api, the art channel and, if you have paired it, Samsung IP Control.

IP Control (JSON-RPC on port 1516, 1515 on pre 2020 TV's) is the most reliable source of power state, and can also switch inputs and power the TV on and off without SmartThings. Enable it on the TV in *Settings > General > Network > Expert Settings > IP Remote*. Pairing only works when the TV is on and **not** in art mode, press Allow on the TV.

```python
from samsungframetv import SamsungTVWS
from samsungframetv.ipcontrol import SamsungTVIPControl

ipc = SamsungTVIPControl('192.168.xxx.xxx', token_file='ipcontrol_token.txt')
if not ipc.token:
    ipc.pair()                      # press Allow on the TV

tv = SamsungTVWS('192.168.xxx.xxx')
print(tv.art().get_power_state(ipc))   # eg 'art'
ipc.set_input_source('HDMI2')       # only works when watching TV, not in art mode
```

For asyncio use `SamsungTVAsyncIPControl` with `SamsungTVAsyncArt.get_power_state()`.

Notes from testing 2021 and 2022 Frames: in standby the 2022 still answers IP Control with `powerOff` but keeps reporting art mode on, the 2021 refuses the connection. 2025 Frames report REST `PowerState: standby` while in art mode, `in_artmode()` and `get_power_state()` allow for this.

### Pairing and connection problems

On port 8002 the TV gives each app a token the first time it connects, after you press Allow on the TV. The token is saved in `token_file`, so use a separate token file for each TV.

Some models (for example 2017 and 2024 Frames) don't show the Allow prompt for the art channel. `pair()` opens the remote control channel instead, which shows it on every model. It works on any of the connection classes:

```python
tv = SamsungTVWS('192.168.xxx.xxx', port=8002, token_file='token_tv1.txt')
token = tv.art().pair()          # press Allow on the TV

art = SamsungTVAsyncArt('192.168.xxx.xxx', port=8002, token_file='token_tv1.txt')
token = await art.pair(timeout=120)
```

If there is already a token, `pair()` checks it first and only asks again if the TV no longer accepts it. TV's can forget tokens after a long power off (issue #30). `pair(force=True)` always asks again.

- `ChannelTimeoutError` (`ms.channel.timeOut`): the TV didn't accept the token. It is missing, belongs to another TV, or Allow wasn't pressed in time. Run `pair()`.
- `UnauthorizedError` (`ms.channel.unauthorized`): the TV refused the app. Check *Settings > General > External Device Manager > Device Connection Manager*: set *Access Notification* to ask, and remove the app from the *Device List* if it was denied, then run `pair(force=True)`.
- Holding `KEY_POWER` doesn't power off 2025 Frames, they treat it as a short press (issue #29). Use IP Control `power_off()` instead.

### Async

Examples are available in the examples folder: `async_remote.py`, `async_rest.py`
Frame TV art examples are available in `async_art.py`, `async_art_slideshow_anything.py`, `async_art_update_from_directry.py`

#### Timeouts and recovery (async)

- Opening a connection gives up after `timeout` seconds (10 if not set), or 60 seconds when there is no token yet, so there is time to press Allow on the TV. A TV that accepts the connection and never answers raises `ConnectionFailure` instead of hanging.
- If the TV drops the connection (common soon after standby), the next command reconnects once, resumes listening with the same callback, and resends.
- An exception in your callback is logged, and the listener keeps running.
- Art requests wait up to 5 seconds for the TV to answer (`async_art.ART_RESPONSE_TIMEOUT`), and are sent without the remote's key press delay. A TV with another client on the art channel, such as openHAB or Home Assistant, can take more than 2 seconds to answer content requests.
- The art service can stop answering while the TV is otherwise fine (seen on 2022 Frames). After 3 art requests in a row get no answer, `SamsungTVAsyncArt` closes the connection, and for the next 30 seconds art requests raise `ArtServiceUnavailableError` straight away instead of waiting. If the TV still doesn't answer after that, the pause doubles each time, up to 4 minutes. `ArtServiceUnavailableError` is a subclass of `AssertionError`, so existing `except AssertionError` handlers still catch it.

```python
from samsungframetv.exceptions import ArtServiceUnavailableError

if not tv.art_service_available:
    print('art service is not answering')
tv.reset_art_service()   # eg when you know the TV has just been turned on
```

### Encrypted API

Examples are available in the examples folder: `encrypted_authenticator.py`, `encrypted_remote.py`

## Supported TVs

List of support TV models. https://developer.samsung.com/smarttv/develop/extension-libraries/smart-view-sdk/supported-device/supported-tvs.html

```
2024 : LS03D
2022/23 : LS03B/C
2021 : LS03A
2017 : M5500 and above
2016 : K4300, K5300 and above
2015 : J5500 and above (except J6203)
2014 : H4500, H5500 and above (except H6003/H6103/H6153/H6201/H6203)
Supported TV models may vary by region.
```

For complete list https://developer.samsung.com/smarttv/develop/specifications/tv-model-groups.html

### Art mode by model

Samsung changes the art API between model years and firmware, and I only own a 2021 and a 2022 Frame. This is what is known:

| Model | Status |
|---|---|
| 2017 Frame (LS003) | Art API 1.07. Upload and thumbnails use the older single binary message, chosen automatically. Based on owner reports, not tested here. |
| 2018 Frame (LS03N) | As 2017. The matte list comes in an older format, which is handled (issue #35). Not tested here. |
| 2019 and 2020 Frames | No reports yet. |
| 2021 Frame (LS03A) | Art API 2.03. Tested here. Has no `get_artmode_settings` (error -9), which arrived in art API 4.3.4.0. `get_brightness()` and `get_color_temperature()` work on both. |
| 2022 Frame (LS03B) | Art API 4.3.4.0. Tested here. `get_thumbnail` was replaced by `get_thumbnail_list` in this version, which `get_thumbnail()` uses automatically. The art service can stop answering, see "Timeouts and recovery". |
| 2023 and 2024 Frames (LS03C, LS03D) | Reported working by users. 2024 models pair on the remote channel first. |
| 2025 Frame (LS03F) | Reports REST `PowerState: standby` in art mode, which `in_artmode()` and `get_power_state()` allow for (issue #33). Long press of `KEY_POWER` does not power off (issue #29), use IP Control `power_off()`. Not tested here. |
| Other TVs with art mode (eg S95H) | Report `FrameTVSupport: false`, so `supported()` asks the art channel instead (issue #34). Not tested here. |

The slideshow request was called `auto_rotation` up to art API 2.03 and renamed `slideshow` in 4.3.4.0. `get_slideshow_status()` and `get_auto_rotation_status()` (and the `set_` calls) are the same, and send whichever name the TV answers.

### Help with a model I don't own

If your TV is not listed as tested, or something doesn't work, please run the diagnose tool with the TV on and send the report in an issue:

```bash
python -m samsungframetv.diagnose 192.168.xxx.xxx --ipcontrol
```

It only reads from the TV (no uploads, deletes or setting changes), prints a summary, and saves `diagnose_<model>.json`. MAC and ip addresses, device ids and tokens are removed from the report, but have a look before you post it. Leave out `--ipcontrol` if you have not enabled IP Remote. Use `-t` to give your usual token file, otherwise you may have to press Allow on the TV again.

Reports are saved in `tests/fixtures/devices/`, and the tests replay them, so a change that would break your model is caught before release.

## License

LGPL-3.0
