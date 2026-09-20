# Apostate

An anti-detect Chromium fork. The fingerprint is changed in C++ where Chromium
produces the value, so there is no injected JavaScript, no CDP override and no
wrapped property for a page to find.

Free and open source, GPL-3.0. No paid tier, no licence key, no gated build, no
telemetry, no phone-home.

- [Flag reference](docs/FLAGS.md)
- [Known limitations](docs/LIMITATIONS.md)
- [Profile and launch specification](docs/PROFILE_SPEC.md)

## Install

```sh
pip install apostate
```

```sh
npm install @heretic-tech/apostate
```

That is the whole install. Patchright comes with the package and is what drives
the browser; `playwright`, and `puppeteer` on Node, work too if you would
rather use one of those, and none of them needs `playwright install` — Apostate
supplies its own browser and a Playwright download would give you the wrong
one.

**Then get the browser.** It is not bundled: the package is a launcher, and the
browser is a separate ~150 MB archive.

```sh
apostate install          # pip; `python -m apostate install` also works
npx apostate install      # npm
```

`launch()` does this on first use as well, so the explicit command is only for
provisioning ahead of time. Both packages share one install on every platform,
so having both does not download twice.

**Already have the browser?** Say where, or let it be found. Four sources, in
this order:

| Where a launch looks | How |
| --- | --- |
| the path you name | `launch(binary_path=…)` / `launch({ executablePath: … })` |
| `APOSTATE_BINARY` | `export APOSTATE_BINARY=/path/to/chrome` |
| this package's own install | whatever `apostate install` wrote |
| well-known locations | the table below |

The first two are you naming a file and are taken at your word. The last two
are searches, and what they find has to prove itself.

| Platform | Searched | For |
| --- | --- | --- |
| macOS | `/Applications`, `~/Applications` | a `Chromium.app` or `Apostate.app` |
| Linux | `~/.cache/apostate`, `/opt/apostate` | a `chrome` |
| Windows | `%LOCALAPPDATA%\apostate` | a `chrome.exe` |

Each directory and one level below it, so an archive extracted where it landed
is found where it sits, and nothing deeper is walked.

**Extracted it by hand?** Move the whole extracted directory, not just the
browser inside it. The archive unpacks to
`apostate-152.0.7977.83-<platform>/`, holding the browser next to
`build/MANIFEST.lock` and `resources/profiles/`, and those two are what say
the browser is this one — a lone `Chromium.app` dragged into `/Applications`
is passed over with "no Apostate payload beside it". Put the directory in
`/Applications` or `~/Applications` on macOS, `~/.cache/apostate` or
`/opt/apostate` on Linux, `%LOCALAPPDATA%\apostate` on Windows, and it is
found where it sits. Or skip placement and name it: `binary_path` and
`APOSTATE_BINARY` each accept the executable, a macOS `.app` bundle, or the
extracted directory.

**macOS: "Chromium is damaged and can't be opened."** It is not damaged.
Gatekeeper marked the archive when a browser downloaded it, and an unsigned
bundle carrying that quarantine attribute is reported as damaged rather than
as unsigned. Clear it on what you extracted:

```sh
xattr -dr com.apple.quarantine ~/Applications/apostate-152.0.7977.83-macos-arm64
```

`apostate install` never needs this. It fetches the archive itself and sets
no quarantine attribute, so only a copy you downloaded through a browser is
affected.

**A stock Chrome or Chromium is never adopted.** The executable is named
`chrome` and the bundle `Chromium.app` exactly as upstream names them, and
Chromium 152.0.7977.83 exists upstream too, so the file on its own proves
nothing. What is checked is the payload staged beside it — `build/MANIFEST.lock`,
which carries this build's patch-series digests, or `resources/profiles/` — and
the version, and both are required. Marker first, then version: nothing is
executed until a file only an Apostate payload carries has already vouched for
the tree. A stock Chrome started with these switches would be a session with no
protection at all and nothing on screen to say so, which is the worst thing
this package could do.

`apostate info` prints which source answered and why anything else was passed
over — trimmed here to the discovery fields:

