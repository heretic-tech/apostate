"""Widevine DRM provisioning.

Chromium fetches the Widevine CDM from Google at runtime into the profile
directory. Apostate cannot ship it -- ``third_party/widevine/LICENSE`` forbids
redistribution -- and the runtime fetch does not happen for an ephemeral
profile, which is what ``launch()`` uses by default. So on a default launch
``navigator.requestMediaKeySystemAccess('com.widevine.alpha', ...)`` rejects
with ``NotSupportedError``, and a site can read that in one call.

A CDM placed in the browser's preinstalled-component directory registers at
startup for every profile, including a fresh ephemeral one, with no network
access and without writing anything into the profile. That directory is the one
the shipped artifact already loads ``MEIPreload`` and
``PrivacySandboxAttestationsPreloaded`` from, and it is where Google Chrome
keeps its own copy, in the same layout and with no version subdirectory.

Nothing is redistributed. The CDM travels from Google to the operator's machine
exactly as it does for Chrome; this module only copies a file that is already
on that machine into the browser that needs it. It is therefore deliberately
**not** part of ``launch()``: on a machine with no CDM anywhere there is
nothing to copy, and a silent no-op inside ``launch()`` would leave a caller
believing DRM works when it does not. It is an explicit, one-time action.

Measured on macos-arm64 at Chromium 152.0.7977.83, with
``--host-resolver-rules=EXCLUDE 127.0.0.1,MAP * 0.0.0.0`` so no route to Google
existed: before, every robustness level rejected ``NotSupportedError``; after,
the empty, ``SW_SECURE_CRYPTO`` and ``SW_SECURE_DECODE`` levels resolved and
``createMediaKeys()`` succeeded, all three ``HW_SECURE_*`` levels rejected,
persistent-license rejected, and the profile directory stayed empty.

Linux and Windows are NOT verified. Their layouts are implemented from the
documented component paths and are reported as unverified by
:func:`provision`; see ``platform_is_verified``.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Iterator

from .config import CHROMIUM_VERSION
from .errors import ApostateError, BinaryError

#: The component directory name, both in a profile and in the browser.
COMPONENT = "WidevineCdm"

#: Per-target: the library subdirectory inside ``_platform_specific``, the
#: library's file name, and the install-relative directory that the browser
#: searches for preinstalled components.
#:
#: macOS is the only entry measured working. On Linux the preinstalled root is
#: the directory holding the executable, and ``cdm_registration.cc`` also has a
#: branch that reads a hint file from the profile instead; on Windows it is
#: beside the executable. Both are implemented from those paths and unverified.
_LAYOUT: dict[str, tuple[str, str, str]] = {
    "macos-arm64": ("mac_arm64", "libwidevinecdm.dylib",
                    "Chromium.app/Contents/Frameworks/Chromium Framework.framework/"
                    "Versions/{chromium_version}/Libraries"),
    "linux-x64": ("linux_x64", "libwidevinecdm.so", ""),
    "linux-arm64": ("linux_arm64", "libwidevinecdm.so", ""),
    "windows-x64": ("win_x64", "widevinecdm.dll", ""),
}

#: Only this target's provisioning has been exercised end to end.
VERIFIED_TARGETS = frozenset({"macos-arm64"})

#: Files copied beside the platform directory. ``_metadata`` is deliberately
#: not copied: Google Chrome's own bundled copy does not carry it.
_TOP_LEVEL = ("manifest.json", "LICENSE")


class WidevineError(ApostateError):
    """Raised when a CDM cannot be found, read, or installed."""


def platform_is_verified(target: str) -> bool:
    return target in VERIFIED_TARGETS


def _search_roots() -> Iterator[Path]:
    """Directories on this machine that a Chromium profile may have fetched into."""
    home = Path.home()
    if platform.system() == "Darwin":
        base = home / "Library" / "Application Support"
        yield from (base.iterdir() if base.is_dir() else ())
        yield Path("/Applications")
    elif os.name == "nt":
        for key in ("LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            value = os.environ.get(key)
            if value:
                yield Path(value)
    else:
        yield home / ".config"
        yield Path("/opt")
        yield Path("/usr/lib")


def _candidate_dirs(root: Path) -> Iterator[Path]:
    """Yield every ``WidevineCdm`` directory at a plausible depth under *root*."""
    if not root.is_dir():
        return
    direct = root / COMPONENT
    if direct.is_dir():
        yield direct
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        nested = entry / COMPONENT
        if nested.is_dir():
            yield nested
        # macOS application bundles keep it under the framework.
        if entry.name.endswith(".app"):
            for found in entry.glob("Contents/Frameworks/*.framework/Libraries/" + COMPONENT):
                if found.is_dir():
                    yield found


def _read_version(directory: Path) -> str | None:
    try:
        value = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = value.get("version") if isinstance(value, dict) else None
    return version if isinstance(version, str) and version else None


def _payload(directory: Path, subdir: str, library: str) -> Path | None:
    """Return *directory* if it holds this platform's library, else None.

    Two layouts exist. The component updater writes a version directory
    (``WidevineCdm/4.10.3050.0/...``); a browser bundle has none
    (``WidevineCdm/_platform_specific/...``). Both are accepted.
    """
    if (directory / "_platform_specific" / subdir / library).is_file():
        return directory
    for child in sorted(directory.iterdir() if directory.is_dir() else (), reverse=True):
        if child.is_dir() and (child / "_platform_specific" / subdir / library).is_file():
            return child
    return None


def discover(target: str) -> list[dict[str, Any]]:
    """Find every CDM already present on this machine, newest version first.

    Only this machine is searched. Nothing is downloaded.
    """
    subdir, library, _ = _resolve_layout(target)
    found: dict[Path, dict[str, Any]] = {}
    for root in _search_roots():
        for directory in _candidate_dirs(root):
            payload = _payload(directory, subdir, library)
            if payload is None:
                continue
            resolved = payload.resolve()
            if resolved in found:
                continue
            size = (payload / "_platform_specific" / subdir / library).stat().st_size
            found[resolved] = {"path": str(payload), "version": _read_version(payload),
                               "bytes": size}
    return sorted(found.values(), key=lambda item: (item["version"] or ""), reverse=True)


def _resolve_layout(target: str) -> tuple[str, str, str]:
    try:
        return _LAYOUT[target]
    except KeyError:
        raise WidevineError(f"no Widevine layout is known for {target}") from None


def _copy(source: Path, destination: Path, subdir: str, library: str) -> None:
    library_source = source / "_platform_specific" / subdir / library
    if not library_source.is_file():
        raise WidevineError(f"{source} does not contain _platform_specific/{subdir}/{library}")
    staged = destination.with_name(destination.name + ".part")
    shutil.rmtree(staged, ignore_errors=True)
    (staged / "_platform_specific").mkdir(parents=True, exist_ok=True)
    for name in _TOP_LEVEL:
        candidate = source / name
        if candidate.is_file():
            shutil.copyfile(candidate, staged / name)
    if not (staged / "manifest.json").is_file():
        shutil.rmtree(staged, ignore_errors=True)
        raise WidevineError(f"{source} has no manifest.json; it is not a CDM directory")
    shutil.copytree(source / "_platform_specific" / subdir,
                    staged / "_platform_specific" / subdir)
    if destination.exists():
        retired = destination.with_name(destination.name + ".stale")
        shutil.rmtree(retired, ignore_errors=True)
        os.replace(destination, retired)
        shutil.rmtree(retired, ignore_errors=True)
    os.replace(staged, destination)


def store_path(cache_dir: str | Path) -> Path:
    """Where a provisioned CDM is kept.

    Outside any per-version install directory on purpose: a reinstall or a
    Chromium upgrade replaces the install tree, and DRM must not silently
    disappear when it does. The CDM is versioned independently of Chromium.
    """
    return Path(cache_dir).expanduser() / COMPONENT.lower()


def apply_to_install(install: Path, target: str, *, cache_dir: str | Path,
                     chromium_version: str) -> Path | None:
    """Copy a previously provisioned CDM into a freshly extracted install.

    Returns the installed component directory, or None when nothing has been
    provisioned. Called by the binary manager after every extraction so that
    ``install --force`` and a Chromium upgrade keep DRM working.
    """
    store = store_path(cache_dir)
    subdir, library, relative = _resolve_layout(target)
    if not (store / "_platform_specific" / subdir / library).is_file():
        return None
    destination = install / relative.format(chromium_version=chromium_version) / COMPONENT
    _copy(store, destination, subdir, library)
    return destination


def provision(*, target: str | None = None, source: str | Path | None = None,
              cache_dir: str | Path | None = None,
              chromium_version: str | None = None,
              install: str | Path | None = None) -> dict[str, Any]:
    """Install a CDM found on this machine into the Apostate browser.

    *source* is a ``WidevineCdm`` directory, in either the component-updater or
    the browser-bundle layout. Omitted, this machine is searched and the newest
    is used.
    """
    from .binary import BinaryManager, target_platform

    target_name = target_platform(target)
    subdir, library, relative = _resolve_layout(target_name)
    manager = BinaryManager(cache_dir=cache_dir)
    cache_root = manager.cache_dir

    if source is None:
        candidates = discover(target_name)
        if not candidates:
            raise WidevineError(
                "no Widevine CDM was found on this machine. Chromium fetches it from "
                "Google into the profile directory, so run a browser with a persistent "
                "--user-data-dir and play any DRM video once, then re-run this; or pass "
                "the directory explicitly with source=."
            )
        chosen = Path(candidates[0]["path"])
    else:
        chosen = Path(source).expanduser()
        if chosen.name != COMPONENT and (chosen / COMPONENT).is_dir():
            chosen = chosen / COMPONENT
        payload = _payload(chosen, subdir, library)
        if payload is None:
            raise WidevineError(
                f"{chosen} does not contain _platform_specific/{subdir}/{library}"
            )
        chosen = payload

    store = store_path(cache_root)
    store.parent.mkdir(parents=True, exist_ok=True)
    _copy(chosen, store, subdir, library)

    result: dict[str, Any] = {
        "platform": target_name,
        "platform_verified": platform_is_verified(target_name),
        "source": str(chosen),
        "version": _read_version(store),
        "store": str(store),
        "installed": None,
        # A CDM being present is not a CDM being registered. `launch()` strips
        # the switch from the driver's defaults, but anyone driving the binary
        # directly must do it too.
        "requires": (
            "the browser must not run with --disable-component-update; it blocks "
            "component registration and a provisioned CDM is silently inert. "
            "launch() removes it from the driver's defaults automatically."
        ),
    }

    if install is not None:
        if chromium_version is None:
            raise WidevineError("chromium_version is required when install is given")
        install_root = Path(install).expanduser()
        version = chromium_version
    else:
        # Acquire the browser if it is not already installed: there is nothing
        # to provision into otherwise, and the caller asked for a working
        # browser rather than a populated cache directory.
        try:
            manager.ensure(target=target_name)
        except BinaryError as exc:
            result["reason"] = str(exc)
            return result
        # The cache is keyed by Chromium version, and this package speaks to
        # exactly one: a manifest naming another is refused when it is read.
        # Asking a manifest for the number would only be a second route to
        # the same answer, and -- when nothing is published and the install
        # came from the release's own manifest -- a way to fail after the
        # install already succeeded.
        version = chromium_version or CHROMIUM_VERSION
        install_root = manager._paths(target_name, version)[1]

    destination = install_root / relative.format(chromium_version=version) / COMPONENT
    _copy(store, destination, subdir, library)
    result["installed"] = str(destination)
    result["chromium_version"] = version
    return result


#: Names the package root re-exports, spelled for a caller who did not import
#: this module directly.
discover_widevine = discover
provision_widevine = provision

__all__ = [
    "COMPONENT", "VERIFIED_TARGETS", "WidevineError", "apply_to_install", "discover",
    "discover_widevine", "platform_is_verified", "provision", "provision_widevine",
    "store_path",
]
