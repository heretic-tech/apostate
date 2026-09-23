"""Widevine DRM for the Apostate browser.

Google's licence forbids shipping the Widevine CDM with the browser, so a
fresh install has none and ``navigator.requestMediaKeySystemAccess(
'com.widevine.alpha', ...)`` rejects with ``NotSupportedError``, which a real
Chrome never does. This module fixes that on the first launch.

The CDM is copied from a local Google Chrome, or, when there is none, fetched
from Google's component update service the way Chromium's own component
updater fetches it, and checked against the SHA-256 the service returns. It is
kept in the package cache directory, so this happens once per machine, and
copied into the browser's preinstalled-component directory, where it
registers at startup for every profile, the throwaway one ``launch()`` uses
included.

Linux also reads the CDM from a hint file inside the profile directory, so a
persistent profile gets that hint too.

If no CDM can be had, the launch goes ahead without one and prints one warning.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterator, NamedTuple

from .config import CHROMIUM_VERSION
from .errors import ApostateError

#: The component directory name, both in a profile and in the browser.
COMPONENT = "WidevineCdm"

#: The file a Linux profile names its CDM directory in.
HINT = "latest-component-updated-widevine-cdm"

#: Google's component update service and the Widevine component's id.
UPDATE_URL = "https://update.googleapis.com/service/update2/json"
APP_ID = "oimompecagnajdejgnnjijobebaeigek"


class _Platform(NamedTuple):
    subdir: str   # directory under _platform_specific
    library: str  # the CDM library's file name
    os: str       # how the component updater names the OS
    arch: str
    os_name: str


_LAYOUT: dict[str, _Platform] = {
    "macos-arm64": _Platform("mac_arm64", "libwidevinecdm.dylib", "mac", "arm64", "Mac OS X"),
    "linux-x64": _Platform("linux_x64", "libwidevinecdm.so", "linux", "x64", "Linux"),
    "linux-arm64": _Platform("linux_arm64", "libwidevinecdm.so", "linux", "arm64", "Linux"),
    "windows-x64": _Platform("win_x64", "widevinecdm.dll", "win", "x64", "Windows"),
}

#: Targets Widevine is known to work on. Windows has not been tried yet.
VERIFIED_TARGETS = frozenset({"macos-arm64", "linux-x64", "linux-arm64"})

#: Files copied beside the platform directory. ``_metadata`` is not copied:
#: Google Chrome's own bundled copy does not carry it.
_TOP_LEVEL = ("manifest.json", "LICENSE")


class WidevineError(ApostateError):
    """Raised when a CDM cannot be found, fetched, or installed."""


def platform_is_verified(target: str) -> bool:
    return target in VERIFIED_TARGETS


def _resolve_layout(target: str) -> _Platform:
    try:
        return _LAYOUT[target]
    except KeyError:
        raise WidevineError(f"no Widevine layout is known for {target}") from None


def _has_cdm(directory: Path, spec: _Platform) -> bool:
    return ((directory / "manifest.json").is_file()
            and (directory / "_platform_specific" / spec.subdir / spec.library).is_file())


def _search_roots() -> Iterator[Path]:
    """Google Chrome's install, then the profiles Chromium browsers fetch into."""
    home = Path.home()
    if platform.system() == "Darwin":
        yield Path("/Applications")
        base = home / "Library" / "Application Support"
        yield from (sorted(base.iterdir()) if base.is_dir() else ())
    elif os.name == "nt":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(key):
                yield Path(os.environ[key]) / "Google" / "Chrome" / "Application"
        if os.environ.get("LOCALAPPDATA"):
            local = Path(os.environ["LOCALAPPDATA"])
            yield local / "Google" / "Chrome" / "User Data"
            yield local / "Chromium" / "User Data"
    else:
        yield Path("/opt/google/chrome")
        yield home / ".config"


def _candidate_dirs(root: Path) -> Iterator[Path]:
    """Every ``WidevineCdm`` directory in *root* or one level below it."""
    if not root.is_dir():
        return
    if (root / COMPONENT).is_dir():
        yield root / COMPONENT
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        if (entry / COMPONENT).is_dir():
            yield entry / COMPONENT
        # macOS application bundles keep it inside the framework.
        if entry.name.endswith(".app"):
            yield from (found for found in
                        entry.glob("Contents/Frameworks/*.framework/Libraries/" + COMPONENT)
                        if found.is_dir())


