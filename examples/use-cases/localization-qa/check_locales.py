"""Open a page in several locales and timezones, save a screenshot of each and print what the page sees.

With no arguments this serves a local page that picks its language from the
Accept-Language header and shows Intl output. Pass a URL to check your own
site. A region whose proxy variable is set (APOSTATE_PROXY_DE and so on) is
opened through that proxy.
"""

import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from apostate import launch

OUT = Path(__file__).resolve().parent / "out"

# Locale, timezone and the variable that may hold a proxy in that region.
REGIONS = [
    ("en-US", "America/New_York", "APOSTATE_PROXY_US"),
    ("de-DE", "Europe/Berlin", "APOSTATE_PROXY_DE"),
    ("ja-JP", "Asia/Tokyo", "APOSTATE_PROXY_JP"),
    ("ar-EG", "Africa/Cairo", "APOSTATE_PROXY_EG"),
]

READ = """() => {
    const instant = new Date(Date.UTC(2026, 0, 15, 12, 0));
    return {
        languages: navigator.languages.join(","),
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        htmlLang: document.documentElement.lang,
        date: instant.toLocaleString(undefined, {dateStyle: "full", timeStyle: "short"}),
        number: (1234567.891).toLocaleString(),
    };
}"""

GREETINGS = {"en": "Welcome", "de": "Willkommen", "ja": "ようこそ", "ar": "أهلاً بك"}
CURRENCIES = {"en": "USD", "de": "EUR", "ja": "JPY", "ar": "EGP"}

PAGE = """<!doctype html>
<html lang="{lang}" dir="{dir}">
<head><meta charset="utf-8"><title>{greeting}</title>
<style>body {{ font-family: system-ui, sans-serif; margin: 2em; }} td {{ padding: 4px 12px; }}</style></head>
<body>
<h1>{greeting}</h1>
<table id="formats"></table>
<script>
const instant = new Date(Date.UTC(2026, 0, 15, 12, 0));
const rows = {{
  "Date": instant.toLocaleString(undefined, {{dateStyle: "full", timeStyle: "short"}}),
  "Number": (1234567.891).toLocaleString(),
  "Price": new Intl.NumberFormat(undefined, {{style: "currency", currency: "{currency}"}}).format(1299.5),
  "Yesterday": new Intl.RelativeTimeFormat(undefined, {{numeric: "auto"}}).format(-1, "day"),
  "List": new Intl.ListFormat(undefined).format(["Anna", "Ben", "Chen"]),
  "Time zone": Intl.DateTimeFormat().resolvedOptions().timeZone,
}};
for (const [name, value] of Object.entries(rows)) {{
  document.getElementById("formats").insertRow().innerHTML = `<th>${{name}}</th><td>${{value}}</td>`;
}}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # The first language in Accept-Language that the page has, as a server would pick it.
        wanted = [part.split(";")[0].strip()[:2] for part in self.headers.get("Accept-Language", "").split(",")]
        lang = next((code for code in wanted if code in GREETINGS), "en")
        body = PAGE.format(lang=lang, dir="rtl" if lang == "ar" else "ltr", greeting=GREETINGS[lang],
                           currency=CURRENCIES[lang]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def check(url, locale, timezone, proxy):
    with launch(fingerprint=42, fingerprint_platform="windows", locale=locale, timezone=timezone,
                geoip=False, proxy=proxy) as browser:
        page = browser.new_page()
        response = page.goto(url)
        values = page.evaluate(READ)
        values["acceptLanguage"] = response.request.all_headers()["accept-language"]
        page.screenshot(path=OUT / f"{locale}.png", full_page=True)
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", nargs="?", help="the page to check; a local test page when omitted")
    url = parser.parse_args().url
    server = None
    if url is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/"
    OUT.mkdir(exist_ok=True)

    for locale, timezone, variable in REGIONS:
        proxy = os.environ.get(variable)
        values = check(url, locale, timezone, proxy)
        print(f"{locale}  {timezone}  {'through ' + variable if proxy else 'direct'}")
        for name in ("languages", "acceptLanguage", "htmlLang", "date", "number"):
            print(f"    {name:15} {values[name]}")
        print(f"    {'screenshot':15} {OUT.name}/{locale}.png")

    if server is not None:
        server.shutdown()


if __name__ == "__main__":
    main()