```sh
apostate info      # npx apostate info
```

```json
{
  "executable": "/opt/apostate/apostate-152.0.7977.83-linux-x64/chrome",
  "executable_source": "well-known",
  "discovery": {
    "order": ["argument", "environment", "cache", "well-known"],
    "searched": ["/opt/apostate"],
    "rejected": [
      {
        "path": "/opt/chromium/chrome",
        "reason": "no Apostate payload beside it (build/MANIFEST.lock or resources/profiles/catalogue.json)"
      }
    ]
  }
}
```

The archive itself comes from this repository's GitHub release, at
`https://github.com/heretic-tech/apostate/releases/download/v<version>/apostate-<chromium-version>-<platform>.tar.zst`
for the Linux targets and `.zip` for macOS and Windows, which is what
`binary_info()["artifact_url"]` returns. Its SHA-256 is checked before the
archive is opened, and a mismatch aborts without extracting anything.

Where that digest came from is the part worth being exact about, and
`apostate info` reports it as `manifest_source`. A package published after
its binaries carries the manifest inside it (`baked`): the digest was pinned
before the download existed and does not depend on it. A package published
*before* them fetches the manifest the release publishes beside each archive
— `releases/download/v<package version>/<archive>.manifest.json` first, then
`releases/latest/download/<archive>.manifest.json` (`release-tag`,
`release-latest`) — which is how a launcher installs binaries released under
another version, and why a launcher fix does not wait on a Chromium rebuild.
A fetched manifest is a transport-integrity check and not provenance: it
catches a truncated or corrupted download and cannot catch a substituted
release, because the digest came from the same place as the bytes. For
provenance, `gh attestation verify <archive> --repo heretic-tech/apostate`.

`launch()` refuses only after both URLs fail, and names both. The answers
then are to point the package at a local archive, at an existing install, or
to [build the archive yourself](docs/BUILD.md). [docs/RELEASE.md](docs/RELEASE.md)
covers how releases are cut, verified, and baked into a package afterwards.

`python/README.md` and `npm/README.md` have the option tables and the cache
layout.

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

The package option is the path to use for a proxy. It splits the URL the way
the browser does: the endpoint goes on the command line and the credential
travels inside the profile envelope, so it is in no log, no socket-pool key and
no `chrome://version`. `http://`, `https://` and `socks5://` all take a
credential this way. Drop the option for a direct launch.

Or run the binary directly, which needs no flags at all:

| Platform | Command |
| --- | --- |
| Linux | `./chrome` |
| macOS | `./Chromium.app/Contents/MacOS/Chromium` |
| Windows | `chrome.exe` |

