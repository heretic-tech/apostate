# apostate

Python interface to the Apostate anti-detect Chromium build. A real browser
binary whose fingerprint is modified in C++ at the source level, driven through
the Playwright API you already use.

Free and open source. No licence key, no account, no telemetry, no paid tier.

## Install

```sh
pip install apostate
```

That is the whole install. Patchright comes with the package and is the default
driver; it is a required dependency because the launcher drives it. Playwright
works too — `pip install playwright`, then `driver="playwright"` — and a launch
falls back to it if Patchright is somehow missing.

The division of labour is that the browser handles what a page can observe about
the browser, and the driver's remaining job is to avoid *creating* artifacts of
its own — main-world `addInitScript` and `exposeFunction` bindings,
`Runtime.addBinding`, evaluation-script names visible in stack traces, and its
automation argv. Patchright is the hardened fork of that family, which is why it
is the default.

Be aware of what has and has not been measured here. The classic sentinels
(`$cdc_`, `__webdriver_evaluate`, `__playwright` and eight others) were **absent
under every driver tested**, and the `window` key set was byte-identical between
a bare launch and a driven page — so the folklore checks are not what
distinguishes these drivers. Patchright's advantage over Playwright has not been
measured on this project, and the default reflects the fork's intent rather than
a result. Treat any claim otherwise, including ours, as unverified.

```python
from apostate import driver_info
print(driver_info())
# {'preference_order': ['patchright', 'playwright'],
#  'installed': ['patchright'], 'selected': 'patchright',
#  'recommended': 'patchright'}
```

Pass `driver="playwright"` to force one, and read `browser.apostate_driver_name`
to see what was used.

The browser is not bundled. On first use the package downloads the ~150 MB
archive for your platform from the GitHub release, checks its SHA-256 against a
manifest shipped inside the package, extracts it, and reuses it afterwards. You
do not need `playwright install` — Apostate supplies its own browser.

Or fetch it ahead of time:

```sh
python -m apostate install
```

### Already have the browser

Four sources, in this order, and the first one that answers wins:

| Where a launch looks | How |
|---|---|
| the path you name | `launch(binary_path="/path/to/chrome")` |
| `APOSTATE_BINARY` | `export APOSTATE_BINARY=/path/to/chrome` |
| this package's own install | whatever `python -m apostate install` wrote |
| well-known locations | macOS `/Applications` and `~/Applications` for a `Chromium.app` or `Apostate.app`; Linux `~/.cache/apostate` and `/opt/apostate` for a `chrome`; Windows `%LOCALAPPDATA%\apostate` for a `chrome.exe` — each directory and one level below it |

The first two are you naming a file and are taken at your word. The last two
are searches, and **a stock Chrome or Chromium is never adopted.** The
executable is named `chrome` and the bundle `Chromium.app` exactly as upstream
names them, and Chromium 152.0.7977.83 exists upstream too, so the file alone
proves nothing. What is checked is the payload staged beside it —
`build/MANIFEST.lock`, which carries this build's patch-series digests, or
`resources/profiles/` — and the version, and both are required. Marker first,
then version: nothing is executed until a file only an Apostate payload carries
has already vouched for the tree, because a stock Chrome started with these
switches is a session with no protection at all and nothing to say so.

```python
from apostate import discovery_report
print(discovery_report())
# {'order': ['argument', 'environment', 'cache', 'well-known'],
#  'searched': ['/opt/apostate'],
#  'found': {'executable': '/opt/apostate/apostate-152.0.7977.83-linux-x64/chrome',
#            'source': 'well-known', 'chromium_version': '152.0.7977.83',
#            'payload_root': '/opt/apostate/apostate-152.0.7977.83-linux-x64'},
#  'rejected': [{'path': '/opt/chromium/chrome',
#                'reason': 'no Apostate payload beside it (build/MANIFEST.lock or resources/profiles/catalogue.json)'}]}
```

`python -m apostate info` prints the same thing as JSON, alongside the manifest
state, as `executable`, `executable_source` and `discovery`.

Supported hosts: `macos-arm64`, `linux-x64`, `linux-arm64`, `windows-x64`.

## Launch

`launch()` returns a Playwright `Browser`. An existing Playwright script works
with only the import changed.

```python
from apostate import launch

browser = launch()
page = browser.new_page()
page.goto("https://example.com")
print(page.title())
browser.close()
```

With no arguments the browser draws a fresh fingerprint seed and composes a
coherent identity — GPU, CPU count, memory, screen, fonts, locale and timezone
all agree with each other. Every launch is a different device.

### A stable identity

A fresh seed every launch means a site you revisit sees a different device each
time. Pass a seed to get the same one back:

```python
browser = launch(fingerprint=42)
```

Same seed, same fingerprint, on every launch and on every machine — a seed
travels as a string, where a profile directory has to be copied.

