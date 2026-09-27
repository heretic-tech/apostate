"""Read a value from public product pages, store it, and print what changed since the last run.

Each site gets its own persistent profile, so it keeps the same machine and
cookies from run to run. Pages that the site's robots.txt disallows are
skipped, and page loads on one site are spaced by a delay with jitter.

With no --watch arguments this runs against demo_shop.py on 127.0.0.1.
"""

import argparse
import os
import random
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from apostate import launch_persistent_context
from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

HERE = Path(__file__).resolve().parent
DATABASE = HERE / "prices.db"
PROFILES = HERE / "profiles"


def robots_rules(page, origin):
    """The site's robots.txt, fetched in the site's own browser profile."""
    rules = RobotFileParser()
    response = page.goto(f"{origin}/robots.txt")
    rules.parse(response.text().splitlines() if response and response.ok else [])
    return rules


def read_value(page, url, selector):
    page.goto(url)
    try:
        return page.locator(selector).first.inner_text(timeout=10_000).strip()
    except PlaywrightTimeoutError:
        return "(not found)"


def record(database, url, selector, value):
    """Store the value and return the one stored before it, if any."""
    previous = database.execute(
        "SELECT value FROM observations WHERE url = ? AND selector = ? ORDER BY id DESC LIMIT 1",
        (url, selector)).fetchone()
    database.execute("INSERT INTO observations (url, selector, value, seen_at) VALUES (?, ?, ?, ?)",
                     (url, selector, value, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    database.commit()
    return previous[0] if previous else None


def watch_site(host, items, database, arguments):
    profile = PROFILES / host
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(items[0][0]))
    with launch_persistent_context(profile, fingerprint_platform="windows",
                                   locale=arguments.locale, timezone=arguments.timezone, geoip=False,
                                   proxy=os.environ.get("APOSTATE_PROXY")) as context:
        seed = (profile / "apostate" / "identity").read_text().strip()
        print(f"{host}  profile {profile.relative_to(HERE)}, seed {seed[:12]}...")
        page = context.new_page()
        rules = robots_rules(page, origin)
        delay = max(arguments.delay, rules.crawl_delay("*") or 0)
        for index, (url, selector) in enumerate(items):
            if not rules.can_fetch("*", url):
                print(f"    {'skipped   disallowed by robots.txt':36} {urlsplit(url).path}")
                continue
            if index:
                time.sleep(delay + random.uniform(0, delay / 2))
            value = read_value(page, url, selector)
            previous = record(database, url, selector, value)
            if previous is None:
                status = f"new       {value}"
            elif previous == value:
                status = f"same      {value}"
            else:
                status = f"changed   {previous} -> {value}"
            print(f"    {status:36} {urlsplit(url).path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--watch", nargs=2, action="append", metavar=("URL", "SELECTOR"),
                        help="a page and the CSS selector of the value to read; repeat for more pages")
    parser.add_argument("--delay", type=float, default=30,
                        help="seconds between page loads on one site, before jitter (default 30)")
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--timezone", default="America/New_York")
    arguments = parser.parse_args()

    server = None
    if not arguments.watch:
        from demo_shop import start
        server = start()
        base = "http://127.0.0.1:8765"
        arguments.watch = [(f"{base}/product/{name}", ".price") for name in ("kettle", "toaster", "lamp")]
        arguments.watch.append((f"{base}/cart", ".total"))
        arguments.delay = 0

    database = sqlite3.connect(DATABASE)
    database.execute("CREATE TABLE IF NOT EXISTS observations "
                     "(id INTEGER PRIMARY KEY, url TEXT, selector TEXT, value TEXT, seen_at TEXT)")
    sites = defaultdict(list)
    for url, selector in arguments.watch:
        sites[urlsplit(url).hostname].append((url, selector))
    for host, items in sites.items():
        watch_site(host, items, database, arguments)
    database.close()
    if server is not None:
        server.shutdown()


if __name__ == "__main__":
    main()
