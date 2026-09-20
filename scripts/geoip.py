#!/usr/bin/env python3
"""Re-export of ``apostate._prelaunch_geoip``, which is where this now lives.

The implementation moved into the Python package. It had to: this file is not
in the wheel, so ``geoip=True`` worked from a checkout and failed from an
installed package with "requires the repository GeoIP helper", naming a helper
the user had no way to obtain. There is now one implementation, it ships, and
this module is the checkout-relative name for it so ``scripts/test_geoip.py``,
``scripts/profile_resolver.py``'s documentation and the capture tooling keep
working unchanged.

Everything the module defines is re-exported, private names included, because
the callers of a repository helper reach for its internals and moving the file
should not break that.
"""

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "python"
if (_PACKAGE_ROOT / "apostate" / "_prelaunch_geoip.py").is_file():
    # Prepended, not appended: this is the checkout's helper under its
    # checkout-relative name, so the checkout's copy is the one it must mean.
    # An apostate installed in the environment -- very possibly an older one
    # that predates this module -- must not answer for it.
    if str(_PACKAGE_ROOT) in sys.path:
        sys.path.remove(str(_PACKAGE_ROOT))
    sys.path.insert(0, str(_PACKAGE_ROOT))

from apostate import _prelaunch_geoip as _module  # noqa: E402

globals().update({
    name: value for name, value in vars(_module).items()
    if name not in {"__name__", "__doc__", "__file__", "__loader__", "__spec__",
                    "__package__", "__builtins__", "__path__", "__cached__"}
})

__all__ = list(_module.__all__)


if __name__ == "__main__":
    raise SystemExit("Import this module from a package adapter; it does not run a network lookup by itself.")
