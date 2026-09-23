#!/usr/bin/env python3
"""Measure one launch on FingerprintJS Pro's public playground.

usage: measure-fpjs.py [--seed N] [--platform windows|macos|linux|host]
                       [--profile FILE] [--out FILE] [-- extra chrome args]

APOSTATE_PROXY must name a residential proxy (socks5://user:pass@host:port);
use a fresh session id per run, or FPJS sees a returning visitor. The exit's
timezone and locale are looked up through the proxy and passed to the launch,
so the timezone-mismatch signal measures the browser and not the harness.
APOSTATE_BINARY selects a local build. Prints the suspect score and every
flag that feeds it.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from apostate import launch_persistent_context  # noqa: E402
from apostate.config import country_locales  # noqa: E402

FLAGS = ("suspect_score", "tampering", "anti_detect_browser", "anomaly_score",
         "virtual_machine", "rare_device", "developer_tools", "incognito", "vpn",
         "timezone_mismatch", "proxy", "proxy_confidence", "bot", "os")


def exit_info(proxy: str) -> dict:
    out = subprocess.run(
        ["curl", "-s", "--max-time", "25", "--proxy", proxy.replace("socks5://", "socks5h://"),
         "http://ip-api.com/json/?fields=query,countryCode,timezone"],
        capture_output=True, text=True).stdout
    return json.loads(out or "{}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", default="42")
    ap.add_argument("--platform", default="windows")
    ap.add_argument("--profile")
    ap.add_argument("--out")
    ap.add_argument("--window", default="1536,864")
    ap.add_argument("extra", nargs="*")
    args = ap.parse_args()

    proxy = os.environ.get("APOSTATE_PROXY")
    if not proxy:
        ap.error("set APOSTATE_PROXY")
    info = exit_info(proxy)
    kw = dict(headless=False, geoip=False, proxy=proxy, timezone=info.get("timezone"),
              locale=country_locales().get(info.get("countryCode", ""), "en-US"),
              args=[f"--window-size={args.window}", "--window-position=0,0", *args.extra])
    if args.profile:
        kw["profile"] = args.profile
    elif args.platform == "host":
        kw["fingerprint"] = "host"
    else:
        kw.update(fingerprint=args.seed, fingerprint_platform=args.platform)

    ctx = launch_persistent_context(tempfile.mkdtemp(prefix="apostate-fpjs-"), **kw)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://demo.fingerprint.com/playground", timeout=90000)
        box = 'div[class*="playground_jsonContainer"]'
        page.wait_for_function(
            f"() => document.querySelector('{box}')?.innerText.includes('raw_device_attributes')",
            timeout=90000)
        text = page.locator(box).first.inner_text()
    finally:
        ctx.close()
    if args.out:
        Path(args.out).write_text(text)

    print(f"exit {info.get('query')} {info.get('countryCode')} {info.get('timezone')}")
    for key in FLAGS:
        match = re.search(rf"^{key}: (.*)$", text, re.M)
        print(f"  {key:22} {match.group(1) if match else '?'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
