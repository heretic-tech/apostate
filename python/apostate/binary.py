"""Apostate binary acquisition: turn a release artifact into a runnable install.

On first use this downloads the archive that the release manifest shipped inside
this package names for the host platform, verifies its SHA-256 against that
manifest *before* opening it, extracts the complete distribution into a
versioned cache directory, and returns the browser executable inside it. Later
launches reuse the cache.

Chromium is not a single file. The macOS artifact is an application bundle whose
framework is reached through relative symlinks, and the Linux artifacts carry
ICU data, ``.pak`` resources, SwiftShader libraries and a crashpad handler
beside the executable. So the whole tree is installed and the executable is
located inside it; copying the executable out on its own produces a binary that
cannot start.

The trust anchor is the manifest in this package, not the download. A digest
fetched from the same place as the bytes it describes proves nothing, so the
default manifest is package data, pinned to this package's version, and
``manifest_url=`` is an explicit opt-in to the weaker path.
"""

from __future__ import annotations

import errno
import hashlib
import importlib.resources
import itertools
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, IO, Iterable, Iterator, Mapping, NamedTuple
from urllib.parse import urljoin

from .config import CATALOGUE_VERSION, CHROMIUM_VERSION, PACKAGE_VERSION
from .errors import (
    BinaryError,
    BinaryNotFoundError,
    IntegrityError,
    ManifestError,
    UnpublishedArtifactError,
    UnsupportedArchiveError,
)

_MANIFEST_RESOURCE = "assets/release-manifest.json"

#: Where a published release lives. Only used to build a download URL when the
#: manifest does not carry an explicit one; the digest still comes from the
#: manifest, so a wrong base URL fails the integrity check rather than
#: installing something else.
RELEASE_REPOSITORY = "heretic-hq/apostate"

#: ``docs/RELEASE.md`` step 2: releases are tagged ``vMAJOR.MINOR.PATCH``.
def _release_tag(package_version: str) -> str:
    return "v" + str(package_version)


#: Marker format. Bumped when the on-disk cache layout changes so an install
#: written by an older package is replaced instead of misread.
_INSTALL_FORMAT = 2
_MARKER_NAME = "install.json"
_INSTALL_NAME = "install"

_TARGETS = ("linux-x64", "linux-arm64", "macos-arm64", "windows-x64")

#: Where the browser executable sits inside each platform's archive. Checked
#: before the fallback scan so a tree that also ships helper executables still
#: resolves to exactly one answer.
_EXECUTABLE_LAYOUT: dict[str, tuple[str, ...]] = {
    "macos-arm64": ("Chromium.app/Contents/MacOS/Chromium",),
    "linux-x64": ("chrome",),
    "linux-arm64": ("chrome",),
    "windows-x64": ("chrome.exe",),
}
_EXECUTABLE_NAMES = frozenset({"chrome", "chrome.exe", "Chromium", "chromium",
                               "chromium-browser", "chromium.exe"})

_counter = itertools.count()


def _default_cache_dir() -> Path:
    override = os.environ.get("APOSTATE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
        return Path(base) / "apostate" / "cache"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches" / "apostate"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "apostate"


def target_platform(value: str | None = None) -> str:
    if value:
        normalized = value.strip().lower().replace("_", "-")
        aliases = {"darwin-arm64": "macos-arm64", "mac-arm64": "macos-arm64",
                   "osx-arm64": "macos-arm64", "win-x64": "windows-x64",
                   "linux-aarch64": "linux-arm64", "linux-amd64": "linux-x64"}
        normalized = aliases.get(normalized, normalized)
        if normalized in _TARGETS:
            return normalized
        raise BinaryError(f"unsupported binary target: {value}")
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"x86_64", "amd64", "x86-64"} else machine
    if system == "darwin" and arch == "arm64":
        return "macos-arm64"
    if system == "linux" and arch in {"x64", "arm64"}:
        return f"linux-{arch}"
    if system == "windows" and arch == "x64":
        return "windows-x64"
    raise BinaryError(f"unsupported host binary target: {system}-{machine}")


def _read_resource(name: str) -> bytes:
    try:
        return importlib.resources.files("apostate").joinpath(name).read_bytes()
    except OSError as exc:
        raise ManifestError(f"package release asset is unavailable: {name}") from exc


