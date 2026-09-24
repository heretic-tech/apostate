"""``python -m apostate`` -- install and run the browser from a terminal.

Get the browser, find out where it is, run it, throw it away, and install the
system fonts a persona lists.
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from .binary import BinaryManager, target_platform
from .config import CHROMIUM_VERSION, PACKAGE_VERSION
from .errors import ApostateError
from .widevine import ensure as ensure_widevine

#: The Windows 11 font set. Cloned by the user's own machine, never shipped.
WINDOWS_FONTS = "https://github.com/MauCariApa-com/windows-11-fonts"
_FONT_SUFFIXES = (".ttf", ".ttc", ".otf")
#: Where a Mac keeps the fonts its persona lists. PingFang lives in the
#: private FontServices directory; downloaded fonts in the font asset folders.
_MAC_FONT_DIRS = (
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    "/System/Library/PrivateFrameworks/FontServices.framework/Versions/A/Resources/Reserved",
)
_MAC_FONT_ASSETS = ("/System/Library/AssetsV2", "com_apple_MobileAsset_Font*/*/AssetData")


def _manager(args: argparse.Namespace) -> BinaryManager:
    return BinaryManager(cache_dir=args.cache_dir, manifest=args.manifest)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apostate", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version",
                        version=f"apostate {PACKAGE_VERSION} (Chromium {CHROMIUM_VERSION})")
    parser.add_argument("--cache-dir", help="override the install cache directory")
    parser.add_argument("--manifest", help="release manifest path, URL, or JSON file")
    parser.add_argument("--target", help="platform target; defaults to this host")
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="download, verify and extract the browser")
    install.add_argument("--force", action="store_true", help="reinstall even when cached")
    install.add_argument("--keep-archive", action="store_true",
                         help="retain the verified archive for `gh attestation verify`")

    commands.add_parser("path", help="print the browser executable path, finding or installing it")
    commands.add_parser("info", help="print install, discovery and manifest state as JSON")
    commands.add_parser("clear", help="delete the install cache")

    drm = commands.add_parser(
        "provision-drm",
        help="install a Widevine CDM now, from --source or found or fetched as a launch would",
    )
    drm.add_argument("--source", help="a WidevineCdm directory to install")
    drm.add_argument("--list", action="store_true", help="list the CDMs on this machine and exit")

    fonts = commands.add_parser("fonts", help="install the system fonts a persona lists")
    font_commands = fonts.add_subparsers(dest="fonts_command", required=True)
    font_install = font_commands.add_parser("install", help="install the Windows or macOS font set")
    font_install.add_argument("platform", choices=("windows", "macos"))
    font_install.add_argument("--from", dest="source", metavar="DIR",
                              help="windows: a Windows Fonts folder to take the core families "
                                   "from instead of cloning; macos: the directory "
                                   "`fonts export-macos` wrote on a Mac")
    export = font_commands.add_parser("export-macos",
                                      help="on a Mac, copy its system fonts into a directory")
    export.add_argument("directory")

    run = commands.add_parser("run", help="run the browser, forwarding any remaining arguments")
    run.add_argument("browser_args", nargs=argparse.REMAINDER,
                     help="arguments passed straight to the browser")
    return parser


def _font_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES)


def _mac_font_files() -> list[Path]:
    """The font files a Mac keeps its own fonts in."""
    root, pattern = _MAC_FONT_ASSETS
    found = [path for directory in _MAC_FONT_DIRS for path in _font_files(Path(directory))]
    found += sorted(path for path in Path(root).glob(pattern + "/*")
                    if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES)
    return found


def _font_directory(name: str) -> Path:
    """This user's font directory for the *name* font set."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Fonts" / f"apostate-{name}"
    return Path.home() / ".local" / "share" / "fonts" / f"apostate-{name}"


def _install_fonts(files: list[Path], name: str) -> None:
    """Copy *files* into this user's font directory for *name* and refresh the cache."""
    if not files:
        raise ApostateError("no .ttf, .ttc or .otf files were found")
    destination = _font_directory(name)
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copyfile(path, destination / path.name)
    if sys.platform != "darwin":
        try:
            subprocess.run(["fc-cache", "-f"], check=True)
        except FileNotFoundError:
            raise ApostateError("fc-cache is not installed; install fontconfig") from None
        except subprocess.CalledProcessError as exc:
            raise ApostateError(f"fc-cache -f failed with exit code {exc.returncode}") from None
    print(f"installed {len(files)} font files into {destination}")


