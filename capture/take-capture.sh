#!/usr/bin/env bash
# Take a reference capture on a rented GPU host and submit it to the probe.
#
# The host has no display and nobody is watching, so this runs a real headed
# browser on a virtual X display and lets the page drive itself through
# ?auto=1 -- the same code path a person's click takes. There is no CDP, no
# WebDriver and no injected script, because the receiver rejects any capture
# carrying an automation signal.
#
# Chrome is pinned to the Chrome for Testing build of build/CHROMIUM_VERSION,
# because the receiver admits only captures from the major Apostate is built
# from (capture/server/receive.py reads the same file). When the script is
# copied to a host without the repository beside it, it falls back to the
# version written here. Override with CHROME_VERSION=<version> for a receiver
# that pins another major.
#
# Usage:  ./take-capture.sh <label>
# Example: ./take-capture.sh rtx-4090-vast
set -euo pipefail

PINNED_VERSION_FILE="$(cd "$(dirname "$0")" && pwd)/../build/CHROMIUM_VERSION"
if [ -z "${CHROME_VERSION:-}" ] && [ -f "$PINNED_VERSION_FILE" ]; then
  CHROME_VERSION="$(tr -d '[:space:]' < "$PINNED_VERSION_FILE")"
fi
CHROME_VERSION="${CHROME_VERSION:-155.0.8059.31}"
PROBE="${PROBE:-https://probe.chaser.sh}"
LABEL="${1:-}"
WORK="${WORK:-$HOME/.apostate-capture}"
SCREEN="${SCREEN:-1920x1080x24}"

if [ -z "$LABEL" ]; then
  echo "usage: $0 <label>    e.g. $0 rtx-4090-vast" >&2
  exit 2
fi

say() { printf '==> %s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

mkdir -p "$WORK"
cd "$WORK"

# ---------------------------------------------------------------- dependencies
say "installing dependencies"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq
  # Chrome for Testing ships no dependencies; these are what it dlopens.
  #
  # The four GL/Vulkan ones are not optional on a GPU host and are the
  # difference between a useful capture and a useless one. The NVIDIA
  # container runtime injects the vendor libraries and the ICD manifest but
  # never the Vulkan loader or the GLVND dispatch libraries, which belong to
  # the image. Without them libGLX_nvidia.so.0 fails to dlopen libEGL.so.1,
  # vk_icdNegotiateLoaderICDInterfaceVersion returns VK_ERROR_INITIALIZATION_
  # FAILED, the loader skips the ICD, and requestAdapter() resolves null --
  # so WebGPU measures nothing while WebGL still looks fine.
  #
  # Deliberately NOT mesa-vulkan-drivers or vulkan-tools. Either pulls in
  # lavapipe, which registers as a Vulkan device, and Chrome can then hand the
  # capture a software adapter on a machine with a real GPU. Observed:
  # vkEnumeratePhysicalDevices returning "llvmpipe (LLVM 15.0.7, 256 bits)".
  GLVND="libvulkan1 libegl1 libgl1 libglx0"
  $SUDO apt-get install -y -qq --no-install-recommends \
    xvfb unzip curl ca-certificates fonts-liberation $GLVND \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libatspi2.0-0t64 \
    >/dev/null 2>&1 || $SUDO apt-get install -y -qq --no-install-recommends \
    xvfb unzip curl ca-certificates fonts-liberation $GLVND \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 >/dev/null
else
  command -v Xvfb  >/dev/null || die "no apt-get and no Xvfb; install Xvfb and unzip first"
  command -v unzip >/dev/null || die "no apt-get and no unzip; install unzip first"
fi

# --------------------------------------------------------------- pinned chrome
CHROME="$WORK/chrome-linux64/chrome"
if [ ! -x "$CHROME" ]; then
  say "fetching Chrome $CHROME_VERSION (pinned; the receiver refuses another major)"
  URL="https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip"
  curl -fsSL --retry 3 -o chrome.zip "$URL" || die "could not fetch $URL"
  unzip -q -o chrome.zip && rm -f chrome.zip
fi
[ -x "$CHROME" ] || die "chrome not executable at $CHROME"

GOT="$("$CHROME" --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){3}' || true)"
[ "$GOT" = "$CHROME_VERSION" ] || die "expected Chrome $CHROME_VERSION, got '${GOT:-nothing}'"
say "Chrome $GOT"

# ------------------------------------------------------------------- gpu check
say "GPU as the host reports it"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | sed 's/^/    /'
else
  echo "    no nvidia-smi -- this will capture a software renderer, which is not a GPU reference" >&2
fi

# ------------------------------------------------------------------ virtual X
DISPLAY_NUM="${DISPLAY_NUM:-99}"
if ! xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1; then
  say "starting Xvfb on :$DISPLAY_NUM at $SCREEN"
  Xvfb ":$DISPLAY_NUM" -screen 0 "$SCREEN" -nolisten tcp >"$WORK/xvfb.log" 2>&1 &
  XVFB_PID=$!
  trap 'kill "$XVFB_PID" 2>/dev/null || true' EXIT
  for _ in $(seq 1 50); do
    xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1 && break
    sleep 0.2
  done
  xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1 || die "Xvfb did not come up; see $WORK/xvfb.log"
fi
export DISPLAY=":$DISPLAY_NUM"

# ------------------------------------------------------------------ the run
# window-management is granted up front because screen.details refuses to
# prompt during a capture and nobody is here to click Allow.
PROFILE="$WORK/profile"
rm -rf "$PROFILE"; mkdir -p "$PROFILE/Default"
ORIGIN="$(printf '%s' "$PROBE" | sed -E 's#(https?://[^/]+).*#\1#')"
cat > "$PROFILE/Default/Preferences" <<PREFS
{"profile":{"content_settings":{"exceptions":{"window_placement":{"${ORIGIN},*":{"setting":1}}}}}}
PREFS

say "capturing as '$LABEL' against $PROBE"
# No --headless, no --remote-debugging-port, no --enable-automation: every one
# of those is a signal the receiver refuses. --no-sandbox is required as root
# in a container and is not page-visible. --log-net-log is browser-side only;
# the page cannot observe it, and it is how we confirm the submit landed
# without driving the page from outside. Response bodies need Everything;
# IncludeSensitive does not log them, which is how a 422 refusal was once
# misread as a 404.
#
# --use-angle=vulkan is required for hardware WebGL under Xvfb, not a
# preference: NVIDIA's EGL refuses an X11 platform display and initialises
# only on SURFACELESS or DEVICE, Mesa answers X11 but has no NVIDIA driver,
# and Chrome allows one GL implementation. Without it the GPU process cannot
# make a context and the capture measures a blocklisted software path. It
# also matches the existing Linux NVIDIA references, which are all
# ANGLE/Vulkan.
#
# --enable-features=Vulkan is what makes WebGPU report an adapter. Dawn's
# Vulkan backend is not initialised by ANGLE already using Vulkan, so a host
# can render WebGL through Vulkan and still resolve requestAdapter() to null.
# That is the exact signature of two references we hold. Deliberately NOT
# --enable-unsafe-webgpu, which also produces adapters but inflates the
# feature set from 19 to 23 and makes the capture incomparable.
NETLOG="$WORK/netlog.json"
rm -f "$NETLOG"
"$CHROME" \
  --user-data-dir="$PROFILE" \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --use-angle=vulkan \
  --enable-features=Vulkan \
  --log-net-log="$NETLOG" \
  --net-log-capture-mode=Everything \
  --window-size="$(printf '%s' "${SCREEN%x*}" | tr x ,)" \
  "$PROBE/?auto=1&label=$LABEL" \
  >"$WORK/chrome.log" 2>&1 &
CHROME_PID=$!

say "waiting for the page to finish and submit"
SUBMIT_STATUS=""
for _ in $(seq 1 240); do
  sleep 1
  if [ -f "$NETLOG" ] && grep -q '/submit' "$NETLOG" 2>/dev/null; then
    SUBMIT_STATUS="$(python3 - "$NETLOG" <<'PY' 2>/dev/null || true
import json,re,sys

# Resolve the URL_REQUEST source whose url ends in /submit, then read the
# status logged against THAT source id. Scanning for any status near the
# string "/submit" reads whatever request happens to be interleaved -- it
# reported 404 from a favicon request for six captures the server accepted,
# and hid a 422 refusal behind it.
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
try:
    log = json.loads(raw)
except ValueError:                      # still being written: repair the tail
    cut = raw.rfind("},")
    log = json.loads(raw[:cut + 1] + "]}") if cut > 0 else {}
events = log.get("events") or []
ids = {e["source"]["id"] for e in events
       if isinstance(e.get("params"), dict)
       and str(e["params"].get("url", "")).rstrip("/").endswith("/submit")}
status, body = None, None
for e in events:
    if e.get("source", {}).get("id") not in ids:
        continue
    p = e.get("params") or {}
    for line in (p.get("headers") or []):
        m = re.match(r"(?:HTTP/[\d.]+|:status:?)\s*(\d{3})", str(line))
        if m:
            status = m.group(1)
    if isinstance(p.get("bytes"), str) and body is None:
        body = p["bytes"]
if status:
    print(status + ("\t" + body if body else ""))
PY
)"
    [ -n "$SUBMIT_STATUS" ] && break
  fi
  kill -0 "$CHROME_PID" 2>/dev/null || break
