"""``python -m apostate`` -- install and run the browser from a terminal.

Four verbs, because they are the four things a terminal is for here: get the
browser, find out where it is, run it, and throw it away.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from .binary import BinaryManager, target_platform
from .config import CHROMIUM_VERSION, PACKAGE_VERSION
from .errors import ApostateError


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
        help="install a Widevine CDM already on this machine so DRM works on ephemeral profiles",
    )
    drm.add_argument("--source", help="a WidevineCdm directory; omitted, this machine is searched")
    drm.add_argument("--list", action="store_true", help="list what was found and exit")

    run = commands.add_parser("run", help="run the browser, forwarding any remaining arguments")
    run.add_argument("browser_args", nargs=argparse.REMAINDER,
                     help="arguments passed straight to the browser")
    return parser


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
            print(executable)
            return 0
        if args.command == "path":
            print(_manager(args).ensure(target=args.target))
            return 0
        if args.command == "provision-drm":
            from .binary import target_platform
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
                print(f"warning: Widevine provisioning is unverified on {target}; only "
                      "macos-arm64 has been exercised end to end", file=sys.stderr)
            return 0 if result.get("installed") else 1
        executable = _manager(args).ensure(target=args.target)
        forwarded = [item for item in args.browser_args if item != "--"]
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
