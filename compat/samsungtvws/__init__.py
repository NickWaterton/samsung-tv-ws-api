"""
samsungtvws compatibility shim for samsungframetv

This fork used to install as `samsungtvws`, which clashed with the upstream package of the same
name. It is now `samsungframetv`. This shim lets existing code keep using `import samsungtvws`:
every `samsungtvws.<module>` is the matching `samsungframetv.<module>`, the same module objects
and classes, so isinstance checks and patches work under either name.

New code should import samsungframetv directly.

SPDX-License-Identifier: LGPL-3.0
"""

import importlib
import importlib.abc
import importlib.util
import sys
from types import ModuleType
from typing import Optional, Sequence

import samsungframetv
from samsungframetv import *  # noqa: F401,F403
from samsungframetv import SamsungTVShortcuts, SamsungTVWS, __version__  # noqa: F401

_OLD = __name__
_NEW = "samsungframetv"


class _AliasImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """import samsungtvws.x as samsungframetv.x, only when it is first used"""

    def find_spec(self, fullname: str, path: Optional[Sequence[str]], target: Optional[ModuleType] = None):  # type: ignore[no-untyped-def]
        if fullname.startswith(_OLD + "."):
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        return importlib.import_module(_NEW + spec.name[len(_OLD):])

    def exec_module(self, module: ModuleType) -> None:
        pass    # already imported as samsungframetv.x


if not any(isinstance(finder, _AliasImporter) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasImporter())

# submodules samsungframetv has already imported are the same objects under the old name
for _name, _module in list(sys.modules.items()):
    if _name.startswith(_NEW + "."):
        sys.modules.setdefault(_OLD + _name[len(_NEW):], _module)
