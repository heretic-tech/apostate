# Apostate

Apostate is a Chromium build that presents a machine you choose: a Windows,
macOS or Linux persona with a GPU, screen, fonts, voices and locale to match.
The values are changed in Chromium's C++ where they are produced. There is no
injected JavaScript and no CDP override for a page to find. The Python and
Node packages download the browser and launch it with Patchright, a drop-in
Playwright build, or with Playwright; the Node package also takes Puppeteer.

Free and open source under GPL-3.0. The current build is Chromium
152.0.7977.83 for Linux x64, Linux arm64, macOS arm64 and Windows x64.

## Install

```sh
pip install apostate
npm install @heretic-tech/apostate
```

The browser is a separate download. The first launch fetches it and checks its
SHA-256; `apostate install` (Python) or `npx apostate install` (Node) does it
ahead of time. Both packages share one install. Do not run
`playwright install`: Apostate brings its own browser.

The CLI also has `path`, `info`, `clear`, `run` and `fonts`. To use a browser
you already have, pass `binary_path` (Python) or `executablePath` (Node), or
set `APOSTATE_BINARY`.

## Quick start

```python
from apostate import launch

browser = launch(
    fingerprint=42,                        # same seed, same machine
    fingerprint_platform="windows",        # windows, macos or linux
    proxy="socks5://user:pass@host:1080",
)
page = browser.new_page()
page.goto("https://example.com")
browser.close()
```

```javascript
import { launch } from "@heretic-tech/apostate";

const browser = await launch({
  fingerprint: 42,
  fingerprintPlatform: "windows",
  proxy: "socks5://user:pass@host:1080",
});
const page = await browser.newPage();
await page.goto("https://example.com");
await browser.close();
```

- `fingerprint` is the seed. The same seed and persona give the same machine
  on every launch and every host, until the browser version changes. Cores
  and memory are capped at the host's. Leave the seed out to get a new machine
  each launch.
- `fingerprint_platform` (`fingerprintPlatform`) picks the persona's
  operating system. The default is the host's own on macOS and Windows, and
  Windows on Linux.
- `proxy` takes `http://`, `https://` or `socks5://`, with the credential in
  the URL. The browser keeps the credential out of `chrome://version`, its
  logs and its error messages.
- `geoip` is on by default: before the browser starts, the package looks up
  the proxy exit's locale and timezone and gives them to the persona. Pass
  `locale` and `timezone` to set them yourself. With `geoip=False` and neither
  set, the persona uses `en-US` and the host's timezone.
- `new_page()` opens pages in a normal profile, a temporary one deleted when
  the browser closes. A context from `new_context()` is off-the-record, as in
  Playwright, and sites can tell.

To keep one machine across runs, together with its cookies and logins, use a
persistent context:

```python
from apostate import launch_persistent_context

context = launch_persistent_context("./profiles/alice", proxy="socks5://user:pass@host:1080")
```

```javascript
import { launchPersistentContext } from "@heretic-tech/apostate";

const context = await launchPersistentContext("./profiles/alice", { proxy: "socks5://user:pass@host:1080" });
```

The first launch draws a machine and stores its seed in
`./profiles/alice/apostate/identity`. Every later launch with that directory
presents the same machine. `launch()` refuses a user data directory.

The browser also runs on its own. `--fingerprint-explain` prints the machine a
launch would present and exits:

```sh
./chrome --fingerprint=42 --fingerprint-platform=windows
./chrome --fingerprint=42 --fingerprint-explain
```

[docs/FLAGS.md](docs/FLAGS.md) lists every switch and package option.

## What a persona covers

- User-Agent, Client Hints, `navigator.platform` and OS version
- GPU: WebGL vendor and renderer, limits, extensions and shader precision,
  and the WebGPU adapter
- CPU core count and memory, never more than the host has
- Screen size, available area (with the Windows taskbar), pixel ratio and
  colour depth
- Fonts: the platform's system fonts, with every other font hidden
- Speech voices, cameras, microphones and speakers
- Languages, UI locale and timezone
- Keyboard layout, system colours and fonts, pointer type
- AudioContext sample rate and buffer size
- Network information and battery state

`--fingerprint=host` turns all of it off and shows the real machine.
[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) explains where the values come
from, and [docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md) lists what is not covered.

