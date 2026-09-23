#!/usr/bin/env python3
"""Take a capture on a host with no display and nobody watching.

Rule 6 is that captures come from a normal headed browser, and the receiver
enforces it: any automation signal, a Headless user agent or a non-secure
context is rejected. A deployment target with no display cannot satisfy that
with --headless, so this runs a real headed browser on a virtual X display.
There is no CDP, no WebDriver and no injected script — the page drives itself
through ?auto=1, the same code path a person's click takes.

Two host facts need handling. Both are recorded rather than papered over.

  WebGL. Under Xvfb the only GL driver is Mesa llvmpipe, which Chrome's
    software-rendering blocklist disables WebGL and WebGPU for, and the
    implicit SwiftShader fallback for WebGL is gone. getContext('webgl') then
    returns null, the webgl1 and webgl2 probes measure nothing, and the
    receiver rejects the capture — correctly, since nothing was measured.
    --enable-unsafe-swiftshader re-permits the fallback and the probes then
    measure a real software backend. It is an opt-in, not an override: where a
    GPU works it changes nothing, unlike --use-angle=swiftshader, which would
    turn a hardware host into a software capture without saying so.

  window-management. screen.details refuses to prompt during a capture, so the
    permission has to already exist. Nobody is present to click Allow, so the
    grant is written into the fresh profile for the receiver's origin: the same
    content setting the click would have produced, and the capture's own
    permissions.states probe records that it was granted.

The renderer that was actually measured is printed at the end. If it names a
software rasteriser then the capture describes SwiftShader and not the host's
GPU.
"""

import argparse
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

CAPTURE_DIR = pathlib.Path(__file__).resolve().parent
RECEIVER = CAPTURE_DIR / "server" / "receive.py"
BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
# Chrome still stores the window-management permission under the name it had
# when the API was called Window Placement. Seeding "window_management"
# instead is silently ignored and the probe stays at 'prompt'.
PERMISSION_PREF = "window_placement"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def find_browser(explicit):
    if explicit:
        return explicit
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    sys.exit("no Chrome or Chromium on PATH; pass --chrome")


def display_is_up(display):
    """True when something already serves this display.

    Reusing a real X server is better than starting Xvfb over it, and a second
    Xvfb on a taken display fails in a way that is easy to misread as a capture
    failure.
    """
    number = display.lstrip(":").split(".")[0]
    return pathlib.Path("/tmp/.X11-unix/X" + number).exists()


def seed_permission(profile, origin):
    """Grant window-management to *origin* in a fresh profile."""
    default = profile / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "Preferences").write_text(json.dumps({
        "profile": {"content_settings": {"exceptions": {PERMISSION_PREF: {
            origin + ",*": {"last_modified": "13400000000000000", "setting": 1},
        }}}},
    }))


