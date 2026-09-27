import subprocess
import tempfile
import time
from pathlib import Path

from apostate import ensure_binary
from patchright.sync_api import sync_playwright

# The installed browser, downloaded first if it is missing.
binary = ensure_binary()

with tempfile.TemporaryDirectory() as user_data_dir:
    browser_process = subprocess.Popen(
        [
            str(binary),
            "--fingerprint=42",
            "--fingerprint-platform=windows",
            "--fingerprint-locale=en-US",
            "--fingerprint-timezone=America/New_York",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={user_data_dir}",
            # Port 0 picks a free port on 127.0.0.1 and writes it to DevToolsActivePort.
            "--remote-debugging-port=0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port_file = Path(user_data_dir, "DevToolsActivePort")
        deadline = time.monotonic() + 30
        while not port_file.is_file() or not port_file.read_text().strip():
            if time.monotonic() > deadline:
                raise TimeoutError("the browser did not open its DevTools port")
            time.sleep(0.1)
        port = port_file.read_text().split()[0]

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            page = browser.contexts[0].new_page()
            page.goto("https://example.com")
            print("platform ", page.evaluate("navigator.platform"))
            print("userAgent", page.evaluate("navigator.userAgent"))
            print("timeZone ", page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone"))
            browser.close()
    finally:
        browser_process.terminate()
        browser_process.wait()