def _read_version(directory: Path) -> str | None:
    try:
        value = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = value.get("version") if isinstance(value, dict) else None
    return version if isinstance(version, str) and version else None


def _version_key(version: str | None) -> tuple[int, ...]:
    return tuple(int(part) for part in (version or "").split(".") if part.isdigit())


def _payload(directory: Path, spec: _Platform) -> Path | None:
    """*directory* itself or its newest version directory, if either holds a CDM.

    The component updater writes a version directory
    (``WidevineCdm/4.10.3050.0/...``); a browser bundle has none.
    """
    if _has_cdm(directory, spec):
        return directory
    children = [child for child in (directory.iterdir() if directory.is_dir() else ())
                if child.is_dir() and _has_cdm(child, spec)]
    return max(children, key=lambda child: _version_key(child.name), default=None)


def discover(target: str) -> list[dict[str, Any]]:
    """Every CDM already on this machine, newest version first. Nothing is downloaded."""
    spec = _resolve_layout(target)
    found: dict[Path, dict[str, Any]] = {}
    for root in _search_roots():
        for directory in _candidate_dirs(root):
            payload = _payload(directory, spec)
            if payload is None or payload.resolve() in found:
                continue
            size = (payload / "_platform_specific" / spec.subdir / spec.library).stat().st_size
            found[payload.resolve()] = {"path": str(payload), "version": _read_version(payload),
                                        "bytes": size}
    return sorted(found.values(), key=lambda item: _version_key(item["version"]), reverse=True)


def _copy(source: Path, destination: Path, spec: _Platform) -> None:
    """Replace *destination* with the CDM in *source*, never half-written."""
    if not (source / "_platform_specific" / spec.subdir / spec.library).is_file():
        raise WidevineError(
            f"{source} does not contain _platform_specific/{spec.subdir}/{spec.library}")
    if not (source / "manifest.json").is_file():
        raise WidevineError(f"{source} has no manifest.json; it is not a CDM directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    staged.chmod(0o755)
    retired = staged.with_name(staged.name + "-old")
    try:
        for name in _TOP_LEVEL:
            if (source / name).is_file():
                shutil.copyfile(source / name, staged / name)
        shutil.copytree(source / "_platform_specific" / spec.subdir,
                        staged / "_platform_specific" / spec.subdir)
        if destination.exists():
            os.replace(destination, retired)
        os.replace(staged, destination)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)


