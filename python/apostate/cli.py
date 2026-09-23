"""``python -m apostate`` -- install and run the browser from a terminal.

Get the browser, find out where it is, run it, throw it away, and install the
system fonts a persona lists.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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
                              help="macos: the directory `fonts export-macos` wrote on a Mac")
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


def _install_fonts(files: list[Path], name: str) -> None:
    """Copy *files* into this user's font directory for *name* and refresh the cache."""
    if not files:
        raise ApostateError("no .ttf, .ttc or .otf files were found")
    if sys.platform == "darwin":
        destination = Path.home() / "Library" / "Fonts" / f"apostate-{name}"
    else:
        destination = Path.home() / ".local" / "share" / "fonts" / f"apostate-{name}"
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


def _fonts(args: argparse.Namespace) -> int:
    if args.fonts_command == "export-macos":
        if sys.platform != "darwin":
            raise ApostateError("fonts export-macos runs on a Mac")
        root, pattern = _MAC_FONT_ASSETS
        found = [path for directory in _MAC_FONT_DIRS for path in _font_files(Path(directory))]
        found += sorted(path for path in Path(root).glob(pattern + "/*")
                        if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES)
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
            _install_fonts(_font_files(clone / "w11-fonts"), "windows")
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
