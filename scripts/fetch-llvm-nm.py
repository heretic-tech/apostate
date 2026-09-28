#!/usr/bin/env python3
"""Put Chromium's pinned llvm-nm into the checkout's llvm-build on a host that lacks it.

The clang package gclient installs carries llvm-nm on Linux only. Chromium pins
llvm-nm for the other hosts inside its llvmobjdump package, but DEPS fetches
that package on Windows only for checkouts that also target Linux, macOS or
Android, so a windows-x64 checkout has none. The symbol-closure phase of
scripts/checkseries.sh needs it.

This reads the package's object name, bucket and sha256 from the checkout's own
DEPS, so it is pinned by the Chromium revision exactly as gclient's copy would
be, checks the digest, and extracts llvm-nm alone.

Usage: fetch-llvm-nm.py <chromium src>
Prints the path of the llvm-nm binary.
"""

import hashlib
import io
import platform
import sys
import tarfile
import urllib.request
from pathlib import Path

DEP = "src/third_party/llvm-build/Release+Asserts"
HOST_PREFIX = {"win32": "Win/", "darwin": "Mac"}


def package(src):
    scope = {"Var": lambda name: "{%s}" % name, "Str": str}
    exec((src / "DEPS").read_text(), scope)
    dep = scope["deps"][DEP]
    prefix = HOST_PREFIX.get(sys.platform, "Linux_x64/")
    for obj in dep["objects"]:
        name = obj["object_name"]
        if name.startswith(prefix) and "/llvmobjdump-" in name:
            if sys.platform == "darwin" and (
                name.startswith("Mac_arm64/") != (platform.machine() == "arm64")
            ):
                continue
            return dep["bucket"], name, obj["sha256sum"]
    raise SystemExit(f"error: {src}/DEPS names no llvmobjdump package for {sys.platform}")


def main(argv):
    if len(argv) != 1:
        raise SystemExit("usage: fetch-llvm-nm.py <chromium src>")
    src = Path(argv[0])
    exe = ".exe" if sys.platform == "win32" else ""
    nm = src / "third_party/llvm-build/Release+Asserts/bin" / f"llvm-nm{exe}"
    if nm.is_file():
        print(nm)
        return 0
    bucket, name, want = package(src)
    url = f"https://storage.googleapis.com/{bucket}/{name}"
    print(f"fetching {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise SystemExit(f"error: {name} sha256 {got}, DEPS pins {want}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tar:
        member = tar.getmember(f"bin/llvm-nm{exe}")
        nm.parent.mkdir(parents=True, exist_ok=True)
        nm.write_bytes(tar.extractfile(member).read())
    nm.chmod(0o755)
    print(nm)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