def _font_families(path: Path, name_ids: tuple[int, ...] = (1,)) -> set[str]:
    """The English family names of every face in a .ttf, .otf or .ttc file.

    Name ID 1 is the family Windows lists a face under: ARIALN.TTF is Arial
    Narrow there, although its typographic family (ID 16) is Arial. Names come
    from a face's Windows records, as Windows reads them, and from its
    Macintosh records only when it has none: Candaral.ttf is Candara Light on
    Windows and Candara in its Macintosh record. A file that cannot be read
    has no families.
    """
    names: set[str] = set()
    try:
        with path.open("rb") as font:
            end = os.fstat(font.fileno()).st_size

            def read(offset: int, size: int) -> bytes:
                if offset + size > end:
                    raise ValueError("truncated font file")
                font.seek(offset)
                return font.read(size)

            faces: tuple[int, ...] = (0,)
            if read(0, 4) == b"ttcf":
                (count,) = struct.unpack(">I", read(8, 4))
                faces = struct.unpack(f">{count}I", read(12, 4 * count))
            for face in faces:
                (tables,) = struct.unpack(">H", read(face + 4, 2))
                for index in range(tables):
                    tag, _, table, _ = struct.unpack(">4sIII", read(face + 12 + 16 * index, 16))
                    if tag == b"name":
                        break
                else:
                    continue
                _, records, storage = struct.unpack(">HHH", read(table, 6))
                windows: set[str] = set()
                mac: set[str] = set()
                for index in range(records):
                    platform, _, language, name_id, length, start = struct.unpack(
                        ">6H", read(table + 6 + 12 * index, 12))
                    if name_id not in name_ids:
                        continue
                    if platform == 3 and language & 0xFF == 0x09:  # Windows, English
                        raw = read(table + storage + start, length)
                        windows.add(raw.decode("utf-16-be", "replace"))
                    elif platform == 1 and language == 0:  # Macintosh, English
                        raw = read(table + storage + start, length)
                        mac.add(raw.decode("mac_roman", "replace"))
                names |= windows or mac
    except (OSError, ValueError, struct.error):
        return set()
    return names


def _windows_core_families() -> set[str]:
    """The families of the Windows core font pack, as this package ships it."""
    try:
        packs = json.loads(importlib.resources.files("apostate")
                           .joinpath("assets", "font_packs.json").read_text(encoding="utf-8"))
        families = {family
                    for option_set in packs["option_sets"]
                    if option_set["key"]["platform"] == "windows"
                    for option in option_set["options"] if option["pack_kind"] == "core"
                    for family in option["value"]["fonts"]["enumeration_allowlist"]}
    except (ModuleNotFoundError, OSError, TypeError, KeyError, ValueError) as exc:
        raise ApostateError("the font packs this package ships are unreadable") from exc
    if not families:
        raise ApostateError("the font packs this package ships list no Windows core families")
    return families