done
sleep 4
kill "$CHROME_PID" 2>/dev/null || true
wait "$CHROME_PID" 2>/dev/null || true

# ------------------------------------------------------------------- verdict
if [ -z "$SUBMIT_STATUS" ]; then
  die "the page never POSTed to /submit -- see $WORK/chrome.log and $NETLOG"
fi
CODE="${SUBMIT_STATUS%%	*}"
BODY=""
case "$SUBMIT_STATUS" in
  *"	"*) BODY="$(printf '%s' "${SUBMIT_STATUS#*	}" | base64 -d 2>/dev/null ||
                 printf '%s' "${SUBMIT_STATUS#*	}")" ;;
esac
case "$CODE" in
  200|201|204)
    say "submitted, $PROBE accepted it (HTTP $CODE)" ;;
  422)
    die "$PROBE REFUSED the capture (HTTP 422).
     It said: ${BODY:-<no body logged>}
     The receiver states its own reason, so read it before changing anything.
     Two causes seen so far: a browser version the receiver does not accept,
     which CHROME_VERSION=<version> $0 $LABEL overrides; and an automation
     signal, which means something drove the browser. Playwright, Puppeteer
     and WebDriver all set navigator.webdriver and are refused by design.
     This script drives nothing -- the page drives itself." ;;
  *)
    die "unexpected response from $PROBE (HTTP $CODE) ${BODY:+-- $BODY}" ;;
esac

say "now confirm the capture is worth keeping:"
echo "    - context.ua reports Chrome $CHROME_VERSION"
echo "    - context.automation_signals is empty and headed is true"
echo "    - webgl1/webgl2 name the real GPU, not SwiftShader or llvmpipe"
echo "    - webgpu.value.adapters are objects, not null"
echo "  netlog: $NETLOG    chrome log: $WORK/chrome.log"
