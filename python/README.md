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

The first two accept three spellings of "this browser", because a release
archive gives you all three: the executable, a macOS `.app` bundle
(`Contents/MacOS/Chromium` is resolved inside it), and the directory the
archive unpacks to, `apostate-152.0.7977.83-<platform>/`. A path that is a
directory with none of those inside is refused with `names a directory with
no browser inside it (expected Chromium.app, chrome or chrome.exe)`, and one
that is nothing at all with `does not name a file`.

If you extracted the archive yourself and want it found without naming it,
move the **whole** extracted directory into one of the well-known locations
above, not just the browser out of it: `build/MANIFEST.lock` and
`resources/profiles/` have to travel with it, and they are what the search
checks.

The first two are you naming a browser and are taken at your word. The last
two are searches, and **a stock Chrome or Chromium is never adopted.** The
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
with only the import changed, with one difference: `new_page()` opens pages in
a normal profile, a temporary one deleted when the browser closes, where
Playwright's opens each page in an off-the-record context that sites can tell
apart. `new_context()` is still off-the-record, so use `new_page()`. Page
options such as `viewport` or `user_agent` go to `launch()`, because every
page shares the one profile.

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

A persistent context keeps one too. `launch_persistent_context(DIR)` stores the
machine's seed in `DIR/apostate/identity` on its first launch and reads it back
on every launch after, so the machine stays with the cookies and logins in that
directory. `launch()` does not take a user data directory. Three lifetimes:

| You launch with | The identity is | It lasts |
|---|---|---|
| `launch()` | drawn fresh from OS entropy | this launch only |
| `launch_persistent_context(DIR)` | bound to `DIR` | until you delete `DIR/apostate/identity`; renaming or moving `DIR` changes nothing |
| `fingerprint=SEED` | the one that seed selects | forever, on any host |

For a fresh machine on every run with a persistent context, give each run its
own directory or delete `DIR/apostate/identity` between runs.

Viewport geometry is handled for you: the drivers' default viewports report
impossible values (Playwright: `screen == inner == avail` with
`devicePixelRatio` flattened to 1; Puppeteer: an inner viewport *larger* than
its own window), so Apostate lets the real window size through and the composed
profile's geometry survives. Pass `viewport=` to `launch()` or `new_context()`
and yours wins.

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
| `locale`, `timezone` | from GeoIP; without it, `en-US` and the host's timezone | Set these two yourself. They are never drawn from the seed, because a drawn timezone would not match the exit IP. A single tag such as `de-DE` sets the UI locale, and the language list is then Chrome's own default for it (`de-DE,de,en-US,en`); a comma list such as `de-DE,de` sets the list exactly. GeoIP always gives a single tag. The locale is also written into the browser's environment (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`) so `Intl` formatting follows it. Only host mode inherits your shell's locale. |
| `geoip` | `True` | Best-effort: derive locale and timezone from the effective egress, the proxy exit when one is configured and the direct IP otherwise. The lookup ships with the package and needs no extra dependency: it speaks `http(s)://`, `socks5://` and `socks5h://` proxies, including username and password, out of the standard library. A failed or partial lookup does not raise from `launch()` and invents nothing: it adds a warning to the plan's `diagnostics["warnings"]` and sends no switch for the field it could not answer, so the persona uses `en-US` and the host's timezone there. Behind a proxy the host's timezone is not the exit's, so pass `locale` and `timezone` explicitly when the match has to be guaranteed. `resolve_geoip()` called directly still raises. Inject your own with `geoip_provider=`. |
| `proxy` | none | `http://`, `https://`, `socks5://`. The endpoint goes on the command line and the credential travels in the launch envelope, so it is in no log and no socket-pool key — and a SOCKS credential is deliberately withheld from the driver, which refuses to start when one is present. `socks5h://` is not a Chromium proxy scheme; use `socks5://`, which already resolves the destination proxy-side. |
| `headless` | `True` | |
| `args` | none | Extra switches passed to the browser. |

### Headless Linux servers

The usual deployment, and the one this is built for: a Linux server with no
graphics device. Nothing extra is needed and no GPU is required. `headless` is
already the default, and the claimed operating system rather than the host's
graphics stack selects the GPU identity, so a GPU-less server presents the
capability cluster and renderer string of the OS it claims.

**On a Linux host the default claimed OS is Windows, so install the Windows
fonts.** It is the one setup step here and the most common cause of a block: a
Windows persona missing Windows faces is measurable in text metrics.

```sh
python -m apostate fonts install windows
```

That clones the font set with `git`, copies the files of the Windows core
families into `~/.local/share/fonts/apostate-windows`, runs `fc-cache -f` and
names any core family the host still lacks. Passing
`fingerprint_platform="linux"` composes the host's own OS instead and needs
nothing installed. Windows-on-Linux is the default because it is the least bad
cross-OS pairing, not because it is free; `fingerprint_platform="macos"` on a
Linux host is the riskier one, at 184 core families.

For the most aggressive targets, the suites that score behaviour as well as the
fingerprint, run headed instead. Still no GPU is needed. On a Linux host with
no display, a headed launch starts its own virtual display with Xvfb, gives it
to the browser, and stops it when the browser closes. The display is the size
of the screen the launch sets, or 3840x2160 when the seed picks the screen.
Only Xvfb has to be installed:

```sh
sudo apt install xvfb
```

```python
browser = launch(
    fingerprint=42,
    headless=False,
    proxy="http://user:pass@residential-host:8080",
)
```