def _host_families() -> set[str] | None:
    """Every family name this host's fonts answer to, lowercased; None if unknown."""
    if sys.platform == "darwin":
        files = _mac_font_files()
        files += [path for path in (Path.home() / "Library" / "Fonts").rglob("*")
                  if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES]
        return {name.lower() for path in files for name in _font_families(path, (1, 16))}
    try:
        listing = subprocess.run(["fc-list", "--format", "%{family}\n"], check=True,
                                 capture_output=True, text=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return {name.strip().lower() for line in listing.splitlines() for name in line.split(",")}


def _install_windows_fonts(files: list[Path]) -> None:
    """Install the files of the Windows core families, and only those."""
    if not files:
        raise ApostateError("no .ttf, .ttc or .otf files were found")
    core = _windows_core_families()
    wanted = {family.lower() for family in core}

    def is_core(path: Path) -> bool:
        return any(family.lower() in wanted for family in _font_families(path))

    selected = [path for path in files if is_core(path)]
    if not selected:
        raise ApostateError("none of the font files is in a core Windows family")
    # Earlier versions installed the whole repository. Files outside the core
    # set come out again, so the host holds the families a persona lists.
    destination = _font_directory("windows")
    stale = [path for path in _font_files(destination) if not is_core(path)]
    for path in stale:
        path.unlink()
    if stale:
        print(f"removed {len(stale)} font files outside the core Windows set from {destination}")
    _install_fonts(selected, "windows")

    present = _host_families()
    if present is None:
        return
    missing = sorted(family for family in core if family.lower() not in present)
    if not missing:
        print("every core Windows family is installed")
        return
    print(f"still missing {len(missing)} core Windows families: {', '.join(missing)}")
    print("add them from a Windows Fonts folder with `fonts install windows --from DIR`")


def _fonts(args: argparse.Namespace) -> int:
    if args.fonts_command == "export-macos":
        if sys.platform != "darwin":
            raise ApostateError("fonts export-macos runs on a Mac")
        found = _mac_font_files()
        # One file per name; the directory listed first wins.
        files: dict[str, Path] = {}
        for path in found:
            files.setdefault(path.name, path)
        destination = Path(args.directory).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        for name, path in files.items():
            shutil.copyfile(path, destination / name)
        print(f"copied {len(files)} font files into {destination}")
        return 0
    if args.platform == "windows":
        if os.name == "nt":
            print("this is a Windows machine; its fonts are already installed")
            return 0
        if args.source:
            _install_windows_fonts(_font_files(Path(args.source).expanduser()))
            return 0
        with tempfile.TemporaryDirectory(prefix="apostate-fonts-") as scratch:
            clone = Path(scratch) / "windows-11-fonts"
            try:
                subprocess.run(["git", "clone", "--depth", "1", WINDOWS_FONTS, str(clone)],
                               check=True)
            except FileNotFoundError:
                raise ApostateError("git is not installed; install it and try again") from None
            except subprocess.CalledProcessError as exc:
                raise ApostateError(f"git clone {WINDOWS_FONTS} failed with exit code "
                                    f"{exc.returncode}") from None
            _install_windows_fonts(_font_files(clone / "w11-fonts"))
        return 0
    if sys.platform == "darwin":
        print("this is a Mac; its fonts are already installed")
        return 0
    if os.name == "nt":
        raise ApostateError("installing macOS fonts on Windows is not supported")
    if not args.source:
        raise ApostateError("pass --from <dir>, a directory `apostate fonts export-macos` "
                            "wrote on a Mac")
    _install_fonts(_font_files(Path(args.source).expanduser()), "macos")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "clear":
            _manager(args).clear()
            return 0
        if args.command == "info":
            print(json.dumps(_manager(args).info(target=args.target),
                             indent=2, sort_keys=True))
            return 0
        if args.command == "install":
            executable = _manager(args).ensure(target=args.target, force=args.force,
                                               keep_archive=args.keep_archive or None)
            ensure_widevine(executable, target=args.target, cache_dir=args.cache_dir)
            print(executable)
            return 0
        if args.command == "path":
            print(_manager(args).ensure(target=args.target))
            return 0
        if args.command == "fonts":
            return _fonts(args)
        if args.command == "provision-drm":
            from .widevine import discover, platform_is_verified, provision
            target = target_platform(args.target)
            if args.list:
                found = discover(target)
                if not found:
                    print("no Widevine CDM found on this machine")
                    return 1
                for item in found:
                    print(f"{item['version'] or 'unknown':<14} {item['bytes']:>10}  {item['path']}")
                return 0
            result = provision(target=target, source=args.source, cache_dir=args.cache_dir)
            print(json.dumps(result, indent=2, sort_keys=True))
            if not platform_is_verified(target):
                print(f"warning: Widevine has not been verified on {target} yet", file=sys.stderr)
            return 0
        executable = _manager(args).ensure(target=args.target)
        forwarded = [item for item in args.browser_args if item != "--"]
        profile = next((item.partition("=")[2] for item in forwarded
                        if item.startswith("--user-data-dir=")), None)
        ensure_widevine(executable, target=args.target, cache_dir=args.cache_dir,
                        user_data_dir=profile)
        # exec rather than spawn: signals, exit code and the terminal belong to
        # the browser, and this process has nothing left to do.
        if os.name == "posix":
            os.execv(str(executable), [str(executable), *forwarded])
        return subprocess.run([str(executable), *forwarded], check=False).returncode
    except ApostateError as exc:
        print(f"apostate: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
