# Capture

Records what a real machine's browser shows a web page. These captures are the
ground truth the personas are built from and measured against. The same
collector page can also be opened in an Apostate build, so its output can be
compared with a capture of the machine it claims to be.

## Rules

1. **Measure or record the failure. Never make a value up.** Each probe returns
   `{ok, value, error}`. A probe that throws records the error and a `null`
   value. There are no fallback constants: a silently zeroed audio buffer once
   made three different devices produce one identical fingerprint.
2. **Errors are data.** A capture with failed probes is still useful. A capture
   that hides them is not, because it looks complete.
3. **Keep the raw values.** Store the actual bytes: canvas PNG, audio float
   samples, full client-rect coordinates, every queried WebGL parameter. Hashes
   are computed afterwards from the stored values. A hash can check a value but
   can never rebuild one.
4. **Read twice.** Every render probe runs twice in one session and both
   results are kept. That shows which fields are stable on real hardware and
   which ones move on their own.
5. **Record the context.** Device, OS, browser build, device pixel ratio, and
   whether anything automation-shaped was present.
6. **No automation.** Captures come from a normal headed browser. Running the
   collector under CDP would record the very artifacts we remove.

## Layout

```
collector/                   The measurement page. No dependencies, no build step
server/receive.py            Local receiver; checks and writes raw captures to disk
take-capture.sh              Takes a capture on a rented GPU host and submits it
collect-unattended.py        Takes a capture on a host with no display
schema/capture.schema.json   The capture format
derive/to_profile.py         Turns one capture into a profile JSON
```

## Taking a capture

```sh
python3 capture/server/receive.py --out resources/fingerprints/raw
```

Open the printed URL on the target device in a normal browser window and
submit. The receiver writes `<sha256 of the submitted bytes>.json`, records its
decision under `admissions/`, and prints a probe summary that names anything
that failed. It rejects a capture with an automation signal, a `Headless` user
agent, a non-secure context, or a browser major other than the release's.

Every capture records the sha256 of the `collector.js` that measured it, so two
captures taken with different collector versions can be told apart.

## Launch flags are part of the measurement

A capture describes the browser as launched, and some flags change what is
being measured.

`--disable-gpu` makes `CollectGraphicsInfoGL` return the literal string
`"Disabled"` for vendor, renderer and version without calling `glGetString`,
while WebGL, which reaches the driver through the command buffer, still reports
a real ANGLE string. A capture taken that way describes a setup no real user
runs.

A host with no display needs more care. `--headless` does not work: the
receiver rejects a `Headless` user agent. Run a real headed browser on a
virtual display instead. `collect-unattended.py` does that:

```sh
python3 capture/collect-unattended.py --label <name> --out <dir>
```

The browser has to be the release major, or the receiver refuses the capture
on its version alone. A distribution's Chrome package is whatever is current.
Google's apt repository serves only the newest stable, but older stable `.deb`
files stay in the pool. Unpack one beside the installed browser and pass it
with `--chrome`:

```sh
curl -O https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_<version>-1_amd64.deb
dpkg-deb -x google-chrome-stable_<version>-1_amd64.deb /opt/chrome<major>
```

`build/CHROMIUM_VERSION` is the build to match. The exact patch release is not
always in the pool; the nearest one with the same major works.

Under Xvfb the only GL driver is Mesa llvmpipe, so Chrome's software rendering
blocklist turns off `webgl` and `webgpu`, and the `webgl1` and `webgl2` probes
measure nothing, which the receiver rejects. `--enable-unsafe-swiftshader`
allows WebGL's software fallback, and the probes then measure a real software
backend. Prefer it to `--use-gl=angle --use-angle=swiftshader`: that pair
replaces the GL driver, so on a host that does have a GPU it quietly produces a
software capture. The fallback flag changes nothing where a GPU works.

`screen.details` will not prompt during a capture, so the window-management
permission has to exist before the run. The script writes the grant into the
fresh profile under the name Chrome still stores it by, `window_placement`.

A capture taken this way describes SwiftShader and not the host's GPU. The
script prints the renderer it measured so the two cannot be confused, and no
GPU anchor may claim such a capture as hardware.

`--no-sandbox` is needed when running as root and is itself a change from a
normal launch; use a non-root user where possible.

## Format

The capture format is our own, defined in `schema/capture.schema.json`. Vendor
fingerprint formats store a hash of each render plus a short lossy tail, which
is enough to tell two devices apart and never enough to reproduce what either
one drew. We store the PNG and the float samples.

We do not redistribute anyone else's fingerprint corpus.
