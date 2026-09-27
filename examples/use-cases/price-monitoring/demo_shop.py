"""A local shop whose prices change from one run to the next, for trying monitor.py.

Each start moves the shop on by one run, counted in out/demo-shop-run.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

COUNTER = Path(__file__).resolve().parent / "out" / "demo-shop-run"

# The price each product shows on run 1, 2, 3 and 4, then again from run 1.
PRICES = {
    "kettle": ["49.99", "44.99", "44.99", "47.99"],
    "toaster": ["34.50", "34.50", "34.50", "34.50"],
    "lamp": ["19.00", "Sold out", "19.00", "Sold out"],
}

ROBOTS = "User-agent: *\nDisallow: /cart\nCrawl-delay: 2\n"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{name}</title></head>
<body><h1>{name}</h1><p>Price: <span class="price">{price}</span></p></body></html>
"""


def start(port=8765):
    """Serve the shop on 127.0.0.1 in a background thread."""
    COUNTER.parent.mkdir(exist_ok=True)
    run = int(COUNTER.read_text()) + 1 if COUNTER.exists() else 1
    COUNTER.write_text(str(run))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.rsplit("/", 1)[-1]
            if self.path == "/robots.txt":
                self.reply(200, "text/plain", ROBOTS)
            elif self.path.startswith("/product/") and name in PRICES:
                price = PRICES[name][(run - 1) % 4]
                self.reply(200, "text/html; charset=utf-8", PAGE.format(name=name, price=price))
            else:
                self.reply(404, "text/plain", "not found")

        def reply(self, status, content_type, text):
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