By hand the same proxy is `--proxy-server=http://user:pass@host:8080`. A
credential in that URL is accepted here and refused by upstream Chromium with
`ERR_NO_SUPPORTED_PROXIES`; it is lifted off the switch before Chromium's proxy
configuration sees it. The package option above is the primary path and this is
the alternative; [docs/FLAGS.md](docs/FLAGS.md#the-proxy) has the encoding
rules and the schemes that take one.

### The identity

What you pass decides how long the composed device is yours, and there are
three answers plus an off switch:

| You launch with | The identity is | It lasts |
| --- | --- | --- |
| nothing | drawn fresh from OS entropy | this launch only, recorded nowhere |
| `--user-data-dir=DIR` | bound to `DIR` | until you delete `DIR`; it survives renaming and moving it |
| `--fingerprint=SEED` | the one that seed selects | forever, on any host — the seed *is* the identity |
| `--fingerprint=host` | no identity; the host's real values | — |

```sh
./chrome --user-data-dir=./work-profile      # same machine, and same cookies
./chrome --fingerprint=12345                 # same machine anywhere
```

[How long an identity lasts](#how-long-an-identity-lasts) has the rest,
including what a persistent profile writes, and where.

### Locale and timezone

GeoIP is on by default in both packages. Before the browser starts, the launch
resolves a locale and timezone from the network exit — the proxy's exit when a
proxy is configured, the direct IP otherwise — and passes them as
`--fingerprint-locale` and `--fingerprint-timezone`, so the device a seed drew
does not turn up in the wrong country. Neither is ever drawn from the seed: a
timezone a seed chose cannot correlate with an exit IP.

A failed or partial lookup invents nothing. It sends no override for the field
it could not answer for, leaves the host's own value there, and warns into the
launch's diagnostics rather than raising. Turn it off, or say what you want
instead — an explicit value outranks GeoIP:

```python
browser = launch(proxy="http://user:pass@host:8080", geoip=False)  # keep the host's
browser = launch(locale="de-DE", timezone="Europe/Berlin")         # or say which
```

```javascript
const asHost = await launch({ proxy: "http://user:pass@host:8080", geoip: false });
const asGerman = await launch({ locale: "de-DE", timezone: "Europe/Berlin" });
```

When the lookup does not answer, the value that applies is the host's own —
which behind a proxy is the wrong country. That is what makes GeoIP
best-effort geo-matching, and passing both explicitly is how it is guaranteed.

## Default behaviour

A launch with no flags composes a complete device profile and presents it. GPU
identity and capability tables, CPU core count, installed memory, screen
geometry and window chrome, fonts, media devices, speech voices, locale,
timezone and theme all come from that profile. The host's own values are not
what a page sees.

A launch with no `--fingerprint` and no `--user-data-dir` draws its own seed,
so every such launch is a different coherent device and nothing about it is
written to disk. A launch against a named `--user-data-dir` keeps one device
for that directory, in a file inside it; see [The identity](#the-identity).

Two reads of the same value inside one launch always agree. There is no canvas
noise, no WebGL noise and no audio noise anywhere in this browser. Variation
comes from a different seed, which is a different device.

## Headless Linux servers

This is the deployment most Apostate installs run in, and it is supported
directly rather than tolerated. No GPU is required and no graphics switch has to
be passed:

```sh
./chrome --headless=new --fingerprint=12345
```

No GPU is needed because the claimed operating system, not the host's graphics
stack, selects the GPU identity. A GPU-less server presents the capability
cluster and renderer string of the OS it claims, and the host's own software
rasteriser is not what a page sees.

**On a Linux host the default claimed OS is Windows, so install the Windows
fonts.** That is the one setup step this deployment has, and it is the most
common cause of a session being blocked. A Windows persona whose Windows faces
are missing is a Windows machine without Arial, which is not a machine that
exists, and text metrics measure it. The families, where to copy them from and
how to check are in [docs/FONTS.md](docs/FONTS.md). The alternative is
`--fingerprint-platform=linux`, which composes the host's own OS and needs
nothing installed.

Windows-on-Linux is the default because it is the least bad cross-OS pairing and
what most deployments want, not because it is free. A macOS persona on a Linux
host is the riskier one: the macOS core set is 184 families.

What a page can still tell on such a host is render *timing* and per-pixel
output, which come from the rasteriser rather than from the identity, and one
allocation probe: a texture at the reported `MAX_TEXTURE_SIZE` does not
actually allocate on a software backend.
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) has the measured numbers for all of
it, and the status: this is new in this release and is on that page's list of
things written but not yet run, so read the renderer string and
`getSupportedExtensions()` from a page on your own host before relying on
either.

Both packages default to headless, so the `launch()` examples above run
unchanged on such a host.

### Headed on a virtual display

For the most aggressive targets, the suites that score behaviour as well as the
fingerprint, run headed on a virtual display instead. There is still no GPU
involved: the display is an X server with nothing behind it.

```sh
sudo apt install xvfb
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
```

Then launch headed, behind a residential proxy:

```python
from apostate import launch

browser = launch(
    fingerprint=12345,
    headless=False,
    proxy="http://user:pass@residential-host:8080",
)
```

```javascript
import { launch } from "@heretic-tech/apostate";

const browser = await launch({
  fingerprint: 12345,
  headless: false,
  proxy: "http://user:pass@residential-host:8080",
});
```

`DISPLAY` is read from the environment the launcher runs in, so exporting it
before the script is the whole of the wiring. The binary run directly takes the
same environment:

```sh
DISPLAY=:99 ./chrome --fingerprint=12345
```

## Pin an identity

A random identity per session looks like a different machine every time. Hitting
one site repeatedly from one address with a new machine each time is its own
signal, and scoring systems such as reCAPTCHA v3 reward a returning visitor. Pass
a seed to get the same device back:

```sh
./chrome --fingerprint=12345
```

```python
browser = launch(args=["--fingerprint=12345"])
```

The seed is any printable ASCII up to 512 bytes. Integers are the usual choice.
Nothing is stored for it, so the same seed gives the same device on any host
that can serve it — a seed travels as a string, where a profile directory has
to be copied.

`--fingerprint-platform` chooses which operating system the identity presents
as, and the GPU follows it:

```sh
./chrome --fingerprint=12345 --fingerprint-platform=windows
```

The default depends on the host: a macOS host claims macOS, a Windows host
claims Windows, and a Linux host claims Windows. The first two are the host's
own OS. The third is not, deliberately — it is the least bad cross-OS pairing
and what most deployments want — and it is the case that needs fonts.

A persona that does not match the host needs that platform's fonts installed on
the machine, which is a one-time setup step you do yourself:
[docs/FONTS.md](docs/FONTS.md). Running without them is a common reason a
session gets blocked. Read [known limitations](docs/LIMITATIONS.md) too, because
the persona moves the GPU identity on every host while the rasteriser that
actually draws stays the host's.

To see exactly what a launch decided and why, ask it:

```sh
./chrome --fingerprint=12345 --fingerprint-explain
```

It prints the resolved value per surface, where the value came from, and the
limitations that apply on this host, then exits. Output goes to stdout and is
not reachable from a page.

The report names which of the three identity lifetimes the launch is in, and
ends with the argument that recreates it — which is how you keep a random
identity you liked:

```text
  seed                616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
  seed source         drawn from OS entropy for this launch only (ephemeral)
  reproduce with      --fingerprint=616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
```

Against a `--user-data-dir` it names the file the identity is bound to, and
whether this launch is the one that created it:

```text
  seed                4f3c8a1e09b7d2650c3ab8f41d7e5920ac6b13f8e04d7a29bb5c1e6370d8f425
  seed source         this profile's identity file (stable for this --user-data-dir)
  identity file       /home/you/work-profile/apostate/identity (read from disk)
  reproduce with      --fingerprint=4f3c8a1e09b7d2650c3ab8f41d7e5920ac6b13f8e04d7a29bb5c1e6370d8f425
```

It is also the first thing to run when a site blocks you. The report carries a
`limitations` block naming what this host could not serve, and most blocks turn
out to be listed there. [docs/FLAGS.md](docs/FLAGS.md) walks through the three
common ones and what to do about each.

## How long an identity lasts

Three lifetimes. Pick the one you want; the launch picks it for you from what
you pass.

| You launch with | The identity is | It lasts |
| --- | --- | --- |
| `--fingerprint=<seed>` | the one that seed selects | forever, anywhere — the seed *is* the identity |
| `--user-data-dir=DIR` | bound to `DIR` | until you delete `DIR`; it survives renaming and moving it |
| neither | drawn fresh from OS entropy | this launch only, recorded nowhere |

A `--user-data-dir` keeps one machine because it keeps everything else. The
first launch against a directory mints an identity into
`DIR/apostate/identity` and every launch after reads it back. That directory
already holds cookies, localStorage and logged-in sessions, so a different
GPU, core count, installed memory and panel on every visit would show a site
one account whose hardware keeps changing — which no real machine does, and
which is a worse story than either signal alone.

So Playwright's `launch_persistent_context` needs no flag. It reuses one
directory by design, and that reuse is now what makes the identity stable:

```python
browser = launch_persistent_context(user_data_dir="./work-profile")
```

**If you wanted a fresh machine each run** and have been reusing a directory
out of habit, you now have to say so, because you will otherwise get the same
one every time. Either drop `--user-data-dir` — that is the ephemeral default,
and the right answer if you did not need the cookies either — or give each run
its own directory, or delete `DIR/apostate/identity` between runs.

Read the identity to move a machine somewhere else, write it to choose one by
hand:

```sh
cat ./work-profile/apostate/identity     # -> pass as --fingerprint=… anywhere
```

Copying a profile directory clones its machine, deliberately: the copy has the
same logged-in sessions, and an identity that changed under them would defeat
the point.

`--fingerprint-explain` names which of the three you got and which file it came
from. [docs/FLAGS.md](docs/FLAGS.md#how-long-an-identity-lasts) has the rest,
including what happens when the directory is read-only or the file is damaged.

## Flags

| Flag | Value | Effect |
| --- | --- | --- |
| `--fingerprint` | printable ASCII, up to 512 bytes | Seed for the whole identity. Same seed, same device. |
| `--fingerprint=host` | `host` | Compose nothing and present the host's real values. |
| `--fingerprint-platform` | `windows`, `macos`, `linux` | Which OS the identity presents as, GPU included. Defaults to the host's own OS on macOS and Windows, and to `windows` on Linux. |
| `--fingerprint-anchor` | anchor id | Pin the GPU capability cluster instead of letting the seed draw one. |
| `--fingerprint-explain` | none | Print the composition report to stdout and exit. |
| `--fingerprint-gpu-vendor` | exact WebGL vendor string | WebGL `UNMASKED_VENDOR_WEBGL`. |
| `--fingerprint-gpu-renderer` | exact WebGL renderer string | WebGL `UNMASKED_RENDERER_WEBGL`. |
| `--fingerprint-hardware-concurrency` | positive integer | `navigator.hardwareConcurrency`. |
| `--fingerprint-device-memory` | positive integer, GiB | Installed memory. |
| `--fingerprint-screen-width` | positive integer, CSS px | `screen.width`. |
| `--fingerprint-screen-height` | positive integer, CSS px | `screen.height`. |
| `--fingerprint-timezone` | IANA name | Timezone, for example `America/New_York`. |
| `--fingerprint-locale` | `Accept-Language` list | For example `en-US,en`. |
| `--fingerprint-webrtc-ip` | IP address | Replace the address in WebRTC ICE candidate text. |
| `--fingerprint-webrtc-udp` | `direct`, `block` | Where WebRTC's UDP packets go. Absent means relay through a proxy that can carry datagrams, direct with no proxy, and no socket at all under a proxy that cannot. |
| `--apostate-profile` | base64 JSON | Launch a profile you composed yourself, bypassing the seed. |

A per-field switch sets that one field and the seed fills in the rest. A value
the host cannot serve is refused on stderr and the launch exits non-zero rather
than quietly ignoring it.

Precedence, strongest first: `--apostate-profile`, then `--fingerprint=host`,
then the per-field overrides, then `--fingerprint=<seed>`, then the fresh seed a
bare launch draws. `--fingerprint=host` is the one row that refuses rather than
outranks: it composes nothing, so pairing it with a persona, an anchor pin or a
per-field override stops the launch instead of quietly winning.
`--fingerprint-explain` still works with it.

Every standard Chromium flag still works, including `--headless`,
`--user-data-dir` and `--lang`. `--proxy-server` works too and is the one that
is not merely standard: a credential in the URL is accepted, as
`--proxy-server=socks5://user:pass@host:1080`, which upstream refuses with
`ERR_NO_SUPPORTED_PROXIES`. The credential is taken off the switch before
Chromium's proxy configuration sees it, so it stays out of NetLog, socket-pool
keys and `chrome://version`. From a package, use the `proxy` option instead —
[Launch](#launch) — which does the same split for you; this is the path for
driving the binary by hand.
[docs/FLAGS.md](docs/FLAGS.md#the-proxy) has the encoding rules, the schemes
that take one, and what happens if you also supply one in a profile envelope.
Drive automation with `--remote-debugging-pipe` rather than
`--remote-debugging-port`: a page can detect an open debugging port. Playwright
already uses the pipe.

[docs/FLAGS.md](docs/FLAGS.md) is the full reference, with accepted values,
precedence and the profile fields behind each flag.

## What is and is not changed

Changed, from the profile:

- GPU vendor and renderer strings, WebGL and WebGPU extensions, numeric limits
  and shader precision
- Logical core count and installed memory
- Screen size, available area, device pixel ratio, colour depth, colour gamut,
  HDR, window chrome deltas
- Platform, OS version, architecture, bitness, WoW64, form factor, and the User
  Agent and Client Hints built from them
- Font enumeration and generic family mapping
- Media device counts, as a floor: extra inputs are added to reach the count,
  and the host's own devices are never removed
- Network information and battery state
- Speech voice list
- Locale, Accept-Language, timezone, keyboard layout
- Colour scheme, accent colours, pointer and hover capability
- Hardware video decode reported by `MediaCapabilities`

Rendered against those inputs rather than replayed: canvas, text metrics, client
rects and audio come out of Chromium's own rasteriser and audio graph, running
under the profile's fonts, screen and GPU values.

Not changed, and not claimable:

- The Chromium version. The binary really is the version it reports.
- Anything the machine cannot do. A profile presents fewer cores, less memory,
  a smaller screen and fewer codecs than the host has, never more.
- The rasteriser that actually draws. The persona moves the GPU identity, so the
  graphics backend *is* claimable now; what is not is throughput and rendered
  bytes. A software backend under a discrete-GPU identity renders at software
  speed and hashes differently, and a texture at the reported
  `MAX_TEXTURE_SIZE` does not allocate — see
  [Headless Linux servers](#headless-linux-servers) and
  [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
- Fonts that are not installed. Enumeration removes families; it cannot add one
  without the font file.
- The WebRTC packet source, for now. A relay through a SOCKS5 proxy is written
  and has not been exercised, so plan as though a peer that completes a
  connectivity check sees the real address.
- Widevine DRM without a persistent `--user-data-dir`. The CDM is fetched at
  runtime and stored there, so a throwaway profile has no DRM.

[docs/LIMITATIONS.md](docs/LIMITATIONS.md) has the full list with the reason for
each, and it opens with the behaviours in this release that have been written
but not yet run.

## Platform support

| Target | Archive | Built |
| --- | --- | --- |
| `linux-x64` | `apostate-152.0.7977.83-linux-x64.tar.zst` | yes |
| `linux-arm64` | `apostate-152.0.7977.83-linux-arm64.tar.zst` | yes |
| `macos-arm64` | `apostate-152.0.7977.83-macos-arm64.zip` | yes |
| `windows-x64` | `apostate-152.0.7977.83-windows-x64.zip` | not yet |

All four are the contract. Three have built green on CI; no Windows build has
completed yet, so no `windows-x64.zip` exists. The packages' Windows
acquisition and discovery paths are written to the same contract as the others
and have never been run on Windows: the `.zip` branch, the `chrome.exe`
location and the `%LOCALAPPDATA%\apostate` search are covered only by
synthetic-archive and planted-payload tests. `--version` is never used to
identify a Windows install, because `chrome.exe` does not answer it.

Based on Chromium 152.0.7977.83. There is no Intel macOS build, no 32-bit
Windows build and no mobile build.

## Build from source

Every input is pinned: Chromium revision, depot_tools revision, GN args,
container image digest and patch series. Same pins in, same binary out.
[docs/BUILD.md](docs/BUILD.md) has the steps.

## Documentation

| Page | What is in it |
| --- | --- |
| [docs/FLAGS.md](docs/FLAGS.md) | Every switch, its values, and precedence |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | What a page can still tell, and why |
| [docs/FONTS.md](docs/FONTS.md) | Installing a persona's fonts |
| [docs/PROFILE_SPEC.md](docs/PROFILE_SPEC.md) | Profile fields, launch configuration, package API |
| [docs/FINGERPRINTS.md](docs/FINGERPRINTS.md) | How a seed becomes a device |
| [docs/BUILD.md](docs/BUILD.md) | Building from source |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | The engineering rules the patches follow |

## Licence

GPL-3.0. See [LICENSE](LICENSE).
