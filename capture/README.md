# Capture

Tools that record what a real machine's browser shows a web page. Personas are
built from these captures and measured against them. The same collector page
opens in an Apostate build, so a build can be compared with a capture of the
machine it claims to be.

The full guide, with every option, is at
[docs.apostate.dev/contributing/captures](https://docs.apostate.dev/contributing/captures).

## Rules

1. **Measure or record the failure.** Each probe returns `{ok, value, error}`.
   A probe that throws records its error and a `null` value. There are no
   fallback values: a silently zeroed audio buffer once made three different
   devices produce one fingerprint.
2. **Keep raw values.** Store the canvas PNG and pixels, the audio samples,
   full client-rect coordinates and every queried WebGL parameter. Compute
   hashes from the stored values. A hash can check a value but cannot rebuild
   it.
3. **Read twice.** Each deterministic probe runs twice in one session and both
   results are kept, which shows the fields that move on real hardware.
4. **Record the context.** Time, device label, User-Agent, the SHA-256 of the
   `collector.js` that measured it, device pixel ratio, secure context,
   headed, and any automation signals.
5. **No automation.** Captures come from a normal headed browser. A collector
   run under CDP records exactly what Apostate removes.

## Layout

```
collector/                   The measurement page. No dependencies, no build step
server/receive.py            Serves the page, checks each capture, writes admitted ones to disk
collect-unattended.py        Takes a capture on a Linux host with no display
take-capture.sh              Takes a capture on a rented GPU host and submits it to a receiver
schema/capture.schema.json   The capture format
derive/to_profile.py         Turns one capture into a profile JSON
```

## Take a capture

```sh
python3 capture/server/receive.py --out resources/fingerprints/raw
```

Open the printed URL in a normal Chrome window on the machine and click
**Run capture**. The page must be a secure context: plain `http` counts only
on `localhost`, so for another device use `--cert` and `--key`, or forward the
port with `ssh -N -L 8777:127.0.0.1:8777 <host>`.

The receiver writes an admitted capture to `<sha256 of the bytes>.json`,
records every decision under `admissions/`, and prints a summary naming each
failed probe. It rejects a capture with an automation signal, a `Headless`
User-Agent, a non-secure context, any failed probe, or a browser major other
than the release's (`build/CHROMIUM_VERSION`). A rejected capture's body is
not kept.

## A host with no display

`--headless` is rejected. `collect-unattended.py` runs a headed browser on Xvfb
instead:

```sh
python3 capture/collect-unattended.py --label <name> --out <dir> --chrome <chrome 152>
```

Google's apt repository serves only the newest stable Chrome, but older `.deb`
files stay in the pool. Unpack one and pass it with `--chrome`:

```sh
curl -O https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_<version>-1_amd64.deb
dpkg-deb -x google-chrome-stable_<version>-1_amd64.deb /opt/chrome<major>
```

The exact patch release is not always in the pool. Any release with the same
major is admitted.

Under Xvfb the only GL driver is Mesa llvmpipe, which Chrome's blocklist
disables WebGL and WebGPU for, so the script passes
`--enable-unsafe-swiftshader`. That allows WebGL's software fallback and
changes nothing where a GPU works. `--use-angle=swiftshader` would replace the
driver even on a GPU host. A capture taken this way describes SwiftShader, not
the host's GPU, and no GPU family may claim it as hardware.

`screen.details` does not prompt during a capture, so the script writes the
window-management grant into the fresh profile, under the name Chrome still
stores it by, `window_placement`.

As root the script adds `--no-sandbox`, a change from a normal launch. Use a
non-root user where you can.

## Switches change the measurement

A capture describes the browser as launched. With `--disable-gpu`, GPU
information outside WebGL reads `"Disabled"` while WebGL still reports a real
ANGLE renderer, a setup no real user runs.

## Format

The format is defined in `schema/capture.schema.json`. Common fingerprint
formats store a hash of each render, which can tell two devices apart but can
never reproduce what either drew. A capture stores the PNG and the samples.

Do not add anyone else's fingerprint corpus to this repository.