def wait_until(predicate, timeout, interval=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def new_admission_record(admissions, before):
    if not admissions.is_dir():
        return None
    for path in sorted(admissions.glob("*.json")):
        if path.name not in before:
            return path
    return None


def terminate(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def renderer_of(capture_path):
    try:
        capture = json.loads(pathlib.Path(capture_path).read_text())
    except (OSError, ValueError):
        return None
    probe = (capture.get("probes") or {}).get("webgl1") or {}
    value = probe.get("value") or {}
    return value.get("unmaskedRenderer") or value.get("renderer")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True, help="device name recorded in the capture")
    ap.add_argument("--out", type=pathlib.Path, required=True,
                    help="directory the receiver writes captures to")
    ap.add_argument("--chrome", help="browser binary; default is the first Chrome on PATH")
    ap.add_argument("--display", default=":99", help="X display to use (default :99)")
    ap.add_argument("--screen", default="1920x1080x24", help="Xvfb screen geometry")
    ap.add_argument("--port", type=int, help="receiver port; default is a free one")
    ap.add_argument("--timeout", type=float, default=300.0,
                    help="seconds to wait for a submission")
    ap.add_argument("extra", nargs="*", metavar="-- FLAG",
                    help="extra browser flags, e.g. -- --enable-logging=stderr")
    args = ap.parse_args()

    if not RECEIVER.exists():
        sys.exit("receiver not found at %s" % RECEIVER)
    browser = find_browser(args.chrome)
    port = args.port or free_port()
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    admissions = out_dir / "admissions"
    before = {path.name for path in admissions.glob("*.json")} if admissions.is_dir() else set()

    run_dir = pathlib.Path(tempfile.mkdtemp(prefix="apostate-capture-"))
    profile = run_dir / "profile"
    receiver_log = run_dir / "receiver.log"
    browser_log = run_dir / "browser.log"
    origin = "http://127.0.0.1:%d" % port
    url = "%s/?auto=1&label=%s" % (origin, args.label)

    version = subprocess.run([browser, "--version"], capture_output=True, text=True)
    print("browser   %s" % (version.stdout.strip() or browser))
    print("receiver  %s  out %s" % (origin, out_dir))
    print("run logs  %s" % run_dir)

    xvfb = None
    receiver = None
    launched = None
    try:
        if display_is_up(args.display):
            print("display   %s (already running)" % args.display)
        else:
            if not shutil.which("Xvfb"):
                sys.exit("Xvfb is not installed and %s is not running" % args.display)
            xvfb = subprocess.Popen(
                ["Xvfb", args.display, "-screen", "0", args.screen],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            if not wait_until(lambda: display_is_up(args.display), 20):
                return 2
            print("display   %s (Xvfb %s)" % (args.display, args.screen))

        with receiver_log.open("wb") as log:
            receiver = subprocess.Popen(
                [sys.executable, "-u", str(RECEIVER), "--out", str(out_dir),
                 "--port", str(port), "--bind", "127.0.0.1", "--once"],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True)
        if not wait_until(lambda: "capture receiver" in receiver_log.read_text(errors="replace"), 30):
            print(receiver_log.read_text(errors="replace"), file=sys.stderr)
            return 2

        seed_permission(profile, origin)
        flags = [
            "--user-data-dir=%s" % profile,
            "--no-first-run", "--no-default-browser-check",
            # See the module docstring: this permits WebGL's software fallback,
            # it does not force software rendering.
            "--enable-unsafe-swiftshader",
        ]
        if os.geteuid() == 0:
            # README: unavoidable as root, and itself a deviation from a normal
            # launch. A non-root user is the better host for a capture.
            print("warning   running as root, adding --no-sandbox")
            flags.append("--no-sandbox")
        flags.extend(args.extra)

        environment = dict(os.environ, DISPLAY=args.display)
        with browser_log.open("wb") as log:
            launched = subprocess.Popen([browser] + flags + [url], env=environment,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)

        print("waiting   up to %gs for a submission" % args.timeout)
        record_path = wait_until(
            lambda: new_admission_record(admissions, before), args.timeout)
    finally:
        terminate(launched)
        terminate(receiver)
        terminate(xvfb)
        shutil.rmtree(profile, ignore_errors=True)

    if record_path is None:
        print("\nnothing was submitted within the timeout.")
        print("  receiver log %s" % receiver_log)
        print("  browser log  %s" % browser_log)
        return 2

    record = json.loads(record_path.read_text())
    print("\ndecision  %s" % record.get("decision"))
    print("reason    %s" % record.get("reason"))
    print("record    %s" % record_path)
    for probe, error in sorted((record.get("probe_errors") or {}).items()):
        print("  failed  %s: %s" % (probe, error))
    if record.get("decision") != "accepted":
        print("\nThe capture was not admitted. Its body is not kept: re-run after")
        print("fixing the cause above, and add -- --enable-logging=stderr for the")
        print("browser's own GPU messages in %s" % browser_log)
        return 1

    capture_path = record.get("capture_path")
    print("capture   %s" % capture_path)
    renderer = renderer_of(capture_path)
    print("renderer  %s" % renderer)
    print("\nIf that renderer is a software rasteriser, this capture describes it")
    print("and not the host's GPU, and no anchor may claim it as hardware.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
