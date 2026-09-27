"""Sign up on a site under several personas, seeds and host mode, and compare what it saw.

With no arguments this serves signup_site.py on 127.0.0.1, submits its form
once per run and prints what the backend recorded. With --url it opens your
own page instead, tagged ?run=<name>, and prints what the browser presented,
so you can find each visit in your logs and your detection vendor's dashboard.
"""

import argparse
import json
import random
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from apostate import launch
from signup_site import LOG, PROBE, start

RUNS = {
    "windows-42": {"fingerprint": 42, "fingerprint_platform": "windows"},
    "windows-7": {"fingerprint": 7, "fingerprint_platform": "windows"},
    "macos-42": {"fingerprint": 42, "fingerprint_platform": "macos"},
    "linux-42": {"fingerprint": 42, "fingerprint_platform": "linux"},
    "host": {"fingerprint": "host"},
}

# One region for every run and no GeoIP lookup. Behind a proxy, leave these
# out and the package sets them from the proxy's exit.
REGION = {"locale": "en-US", "timezone": "America/New_York", "geoip": False}


def tagged(url, run):
    parts = urlsplit(url)
    query = "&".join(filter(None, [parts.query, urlencode({"run": run})]))
    return urlunsplit(parts._replace(query=query))


def os_of_user_agent(user_agent):
    for token, name in (("Windows NT", "Windows"), ("Macintosh", "macOS"), ("Linux", "Linux")):
        if token in user_agent:
            return name
    return "other"


def os_of_platform(platform):
    for prefix, name in (("Win", "Windows"), ("Mac", "macOS"), ("Linux", "Linux")):
        if platform.startswith(prefix):
            return name
    return "other"


def checks(headers, probe):
    """The cross-checks a backend can run on one request. Returns the ones that failed."""
    failed = []
    if headers["User-Agent"] != probe["userAgent"]:
        failed.append("User-Agent header differs from navigator.userAgent")
    if (headers["Sec-CH-UA-Platform"] or "").strip('"') != probe["uaDataPlatform"]:
        failed.append("Sec-CH-UA-Platform differs from userAgentData.platform")
    if os_of_user_agent(probe["userAgent"]) != os_of_platform(probe["platform"]):
        failed.append("User-Agent OS differs from navigator.platform")
    if "HeadlessChrome" in headers["User-Agent"]:
        failed.append("HeadlessChrome in User-Agent")
    if probe["webdriver"]:
        failed.append("navigator.webdriver is true")
    if probe["screen"] == [800, 600, 800, 600]:
        failed.append("800x600 screen with no taskbar, the headless default")
    return failed


def sign_up(url, run, options):
    with launch(**options, **REGION) as browser:
        page = browser.new_page()
        page.goto(tagged(url, run))
        page.fill("#email", f"{run}@example.test")
        page.click("button[type=submit]")
        page.wait_for_selector("#result:not(:empty)")


def visit(url, run, options):
    with launch(**options, **REGION) as browser:
        page = browser.new_page()
        page.goto(tagged(url, run), wait_until="networkidle")
        return page.evaluate(PROBE)


def print_table(rows):
    columns = ["run", "User-Agent OS", "Sec-CH-UA-Platform", "navigator.platform",
               "cores", "memory", "screen", "checks"]
    widths = [max(len(str(row[i])) for row in [columns, *rows]) for i in range(len(columns))]
    for row in [columns, *rows]:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


def local_site():
    LOG.unlink(missing_ok=True)
    server = start()
    url = f"http://127.0.0.1:{server.server_port}/"
    for run, options in RUNS.items():
        sign_up(url, run, options)
    server.shutdown()

    rows, renderers = [], []
    for line in LOG.read_text().splitlines():
        record = json.loads(line)
        headers, probe = record["headers"], record["probe"]
        run = parse_qs(urlsplit(record["path"]).query)["run"][0]
        width, height, _, avail_height = probe["screen"]
        failed = checks(headers, probe)
        rows.append([run, os_of_user_agent(headers["User-Agent"]), headers["Sec-CH-UA-Platform"],
                     probe["platform"], probe["cores"], probe["memory"],
                     f"{width}x{height} avail {avail_height}", "; ".join(failed) or "pass"])
        renderers.append((run, probe["webglRenderer"]))
    print_table(rows)
    print()
    for run, renderer in renderers:
        print(f"{run:11} {renderer}")
    print(f"\nFull records: {LOG.parent.name}/{LOG.name}")


def your_site(url, delay):
    for index, (run, options) in enumerate(RUNS.items()):
        if index:
            time.sleep(delay + random.uniform(0, delay / 2))
        probe = visit(url, run, options)
        print(f"{datetime.now(timezone.utc):%H:%M:%S} UTC  {tagged(url, run)}")
        print(f"    {probe['platform']}, {probe['cores']} cores, {probe['memory']} GB, "
              f"{probe['timeZone']}, {probe['webglRenderer']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="your own page, for example a staging signup page")
    parser.add_argument("--delay", type=float, default=10, help="seconds between runs with --url")
    arguments = parser.parse_args()
    if arguments.url:
        your_site(arguments.url, arguments.delay)
    else:
        local_site()
