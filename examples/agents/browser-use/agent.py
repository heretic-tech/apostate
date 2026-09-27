"""browser-use driving an Apostate browser over CDP.

The script starts Apostate with a Windows persona and a DevTools port on
127.0.0.1, then hands browser-use the endpoint. Starting the browser here,
rather than letting browser-use launch it, keeps browser-use's default
switches out: they turn off speech synthesis, which a Windows machine has.

Needs `pip install apostate browser-use` and ANTHROPIC_API_KEY.

    python3 agent.py "Open https://example.com and say what the page is for."
    python3 agent.py --check      # start and connect, no model call
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")

from apostate import ensure_binary  # noqa: E402
from browser_use import Agent, Browser, ChatAnthropic  # noqa: E402

PROFILE = os.path.abspath("profiles/browser-use")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_apostate(port: int) -> subprocess.Popen:
    """Apostate with a Windows persona, a fixed locale and timezone, and a DevTools port."""
    env = dict(os.environ)
    # What the Python package would set: the locale environment, and no
    # "Google API keys are missing" bar on the first tab.
    env.update(LANGUAGE="en-US", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", LC_MESSAGES="en_US.UTF-8",
               GOOGLE_API_KEY="no", GOOGLE_DEFAULT_CLIENT_ID="no", GOOGLE_DEFAULT_CLIENT_SECRET="no")
    args = [
        str(ensure_binary()),
        f"--user-data-dir={PROFILE}",
        "--fingerprint-platform=windows",
        "--fingerprint-locale=en-US",
        "--fingerprint-timezone=America/New_York",
        f"--remote-debugging-port={port}",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    process = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read()
            return process
        except OSError:
            time.sleep(0.2)
    process.kill()
    raise RuntimeError("Apostate did not open its DevTools port")


def connect(port: int) -> Browser:
    return Browser(
        cdp_url=f"http://127.0.0.1:{port}",
        headless=False,           # headless here means viewport emulation; the browser is already headless
        no_viewport=True,         # keep the persona's window size
        permissions=[],           # do not grant clipboard and notifications to every site
        highlight_elements=False,  # no overlays injected into pages
        keep_alive=True,
    )


async def check(browser: Browser) -> None:
    await browser.start()
    await browser.navigate_to("https://example.com")
    print("title", await browser.get_current_page_title())
    print("url", await browser.get_current_page_url())
    await browser.stop()


async def run(browser: Browser, task: str) -> None:
    agent = Agent(task=task, llm=ChatAnthropic(model="claude-opus-5"), browser=browser)
    history = await agent.run(max_steps=25)
    print(history.final_result())


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    if sys.argv[1] != "--check" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first.")
        return 2
    os.makedirs(PROFILE, exist_ok=True)
    port = free_port()
    process = start_apostate(port)
    try:
        browser = connect(port)
        asyncio.run(check(browser) if sys.argv[1] == "--check" else run(browser, sys.argv[1]))
    finally:
        process.terminate()
        process.wait(timeout=15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