It is not the only thing that pins a device, and this changed: a persistent
`user_data_dir` now keeps one too. The first launch against a directory mints
an identity into `DIR/apostate/identity` and every launch after reads it back,
because that directory already holds cookies and logged-in sessions, and one
account whose hardware changes between visits is a worse story than any single
fingerprint value. Three lifetimes:

| You launch with | The identity is | It lasts |
|---|---|---|
| nothing | drawn fresh from OS entropy | this launch only, recorded nowhere |
| `user_data_dir=DIR` | bound to `DIR` | until you delete `DIR`; it survives renaming and moving it |
| `fingerprint=SEED` | the one that seed selects | forever, on any host |

If you wanted a fresh machine every run and have been reusing one directory out
of habit, you now have to say so — drop `user_data_dir`, give each run its own,
or delete `DIR/apostate/identity` between runs.

Viewport geometry is handled for you: the drivers' default viewports report
impossible values (Playwright: `screen == inner == avail` with
`devicePixelRatio` flattened to 1; Puppeteer: an inner viewport *larger* than
its own window), so Apostate lets the real window size through and the composed
profile's geometry survives. Pass a viewport explicitly and yours wins.

One case Apostate cannot fix: `fingerprint="host"` under `headless=True` has no
display to inherit, so headless Chrome reports its synthetic 800x600 with
`availHeight == height`. No real desktop looks like that. Use a seed — any
composed profile supplies coherent geometry, measured at screen 1710x1112
against avail 1710x1079 with `devicePixelRatio` 2 — or run headful.

### Other options

```python
browser = launch(
    fingerprint=42,
    fingerprint_platform="windows",   # present as a Windows desktop
    proxy="http://user:pass@host:8080",
    headless=False,
    args=["--fingerprint-hardware-concurrency=8"],
)
```

| Option | Default | Meaning |
|---|---|---|
| `fingerprint` | a fresh random seed | Seed for the whole identity. `"host"` (also `"off"`, `"false"`, `"0"`, `"disable"`, `"disabled"`) inherits the real machine and composes nothing. |
| `fingerprint_platform` | host's own OS on macOS and Windows; `"windows"` on Linux | `windows`, `macos` or `linux`. Selects the GPU cluster as well as the OS identity. Needs that platform's fonts installed — see below. Cannot be combined with host inheritance. |
| `locale`, `timezone` | GeoIP of the effective egress, else the host's own | Override just these. Never drawn from the seed: a timezone the seed chose cannot correlate with the exit IP, so the precedence is this override, then GeoIP, then the host. The resolved locale is also written into the browser process environment (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`), which is what moves `Intl` formatting rather than only the language list; a composed launch never inherits those from your shell, so your own locale cannot leak into a synthetic identity. Host inheritance is the one exception and does inherit them. Takes effect once the artifact ships the full locale pak set — see docs/FINGERPRINTS.md section 8. |
| `geoip` | `True` | Best-effort: derive locale and timezone from the effective egress — the proxy exit when one is configured, the direct IP otherwise. A failed or partial lookup never invents one and no longer raises from `launch()` — it warns into the plan's `diagnostics["warnings"]`, sends no override for the fields it could not answer for, and the launch serves the host's own for those. Behind a proxy that is the host's and not the exit's. Pass `locale` and `timezone` explicitly when geo-matching has to be guaranteed. `resolve_geoip()` called directly still raises. |
| `proxy` | none | `http://`, `https://`, `socks5://`; credentials are kept out of the command line. |
| `headless` | `True` | |
| `user_data_dir` | off-the-record | Persist cookies and storage. |
| `args` | none | Extra switches passed to the browser. |

### Headless Linux servers

The usual deployment, and the one this is built for: a Linux server with no
graphics device. Nothing extra is needed and no GPU is required. `headless` is
already the default, and the claimed operating system rather than the host's
graphics stack selects the GPU identity, so a GPU-less server presents the
capability cluster and renderer string of the OS it claims.

