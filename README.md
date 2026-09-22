# Apostate

An anti-detect Chromium fork. The fingerprint is changed in C++, at the point
where Chromium produces each value. There is no injected JavaScript, no CDP
override and no wrapped property for a page to find.

Free and open source under GPL-3.0. No paid tier, no licence key, no
telemetry.

Current release: Chromium 152.0.7977.83, for Linux x64, Linux arm64, macOS
arm64 and Windows x64.

## Install

```sh
pip install apostate
```

```sh
npm install @heretic-tech/apostate
```

Patchright ships with both packages and drives the browser. Do not run
`playwright install`. Apostate supplies its own browser, and a Playwright
download would give you the wrong one.

The browser is a separate archive, 150 to 185 MB depending on platform.
`launch()` downloads it on first use. To fetch it ahead of time:

```sh
apostate install          # pip; `python -m apostate install` also works
npx apostate install      # npm
```

Both packages use the same install directory, so having both does not
download twice. `apostate path`, `info`, `clear` and `run` are the other
subcommands; `apostate --help` lists them.

### Pointing at an existing browser

A launch looks in four places, in this order:

| Where | How |
| --- | --- |
| a path you name | `launch(binary_path=...)` or `launch({ executablePath: ... })` |
| `APOSTATE_BINARY` | `export APOSTATE_BINARY=/path/to/chrome` |
| the package's own install | whatever `apostate install` wrote |
| well-known locations | the table below |

| Platform | Searched | For |
| --- | --- | --- |
| macOS | `/Applications`, `~/Applications` | `Chromium.app` |
| Linux | `~/.cache/apostate`, `/opt/apostate` | `chrome` |
| Windows | `%LOCALAPPDATA%\apostate` | `chrome.exe` |

Each directory and one level below it is searched. If you extracted an
archive by hand, move the whole `apostate-152.0.7977.83-<platform>/`
directory, not just the browser inside it. The launcher identifies an Apostate
build by the `build/MANIFEST.lock` and `resources/profiles/` staged beside the
executable, and a stock Chrome or Chromium is never adopted. `binary_path` and
`APOSTATE_BINARY` accept the executable, a macOS `.app` bundle, or the
extracted directory.

`apostate info` prints which source answered and why anything else was passed
over.

### Where the archive comes from

The package downloads
`https://github.com/heretic-tech/apostate/releases/download/v<version>/apostate-152.0.7977.83-<platform>.<ext>`,
`.tar.zst` on Linux and `.zip` on macOS and Windows. The SHA-256 is checked
before the archive is opened; a mismatch aborts without extracting anything.

That digest is a transport check, not provenance. For provenance:

```sh
gh attestation verify apostate-152.0.7977.83-linux-x64.tar.zst --repo heretic-tech/apostate
```

macOS builds from v0.2.0 are signed with a Developer ID certificate,
notarized and stapled, so they open on a double-click.
`codesign -dv --verbose=2 Chromium.app` shows the signature.

## Launch

```python
from apostate import launch

browser = launch(proxy="http://user:pass@host:8080")
page = browser.new_page()
page.goto("https://example.com")
print(page.title())
browser.close()
```

```javascript
import { launch } from "@heretic-tech/apostate";

const browser = await launch({ proxy: "http://user:pass@host:8080" });
const page = await browser.newPage();
await page.goto("https://example.com");
console.log(await page.title());
await browser.close();
```

The `proxy` option takes `http://`, `https://` or `socks5://` with a
credential in the URL. The launcher puts the endpoint on the command line and
passes the credential inside the profile, so it appears in no log, no
socket-pool key and no `chrome://version`. Drop the option for a direct
connection.

The binary also runs on its own, with no flags:

| Platform | Command |
| --- | --- |
| Linux | `./chrome` |
| macOS | `./Chromium.app/Contents/MacOS/Chromium` |
| Windows | `chrome.exe` |

