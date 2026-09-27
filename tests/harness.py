"""Launch Apostate against local probe pages and collect what the pages read.

Two launch modes:

- ``bare`` starts the browser binary with switches and no automation at all.
  The page drives itself and posts its results to the local server.
- ``package`` launches through the Python package (Patchright), the way a user
  script does, and loads the same page.

Either way the browser's composed profile is read from a child process's
``--apostate-profile`` switch, so a test can compare what the browser composed
with what the page read.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGES = Path(__file__).resolve().parent / "pages"
TABLES = ROOT / "resources" / "profiles" / "dispersion"

sys.path.insert(0, str(ROOT / "python"))

#: Locale and timezone every composed launch gets, so results do not depend on
#: where the test runs.
LOCALE = "en-US"
TIMEZONE = "America/New_York"

#: High-entropy client hints the probe page asks the browser to send.
ACCEPT_CH = ", ".join((
    "Sec-CH-UA-Arch", "Sec-CH-UA-Bitness", "Sec-CH-UA-Full-Version-List", "Sec-CH-UA-Model",
    "Sec-CH-UA-Platform-Version", "Sec-CH-UA-WoW64", "Sec-CH-UA-Form-Factors", "Sec-CH-DPR",
    "Sec-CH-Device-Memory",
))

#: Names a Windows persona answers to on top of its list, with the family
#: Windows uses for them (docs/guides/fonts).
WINDOWS_ALIASES = {"Courier", "MS Sans Serif", "MS Serif", "Times", "Helvetica", "Franklin Gothic", "Arial Narrow"}

#: Fonts a host commonly has that no persona lists.
CANARY_FONTS = [
    "Menlo", "Helvetica Neue", "Apple Color Emoji", "SF Pro", "Avenir Next", "DejaVu Sans Mono",
    "Liberation Mono", "Noto Color Emoji", "Ubuntu Mono", "Segoe UI Variable Display", "Consolas",
]


class ProbeServer:
    """Serves the probe pages and receives results, on two origins."""

    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}
        self.document_headers: dict[str, dict[str, str]] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.send_header("cache-control", "no-store")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                headers = {key.lower(): value for key, value in self.headers.items()}
                if url.path == "/echo":
                    self._send(200, json.dumps(headers).encode(), "application/json")
                    return
                name = url.path.lstrip("/") or "probe.html"
                path = (PAGES / name).resolve()
                if path.parent != PAGES or not path.is_file():
                    self._send(404, b"not found", "text/plain")
                    return
                if name == "probe.html" and "token" in query:
                    with server._lock:
                        server.document_headers[query["token"][0]] = headers
                kind = {".html": "text/html; charset=utf-8", ".js": "text/javascript"}.get(path.suffix, "application/octet-stream")
                self._send(200, path.read_bytes(), kind, {"accept-ch": ACCEPT_CH} if name.endswith(".html") else None)

            def do_POST(self) -> None:
                url = urlsplit(self.path)
                token = parse_qs(url.query).get("token", [""])[0]
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                try:
                    value = json.loads(body)
                except ValueError:
                    value = {"error": "unparseable result"}
                with server._lock:
                    server.results[token] = value
                    event = server._events.setdefault(token, threading.Event())
                event.set()
                self._send(204, b"", "text/plain")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.cross_origin = f"http://localhost:{self.port}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def url(self, token: str, fonts: list[str], generic: list[str]) -> str:
        return (f"{self.origin}/probe.html?token={token}&cross={quote(self.cross_origin, safe='')}"
                f"&fonts={quote('|'.join(fonts))}&generic={quote('|'.join(generic))}")

    def wait(self, token: str, timeout: float) -> dict[str, Any]:
        with self._lock:
            event = self._events.setdefault(token, threading.Event())
        if not event.wait(timeout):
            raise TimeoutError(f"the probe page posted no result within {timeout:.0f} s")
        return self.results.pop(token)

    def close(self) -> None:
        self._httpd.shutdown()


@dataclass
class Launch:
    persona: str | None
    seed: str | None
    mode: str = "bare"
    headed: bool = False
    args: list[str] = field(default_factory=list)
    user_data_dir: str | None = None
    locale: str | None = LOCALE
    timezone: str | None = TIMEZONE

    @property
    def host_mode(self) -> bool:
        return self.seed == "host"

    def label(self) -> str:
        return f"{self.persona or 'default'}-{self.seed or 'none'}-{self.mode}{'-headed' if self.headed else ''}"


@dataclass
class Probe:
    launch: Launch
    values: dict[str, Any]
    profile: dict[str, Any] | None
    document_headers: dict[str, str]
    seconds: float


def _processes() -> list[tuple[int, int, str]]:
    if os.name == "nt":
        output = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }"],
            capture_output=True, text=True, check=False).stdout
        rows = []
        for line in output.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rows.append((int(parts[0]), int(parts[1]), parts[2]))
        return rows
    output = subprocess.run(["ps", "-Awwo", "pid=,ppid=,args="], capture_output=True, text=True, check=False).stdout
    rows = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def _descendants(rows: list[tuple[int, int, str]], root: int) -> list[str]:
    children: dict[int, list[int]] = {}
    args = {}
    for pid, ppid, command in rows:
        children.setdefault(ppid, []).append(pid)
        args[pid] = command
    found, stack = [], [root]
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            found.append(args[child])
            stack.append(child)
    return found


def composed_profile(browser_pid: int) -> dict[str, Any] | None:
    """The profile a browser composed, from its first child's --apostate-profile."""
    for command in _descendants(_processes(), browser_pid):
        for token in command.split():
            if token.startswith("--apostate-profile="):
                payload = json.loads(base64.b64decode(token.split("=", 1)[1]))
                return payload.get("device_profile", payload)
    return None


