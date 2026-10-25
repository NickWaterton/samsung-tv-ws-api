# Store the version here so:
# 1) we don't load dependencies by storing it in __init__.py
# 2) we can import it in setup.py for the same reason
# 3) we can import it into your module module

# version 3.0.0 N Waterton Dec 2025 - added version number.
# version 3.0.1 N Waterton 16th Jan 2025 added async_art_remove_mats.py example, fixed art_remove_mats.py
# version 3.0.2 N Waterton 11th March 2025 - changed image compare function in async_update_from_directory.py, updated README and setup.py
# version 3.0.3 N Waterton 24th March 2025 added asyncio.Lock to '_get_device_info' in async_art.py updated web_interface to fully async.
# version 3.0.4 N Waterton 8th December 2025 added set_brightness_sensor_setting(), set_motion_timer(), set_motion_sensitivity() and is_true(), cleaned up set_artmode() input
# version 3.0.5 N Waterton 8th December 2025 rewrote async_art_remove_mats.py to support portait mat, fixed depreciation warnings, reworked upload logic, and added URL support (for upload)

# version 4.0.0 N Waterton October 2026 - renamed to samsungframetv (samsungtvws clashed with upstream on PyPI), IP Control and
#               get_power_state(), async timeouts, reconnect and art service back off, 2017/2018 Frame support, Python 3.10+

# version 4.1.0 (in development) diagnose tool, get_thumbnail() falls back to get_thumbnail_list() on 2022+ Frames

__version__ = '4.1.0.dev2'