By hand, the same proxy is `--proxy-server=http://user:pass@host:8080`.
Upstream Chromium rejects a credential in that URL with
`ERR_NO_SUPPORTED_PROXIES`; Apostate accepts it and removes it from the switch
before the proxy configuration sees it. [docs/FLAGS.md](docs/FLAGS.md#the-proxy)
has the encoding rules.

### How long an identity lasts

What you pass decides how long the composed device is yours.

| You launch with | The identity is | It lasts |
| --- | --- | --- |
| nothing | drawn from OS entropy | this launch only, recorded nowhere |
| `--user-data-dir=DIR` | bound to `DIR` | until you delete `DIR`; renaming or moving it changes nothing |
| `--fingerprint=SEED` | the one that seed selects | forever, on any host |
| `--fingerprint=host` | none; the host's real values | |

```sh
./chrome --user-data-dir=./work-profile      # same machine, same cookies
./chrome --fingerprint=12345                 # same machine anywhere
```

A profile directory keeps one machine because it already keeps one set of
cookies and logged-in sessions. The first launch writes
`DIR/apostate/identity`; every launch after reads it. Copying the directory
copies the machine. `cat DIR/apostate/identity` gives you a value to pass as
`--fingerprint=` anywhere else.

From the packages, a persistent profile is a context, not a browser option:

```python
context = launch_persistent_context("./work-profile", proxy="http://user:pass@host:8080")
```

```javascript
const context = await launchPersistentContext("./work-profile", { proxy: "http://user:pass@host:8080" });
```

`launch()` refuses `user_data_dir` and `--user-data-dir`, because it returns a
browser and the directory belongs to a context. Reusing a directory is what
keeps the identity stable, with no extra flag. If you were reusing one out of
habit and want a fresh machine each run, drop the directory, give each run its
own, or delete `DIR/apostate/identity` between runs.

The seed is any printable ASCII up to 512 bytes. Nothing is stored for it.

### Locale and timezone

GeoIP is on by default in both packages. Before the browser starts, the launch
resolves a locale and timezone from the network exit and passes them as
`--fingerprint-locale` and `--fingerprint-timezone`. Behind a proxy, the exit
is the proxy's. Neither value is ever drawn from the seed, because a
timezone chosen by a seed cannot match an exit IP.

A failed lookup sets nothing for the field it could not answer and leaves the
host's value there, with a warning in the launch diagnostics. Behind a proxy
that is the wrong country, so pass both explicitly if you need the guarantee.
An explicit value always wins:

```python
browser = launch(proxy="http://user:pass@host:8080", geoip=False)  # keep the host's
browser = launch(locale="de-DE", timezone="Europe/Berlin")         # or say which
```

```javascript
const asHost = await launch({ proxy: "http://user:pass@host:8080", geoip: false });
const asGerman = await launch({ locale: "de-DE", timezone: "Europe/Berlin" });
```

## What a launch presents

A launch with no flags composes a complete device and presents it: GPU
identity and capability tables, core count, installed memory, screen geometry
and window chrome, fonts, media devices, speech voices, locale, timezone and
theme. The host's values are not what a page sees.

Two reads of one value in one launch always agree. Canvas, WebGL and audio
readback are byte-for-byte what stock Chromium of this version produces.
`--fingerprint-noise` is an opt-in, off by default, that perturbs canvas and
WebGL pixel readback by one step; the perturbation is a function of the seed
and the pixels, so the same seed gives the same bytes on every launch and
every route through the canvas agrees.

## Headless Linux servers

This is the deployment most installs run in. No GPU is required and no
graphics switch is needed:

```sh
./chrome --headless=new --fingerprint=12345
```

The claimed operating system selects the GPU identity, so a GPU-less server
presents the renderer string and capability tables of the OS it claims rather
than its own software rasteriser.

On a Linux host the default persona is Windows. That persona needs the
Windows fonts installed on the host: 35 families, listed with sources in
[docs/FONTS.md](docs/FONTS.md). A Windows machine without Arial does not exist,
and text metrics measure it. This is the one setup step on a Linux server, and
missing fonts are the most common reason a session is blocked. The
alternative, `--fingerprint-platform=linux`, composes the host's own OS and
needs nothing installed.

Both packages default to headless, so the `launch()` examples above run
unchanged on such a host.

For the strictest targets, run headed on a virtual display:

```sh
sudo apt install xvfb
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
```

```python
browser = launch(fingerprint=12345, headless=False, proxy="http://user:pass@host:8080")
```

The launcher reads `DISPLAY` from its environment, and so does the binary run
directly: `DISPLAY=:99 ./chrome --fingerprint=12345`.

## Pin an identity

A new machine on every visit from one address is its own signal, and systems
such as reCAPTCHA v3 score a returning visitor higher. Pass a seed to get the
same device back:

```sh
./chrome --fingerprint=12345 --fingerprint-platform=windows
```

`--fingerprint-platform` chooses the operating system the identity presents
as, and the GPU follows it. The default is the host's OS on macOS and Windows,
and Windows on Linux.

To see what a launch decided and why:

```sh
./chrome --fingerprint=12345 --fingerprint-explain
```

This prints the resolved value for every surface, where it came from, the
limitations that apply on this host, and the argument that reproduces the
identity, then exits. It is the first thing to run when a site blocks you; the
`limitations` block names what this host could not serve.

```text
  seed                616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
  seed source         drawn from OS entropy for this launch only (ephemeral)
  reproduce with      --fingerprint=616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
```

## Flags

| Flag | Value | Effect |
| --- | --- | --- |
| `--fingerprint` | printable ASCII, up to 512 bytes | Seed for the whole identity. |
| `--fingerprint=host` | `host` | Compose nothing; present the host's real values. |
| `--fingerprint-platform` | `windows`, `macos`, `linux` | OS the identity presents as, GPU included. |
| `--fingerprint-anchor` | anchor id | Pin the GPU capability cluster instead of letting the seed draw one. |
| `--fingerprint-explain` | none | Print the composition report and exit. |
| `--fingerprint-noise` | none | Deterministic one-step perturbation of canvas and WebGL readback. Off by default. |
| `--fingerprint-gpu-vendor` | string | `UNMASKED_VENDOR_WEBGL`. |
| `--fingerprint-gpu-renderer` | string | `UNMASKED_RENDERER_WEBGL`. |
| `--fingerprint-hardware-concurrency` | positive integer | `navigator.hardwareConcurrency`. |
| `--fingerprint-device-memory` | positive integer, GiB | Installed memory. |
| `--fingerprint-screen-width` | positive integer, CSS px | `screen.width`. |
| `--fingerprint-screen-height` | positive integer, CSS px | `screen.height`. |
| `--fingerprint-timezone` | IANA name | For example `America/New_York`. |
| `--fingerprint-locale` | `Accept-Language` list | For example `en-US,en`. |
| `--fingerprint-webrtc-ip` | IP address | Address written into ICE candidate text. |
| `--fingerprint-webrtc-udp` | `direct`, `block` | Where WebRTC's UDP goes. Absent means relayed through a SOCKS5 proxy, direct with no proxy, and no socket under a proxy that cannot carry datagrams. |
| `--apostate-profile` | base64 JSON | Launch a profile you composed yourself. |

A per-field switch sets that field and the seed fills in the rest. A value
the host cannot serve is refused on stderr and the launch exits non-zero.

Precedence, strongest first: `--apostate-profile`, `--fingerprint=host`, the
per-field overrides, `--fingerprint=<seed>`, then the seed a bare launch
draws. `--fingerprint=host` composes nothing, so pairing it with a persona, an
anchor or a per-field override stops the launch instead of silently winning.

Every standard Chromium flag still works, including `--headless`,
`--user-data-dir` and `--lang`. Drive automation over
`--remote-debugging-pipe` rather than `--remote-debugging-port`; a page can
detect an open debugging port, and Playwright already uses the pipe.

[docs/FLAGS.md](docs/FLAGS.md) is the full reference.

## What is and is not changed

Changed, from the profile:

- GPU vendor and renderer strings, WebGL and WebGPU extensions, numeric limits
  and shader precision
- Logical core count and installed memory
- Screen size, available area, device pixel ratio, colour depth, colour gamut,
  HDR, window chrome deltas
- Platform, OS version, architecture, bitness, WoW64, form factor, and the
  User-Agent and Client Hints built from them
- Font enumeration, generic family mapping, and text rasterisation parameters
- Media device counts, as a floor. Inputs are added to reach the count; the
  host's own devices are never removed
- Network information and battery state
- Speech voice list
- Locale, Accept-Language, timezone, keyboard layout
- Colour scheme, accent colours, pointer and hover capability
- Hardware video decode reported by `MediaCapabilities`
- The `chrome.runtime` object an externally-connectable extension leaves
  behind, present or absent by seed
- WebRTC: candidate text, and the packets themselves, which follow a SOCKS5
  proxy's UDP relay

Canvas, text metrics, client rects and audio are rendered by Chromium's own
rasteriser and audio graph under the profile's fonts, screen and GPU values,
not replayed from a recording.

Not changed:

- The Chromium version. The binary is the version it reports.
- Anything the machine cannot do. A profile presents fewer cores, less memory,
  a smaller screen and fewer codecs than the host has, never more.
- Rendering throughput and rendered bytes. The persona moves the GPU identity,
  but a software backend under a discrete-GPU identity still renders at
  software speed, and a texture at the reported `MAX_TEXTURE_SIZE` does not
  allocate.
- Fonts that are not installed. Enumeration can remove a family, not add one.
- Widevine DRM on an ephemeral profile. The CDM cannot be redistributed, so it
  is not in the archive. A persistent `--user-data-dir` fetches it from Google
  at runtime as Chrome does. For ephemeral profiles, `apostate provision-drm`
  (pip) copies a CDM already on the machine into the browser's component
  directory, once.

[docs/LIMITATIONS.md](docs/LIMITATIONS.md) has every residual with its
measurement.

## Platforms

| Target | Archive |
| --- | --- |
| `linux-x64` | `apostate-152.0.7977.83-linux-x64.tar.zst` |
| `linux-arm64` | `apostate-152.0.7977.83-linux-arm64.tar.zst` |
| `macos-arm64` | `apostate-152.0.7977.83-macos-arm64.zip` |
| `windows-x64` | `apostate-152.0.7977.83-windows-x64.zip` |

There is no Intel macOS build, no 32-bit Windows build and no mobile build.

## Build from source

Every input is pinned: Chromium revision, depot_tools revision, GN args,
container image digest and patch series. The same pins produce the same
binary. [docs/BUILD.md](docs/BUILD.md) has the steps.

## Documentation

| Page | Contents |
| --- | --- |
| [docs/FLAGS.md](docs/FLAGS.md) | Every switch, its values and precedence |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | What a page can still tell, with measurements |
| [docs/FONTS.md](docs/FONTS.md) | Installing a persona's fonts |
| [docs/PROFILE_SPEC.md](docs/PROFILE_SPEC.md) | Profile fields, launch configuration, package API |
| [docs/FINGERPRINTS.md](docs/FINGERPRINTS.md) | How a seed becomes a device |
| [docs/BUILD.md](docs/BUILD.md) | Building from source |
| [docs/RELEASE.md](docs/RELEASE.md) | How releases are cut and verified |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | The rules the patches follow |
| [python/README.md](python/README.md), [npm/README.md](npm/README.md) | Package options and cache layout |

## Licence

GPL-3.0. See [LICENSE](LICENSE).