## Fonts

A persona shows the fonts of its own platform and hides the rest, but only
fonts installed on the host can appear. Apostate does not ship fonts. On a
Linux or macOS host, a Windows persona needs the Windows fonts:

```sh
apostate fonts install windows        # or: npx apostate fonts install windows
```

This clones `github.com/MauCariApa-com/windows-11-fonts` with `git` and
installs, for your user, the files of the families a Windows persona lists.
`--from DIR` takes them from a Windows Fonts folder instead, which is how to
add Marlett. [docs/FONTS.md](docs/FONTS.md) has the font lists and the steps
for a macOS persona on Linux.

## Headless Linux servers

A Linux server with no GPU and no display works. The persona's GPU values are
served whatever the host renders with. The default persona on Linux is
Windows, so install the Windows fonts first (above).

`headless=True` is the default and needs nothing else. With `headless=False`
on a machine with no display, the package starts Xvfb for the browser and
stops it when the browser closes. Only Xvfb has to be installed:

```sh
sudo apt install xvfb
```

The virtual screen matches the persona when you set the screen size with
`--fingerprint-screen-width` and `--fingerprint-screen-height`, and is
1920x1080 otherwise. The bare binary does not start Xvfb; run it under
`xvfb-run` instead.

## Widevine

Widevine, the DRM module video sites use, cannot be shipped in the archive.
On the first launch, or during `apostate install`, the package copies it from
Google Chrome on the machine, or downloads it from Google's component update
service and checks its SHA-256. It is kept in the cache, so this happens once.
If it fails, the browser starts without DRM and prints a warning. The Python
CLI's `apostate provision-drm --source DIR` installs a Widevine directory you
already have. It works on macOS arm64, Linux x64 and Linux arm64 hosts;
Windows hosts are not verified yet.

## Platforms and hosts

| Host | Archive |
| --- | --- |
| Linux x64 | `apostate-152.0.7977.83-linux-x64.tar.zst` |
| Linux arm64 | `apostate-152.0.7977.83-linux-arm64.tar.zst` |
| macOS arm64 | `apostate-152.0.7977.83-macos-arm64.zip` |
| Windows x64 | `apostate-152.0.7977.83-windows-x64.zip` |

Any host can present any persona, but some pairs look more real than others:

- Windows persona: an x86 Linux server or a Windows machine. On an ARM host
  (Apple silicon, Linux arm64) the persona reports an ARM CPU next to a
  desktop Intel or NVIDIA GPU, a pair few real machines have.
- macOS persona: a Mac. It always reports Apple silicon.
- Linux persona: an x86 Linux machine.

A persona never claims more cores or memory than the host has, so a small
server makes a small machine. [docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md) has the
details.

## Measuring

`scripts/measure-fpjs.py` launches one persona through a proxy, loads
FingerprintJS Pro's public playground and prints the suspect score and every
flag behind it. It runs from a checkout of this repository and needs `curl`,
a residential proxy, and on a server Xvfb, because it runs headed. Use a fresh
proxy session for each run, or the service sees a returning visitor.

```sh
APOSTATE_PROXY=socks5://user:pass@host:1080 \
  python3 scripts/measure-fpjs.py --platform windows --seed 42
```

`--platform host` measures the real machine for comparison, `--out FILE` saves
the full response, and `APOSTATE_BINARY` points it at a local build.

## Build from source

Every input is pinned in `build/` and every step is a script in `scripts/`.
[docs/BUILD.md](docs/BUILD.md) has the steps.

## Documentation

| Page | Contents |
| --- | --- |
| [docs/FLAGS.md](docs/FLAGS.md) | Every switch and package option |
| [docs/FONTS.md](docs/FONTS.md) | Font lists and how to install them |
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | The profile, the seed, where values come from, the repository |
| [docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md) | What is not done or cannot be hidden |
| [docs/BUILD.md](docs/BUILD.md) | Building the browser |
| [docs/RELEASE.md](docs/RELEASE.md) | Cutting a release and checking a download |
| [python/README.md](python/README.md), [npm/README.md](npm/README.md) | Package details |

## Licence

GPL-3.0. See [LICENSE](LICENSE).
