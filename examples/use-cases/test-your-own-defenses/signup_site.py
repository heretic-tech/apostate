"""A local signup page that records what a backend sees.

Each form submission appends one JSON line to out/visits.jsonl with the
request headers and a probe the page's own script collects. Run this file
on its own to serve the page on http://127.0.0.1:8000/.
"""

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

LOG = Path(__file__).resolve().parent / "out" / "visits.jsonl"

# The values a bot-protection script typically reads in the browser.
PROBE = """async () => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl && gl.getExtension("WEBGL_debug_renderer_info");
    const uaData = navigator.userAgentData;
    const high = uaData
        ? await uaData.getHighEntropyValues(["architecture", "platformVersion"])
        : {};
    return {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        uaDataPlatform: uaData ? uaData.platform : null,
        uaDataPlatformVersion: high.platformVersion || null,
        architecture: high.architecture || null,
        webdriver: navigator.webdriver,
        cores: navigator.hardwareConcurrency,
        memory: navigator.deviceMemory,
        languages: navigator.languages,
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
        webglRenderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null,
    };
}"""

PAGE = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Sign up</title></head>
<body>
<form id="signup">
  <label>Email <input id="email" name="email" type="email" required></label>
  <button type="submit">Create account</button>
</form>
<p id="result"></p>
<script>
const probe = {PROBE};
document.getElementById("signup").addEventListener("submit", async (event) => {{
  event.preventDefault();
  const response = await fetch("/signup" + location.search, {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{email: document.getElementById("email").value, probe: await probe()}}),
  }});
  document.getElementById("result").textContent = (await response.json()).message;
}});
</script>
</body>
</html>
"""

HEADERS = ("User-Agent", "Accept-Language", "Sec-CH-UA", "Sec-CH-UA-Mobile", "Sec-CH-UA-Platform")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        submitted = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "path": self.path,
            "headers": {name: self.headers.get(name) for name in HEADERS},
            "email": submitted["email"],
            "probe": submitted["probe"],
        }
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as log:
            log.write(json.dumps(record) + "\n")
        body = json.dumps({"message": f"Account created for {submitted['email']}"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start(port=0):
    """Serve the page on 127.0.0.1 in a background thread. Port 0 picks a free port."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print(f"serving http://127.0.0.1:8000/ and logging to {LOG}")
    server.serve_forever()