If the host already has a display, the browser uses it. Without Xvfb, a
headed launch on such a host stops with an error that says to install it or
pass `headless=True`.

What a page can still tell on such a host, such as rendering speed and the
rendered pixels, is listed in
[docs/KNOWN_GAPS.md](https://github.com/heretic-tech/apostate/blob/main/docs/KNOWN_GAPS.md).

### Fonts for a cross-platform persona

Asking for a platform other than the one you are running on means that
platform's fonts have to be on the host. Apple and Microsoft fonts cannot be
redistributed, so they come from your own machines; a persona without its
fonts is a common cause of blocks.

```sh
python -m apostate fonts install windows                    # Linux or macOS host
python -m apostate fonts export-macos ~/mac-fonts           # on a Mac
python -m apostate fonts install macos --from ~/mac-fonts   # on the Linux host
```

`fonts install windows` clones the font set with `git` and installs the files
of the Windows core families; `--from DIR` takes them from a Windows Fonts
folder instead, which is how to add Marlett. `fonts export-macos`
copies a Mac's system fonts into a directory; copy that directory to the Linux
host and install it from there. Fonts go into `~/.local/share/fonts/apostate-*`
on Linux, followed by `fc-cache -f`, and `~/Library/Fonts/apostate-*` on
macOS.
[docs/FONTS.md](https://github.com/heretic-tech/apostate/blob/main/docs/FONTS.md)
has the per-persona family lists and a verification command.

The browser assumes you have done this and does not check. What it will not do
is claim a face that is absent: the font list a page sees is filtered down from
what the host actually has, never added to.

Also available: `launch_context()`, which returns the temporary profile's own
context, `launch_persistent_context()`, and `launch_async()` /
`launch_context_async()` / `launch_persistent_context_async()` for the async
API.

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
python -m apostate install        # download, verify and extract, then add Widevine
python -m apostate path           # print the executable path
python -m apostate info           # install and manifest state as JSON
python -m apostate run -- --version
python -m apostate clear          # delete the cache
python -m apostate fonts install windows [--from DIR]
python -m apostate fonts install macos --from DIR
python -m apostate fonts export-macos DIR
```

## DRM (Widevine)

Widevine is the DRM module video sites use, and Google's licence forbids
shipping it with the browser. So the first launch adds it: it copies it from a
browser on the machine that has it, such as Google Chrome, and otherwise
downloads it from Google's update server and checks its SHA-256. It is kept in
the cache directory, so this happens once per machine, and it goes into the
browser's own directory, so every profile gets it, the throwaway one
`launch()` uses included. With it, a page's
`navigator.requestMediaKeySystemAccess("com.widevine.alpha", ...)` call
answers the way a real Chrome does.

`python -m apostate install` does the same ahead of time. If no copy can be
had, the browser launches without DRM and prints one warning. Windows hosts are
not verified yet.

To install a particular copy instead:

```sh
python -m apostate provision-drm --list                 # what this machine has
python -m apostate provision-drm --source /path/to/WidevineCdm
```

Do not pass `--disable-component-update` to the browser. It stops every
preinstalled component from loading, Widevine included. `launch()` removes it
from the driver's default arguments for you. If you drive the binary yourself,
Playwright passes it by default, so use
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
| `APOSTATE_BINARY` | Use this browser and skip acquisition entirely. An executable, a macOS `.app` bundle, or the directory the archive unpacks to. |
| `APOSTATE_DOWNLOAD_BASE_URL` | Fetch archives from a mirror. The digest is never taken from the mirror: it comes from the package, or from the release. |
| `APOSTATE_KEEP_ARCHIVE` | Keep the verified archive after extracting, for `gh attestation verify`. |

## Integrity

The archive's SHA-256 is checked **before** the archive is opened, and a
mismatch aborts without extracting anything. Where that digest comes from
depends on whether this package is older than the binaries it installs, and
`python -m apostate info` reports which of the two answered as
`manifest_source`:

| `manifest_source` | Where the digest came from |
|---|---|
| `baked` | The manifest shipped inside this package, pinned at publish time. The strong case: the digest did not travel with the bytes. |
| `configured` | A manifest you passed as `manifest=` or `--manifest`. Never replaced by a fetched one — pinning a digest and then fetching a different one would unpin it. |
| `release-tag` | `releases/download/v<this package's version>/<archive>.manifest.json`, fetched at run time. |
| `release-latest` | `releases/latest/download/<archive>.manifest.json`, fetched at run time. |

The last two let a launcher install binaries published before it, so a
launcher fix does not wait on a Chromium rebuild. They are a
transport-integrity check and not provenance: they catch a truncated or
corrupted download, and they cannot catch a substituted release, because the
digest came from the same place as the bytes. `manifest_trust` says `pinned`
or `transport-integrity` for exactly this reason, and an install that used a
fetched manifest says so on stderr before it downloads anything.

If neither URL answers, the refusal names both:

```
apostate: Release manifest is unpublished; Apostate binary artifacts are not
available for acquisition. No release manifest for
apostate-152.0.7977.83-macos-arm64.zip could be fetched. Tried: <url>, <url>
```

Releases also carry GitHub build-provenance attestations, which is the check
that does establish where an archive came from. There is no signing key to
hold or rotate; verification is an optional extra step:

```sh
gh attestation verify apostate-152.0.7977.83-macos-arm64.zip --repo heretic-tech/apostate
```

That needs the archive, so run `python -m apostate install --keep-archive`
first, or download it from the release page.

## Licence

GPL-3.0-only. The browser binary is GPL-3.0-or-later.
