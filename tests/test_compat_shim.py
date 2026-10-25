"""The samsungtvws shim (compat/) maps old imports onto samsungframetv."""

import importlib
import sys
from pathlib import Path

import pytest

COMPAT = str(Path(__file__).resolve().parent.parent / "compat")


@pytest.fixture
def shim():
    sys.path.insert(0, COMPAT)
    try:
        yield importlib.import_module("samsungtvws")
    finally:
        sys.path.remove(COMPAT)


def test_old_imports_are_the_same_objects(shim) -> None:
    import samsungframetv
    from samsungframetv import async_art, exceptions

    assert shim.SamsungTVWS is samsungframetv.SamsungTVWS
    assert shim.__version__ == samsungframetv.__version__

    from samsungtvws.async_art import SamsungTVAsyncArt
    import samsungtvws.exceptions as old_exceptions

    assert SamsungTVAsyncArt is async_art.SamsungTVAsyncArt
    assert old_exceptions is exceptions
    assert sys.modules["samsungtvws.async_art"] is async_art


def test_submodule_imported_first_under_old_name(shim) -> None:
    old = importlib.import_module("samsungtvws.ipcontrol")
    new = importlib.import_module("samsungframetv.ipcontrol")
    assert old is new
    assert new.__name__ == "samsungframetv.ipcontrol"


def test_unknown_submodule_fails(shim) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("samsungtvws.does_not_exist")