**On a Linux host the default claimed OS is Windows, so install the Windows
fonts.** It is the one setup step here and the most common cause of a block: a
Windows persona missing Windows faces is measurable in text metrics. The
families and where to copy them from are in
[docs/FONTS.md](https://github.com/heretic-tech/apostate/blob/main/docs/FONTS.md).
Passing `fingerprint_platform="linux"` composes the host's own OS instead and
needs nothing installed. Windows-on-Linux is the default because it is the least
bad cross-OS pairing, not because it is free; `fingerprint_platform="macos"` on
a Linux host is the riskier one, at 184 core families.

For the most aggressive targets, the suites that score behaviour as well as the
fingerprint, run headed on a virtual display instead. Still no GPU: the display
is an X server with nothing behind it.

```sh
sudo apt install xvfb
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
```

```python
browser = launch(
    fingerprint=42,
    headless=False,
    proxy="http://user:pass@residential-host:8080",
)
```

`DISPLAY` reaches the browser through the environment. The launcher sets no
environment of its own, so the driver hands the browser yours, and exporting
`DISPLAY` before the script runs is the whole of the wiring.

What a page can still tell on such a host is render timing and per-pixel output,
which come from the rasteriser rather than from the identity. The measured
numbers, the exact extension names a software backend does not currently serve,
and the status of the served GPU identity — new in this release, and on that
page's list of behaviours written but not yet run — are in
[docs/LIMITATIONS.md](https://github.com/heretic-tech/apostate/blob/main/docs/LIMITATIONS.md).

### Fonts for a cross-platform persona

Asking for a platform other than the one you are running on means that
platform's fonts have to be on the host. Apple and Microsoft fonts cannot be
redistributed, so you supply them; a persona without its fonts is a common
cause of blocks. Install them into the normal OS font directories (on Linux,
run `fc-cache -f` afterwards).
[docs/FONTS.md](https://github.com/heretic-tech/apostate/blob/main/docs/FONTS.md)
has the per-persona family lists, source directories and a verification
command.

The browser assumes you have done this and does not check. What it will not do
is claim a face that is absent: the font list a page sees is filtered down from
what the host actually has, never added to.

Also available: `launch_context()`, `launch_persistent_context()`, and
`launch_async()` / `launch_context_async()` /
`launch_persistent_context_async()` for the async API.

### Checking what you got

```python
browser = launch(fingerprint=42)
page = browser.new_page()
print(page.evaluate("navigator.hardwareConcurrency"))
```

Or ask the browser directly, without launching a session:

```sh
python -m apostate run -- --fingerprint=42 --fingerprint-explain
```

## Command line

```sh
python -m apostate install        # download, verify and extract
python -m apostate path           # print the executable path
python -m apostate info           # install and manifest state as JSON
python -m apostate run -- --version
python -m apostate clear          # delete the cache
```

## DRM (Widevine)

Chromium fetches the Widevine CDM from Google at runtime into the profile
directory, and it cannot be redistributed, so Apostate does not ship it. A
default `launch()` uses a throwaway profile, so there is no CDM and
`navigator.requestMediaKeySystemAccess("com.widevine.alpha", ...)` rejects with
`NotSupportedError` — which a site can read in a single call.

If you need DRM, or you want that call to answer the way a real browser does,
provision a CDM that is already on your machine:

```sh
python -m apostate provision-drm --list     # what was found
python -m apostate provision-drm            # install the newest
```

That copies it into the browser's preinstalled-component directory, where it
registers at startup for every profile including a throwaway one, with no
network and without writing into the profile. It survives
`apostate install --force` and a Chromium upgrade.

Nothing is redistributed: the CDM travels from Google to your machine exactly as
it does for Chrome, and this only moves a file already there. If no CDM is
found, run any Chromium-based browser with a persistent profile and play a DRM
video once, then re-run. Measured on macOS; Linux and Windows use the same
command but have not been verified.

One switch matters: `--disable-component-update` gates the whole of
Chromium's component registration, not just downloading. With it set, **no**
preinstalled component registers — measured offline, zero of them — so a
provisioned CDM is silently inert and the browser diverges from a real Chrome
across every preinstalled component at once. `launch()` removes it from the
driver's default arguments for you. If you drive the binary yourself, do not
pass it — Playwright passes it by default, so use
`ignore_default_args=["--disable-component-update"]` (Python) or
`ignoreDefaultArgs: ["--disable-component-update"]` (Node). Patchright and
Puppeteer do not pass it.

## The browser cache

The install lives under `~/Library/Caches/apostate` on macOS,
`$XDG_CACHE_HOME/apostate` (or `~/.cache/apostate`) on Linux, and
`%LOCALAPPDATA%\apostate\cache` on Windows, keyed by Chromium version and
platform. `APOSTATE_CACHE_DIR` overrides it. The npm package uses the same
layout, so both share one install.

| Variable | Effect |
|---|---|
| `APOSTATE_CACHE_DIR` | Where the browser is installed. |
| `APOSTATE_BINARY` | Use this executable and skip acquisition entirely. |
| `APOSTATE_DOWNLOAD_BASE_URL` | Fetch archives from a mirror. The digest still comes from the package manifest. |
| `APOSTATE_KEEP_ARCHIVE` | Keep the verified archive after extracting, for `gh attestation verify`. |

## Integrity

The archive's SHA-256 is checked against the manifest **before** the archive is
opened, and a mismatch aborts without extracting anything. That manifest ships
inside this package rather than being fetched alongside the download, because a
digest served from the same place as the bytes it describes proves nothing.

Releases also carry GitHub build-provenance attestations. There is no signing
key to hold or rotate; verification is an optional extra step:

```sh
gh attestation verify apostate-152.0.7977.83-macos-arm64.zip --repo heretic-tech/apostate
```

That needs the archive, so run `python -m apostate install --keep-archive`
first, or download it from the release page.

## Licence

GPL-3.0-only. The browser binary is GPL-3.0-or-later.
