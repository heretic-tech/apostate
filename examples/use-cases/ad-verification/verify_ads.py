"""Open an ad link as visitors from several regions and desktops, and record where it leads.

For each region and persona this prints every main-frame response from the
first request to the final page, HTTP redirects and script redirects alike,
and saves a screenshot of the final page.

With a URL, each region goes through the proxy in its variable
(APOSTATE_PROXY_US, APOSTATE_PROXY_DE, APOSTATE_PROXY_FR), and a region whose
variable is not set is skipped. With no URL, this runs against a local ad
server that picks the landing page from Accept-Language and the platform.
"""

import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from apostate import launch

OUT = Path(__file__).resolve().parent / "out"

# Region, the locale and timezone the local demo uses for it, and its proxy variable.
REGIONS = [
    ("US", "en-US", "America/New_York", "APOSTATE_PROXY_US"),
    ("DE", "de-DE", "Europe/Berlin", "APOSTATE_PROXY_DE"),
    ("FR", "fr-FR", "Europe/Paris", "APOSTATE_PROXY_FR"),
]
PERSONAS = ["windows", "macos"]
DEMO_PORT = 8766


class DemoAdServer(BaseHTTPRequestHandler):
    """/click redirects to /track, which redirects to a landing page in the visitor's language."""

    def do_GET(self):
        path, query = urlsplit(self.path).path, parse_qs(urlsplit(self.path).query)
        campaign = query.get("campaign", ["none"])[0]
        if path == "/click":
            self.redirect(f"/track?campaign={campaign}")
        elif path == "/track":
            language = self.headers.get("Accept-Language", "en")[:2]
            self.redirect(f"/landing/{language if language in ('de', 'fr') else 'en'}?campaign={campaign}")
        elif path.startswith("/landing/"):
            # Mac visitors are sent on by the page's script, as download pages often do.
            body = (f"<!doctype html><title>Landing {path}</title><h1>Offer for {path}</h1>"
                    "<script>if (navigator.platform.startsWith('Mac') && !location.pathname.endsWith('/mac'))"
                    " location.replace(location.pathname + '/mac' + location.search);</script>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def follow(url, persona, options, screenshot):
    """Open url and return the main-frame responses in order, the final URL and the page's region."""
    chain = []
    with launch(fingerprint=42, fingerprint_platform=persona, **options) as browser:
        page = browser.new_page()
        page.on("response", lambda response: chain.append((response.status, response.url))
                if response.request.is_navigation_request() and response.frame == page.main_frame
                else None)
        page.goto(url)
        page.wait_for_load_state("networkidle")
        region = page.evaluate("[Intl.DateTimeFormat().resolvedOptions().timeZone, navigator.language]")
        page.screenshot(path=screenshot, full_page=True)
        return chain, page.url, region


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", nargs="?", help="the ad or tracking link; a local demo when omitted")
    url = parser.parse_args().url
    server = None
    if url is None:
        server = ThreadingHTTPServer(("127.0.0.1", DEMO_PORT), DemoAdServer)
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{DEMO_PORT}/click?campaign=spring"
    OUT.mkdir(exist_ok=True)

    for region, locale, timezone, variable in REGIONS:
        proxy = os.environ.get(variable)
        if server is not None:
            # The local demo has no proxies, so each region is its locale and timezone.
            options = {"locale": locale, "timezone": timezone, "geoip": False}
        elif proxy:
            # Through a proxy the package sets locale and timezone from the exit.
            options = {"proxy": proxy}
        else:
            print(f"{region}  skipped: {variable} is not set")
            continue
        for persona in PERSONAS:
            screenshot = OUT / f"{region}-{persona}.png"
            chain, final, (zone, language) = follow(url, persona, options, screenshot)
            print(f"{region}  {persona}  {zone}  {language}")
            for status, address in chain:
                print(f"    {status}  {address}")
            print(f"    final {final}")
            print(f"    screenshot {OUT.name}/{screenshot.name}")

    if server is not None:
        server.shutdown()


if __name__ == "__main__":
    main()