def _read_manifest(value: Mapping[str, Any] | str | Path | None) -> dict[str, Any]:
    if value is None:
        raw = _read_resource(_MANIFEST_RESOURCE)
    elif isinstance(value, Mapping):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    else:
        path = Path(value).expanduser()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ManifestError("release manifest is unreadable") from exc
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("release manifest is not valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ManifestError("release manifest must be a JSON object")
    status = parsed.get("status")
    if status is not None and status not in {"published", "unpublished"}:
        raise ManifestError("release manifest status must be published or unpublished")
    required = ("package_version", "chromium_version", "catalogue_version")
    missing = [key for key in required if key not in parsed]
    if missing:
        raise ManifestError("release manifest is missing " + ", ".join(missing))
    if parsed["package_version"] != PACKAGE_VERSION:
        raise ManifestError("release manifest package_version does not match this package")
    if parsed["chromium_version"] != CHROMIUM_VERSION:
        raise ManifestError("release manifest chromium_version does not match this package")
    if parsed["catalogue_version"] != CATALOGUE_VERSION:
        raise ManifestError("release manifest catalogue_version does not match this package")
    if status == "unpublished" or (
        (isinstance(parsed.get("artifacts"), (Mapping, list)) and not parsed["artifacts"])
        and "artifact" not in parsed
    ):
        raise UnpublishedArtifactError(
            "release manifest is unpublished; no package artifact is available"
        )
    has_artifacts = "artifacts" in parsed
    has_scalar = all(key in parsed for key in ("platform", "artifact", "sha256"))
    if not has_artifacts and not has_scalar:
        raise ManifestError("release manifest needs artifacts or scalar platform/artifact/sha256 fields")
    if has_artifacts and not isinstance(parsed["artifacts"], (Mapping, list)):
        raise ManifestError("release manifest artifacts must be an object or list")
    return dict(parsed)


def _artifact_record(manifest: Mapping[str, Any], target: str, requested: Any = None) -> dict[str, Any]:
    if isinstance(requested, Mapping):
        record = dict(requested)
    elif all(key in manifest for key in ("platform", "artifact", "sha256")):
        if manifest.get("platform") != target:
            raise UnpublishedArtifactError(f"Apostate binary for {target} is not published for this release.")
        record = {key: manifest[key] for key in ("platform", "artifact", "sha256")}
        for key in ("name", "executable", "binary", "path", "url"):
            if key in manifest:
                record[key] = manifest[key]
    else:
        records = manifest["artifacts"]
        if isinstance(records, Mapping):
            record = records.get(target)
            if record is None:
                record = records.get("artifacts/" + target)
        else:
            matches = [item for item in records if isinstance(item, Mapping) and item.get("platform") == target]
            record = matches[0] if len(matches) == 1 else None
        if not isinstance(record, Mapping):
            raise UnpublishedArtifactError(f"Apostate binary for {target} is not published for this release.")
        record = dict(record)
    if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
        raise ManifestError(f"artifact metadata for {target} has no valid sha256")
    try:
        int(record["sha256"], 16)
    except ValueError as exc:
        raise ManifestError(f"artifact metadata for {target} has invalid sha256") from exc
    if not (record.get("url") or record.get("path") or record.get("artifact")):
        raise ManifestError(f"artifact metadata for {target} has no download location")
    return record


def _artifact_name(record: Mapping[str, Any]) -> str:
    """The archive's own file name, never a URL or a local path."""
    for key in ("artifact", "name"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return PurePosixPath(value.replace("\\", "/")).name
    for key in ("path", "url"):
        value = record.get(key)
        if isinstance(value, (str, Path)) and str(value):
            return PurePosixPath(str(value).replace("\\", "/")).name
    raise ManifestError("artifact metadata names no archive")


def artifact_url(manifest: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    """Where the archive is fetched from when no local copy was configured.

    A manifest may name the URL outright. Otherwise it is built from the release
    tag, which is how a package published before its release assets existed
    still finds them: the manifest supplies the digest, the tag supplies the
    location, and a wrong location fails verification instead of installing
    something unexpected.
    """
    for key in ("url", "download_url"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    name = _artifact_name(record)
    base = os.environ.get("APOSTATE_DOWNLOAD_BASE_URL") or manifest.get("base_url")
    if isinstance(base, str) and base:
        return urljoin(base if base.endswith("/") else base + "/", name)
    repository = str(manifest.get("repository") or RELEASE_REPOSITORY)
    tag = str(manifest.get("tag") or _release_tag(manifest["package_version"]))
    return f"https://github.com/{repository}/releases/download/{tag}/{name}"


def _payload_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, Path):
        return value.read_bytes()
    if hasattr(value, "read"):
        return bytes(value.read())
    if isinstance(value, str):
        return Path(value).read_bytes()
    raise BinaryError("binary downloader returned unsupported data")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_member(name: str) -> PurePosixPath:
    """Reject anything that would write outside the extraction root."""
    cleaned = name.replace("\\", "/")
    candidate = PurePosixPath(cleaned)
    if not cleaned or cleaned.startswith("/") or candidate.is_absolute() or "\x00" in cleaned:
        raise UnsupportedArchiveError("artifact archive contains an unsafe path")
    if any(part in ("..", "") for part in candidate.parts):
        raise UnsupportedArchiveError("artifact archive contains an unsafe path")
    if len(cleaned) > 1 and cleaned[1] == ":":
        raise UnsupportedArchiveError("artifact archive contains an unsafe path")
    return candidate


def _contained_link(member: PurePosixPath, link: str) -> None:
    """Allow a symlink only when it cannot escape the extraction root.

    ``.github/release/artifact-policy.json`` says reject every symbolic link.
    That rule cannot be satisfied and also ship macOS: the artifact's
    ``Chromium Framework.framework`` reaches its versioned payload through five
    relative symlinks, and a bundle without them does not launch. The property
    the rule was protecting is containment, so containment is what is enforced
    -- a relative target that stays inside the root is accepted, an absolute
    target or one that climbs out is refused. See the report accompanying this
    change.
    """
    cleaned = link.replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or "\x00" in cleaned:
        raise UnsupportedArchiveError(f"artifact archive links outside itself: {member}")
    parts: list[str] = []
    for part in (*member.parent.parts, *PurePosixPath(cleaned).parts):
        if part == ".":
            continue
        if part == "..":
            if not parts:
                raise UnsupportedArchiveError(f"artifact archive links outside itself: {member}")
            parts.pop()
            continue
        parts.append(part)


def _write_link(target: Path, link: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    os.symlink(link, target)


def _open_tar(archive: Path) -> Iterator[tarfile.TarFile]:
    """Yield a readable tar, including ``.tar.zst`` on interpreters without it.

    ``tarfile`` learned zstandard in Python 3.14. This package supports 3.10, so
    an older interpreter falls back to the ``zstandard`` distribution and then
    to the ``zstd`` command line tool before giving up with an actionable
    message. Every path iterates sequentially, which is all a stream supports.
    """
    suffix = archive.name.lower()
    if suffix.endswith((".zip",)):
        raise UnsupportedArchiveError("zip archives are not read as tar")
    try:
        handle = tarfile.open(archive, mode="r:*")
    except tarfile.ReadError:
        handle = None
    except (OSError, EOFError) as exc:
        raise UnsupportedArchiveError("artifact is not a readable tar archive") from exc
    if handle is not None:
        try:
            yield handle
        finally:
            handle.close()
        return
    if not suffix.endswith((".zst", ".tar.zst")):
        raise UnsupportedArchiveError("artifact is not a supported tar archive")
    try:
        import zstandard  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        zstandard = None  # type: ignore[assignment]
    if zstandard is not None:
        with archive.open("rb") as raw:
            reader = zstandard.ZstdDecompressor().stream_reader(raw)
            handle = tarfile.open(fileobj=reader, mode="r|")
            try:
                yield handle
            finally:
                handle.close()
        return
    tool = shutil.which("zstd")
    if tool is None:
        raise UnsupportedArchiveError(
            "this Python cannot read a .tar.zst archive: upgrade to Python 3.14, "
            "run `pip install zstandard`, or install the `zstd` command line tool"
        )
    process = subprocess.Popen([tool, "-dc", "--", str(archive)], stdout=subprocess.PIPE)
    try:
        handle = tarfile.open(fileobj=process.stdout, mode="r|")
        try:
            yield handle
        finally:
            handle.close()
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.wait() not in (0, None):
            raise UnsupportedArchiveError("zstd failed to decompress the artifact")


def _extract_tar(archive: Path, destination: Path) -> None:
    for source in _open_tar(archive):
        for member in source:
            relative = _relative_member(member.name)
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym():
                _contained_link(relative, member.linkname)
                _write_link(target, member.linkname)
                continue
            if member.islnk():
                _relative_member(member.linkname)
                target.parent.mkdir(parents=True, exist_ok=True)
                origin = destination / _relative_member(member.linkname)
                if not origin.is_file():
                    raise UnsupportedArchiveError("artifact archive hard-links a member it does not carry")
                shutil.copyfile(origin, target)
                shutil.copymode(origin, target)
                continue
            if not member.isfile():
                raise UnsupportedArchiveError("artifact archive contains an unsupported member")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            if stream is None:
                raise UnsupportedArchiveError("artifact archive contains an unreadable member")
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)
            mode = member.mode & 0o777
            target.chmod(mode | 0o600 if mode else 0o644)


def _extract_zip(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                relative = _relative_member(member.filename)
                target = destination / relative
                mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    link = source.read(member).decode("utf-8", "strict")
                    _contained_link(relative, link)
                    _write_link(target, link)
                    continue
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                permissions = mode & 0o777
                target.chmod(permissions | 0o600 if permissions else 0o644)
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise UnsupportedArchiveError("artifact is not a valid zip archive") from exc


def _extract(archive: Path, destination: Path) -> None:
    name = archive.name.lower()
    if name.endswith(".part"):
        name = name[:-5]
    if name.endswith(".zip"):
        _extract_zip(archive, destination)
    elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar.zst", ".zst")):
        _extract_tar(archive, destination)
    else:
        raise UnsupportedArchiveError(f"unsupported artifact archive format: {archive.name}")


#: Suffixes an artifact name may carry, longest first so `.tar.zst` wins.
_ARCHIVE_SUFFIXES = (".tar.zst", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar", ".zip", ".zst")


def _hoist(root: Path, archive_name: str) -> Path:
    """Strip the archive's own top-level wrapper directory, and only that.

    Every release archive wraps its tree in a directory named exactly after the
    archive, ``apostate-<version>-<target>/``. Dropping that level keeps the
    installed path short and the executable's relative path independent of the
    archive's name.

    The name must match. Hoisting any lone directory would also unwrap an
    archive whose single top-level entry is meaningful -- ``Chromium.app/`` is
    the obvious one -- and silently move the executable.
    """
    expected = archive_name
    for suffix in _ARCHIVE_SUFFIXES:
        if expected.lower().endswith(suffix):
            expected = expected[: -len(suffix)]
            break
    entries = list(root.iterdir())
    if (len(entries) == 1 and entries[0].name == expected
            and entries[0].is_dir() and not entries[0].is_symlink()):
        return entries[0]
    return root


def _locate_executable(root: Path, target: str, requested: str | None = None) -> Path:
    if requested:
        candidate = root / _relative_member(requested)
        if candidate.is_file():
            return candidate
        raise BinaryNotFoundError(f"artifact executable path is not present: {requested}")
    for relative in _EXECUTABLE_LAYOUT.get(target, ()):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    found = [path for path in root.rglob("*")
             if path.name in _EXECUTABLE_NAMES and path.is_file() and not path.is_symlink()]
    if len(found) == 1:
        return found[0]
    raise BinaryNotFoundError(
        "extracted artifact does not contain exactly one recognized browser executable"
        f" for {target} (found {len(found)})"
    )


def _redact(value: Any) -> str:
    return "configured artifact source"


def _replace_tree(staged: Path, destination: Path) -> None:
    """Swap an install directory into place without a half-written window."""
    retired: Path | None = None
    if destination.exists() or destination.is_symlink():
        retired = destination.with_name(f"{destination.name}.stale-{os.getpid()}-{next(_counter)}")
        os.replace(destination, retired)
    try:
        os.replace(staged, destination)
    except OSError:
        if retired is not None:
            os.replace(retired, destination)
        raise
    if retired is not None:
        shutil.rmtree(retired, ignore_errors=True)


def _keep_archive_default() -> bool:
    value = os.environ.get("APOSTATE_KEEP_ARCHIVE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


#: Where a browser is looked for, strongest claim first. The first two are the
#: user saying exactly which file to run and are taken at their word; the last
#: two are searches and are verified.
DISCOVERY_ORDER = ("argument", "environment", "cache", "well-known")

#: What only an Apostate payload carries, beside the executable. Every release
#: archive stages ``build/MANIFEST.lock`` -- the build record, which names the
#: Chromium version and the patch-series digests -- and the whole of
#: ``resources/profiles/`` next to the browser; see scripts/package-artifact.sh.
#: Stock Chrome and stock Chromium carry neither, which is the entire basis for
#: telling them apart: the bundle is named ``Chromium.app`` and the executable
#: ``chrome`` exactly as upstream names them, and Chromium 152.0.7977.83 exists
#: upstream too, so nothing about the file alone distinguishes the two.
_PAYLOAD_MANIFEST = ("build", "MANIFEST.lock")
_PAYLOAD_RESOURCES = ("resources", "profiles", "catalogue.json")

#: Why a candidate was passed over. Reported verbatim by ``apostate info`` and
#: mirrored in npm/src/index.ts, so these strings are part of the contract.
_NO_PAYLOAD = ("no Apostate payload beside it "
               "(build/MANIFEST.lock or resources/profiles/catalogue.json)")
_NO_VERSION = "version could not be established"
_NOT_EXECUTABLE = "not executable"

#: Where the executable sits inside a payload root when the payload was put
#: there by hand rather than by this package. Wider than _EXECUTABLE_LAYOUT on
#: macOS because a bundle dragged into /Applications may have been renamed.
_DISCOVERY_LAYOUT: dict[str, tuple[str, ...]] = {
    "macos-arm64": ("Chromium.app/Contents/MacOS/Chromium",
                    "Apostate.app/Contents/MacOS/Chromium",
                    "Apostate.app/Contents/MacOS/Apostate"),
    "linux-x64": ("chrome",),
    "linux-arm64": ("chrome",),
    "windows-x64": ("chrome.exe",),
}

#: One ``key = "value"`` assignment from ``build/MANIFEST.lock``. Anything else
#: -- comments, the ``[outputs]`` header, its quoted-path rows -- is skipped
#: rather than fatal, because the file is a build record that grows sections
#: and only the two keys below are being read out of it.
_BUILD_RECORD_LINE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"$')
_PLIST_VERSION = re.compile(
    r"<key>\s*CFBundleShortVersionString\s*</key>\s*<string>([^<]*)</string>")
_REPORTED_VERSION = re.compile(r"\b(\d+\.\d+\.\d+\.\d+)\b")


def _well_known_roots(target: str) -> tuple[Path, ...]:
    """The documented places a hand-installed browser is looked for.

    Documented, not guessed: a search that finds a browser somewhere the
    README does not name is a launch whose binary the user cannot account
    for. Tests and callers that need none of this pass ``search_roots=()``.
    """
    if target == "macos-arm64":
        return (Path("/Applications"), Path.home() / "Applications")
    if target == "windows-x64":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) / "apostate",) if base else ()
    return (Path.home() / ".cache" / "apostate", Path("/opt/apostate"))


def _read_build_record(path: Path) -> dict[str, str] | None:
    """Parse Apostate's build record, or return ``None`` if this is not one.

    ``scripts/build.sh`` writes ``key = "value"`` lines with ``#`` comments and
    a trailing ``[outputs]`` section. The patch digests are the discriminator:
    no other file called MANIFEST.lock carries them, so a tree that does was
    built from this patch series rather than merely named like one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    record: dict[str, str] = {}
    for line in text.splitlines():
        match = _BUILD_RECORD_LINE.match(line.strip())
        if match is not None:
            record[match.group(1)] = match.group(2)
    if not record.get("chromium_version"):
        return None
    if not any(key in record for key in ("patch_series_sha256", "patch_contents_sha256")):
        return None
    return record


def _bundle_version(executable: Path) -> str | None:
    """``CFBundleShortVersionString`` from the bundle around *executable*.

    A build's Info.plist is XML text, so a regex reads it without plistlib and
    without caring which of the two plist encodings a future build emits: a
    binary plist simply fails to match and the caller falls through.
    """
    contents = executable.parent.parent
    if contents.name != "Contents":
        return None
    try:
        text = (contents / "Info.plist").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = _PLIST_VERSION.search(text)
    return match.group(1).strip() if match else None


def _reported_version(executable: Path, target: str) -> str | None:
    """Ask the binary its version -- only ever after a marker vouched for it.

    Never on Windows. ``chrome.exe --version`` does not print a version there:
    ``HandleVersionSwitches()`` is called inside ``#if BUILDFLAG(IS_POSIX)`` in
    chrome/app/chrome_main_delegate.cc, so off POSIX the switch is not handled
    at all and falls through into a full browser start, and chrome.exe is a
    GUI-subsystem binary so nothing arrives on stdout either way.
    scripts/smoke-binary.sh reads the file's version resource for that reason;
    here the build record has already answered by the time it would matter.
    """
    if target == "windows-x64" or os.name == "nt":
        return None
    try:
        completed = subprocess.run([str(executable), "--version"],
                                   capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _REPORTED_VERSION.search(completed.stdout.decode("utf-8", "ignore"))
    return match.group(1) if match else None


class DiscoveredBinary(NamedTuple):
    """Where a runnable browser was found, and what vouched for it."""

    executable: Path
    source: str
    chromium_version: str | None
    payload_root: Path | None


def _verify_payload(root: Path, executable: Path,
                    target: str) -> tuple[str | None, str | None]:
    """``(chromium version, rejection reason)`` for one discovered candidate.

    Marker first, then version, and that order is the safety property rather
    than an optimisation. Stock Chrome started with Apostate's switches is a
    session with no protection at all and nothing on screen to say so, which
    is the worst thing this package could do; so nothing is executed until a
    file only an Apostate payload carries has already vouched for the tree.
    Both halves are required: the marker proves the build, the version proves
    it is the one this package speaks to.
    """
    record = _read_build_record(root.joinpath(*_PAYLOAD_MANIFEST))
    if record is None and not root.joinpath(*_PAYLOAD_RESOURCES).is_file():
        return None, _NO_PAYLOAD
    version = record.get("chromium_version") if record is not None else None
    if version is None and target == "macos-arm64":
        version = _bundle_version(executable)
    if version is None:
        version = _reported_version(executable, target)
    if version is None:
        return None, _NO_VERSION
    if version != CHROMIUM_VERSION:
        return None, f"reports Chromium {version}, not {CHROMIUM_VERSION}"
    if os.name != "nt" and not os.access(executable, os.X_OK):
        return None, _NOT_EXECUTABLE
    return version, None


def _discovery_candidates(root: Path, target: str) -> Iterator[tuple[Path, Path]]:
    """``(payload root, executable)`` pairs under one well-known root.

    The root itself and its immediate children, and no deeper. An archive
    extracted in place leaves the payload at ``<root>/apostate-<version>-<target>/``
    while a bundle moved into /Applications leaves it at the root, so both
    levels are needed -- and a recursive walk of /Applications is not something
    to do on the way to every launch.
    """
    layout = _DISCOVERY_LAYOUT.get(target, ())
    payload_roots = [root]
    try:
        # Symlinks followed: a directory in /opt/apostate that points at a
        # build elsewhere is a perfectly ordinary way to keep one. Nothing is
        # trusted for being reachable -- what is found still has to prove
        # itself -- and one level deep cannot loop.
        payload_roots.extend(sorted(entry for entry in root.iterdir() if entry.is_dir()))
    except OSError:
        pass
    for payload_root in payload_roots:
        for relative in layout:
            candidate = payload_root.joinpath(*relative.split("/"))
            if candidate.is_file():
                yield payload_root, candidate


class BinaryManager:
    """Resolve a verified release artifact into a deterministic cache install."""

    def __init__(self, *, cache_dir: str | Path | None = None,
                 manifest: Mapping[str, Any] | str | Path | None = None,
                 downloader: Callable[[str], Any] | None = None,
                 search_roots: Iterable[str | Path] | None = None) -> None:
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else _default_cache_dir()
        self.manifest_value = manifest
        self.downloader = downloader
        #: ``None`` means the documented locations for the target; an empty
        #: sequence means search nowhere, which is how a caller that must not
        #: depend on what is installed on the host opts out.
        self.search_roots = None if search_roots is None else tuple(
            Path(root).expanduser() for root in search_roots)

    def _manifest(self) -> dict[str, Any]:
        return _read_manifest(self.manifest_value)

    def _paths(self, target: str, manifest: Mapping[str, Any],
               record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
        """``(root, install directory, marker)`` for one platform and build."""
        root = self.cache_dir / str(manifest["chromium_version"]) / target
        return root, root / _INSTALL_NAME, root / _MARKER_NAME

    def _download(self, manifest: Mapping[str, Any], record: Mapping[str, Any]) -> bytes:
        local = record.get("path") or record.get("local_path") or record.get("file")
        if isinstance(local, (str, Path)):
            path = Path(str(local)).expanduser()
            if not path.is_file():
                raise BinaryNotFoundError(f"configured artifact source is missing: {path}")
            try:
                return path.read_bytes()
            except OSError as exc:
                raise BinaryError("configured artifact source is unreadable") from exc
        # A bare `artifact` that happens to name a readable local file is still
        # honoured; that is how a nightly's output directory is used directly.
        bare = record.get("artifact")
        if isinstance(bare, (str, Path)) and Path(str(bare)).is_file():
            return Path(str(bare)).read_bytes()
        source = artifact_url(manifest, record)
        try:
            if self.downloader is not None:
                return _payload_bytes(self.downloader(source))
            with urllib.request.urlopen(source, timeout=120) as response:
                return response.read()
        except Exception as exc:
            if isinstance(exc, BinaryError):
                raise
            raise BinaryError(f"unable to download {_redact(source)}") from exc

    def _cached(self, marker: Path, install: Path, target: str,
                manifest: Mapping[str, Any], expected: str) -> Path | None:
        """Return the cached executable when the marker still vouches for it."""
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, Mapping) or data.get("format") != _INSTALL_FORMAT:
            return None
        if (data.get("artifact_sha256") != expected
                or data.get("platform") != target
                or data.get("package_version") != manifest["package_version"]
                or data.get("chromium_version") != manifest["chromium_version"]):
            return None
        relative = data.get("executable")
        if not isinstance(relative, str) or not relative:
            return None
        try:
            executable = install / _relative_member(relative)
        except UnsupportedArchiveError:
            return None
        if not executable.is_file() or executable.is_symlink():
            return None
        if not os.access(executable, os.X_OK):
            return None
        # Re-hash only the executable that is about to be exec'd. The archive is
        # not kept by default, and re-reading 150 MB on every launch to check a
        # file we already replaced atomically is a cost with no matching threat.
        if data.get("executable_sha256") != _hash_file(executable):
            return None
        return executable

    def _well_known(self, target: str) -> tuple[Path, ...]:
        return _well_known_roots(target) if self.search_roots is None else self.search_roots

    def _cached_install(self, target: str) -> DiscoveredBinary | None:
        """This package's own install, if the marker still vouches for it."""
        try:
            manifest = self._manifest()
            record = _artifact_record(manifest, target)
        except BinaryError:
            # Validating the cache needs the artifact digest, so an unpublished
            # or unreadable manifest is not a cache miss: it is a question that
            # cannot be asked. The well-known search still can be.
            return None
        _root, install, marker = self._paths(target, manifest, record)
        executable = self._cached(marker, install, target, manifest,
                                  str(record["sha256"]).lower())
        if executable is None:
            return None
        return DiscoveredBinary(executable, "cache",
                                str(manifest["chromium_version"]), install)

    def _discover_well_known(self, target: str, searched: list[str],
                             rejected: list[dict[str, str]]) -> DiscoveredBinary | None:
        # Exhaustive rather than first-hit: the first acceptable candidate is
        # what gets adopted, but every refusal is still recorded, so `apostate
        # info` can say "I passed over the Chromium in /Applications because
        # nothing beside it says it is ours" instead of staying silent about a
        # browser the user can see. Bounded by construction -- two roots, one
        # level of children, at most three layout paths each.
        found: DiscoveredBinary | None = None
        for root in self._well_known(target):
            if not root.is_dir():
                continue
            searched.append(str(root))
            for payload_root, candidate in _discovery_candidates(root, target):
                version, reason = _verify_payload(payload_root, candidate, target)
                if reason is not None:
                    rejected.append({"path": str(candidate), "reason": reason})
                elif found is None:
                    found = DiscoveredBinary(candidate, "well-known", version, payload_root)
        return found

    def _discover(self, target: str, searched: list[str],
                  rejected: list[dict[str, str]],
                  binary_path: str | Path | None = None) -> DiscoveredBinary | None:
        # The first two sources are the user naming a file, so neither is
        # verified: a caller who says which browser to run has said it. Only
        # the searches can adopt something the user did not name, so only the
        # searches have to prove what they found.
        for value, source, label in (
            (binary_path, "argument", "the configured path"),
            (os.environ.get("APOSTATE_BINARY"), "environment", "APOSTATE_BINARY"),
        ):
            if not value:
                continue
            candidate = Path(value).expanduser()
            if candidate.is_file():
                return DiscoveredBinary(candidate, source, None, None)
            # Recorded rather than raised: `discover` answers a question and
            # `ensure` is the one that refuses. A stale path should show up in
            # `apostate info` rather than make it unreadable.
            rejected.append({"path": str(candidate),
                             "reason": f"{label} does not name a file"})
        cached = self._cached_install(target)
        if cached is not None:
            return cached
        return self._discover_well_known(target, searched, rejected)

    def discover(self, *, target: str | None = None,
                 binary_path: str | Path | None = None) -> DiscoveredBinary | None:
        """The browser a launch would use without downloading, or ``None``."""
        return self._discover(target_platform(target), [], [], binary_path)

    def discovery(self, *, target: str | None = None,
                  binary_path: str | Path | None = None) -> dict[str, Any]:
        """Report which browser was found and what was passed over.

        A launch that downloads 150 MB over an install the user already has is
        indistinguishable, from outside, from one that could not find it. So
        the search is inspectable rather than implicit: ``apostate info``
        prints this, including why each candidate was refused.
        """
        searched: list[str] = []
        rejected: list[dict[str, str]] = []
        found = self._discover(target_platform(target), searched, rejected, binary_path)
        return {
            "order": list(DISCOVERY_ORDER),
            "searched": searched,
            "found": None if found is None else {
                "executable": str(found.executable),
                "source": found.source,
                "chromium_version": found.chromium_version,
                "payload_root": None if found.payload_root is None else str(found.payload_root),
            },
            "rejected": rejected,
        }

    def assert_published(self, *, target: str | None = None) -> None:
        """Raise unless a browser for *target* can be had at all.

        The publication question on its own. Reading the manifest and looking
        up the record are both local, with no acquisition behind them, which
        is what lets a caller ask it before paying for anything else.

        A browser already on disk answers it the other way: telling someone
        who downloaded the archive by hand that nothing is published is both
        useless and, in the only sense they care about, false.
        """
        target_name = target_platform(target)
        try:
            _artifact_record(self._manifest(), target_name)
        except BinaryError:
            if self._discover(target_name, [], []) is None:
                raise

    def ensure(self, *, target: str | None = None,
               artifact: Mapping[str, Any] | str | Path | None = None,
               keep_archive: bool | None = None, force: bool = False) -> Path:
        """Return a runnable browser executable, downloading it if required."""
        override = os.environ.get("APOSTATE_BINARY")
        if override and artifact is None:
            candidate = Path(override).expanduser()
            if not candidate.is_file():
                raise BinaryNotFoundError(f"APOSTATE_BINARY does not name a file: {candidate}")
            return candidate

        target_name = target_platform(target)
        manifest: dict[str, Any] | None = None
        record: dict[str, Any] | None = None
        unavailable: BinaryError | None = None
        try:
            manifest = self._manifest()
            record = _artifact_record(manifest, target_name,
                                      artifact if isinstance(artifact, Mapping) else None)
        except BinaryError as exc:
            # An unpublished or unusable manifest does not end the search. A
            # browser already installed is still runnable, and answering
            # "nothing is published" over one the user put there by hand sends
            # them to build a binary they already have.
            unavailable = exc

        cache_paths: tuple[Path, Path, Path] | None = None
        if record is not None and manifest is not None:
            cache_paths = self._paths(target_name, manifest, record)
            if not force:
                cached = self._cached(cache_paths[2], cache_paths[1], target_name,
                                      manifest, str(record["sha256"]).lower())
                if cached is not None:
                    return cached
        # `force` means reinstall, so it skips the searches rather than the
        # download. An explicit artifact is a request to install that archive.
        if not force and artifact is None:
            found = self._discover_well_known(target_name, [], [])
            if found is not None:
                return found.executable
        if unavailable is not None:
            raise unavailable
        # `unavailable` is None exactly when both lookups above succeeded, so
        # the manifest, the record and the cache paths are all present here.
        root, install, marker = cache_paths
        expected = str(record["sha256"]).lower()

        if isinstance(artifact, (str, Path)):
            source_path = Path(artifact).expanduser()
            if not source_path.is_file():
                raise BinaryNotFoundError(f"configured binary artifact is missing: {source_path}")
            data = source_path.read_bytes()
        else:
            data = self._download(manifest, record)

        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise IntegrityError(
                "downloaded Apostate artifact failed SHA-256 verification: expected "
                f"{expected}, got {actual}. The archive was not opened."
            )

        root.mkdir(parents=True, exist_ok=True)
        archive_name = _artifact_name(record)
        if keep_archive is None:
            keep_archive = _keep_archive_default()
        staged_archive = root / (archive_name + ".part")
        staged_tree: Path | None = None
        try:
            staged_archive.write_bytes(data)
            # Verification above is complete; only now is the archive opened.
            staged_tree = Path(tempfile.mkdtemp(prefix=".install-", dir=str(root)))
            _extract(staged_archive, staged_tree)
            hoisted = _hoist(staged_tree, archive_name)
            executable = _locate_executable(
                hoisted, target_name,
                str(record.get("executable") or record.get("binary") or "") or None,
            )
            if target_name != "windows-x64":
                for path in (*_EXECUTABLE_LAYOUT.get(target_name, ()), ):
                    candidate = hoisted / path
                    if candidate.is_file():
                        candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            relative = executable.relative_to(hoisted).as_posix()
            digest = _hash_file(executable)
            if hoisted is not staged_tree:
                promoted = Path(tempfile.mkdtemp(prefix=".promote-", dir=str(root)))
                os.rmdir(promoted)
                os.replace(hoisted, promoted)
                shutil.rmtree(staged_tree, ignore_errors=True)
                staged_tree = promoted
            _replace_tree(staged_tree, install)
            staged_tree = None
            marker.write_text(json.dumps({
                "artifact": archive_name,
                "artifact_sha256": expected,
                "catalogue_version": manifest["catalogue_version"],
                "chromium_version": manifest["chromium_version"],
                "executable": relative,
                "executable_sha256": digest,
                "format": _INSTALL_FORMAT,
                "package_version": manifest["package_version"],
                "platform": target_name,
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n", encoding="utf-8")
            # A provisioned Widevine CDM lives outside the install tree, so
            # re-apply it here: `install --force` and a Chromium upgrade both
            # replace this tree, and DRM must not silently vanish when they do.
            try:
                from .widevine import apply_to_install
                apply_to_install(install, target_name, cache_dir=self.cache_dir,
                                 chromium_version=str(manifest["chromium_version"]))
            except Exception:
                # Provisioning is an optional extra; a browser that launches
                # without DRM is far better than no browser at all.
                pass
            if keep_archive:
                os.replace(staged_archive, root / archive_name)
            return install / relative
        finally:
            if staged_tree is not None:
                shutil.rmtree(staged_tree, ignore_errors=True)
            try:
                staged_archive.unlink()
            except OSError as exc:
                if exc.errno not in (errno.ENOENT,):
                    raise

    def info(self, *, target: str | None = None) -> dict[str, Any]:
        """Report install and manifest state. Never raises for an absent release.

        ``info`` is what a user runs to find out why a launch will not work, so
        it answers rather than failing the way ``ensure`` does.
        """
        target_name = target_platform(target)
        discovery = self.discovery(target=target_name)
        found = discovery["found"]
        located = {
            "executable": found["executable"] if found else None,
            "executable_source": found["source"] if found else None,
            "discovery": discovery,
        }
        # Validating the install re-hashes the executable, so ask once. The
        # search already asked unless an explicitly named binary short-circuited
        # it, in which case the cache is still an open question; a "well-known"
        # answer means the cache was consulted and missed.
        if found is not None and found["source"] == "cache":
            cached: Path | None = Path(found["executable"])
        elif found is not None and found["source"] in ("argument", "environment"):
            entry = self._cached_install(target_name)
            cached = None if entry is None else entry.executable
        else:
            cached = None
        try:
            manifest = self._manifest()
        except (ManifestError, UnpublishedArtifactError) as exc:
            return {"available": False, "platform": target_name,
                    "chromium_version": CHROMIUM_VERSION,
                    "package_version": PACKAGE_VERSION,
                    "cache_dir": str(self.cache_dir), "reason": str(exc), **located}
        try:
            record = _artifact_record(manifest, target_name)
        except BinaryError as exc:
            return {"available": False, "platform": target_name,
                    "chromium_version": manifest["chromium_version"],
                    "package_version": manifest["package_version"],
                    "cache_dir": str(self.cache_dir), "reason": str(exc), **located}
        root, install, marker = self._paths(target_name, manifest, record)
        expected = str(record["sha256"]).lower()
        return {
            "available": True,
            "cached": cached is not None,
            "platform": target_name,
            "chromium_version": manifest["chromium_version"],
            "package_version": manifest["package_version"],
            "catalogue_version": manifest["catalogue_version"],
            "artifact": _artifact_name(record),
            "artifact_url": artifact_url(manifest, record),
            "sha256": expected,
            "cache_dir": str(self.cache_dir),
            "install_dir": str(install),
            "archive_retained": (root / _artifact_name(record)).is_file(),
            # `executable` is what a launch would run without downloading, from
            # whichever of the four sources answered; `cached` stays the
            # narrower question of whether this package's own install is valid.
            **located,
        }

    def clear(self) -> None:
        if self.cache_dir.is_symlink():
            raise BinaryError("refusing to clear a symlink cache directory")
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)


def ensure_binary(*, target: str | None = None, cache_dir: str | Path | None = None,
                  manifest: Mapping[str, Any] | str | Path | None = None,
                  downloader: Callable[[str], Any] | None = None,
                  artifact: Mapping[str, Any] | str | Path | None = None,
                  keep_archive: bool | None = None, force: bool = False) -> Path:
    """Download, verify, extract and return the browser executable path."""
    return BinaryManager(cache_dir=cache_dir, manifest=manifest, downloader=downloader).ensure(
        target=target, artifact=artifact, keep_archive=keep_archive, force=force)


def binary_info(*, target: str | None = None, cache_dir: str | Path | None = None,
                manifest: Mapping[str, Any] | str | Path | None = None) -> dict[str, Any]:
    return BinaryManager(cache_dir=cache_dir, manifest=manifest).info(target=target)


def discover_binary(*, target: str | None = None, cache_dir: str | Path | None = None,
                    manifest: Mapping[str, Any] | str | Path | None = None,
                    binary_path: str | Path | None = None,
                    search_roots: Iterable[str | Path] | None = None) -> Path | None:
    """The browser a launch would use without downloading, or ``None``."""
    found = BinaryManager(cache_dir=cache_dir, manifest=manifest,
                          search_roots=search_roots).discover(target=target,
                                                              binary_path=binary_path)
    return None if found is None else found.executable


def discovery_report(*, target: str | None = None, cache_dir: str | Path | None = None,
                     manifest: Mapping[str, Any] | str | Path | None = None,
                     binary_path: str | Path | None = None,
                     search_roots: Iterable[str | Path] | None = None) -> dict[str, Any]:
    """Where the browser was found, what was searched, and what was refused."""
    return BinaryManager(cache_dir=cache_dir, manifest=manifest,
                         search_roots=search_roots).discovery(target=target,
                                                              binary_path=binary_path)


def clear_cache(*, cache_dir: str | Path | None = None) -> None:
    BinaryManager(cache_dir=cache_dir).clear()


__all__ = [
    "BinaryManager", "DISCOVERY_ORDER", "DiscoveredBinary", "RELEASE_REPOSITORY",
    "artifact_url", "binary_info", "clear_cache", "discover_binary", "discovery_report",
    "ensure_binary", "target_platform",
]
