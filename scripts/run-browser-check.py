#!/usr/bin/env python3
"""Run an authored browser diagnostic without CDP or JavaScript API overrides.

Example:
  python3 scripts/run-browser-check.py --browser /path/to/chrome \
    --fixture scripts/fixtures/gl-profile-boundaries.html \
    --profile /path/to/profile.json --angle-backend swiftshader --out /fresh/run

Fixtures POST a JSON object to /result?token=<token from their page URL> when
?report=1. GET /parameters returns optional supplied parameters, with profile
gl_limits exposed as expected_limits by default. Receipts are diagnostics, not
reference captures or corpus admissions. Browser userdata is temporary.
"""

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ("swiftshader", "default", "gl", "gl-egl", "gles", "gles-egl", "vulkan")

# Profiles are checked by the launcher's own validator, so a profile this
# accepts is one the launcher would accept.
sys.path.insert(0, str(ROOT / "python"))
from apostate.errors import ProfileError  # noqa: E402
from apostate.profile_validation import validate_profile  # noqa: E402


def strict_json(data):
    def invalid(value):
        raise ValueError(f"non-finite JSON number: {value}")
    return json.loads(data, parse_constant=invalid)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def file_receipt(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {"path": str(path), "sha256": digest.hexdigest(), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns}


def backend_args(backend):
    if backend == "default":
        return []
    args = ["--use-gl=angle", f"--use-angle={backend}"]
    if backend == "swiftshader":
        args.append("--enable-unsafe-swiftshader")
    return args


class ResultServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, fixture, parameters, token, out):
        super().__init__(("127.0.0.1", 0), ResultHandler)
        self.fixture = fixture
        self.parameters = json.dumps(parameters, allow_nan=False).encode()
        self.token = token
        self.out = out
        self.completed = threading.Event()
        self.result_lock = threading.Lock()
        self.result = None
        self.origin = f"http://127.0.0.1:{self.server_port}"


class ResultHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, data=b"", content_type="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        query = parse_qs(urlsplit(self.path).query)
        return (self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                and query.get("token") == [self.server.token])

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.authorized():
            return self.respond(403)
        if path == "/":
            return self.respond(200, self.server.fixture, "text/html; charset=utf-8")
        if path == "/parameters":
            return self.respond(200, self.server.parameters, "application/json")
        self.respond(404)

    def do_POST(self):
        if urlsplit(self.path).path != "/result":
            return self.respond(404)
        if (not self.authorized() or self.headers.get("Origin") != self.server.origin
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
            return self.respond(403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8 * 1024 * 1024:
                return self.respond(413)
            self.connection.settimeout(5)
            data = self.rfile.read(length)
            if len(data) != length:
                return self.respond(400)
            result = strict_json(data)
            if not isinstance(result, dict):
                return self.respond(400)
        except (OSError, ValueError):
            return self.respond(400)
        with self.server.result_lock:
            if self.server.completed.is_set():
                return self.respond(409)
            write_json(self.server.out / "result.json", result)
            self.server.result = result
            self.server.completed.set()
        self.respond(200, b'{"accepted":true}', "application/json")


def stop_owned_process(process):
    """Only the process group created by this invocation is eligible."""
    signals = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
            signals.append(sig.name)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
    process.wait(timeout=3)
    return signals


def run(args):
    out = args.out.absolute()
    # Refuse an existing directory, including an empty one. No user's previous
    # receipts or browser state are ever removed to make a new run fit.
    out.mkdir(parents=True, exist_ok=False)
    receipt = {"diagnostic_only": True, "corpus_admission": False,
               "status": "preflight", "started_unix": time.time()}
    write_json(out / "configuration.json", {
        "diagnostic_only": True, "angle_backend": args.angle_backend,
        "enable_gpu": args.enable_gpu,
        "disable_web_audio_rust_fft": args.disable_web_audio_rust_fft,
        "grant_display_controls": args.grant_display_controls,
        "timeout_seconds": args.timeout,
        "requested": {name: str(getattr(args, name)) if getattr(args, name) else None
                      for name in ("browser", "fixture", "profile", "fontconfig",
                                   "parameters", "widevine_source")}})
    process = None
    server = None
    server_thread = None
    try:
        browser = args.browser.resolve(strict=True)
        fixture = args.fixture.resolve(strict=True)
        if not browser.is_file() or not os.access(browser, os.X_OK):
            raise ValueError(f"browser is not executable: {browser}")
        fixture_bytes = fixture.read_bytes()
        parameters = {}
        profile_bytes = None
        (out / "fixture.html").write_bytes(fixture_bytes)
        inputs = {"browser": file_receipt(browser), "fixture": file_receipt(out / "fixture.html")}
        inputs["fixture"]["source"] = str(fixture)
        extension = None
        if getattr(args, "extension_dir", None):
            extension = args.extension_dir.resolve(strict=True)
            if not extension.is_dir() or not (extension / "manifest.json").is_file():
                raise ValueError("extension directory requires its original manifest.json")
            manifest = strict_json((extension / "manifest.json").read_bytes())
            extension_files = sorted(path for path in extension.rglob('*') if path.is_file())
            if any(path.is_symlink() for path in extension.rglob('*')):
                raise ValueError("extension directory must not contain symlinks")
            inputs["extension"] = {"path": str(extension), "version": manifest.get("version"),
                "files": {str(path.relative_to(extension)): file_receipt(path)["sha256"]
                          for path in extension_files}}
        if args.profile:
            source = args.profile.resolve(strict=True)
            profile_bytes = source.read_bytes()
            profile = strict_json(profile_bytes)
            (out / "profile.json").write_bytes(profile_bytes)
            try:
                validate_profile(profile)
            except ProfileError as error:
                raise ValueError(f"profile failed validation: {error}") from error
            inputs["profile"] = file_receipt(out / "profile.json")
            inputs["profile"]["source"] = str(source)
            parameters["expected_limits"] = profile.get("gl_limits", {})
            parameters["expected_displays"] = profile.get("screen", {}).get("displays", [])
        if args.parameters:
            parameter_bytes = args.parameters.read_bytes()
            supplied = strict_json(parameter_bytes)
            if not isinstance(supplied, dict):
                raise ValueError("parameters must be a JSON object")
            parameters.update(supplied)
            (out / "parameters-input.json").write_bytes(parameter_bytes)
            inputs["parameters"] = file_receipt(out / "parameters-input.json")
            inputs["parameters"]["source"] = str(args.parameters.resolve())
        env = os.environ.copy()
        if args.fontconfig:
            fontconfig = args.fontconfig.resolve(strict=True)
            inputs["fontconfig"] = file_receipt(fontconfig)
            env["FONTCONFIG_FILE"] = str(fontconfig)
        elif env.get("FONTCONFIG_FILE"):
            inputs["inherited_fontconfig"] = file_receipt(Path(env["FONTCONFIG_FILE"]).resolve())
        if args.widevine_source:
            inputs["widevine"] = {
                "source_directory": str(args.widevine_source.resolve(strict=True)),
                "required_pin": file_receipt(ROOT / "build/widevine-local.json")}
            (out / "widevine-pin.json").write_bytes((ROOT / "build/widevine-local.json").read_bytes())
        write_json(out / "parameters.json", parameters)
        write_json(out / "configuration.json", {"inputs": inputs, "angle_backend": args.angle_backend,
                   "enable_gpu": args.enable_gpu,
                   "disable_web_audio_rust_fft": args.disable_web_audio_rust_fft,
                   "grant_display_controls": args.grant_display_controls,
                   "timeout_seconds": args.timeout, "diagnostic_only": True})
        token = secrets.token_urlsafe(24)
        server = ResultServer(fixture_bytes, parameters, token, out)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        with tempfile.TemporaryDirectory(prefix="apostate-browser-check-") as user_data:
            receipt["user_data_dir"] = user_data
            if args.grant_display_controls:
                # Ordinary per-origin permissions for the authored movement
                # fixture. No user input or page API is synthesized.
                preferences = Path(user_data) / "Default/Preferences"
                preferences.parent.mkdir(parents=True)
                pattern = server.origin + ",*"
                write_json(preferences, {"profile": {"content_settings": {"exceptions": {
                    "window_placement": {pattern: {"setting": 1}},
                    "automatic_fullscreen": {pattern: {"setting": 1}},
                    "popups": {pattern: {"setting": 1}}
                }}}})
            if args.widevine_source:
                provision = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/provision-widevine.py"),
                     "--source", str(args.widevine_source.resolve()), "--user-data-dir", user_data],
                    capture_output=True, text=True, timeout=30)
                (out / "widevine-provisioning.log").write_text(provision.stdout + provision.stderr)
                if provision.returncode:
                    raise ValueError("Widevine provisioning failed; see widevine-provisioning.log")
            url = f"{server.origin}/?report=1&token={token}"
            command = [str(browser), "--headless=new", "--no-sandbox", "--disable-gpu-sandbox",
                       "--no-first-run", "--no-default-browser-check", f"--user-data-dir={user_data}"]
            # Chromium 152.0.7977.83 headless_mode_init.cc:106-113 selects
            # SwiftShader when neither GL/ANGLE nor enable-gpu is supplied.
            if args.enable_gpu:
                command.append("--enable-gpu")
            if args.disable_web_audio_rust_fft:
                command.append("--disable-blink-features=WebAudioRustFft")
            if profile_bytes is not None:
                command.append("--apostate-profile=" + base64.b64encode(profile_bytes).decode("ascii"))
            if extension is not None:
                command.append("--load-extension=" + str(extension))
            command += backend_args(args.angle_backend) + [url]
            launch = {"command": command, "url": url, "user_data_dir": user_data,
                      "fontconfig": env.get("FONTCONFIG_FILE"), "diagnostic_only": True}
            with (out / "browser.log").open("wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           env=env, start_new_session=True)
                launch.update(pid=process.pid, process_group=process.pid)
                write_json(out / "launch.json", launch)
                receipt["status"] = "running"
                deadline = time.monotonic() + args.timeout
                try:
                    while not server.completed.wait(0.1):
                        if process.poll() is not None:
                            raise RuntimeError(f"browser exited before reporting: {process.returncode}")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("fixture did not report before timeout")
                    result = server.result
                    receipt.update(status="completed", fixture_passed=result.get("passed"))
                finally:
                    receipt["cleanup_signals"] = stop_owned_process(process)
                    receipt["browser_exit_code"] = process.returncode
                    process = None
            receipt["userdata_removed"] = False
        receipt["userdata_removed"] = True
        return 1 if receipt.get("fixture_passed") is False else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        receipt.update(status="failed", error=f"{type(error).__name__}: {error}")
        return 2
    finally:
        if process is not None:
            receipt["cleanup_signals"] = stop_owned_process(process)
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=3)
        if "user_data_dir" in receipt:
            receipt["userdata_removed"] = not Path(receipt["user_data_dir"]).exists()
        receipt["finished_unix"] = time.time()
        write_json(out / "receipt.json", receipt)
        print(json.dumps({"out": str(out), "status": receipt["status"],
                          "diagnostic_only": True, "error": receipt.get("error")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="new directory; must not exist")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--fontconfig", type=Path)
    parser.add_argument("--parameters", type=Path)
    parser.add_argument("--widevine-source", type=Path)
    parser.add_argument("--extension-dir", type=Path,
                        help="load an operator-provisioned extension only in this fresh diagnostic profile")
    parser.add_argument("--angle-backend", choices=BACKENDS, default="swiftshader")
    parser.add_argument("--enable-gpu", action="store_true",
                        help="allow headless default GPU selection; does not bypass driver blocklists")
    parser.add_argument("--disable-web-audio-rust-fft", action="store_true",
                        help="diagnostic A/B control for the WebAudioRustFft implementation")
    parser.add_argument("--grant-display-controls", action="store_true",
                        help="pregrant window management, fullscreen and popups to the local diagnostic origin")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not 0 < args.timeout <= 3600:
        parser.error("timeout must be in (0,3600] seconds")
    try:
        return run(args)
    except OSError as error:
        print(f"browser check refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