def _fetch(request: str | urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download(target: str, destination: str | Path) -> str:
    """Fetch the CDM for *target* from Google into *destination*; return its URL.

    One update check against Google's component update service, as Chromium's
    component updater makes it, then the package it names, verified against
    the SHA-256 in the same reply.
    """
    spec = _resolve_layout(target)
    body = json.dumps({"request": {
        "protocol": "3.1", "acceptformat": "crx3", "ismachine": False,
        "@updater": "chromium", "@os": spec.os, "arch": spec.arch, "nacl_arch": spec.arch,
        "os": {"platform": spec.os_name, "arch": spec.arch},
        "prodversion": CHROMIUM_VERSION, "updaterversion": CHROMIUM_VERSION,
        "prodchannel": "stable", "updaterchannel": "stable",
        "requestid": f"{{{uuid.uuid4()}}}", "sessionid": f"{{{uuid.uuid4()}}}",
        "app": [{"appid": APP_ID, "version": "0.0.0.0", "enabled": True, "updatecheck": {}}],
    }}).encode("utf-8")
    reply = _fetch(urllib.request.Request(
        UPDATE_URL, data=body, headers={"Content-Type": "application/json"}), 30)
    text = reply.decode("utf-8", "replace")
    try:
        # The reply starts with an anti-XSSI prefix before the JSON.
        check = json.loads(text[text.index("{"):])["response"]["app"][0]["updatecheck"]
        package = check["manifest"]["packages"]["package"][0]
        name, digest = str(package["name"]), str(package["hash_sha256"]).lower()
        bases = [str(item["codebase"]) for item in check["urls"]["url"]]
    except (ValueError, KeyError, IndexError, TypeError):
        raise WidevineError("Google's update service returned no Widevine package for "
                            f"{target}") from None
    urls = [base + name for base in bases if base.startswith("https://")]
    if not urls:
        raise WidevineError("Google's update service returned no https download for Widevine")
    failure: Exception | None = None
    for url in urls:
        try:
            data = _fetch(url, 120)
        except OSError as exc:
            failure = exc
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            raise WidevineError(f"the Widevine download from {url} failed SHA-256 verification")
        _unpack_crx(data, Path(destination), spec)
        return url
    raise WidevineError(f"could not download Widevine from Google: {failure}")


def _unpack_crx(data: bytes, destination: Path, spec: _Platform) -> None:
    """Write the CDM files out of a CRX3 package: a header, then a zip."""
    if data[:4] != b"Cr24" or len(data) < 12:
        raise WidevineError("the Widevine download is not a CRX package")
    start = 12 + int.from_bytes(data[8:12], "little")
    prefix = f"_platform_specific/{spec.subdir}/"
    try:
        with zipfile.ZipFile(io.BytesIO(data[start:])) as archive:
            for member in archive.infolist():
                name = member.filename
                leaf = name[len(prefix):] if name.startswith(prefix) else None
                if member.is_dir() or not (name in _TOP_LEVEL
                                           or (leaf and "/" not in leaf and leaf not in (".", ".."))):
                    continue
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(member))
                if leaf == spec.library:
                    path.chmod(0o755)
    except zipfile.BadZipFile:
        raise WidevineError("the Widevine download is not a readable CRX package") from None
    if not _has_cdm(destination, spec):
        raise WidevineError("the Widevine download does not contain the CDM for "
                            f"_platform_specific/{spec.subdir}")


def store_path(cache_dir: str | Path) -> Path:
    """Where the CDM is kept: outside every install, so reinstalling keeps it."""
    return Path(cache_dir).expanduser() / COMPONENT.lower()


def _fill_store(store: Path, target: str, source: Path | None = None) -> str:
    """Put a CDM into *store*: *source*, else a local copy, else Google's. Returns the origin."""
    spec = _resolve_layout(target)
    if source is None:
        found = discover(target)
        source = Path(found[0]["path"]) if found else None
    store.parent.mkdir(parents=True, exist_ok=True)
    if source is not None:
        _copy(source, store, spec)
        return str(source)
    with tempfile.TemporaryDirectory(prefix=".widevine-download-", dir=store.parent) as scratch:
        url = download(target, scratch)
        _copy(Path(scratch), store, spec)
    return url


def _component_dir(executable: Path, target: str) -> Path | None:
    """The browser's preinstalled-component ``WidevineCdm`` directory.

    ``None`` unless *executable* sits in an Apostate payload, the tree
    scripts/package-artifact.sh stages with ``build/MANIFEST.lock`` and
    ``resources/profiles/`` beside the browser. Nothing else is written into.
    """
    executable = Path(executable).expanduser().absolute()
    if target == "macos-arm64":
        bundle = executable.parent.parent.parent
        if bundle.suffix != ".app":
            return None
        root = bundle.parent
        current = next(bundle.glob("Contents/Frameworks/*.framework/Versions/Current"), None)
        if current is None or not (current / "Libraries").is_dir():
            return None
        # The framework loads from Versions/<version>/, which Current names.
        version = os.readlink(current) if current.is_symlink() else current.name
        component = current.parent / version / "Libraries" / COMPONENT
    else:
        root = executable.parent
        component = root / COMPONENT
    if not ((root / "build" / "MANIFEST.lock").is_file()
            or (root / "resources" / "profiles" / "catalogue.json").is_file()):
        return None
    return component