def _browser_pid(marker: str) -> int | None:
    """The newest browser process (no --type) whose command line holds *marker*."""
    candidates = [pid for pid, _, command in _processes() if marker in command and "--type=" not in command]
    return max(candidates) if candidates else None


def binary_path(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if os.environ.get("APOSTATE_BINARY"):
        from apostate import ensure_binary
        return str(ensure_binary(binary_path=os.environ["APOSTATE_BINARY"]))
    from apostate import ensure_binary
    return str(ensure_binary())


def explain(binary: str, launch: Launch) -> str:
    args = [binary, *switches(launch, explain=True)]
    return subprocess.run(args, capture_output=True, text=True, timeout=60, check=False).stdout


def switches(launch: Launch, *, explain: bool = False) -> list[str]:
    out: list[str] = []
    if launch.seed is not None:
        out.append(f"--fingerprint={launch.seed}")
    if launch.persona is not None and not launch.host_mode:
        out.append(f"--fingerprint-platform={launch.persona}")
    if not launch.host_mode:
        if launch.locale:
            out.append(f"--fingerprint-locale={launch.locale}")
        if launch.timezone:
            out.append(f"--fingerprint-timezone={launch.timezone}")
    out.extend(launch.args)
    if explain:
        out.append("--fingerprint-explain")
    return out


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _font_candidates() -> tuple[list[str], list[str]]:
    packs = json.loads((TABLES / "font_packs.json").read_text())
    families: set[str] = set()
    for option_set in packs["option_sets"]:
        for option in option_set["options"]:
            families.update(option["value"]["fonts"].get("enumeration_allowlist", []))
    generic: set[str] = set()
    for option_set in packs["option_sets"]:
        for option in option_set["options"]:
            generic.update((option["value"]["fonts"].get("generic_family_map") or {}).values())
    return sorted(families | WINDOWS_ALIASES | set(CANARY_FONTS)), sorted(generic)


FONT_CANDIDATES, GENERIC_FAMILIES = _font_candidates()


def run_probe(server: ProbeServer, binary: str, launch: Launch, *, timeout: float = 90) -> Probe:
    token = secrets.token_hex(8)
    url = server.url(token, FONT_CANDIDATES, GENERIC_FAMILIES)
    started = time.monotonic()
    if launch.mode == "bare":
        values, profile = _run_bare(server, binary, launch, token, url, timeout)
    elif launch.mode == "package":
        values, profile = _run_package(server, binary, launch, token, url, timeout)
    else:
        raise ValueError(f"unknown mode {launch.mode!r}")
    return Probe(launch, values, profile, server.document_headers.pop(token, {}), time.monotonic() - started)


def _run_bare(server: ProbeServer, binary: str, launch: Launch, token: str, url: str,
              timeout: float) -> tuple[dict[str, Any], dict[str, Any] | None]:
    owned = launch.user_data_dir is None
    user_data_dir = launch.user_data_dir or tempfile.mkdtemp(prefix="apostate-probe-")
    args = [binary, f"--user-data-dir={user_data_dir}", "--no-first-run", "--no-default-browser-check",
            *switches(launch)]
    if not launch.headed:
        args.append("--headless=new")
    if _is_root():
        args.append("--no-sandbox")
    args.append(url)
    env = dict(os.environ)
    display = None
    if launch.headed and sys.platform.startswith("linux") and not env.get("DISPLAY"):
        from apostate import xvfb
        display = xvfb.start(3840, 2160)
        env["DISPLAY"] = display.name
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        values = server.wait(token, timeout)
        profile = composed_profile(process.pid)
        return values, profile
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        if display is not None:
            display.stop()
        if owned:
            shutil.rmtree(user_data_dir, ignore_errors=True)


def _run_package(server: ProbeServer, binary: str, launch: Launch, token: str, url: str,
                 timeout: float) -> tuple[dict[str, Any], dict[str, Any] | None]:
    import apostate

    options: dict[str, Any] = {
        "fingerprint": launch.seed,
        "fingerprint_platform": None if launch.host_mode else launch.persona,
        "geoip": False,
        "headless": not launch.headed,
        "binary_path": binary,
        "args": list(launch.args),
    }
    if not launch.host_mode:
        options["locale"] = launch.locale
        options["timezone"] = launch.timezone
    marker = f"--fingerprint={launch.seed}" if launch.seed is not None else None
    if launch.user_data_dir:
        context = apostate.launch_persistent_context(launch.user_data_dir, **options)
        page = context.new_page()
        closer = context
        marker = f"--user-data-dir={launch.user_data_dir}"
    else:
        browser = apostate.launch(**options)
        page = browser.new_page()
        closer = browser
    try:
        page.goto(url)
        values = server.wait(token, timeout)
        pid = _browser_pid(marker) if marker else None
        profile = composed_profile(pid) if pid else None
        return values, profile
    finally:
        closer.close()


def host_facts() -> dict[str, Any]:
    facts: dict[str, Any] = {
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cores": os.cpu_count(),
        "python": platform.python_version(),
    }
    try:
        if sys.platform == "darwin":
            facts["memory_bytes"] = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
            facts["os_version"] = "macOS " + subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip()
            facts["cpu"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        elif sys.platform.startswith("linux"):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    facts["memory_bytes"] = int(line.split()[1]) * 1024
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    facts["cpu"] = line.split(":", 1)[1].strip()
                    break
            release = Path("/etc/os-release")
            if release.exists():
                for line in release.read_text().splitlines():
                    if line.startswith("PRETTY_NAME="):
                        facts["os_version"] = line.split("=", 1)[1].strip('"')
    except (OSError, ValueError):
        pass
    return facts
