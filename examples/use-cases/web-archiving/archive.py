"""Capture pages as a visitor sees them, with a manifest that records how and when.

Each capture goes into out/<host>-<UTC time>/ and holds a full-page
screenshot, a PDF, the HTML of the rendered page, a HAR file of every request
and response, and manifest.json with the SHA-256 of each file. With
APOSTATE_PROXY set, the pages are opened through that proxy.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from apostate import launch

OUT = Path(__file__).resolve().parent / "out"
SEED = 42
PERSONA = "windows"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def capture(url, locale, zone):
    taken = datetime.now(timezone.utc)
    folder = OUT / f"{urlsplit(url).hostname}-{taken:%Y%m%dT%H%M%SZ}"
    folder.mkdir(parents=True)
    proxy = os.environ.get("APOSTATE_PROXY")
    # Behind a proxy, the package sets locale and timezone from the proxy's exit.
    region = {} if proxy else {"locale": locale, "timezone": zone, "geoip": False}
    with launch(fingerprint=SEED, fingerprint_platform=PERSONA, proxy=proxy, **region,
                record_har_path=folder / "page.har") as browser:
        page = browser.new_page()
        response = page.goto(url, wait_until="networkidle")
        page.screenshot(path=folder / "screenshot.png", full_page=True)
        # page.pdf() works only in a headless browser, which is the default.
        page.pdf(path=folder / "page.pdf", format="A4", print_background=True)
        (folder / "page.html").write_text(page.content(), encoding="utf-8")
        seen = page.evaluate("""() => ({
            userAgent: navigator.userAgent,
            language: navigator.language,
            timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        })""")
        details = {"final_url": page.url, "status": response.status, "title": page.title(),
                   "browser": browser.version}
    # The HAR file is complete once the browser has closed.
    manifest = {
        "url": url,
        **details,
        "captured_at": taken.isoformat(timespec="seconds"),
        "persona": PERSONA,
        "seed": SEED,
        "apostate": version("apostate"),
        "proxy": bool(proxy),
        **seen,
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
                  for path in sorted(folder.iterdir())},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return folder, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("urls", nargs="*", default=["https://example.com"])
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--timezone", default="America/New_York")
    arguments = parser.parse_args()
    for url in arguments.urls:
        folder, manifest = capture(url, arguments.locale, arguments.timezone)
        print(f"{manifest['url']}  {manifest['status']}  {manifest['title']}")
        print(f"    {folder.parent.name}/{folder.name}/")
        for name, entry in manifest["files"].items():
            print(f"    {name:15} {entry['bytes']:>8} bytes  sha256 {entry['sha256'][:16]}...")


if __name__ == "__main__":
    main()