def _run_once(executable: Path) -> None:
    """Run a macOS bundle once before anything is added to it.

    Gatekeeper checks a bundle's signature seal the first time it runs and
    refuses one that changed since it was signed. After one clean run it does
    not check again, so the CDM can go in.
    """
    try:
        subprocess.run([str(executable), "--version"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                       check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _write_hint(user_data_dir: Path, component: Path, spec: _Platform) -> None:
    """Point a Linux profile at the CDM, unless it already names a working one."""
    hint = user_data_dir / COMPONENT / HINT
    try:
        current = json.loads(hint.read_text(encoding="utf-8")).get("Path")
    except (OSError, ValueError, AttributeError):
        current = None
    if isinstance(current, str) and _has_cdm(Path(current), spec):
        return
    hint.parent.mkdir(parents=True, exist_ok=True)
    hint.write_text(json.dumps({"Path": str(component)}), encoding="utf-8")


def _install(executable: Path, target: str, store: Path, spec: _Platform) -> Path | None:
    component = _component_dir(executable, target)
    if component is None or _has_cdm(component, spec):
        return component
    if target == "macos-arm64" and sys.platform == "darwin":
        _run_once(executable)
    _copy(store, component, spec)
    return component


def ensure(executable: str | Path, *, target: str | None = None,
           cache_dir: str | Path | None = None,
           user_data_dir: str | Path | None = None) -> Path | None:
    """Give the browser at *executable* a Widevine CDM if it has none.

    Returns the browser's CDM directory, or ``None`` when *executable* is not
    an Apostate install or no CDM could be had. Never raises: a browser
    without DRM still launches, and the failure is one line on stderr.
    """
    from .binary import _default_cache_dir, target_platform

    try:
        target_name = target_platform(target)
        spec = _resolve_layout(target_name)
        executable = Path(executable)
        component = _component_dir(executable, target_name)
        if component is None:
            return None
        if not _has_cdm(component, spec):
            store = store_path(_default_cache_dir() if cache_dir is None else cache_dir)
            if not _has_cdm(store, spec):
                _fill_store(store, target_name)
            _install(executable, target_name, store, spec)
        if user_data_dir is not None and target_name.startswith("linux-"):
            _write_hint(Path(user_data_dir).expanduser().absolute(), component, spec)
        return component
    except Exception as exc:  # noqa: BLE001 - DRM must never cost the launch
        print(f"apostate: Widevine is unavailable, launching without DRM: {exc}",
              file=sys.stderr)
        return None


def provision(*, target: str | None = None, source: str | Path | None = None,
              cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Install a CDM into this package's browser now, replacing any it has.

    *source* is a ``WidevineCdm`` directory, with or without a version
    directory inside. Without it a local copy is used, else Google's, as a
    launch would. Unlike a launch, this raises when no CDM can be had.
    """
    from .binary import BinaryManager, target_platform

    target_name = target_platform(target)
    spec = _resolve_layout(target_name)
    chosen: Path | None = None
    if source is not None:
        chosen = Path(source).expanduser()
        if chosen.name != COMPONENT and (chosen / COMPONENT).is_dir():
            chosen = chosen / COMPONENT
        payload = _payload(chosen, spec)
        if payload is None:
            raise WidevineError(
                f"{chosen} does not contain _platform_specific/{spec.subdir}/{spec.library}")
        chosen = payload
    manager = BinaryManager(cache_dir=cache_dir)
    store = store_path(manager.cache_dir)
    origin = _fill_store(store, target_name, chosen)
    executable = manager.ensure(target=target_name)
    component = _component_dir(executable, target_name)
    if component is None:
        raise WidevineError(f"{executable} is not inside an Apostate install")
    if target_name == "macos-arm64" and sys.platform == "darwin" and not _has_cdm(component, spec):
        _run_once(executable)
    _copy(store, component, spec)
    return {
        "platform": target_name,
        "platform_verified": platform_is_verified(target_name),
        "source": origin,
        "version": _read_version(store),
        "store": str(store),
        "installed": str(component),
    }


#: Names the package root re-exports, spelled for a caller who did not import
#: this module directly.
discover_widevine = discover
ensure_widevine = ensure
provision_widevine = provision

__all__ = [
    "COMPONENT", "VERIFIED_TARGETS", "WidevineError", "discover", "discover_widevine",
    "download", "ensure", "ensure_widevine", "platform_is_verified", "provision",
    "provision_widevine", "store_path",
]
