"""A small signup app that stands in for the app your tests cover.

Its bot check turns away a signup whose User-Agent contains HeadlessChrome
or whose page reports navigator.webdriver. Run it on its own with
`python3 app.py 8767`.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

PAGE = b"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Sign up</title></head>
<body>
<form id="signup">
  <label>Email <input id="email" type="email" required></label>
  <button type="submit">Create account</button>
</form>
<p id="result"></p>
<script>
document.getElementById("signup").addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/signup", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({email: document.getElementById("email").value, webdriver: navigator.webdriver}),
  });
  document.getElementById("result").textContent = (await response.json()).message;
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.reply(200, "text/html; charset=utf-8", PAGE)

    def do_POST(self):
        form = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "HeadlessChrome" in self.headers.get("User-Agent", "") or form["webdriver"]:
            status, message = 403, "Blocked as a bot"
        else:
            status, message = 200, f"Account created for {form['email']}"
        self.reply(status, "application/json", json.dumps({"message": message}).encode())

    def reply(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start(port=0):
    """Serve the app on 127.0.0.1 in a background thread. Port 0 picks a free port."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
    print(f"serving http://127.0.0.1:{port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
