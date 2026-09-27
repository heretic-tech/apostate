"""Print, as JSON, what a page, a worker and the request headers show.

The page is served from a local HTTP server. It reads the values, starts a
worker that reads them again, fetches the headers the browser sent, and posts
everything back to the server.

usage: what_a_page_sees.py [--seed N] [--platform windows|macos|linux]
                           [--locale TAG] [--timezone ZONE] [--headed]

Without --locale and --timezone, the package looks them up from your IP
address.
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from apostate import launch

# Asked for on the page's response, so the browser sends them on later requests.
HINTS = ("Sec-CH-UA-Arch, Sec-CH-UA-Bitness, Sec-CH-UA-Full-Version-List, "
         "Sec-CH-UA-Model, Sec-CH-UA-Platform-Version, Sec-CH-UA-WoW64")

WORKER = """
const gl = new OffscreenCanvas(1, 1).getContext("webgl");
const info = gl.getExtension("WEBGL_debug_renderer_info");
postMessage({
  userAgent: navigator.userAgent,
  platform: navigator.platform,
  uaPlatform: navigator.userAgentData.platform,
  cores: navigator.hardwareConcurrency,
  memory: navigator.deviceMemory,
  languages: navigator.languages,
  timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  gpu: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
});
"""

PAGE = """<!doctype html><meta charset="utf-8"><script>
async function gpuAdapter() {
  if (!navigator.gpu) return null;
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter) return null;
  const { vendor, architecture, device, description } = adapter.info;
  return { vendor, architecture, device, description };
}

async function voices() {
  if (speechSynthesis.getVoices().length === 0) {
    await new Promise((done) => {
      speechSynthesis.onvoiceschanged = done;
      setTimeout(done, 2000);
    });
  }
  const names = speechSynthesis.getVoices().map((voice) => voice.name);
  return { count: names.length, first: names.slice(0, 3) };
}

// A family is present when text set in it differs in width from a fallback.
function hasFont(family) {
  const context = document.createElement("canvas").getContext("2d");
  return ["monospace", "serif", "sans-serif"].some((fallback) => {
    context.font = `72px ${fallback}`;
    const width = context.measureText("mmmmmmmmmmlli").width;
    context.font = `72px "${family}", ${fallback}`;
    return context.measureText("mmmmmmmmmmlli").width !== width;
  });
}

async function read() {
  const gl = document.createElement("canvas").getContext("webgl");
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  const audio = new AudioContext();
  const devices = await navigator.mediaDevices.enumerateDevices();
  const worker = await new Promise((done) => {
    new Worker("/worker.js").onmessage = (event) => done(event.data);
  });
  return {
    navigator: {
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      cores: navigator.hardwareConcurrency,
      memory: navigator.deviceMemory,
      languages: navigator.languages,
      maxTouchPoints: navigator.maxTouchPoints,
      webdriver: navigator.webdriver,
    },
    userAgentData: await navigator.userAgentData.getHighEntropyValues(
      ["architecture", "bitness", "model", "platformVersion", "fullVersionList"]),
    intl: {
      locale: Intl.DateTimeFormat().resolvedOptions().locale,
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      date: new Date(0).toLocaleString(),
    },
    screen: {
      size: [screen.width, screen.height],
      available: [screen.availWidth, screen.availHeight],
      window: [outerWidth, outerHeight],
      viewport: [innerWidth, innerHeight],
      pixelRatio: devicePixelRatio,
      colorDepth: screen.colorDepth,
    },
    webgl: {
      vendor: gl.getParameter(info.UNMASKED_VENDOR_WEBGL),
      renderer: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
    },
    webgpu: await gpuAdapter(),
    fonts: Object.fromEntries(["Segoe UI", "Calibri", "Helvetica Neue", "Menlo", "DejaVu Sans"]
      .map((family) => [family, hasFont(family)])),
    voices: await voices(),
    mediaDevices: devices.map((device) => device.kind),
    audio: { sampleRate: audio.sampleRate, baseLatency: audio.baseLatency },
    worker,
    headers: await (await fetch("/headers")).json(),
  };
}

read().then(
  (values) => fetch("/result", { method: "POST", body: JSON.stringify(values) }),
  (error) => fetch("/result", { method: "POST", body: JSON.stringify({ error: String(error) }) }),
);
</script>"""

result = {}
received = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.reply("text/html", PAGE, {"Accept-CH": HINTS})
        elif self.path == "/worker.js":
            self.reply("text/javascript", WORKER)
        elif self.path == "/headers":
            sent = {name.lower(): value for name, value in self.headers.items()
                    if name.lower() in ("user-agent", "accept-language")
                    or name.lower().startswith("sec-ch-ua")}
            self.reply("application/json", json.dumps(sent, sort_keys=True))
        else:
            self.send_error(404)

    def do_POST(self):
        result.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(204)
        self.end_headers()
        received.set()

    def reply(self, content_type, body, headers=None):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


parser = argparse.ArgumentParser()
parser.add_argument("--seed", default="42")
parser.add_argument("--platform", default="windows", choices=("windows", "macos", "linux"))
parser.add_argument("--locale")
parser.add_argument("--timezone")
parser.add_argument("--headed", action="store_true")
args = parser.parse_args()

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
with launch(fingerprint=args.seed, fingerprint_platform=args.platform,
            locale=args.locale, timezone=args.timezone, headless=not args.headed) as browser:
    page = browser.new_page()
    page.goto(f"http://127.0.0.1:{server.server_port}/")
    if not received.wait(30):
        raise SystemExit("the page did not report back within 30 seconds")
server.shutdown()
print(json.dumps(result, indent=2))
