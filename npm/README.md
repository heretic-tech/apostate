# @heretic-tech/apostate

Node.js interface to the Apostate anti-detect Chromium build. A real browser
binary whose fingerprint is modified in C++ at the source level, driven through
the Playwright or Puppeteer API you already use.

Free and open source. No licence key, no account, no telemetry, no paid tier.

## Install

```sh
npm install @heretic-tech/apostate
```

That is the whole install. Patchright comes with the package and is the default
driver; it is a real dependency because the launcher drives it. `playwright`,
`playwright-core`, `puppeteer` and `puppeteer-core` all work as well, and
Apostate falls back to them in that order.

The division of labour is that the browser handles what a page can observe about
the browser, and the driver's remaining job is to avoid *creating* artifacts of
its own — main-world `addInitScript` and `exposeFunction` bindings,
`Runtime.addBinding`, evaluation-script names visible in stack traces, and its
automation argv. Patchright is the hardened fork of that family, which is why it
is the default.

The classic sentinels (`$cdc_`, `__webdriver_evaluate`, `__playwright` and eight
others) were **absent under every driver tested**, and the `window` key set was
byte-identical between a bare launch and a driven page, so the folklore checks
are not what distinguishes these drivers. What does: FingerprintJS Pro reports
`developer_tools: true` on every run under Playwright and `false` under
Patchright, the same as with no driver attached. That is why Patchright is the
default. The likely cause, not isolated, is `Runtime.enable`, which Playwright
sends and Patchright does not.

Two things about Puppeteer *are* measured, and are why it sits last in the order.
Stack traces from driver-evaluated code carry your **absolute filesystem path**
(`at pptr:evaluate;file%3A%2F%2F%2FUsers%2F...`), where Playwright's equivalent
names no driver, scheme or path. And `exposeFunction` installs
`puppeteer___yourName` alongside the name you asked for. Both are driver-side;
nothing in the browser can remove them. Choose Puppeteer knowing that.

```javascript
import { driverInfo } from "@heretic-tech/apostate";
console.log(await driverInfo());
```

Pass `driver: "puppeteer-core"` to force one, and read
`browser.apostateDriverName` to see what was used.

The browser is not bundled. On first use the package downloads the ~150 MB
archive for your platform from the GitHub release, checks its SHA-256 before
opening it, extracts it, and reuses it afterwards. No driver needs to download
a browser of its own — Apostate supplies it.

The digest comes from one of two places, and `npx apostate info` tells you
which:

| `manifest_source` | Where the digest came from | `manifest_trust` |
|---|---|---|
| `baked` | the manifest shipped inside this package | `pinned` |
| `configured` | a manifest you named with `manifest`, `manifestPath` or `manifestUrl` | `pinned` |
| `release-tag` | `…/releases/download/v<package version>/<archive>.manifest.json` | `transport-integrity` |
| `release-latest` | `…/releases/latest/download/<archive>.manifest.json` | `transport-integrity` |

A launcher release and a browser release move independently, so the package
cannot always carry the digest of the archive it needs. When it cannot, it
fetches the manifest the release publishes beside the archive, and it says so on
stderr. **That is a transport-integrity check, not provenance:** it catches a
corrupted or truncated download, and nothing more, because the digest then
travels with the bytes. Provenance is `gh attestation verify` — see
[Integrity](#integrity).

Both URLs are tried, tagged first. Only if both fail does acquisition refuse,
and the refusal names both.

Or fetch it ahead of time:

```sh
npx apostate install
```

### Already have the browser

Four sources, in this order, and the first one that answers wins:

| Where a launch looks | How |
|---|---|
| the path you name | `launch({ executablePath: "/path/to/chrome" })` |
| `APOSTATE_BINARY` | `export APOSTATE_BINARY=/path/to/chrome` |
| this package's own install | whatever `npx apostate install` wrote |
| well-known locations | macOS `/Applications` and `~/Applications` for a `Chromium.app` or `Apostate.app`; Linux `~/.cache/apostate` and `/opt/apostate` for a `chrome`; Windows `%LOCALAPPDATA%\apostate` for a `chrome.exe` — each directory and one level below it |

The first two are you naming a path and are taken at your word. Three forms
are accepted, because all three are things people have:

| What you name | What runs |
|---|---|
| the executable | itself |
| a macOS bundle directory, `Chromium.app` | `Contents/MacOS/Chromium`, then `Contents/MacOS/Apostate` |
| a payload root, the extracted `apostate-152.0.7977.83-<target>/` | the `Chromium.app`, `chrome` or `chrome.exe` inside it |

A path that names nothing is refused with `does not name a file`; a directory
with no browser in it with `names a directory with no browser inside it
(expected Chromium.app, chrome or chrome.exe)`.

**Unpacked the release archive by hand?** Move the *whole* extracted
directory, not just `Chromium.app` — the build record and the profile
resources beside it are what identify the tree as Apostate's. Put it in
`/Applications` or `~/Applications` on macOS, `~/.cache/apostate` or
`/opt/apostate` on Linux, `%LOCALAPPDATA%\apostate` on Windows, and the
search finds it with no configuration at all: it looks in each of those
directories and one level below.

Releases from v0.2.0 are Developer ID signed, notarized and stapled, so a
bundle downloaded with a browser opens without ceremony. v0.1.0 was not: its
archive carries the quarantine flag a browser sets, and Gatekeeper reports an
unsigned bundle with that flag as damaged. Clear it on the extracted tree:

```sh
xattr -dr com.apple.quarantine ~/Applications/apostate-152.0.7977.83-macos-arm64
```

The last two sources are searches, and **a stock Chrome or Chromium is never
adopted.** The executable is named `chrome` and the bundle `Chromium.app`
exactly as upstream names them, and Chromium 152.0.7977.83 exists upstream
too, so the file alone proves nothing. What is checked is the payload staged
beside it — `build/MANIFEST.lock`, which carries this build's patch-series
digests, or `resources/profiles/` — and the version, and both are required.
Marker first, then version: nothing is executed until a file only an Apostate
payload carries has already vouched for the tree, because a stock Chrome
started with these switches is a session with no protection at all and
nothing to say so.

```javascript
import { discoveryReport } from "@heretic-tech/apostate";
console.log(await discoveryReport());
// {
//   order: [ 'argument', 'environment', 'cache', 'well-known' ],
//   searched: [ '/opt/apostate' ],
//   found: {
//     executable: '/opt/apostate/apostate-152.0.7977.83-linux-x64/chrome',
//     source: 'well-known',
//     chromium_version: '152.0.7977.83',
//     payload_root: '/opt/apostate/apostate-152.0.7977.83-linux-x64'
//   },
//   rejected: [ { path: '/opt/chromium/chrome', reason: 'no Apostate payload beside it (…)' } ]
// }
```

`npx apostate info` prints the same thing as JSON, as `executable`,
`executable_source` and `discovery`, alongside where the digest came from
(`manifest_source`, `manifest_url`, `manifest_urls_tried`, `manifest_trust`,
`manifest_note`) and the `provenance` command to run against the archive.

Supported hosts: `macos-arm64`, `linux-x64`, `linux-arm64`, `windows-x64`.

## Launch

`launch()` returns whatever driver you installed: a Playwright `Browser` or a
Puppeteer `Browser`. An existing script works with only the import changed.
Under Playwright, `newPage()` opens pages in a normal profile, a temporary one
deleted when the browser closes, where Playwright's opens each page in an
off-the-record context that sites can tell apart. `newContext()` is still
off-the-record, so use `newPage()`. Puppeteer's `newPage()` already uses a
normal profile.

Verified against the macos-arm64 build for Patchright, Playwright and
puppeteer-core, including `launchPersistentContext` and `launchContext`.

```javascript
import { launch } from "@heretic-tech/apostate";

const browser = await launch();
const page = await browser.newPage();
await page.goto("https://example.com");
console.log(await page.title());
await browser.close();
```

With no arguments the browser draws a fresh fingerprint seed and composes a
coherent identity — GPU, CPU count, memory, screen, fonts, locale and timezone
all agree with each other. Every launch is a different device.

### A stable identity

A fresh seed every launch means a site you revisit sees a different device each
time. Pass a seed to get the same one back:

```javascript
const browser = await launch({ fingerprint: 42 });
```

Same seed, same fingerprint, on every launch and on every machine — a seed
travels as a string, where a profile directory has to be copied.

A persistent context keeps one too. `launchPersistentContext(DIR)` stores the
machine's seed in `DIR/apostate/identity` on its first launch and reads it back
on every launch after, so the machine stays with the cookies and logins in that
directory. `launch()` does not take a user data directory. Three lifetimes:

| You launch with | The identity is | It lasts |
|---|---|---|
| `launch()` | drawn fresh from OS entropy | this launch only |
| `launchPersistentContext(DIR)` | bound to `DIR` | until you delete `DIR/apostate/identity`; renaming or moving `DIR` changes nothing |
| `fingerprint: SEED` | the one that seed selects | forever, on any host |

For a fresh machine on every run with a persistent context, give each run its
own directory or delete `DIR/apostate/identity` between runs.

Viewport geometry is handled for you: the drivers' default viewports report
impossible values (Playwright: `screen == inner == avail` with
`devicePixelRatio` flattened to 1; Puppeteer: an inner viewport *larger* than
its own window), so Apostate lets the real window size through and the composed
profile's geometry survives. Set one with `page.setViewportSize()` or
`newContext({ viewport })` and yours wins.

One case Apostate cannot fix: `fingerprint: "host"` under `headless: true` has no
display to inherit, so headless Chrome reports its synthetic 800x600 with
`availHeight == height`. No real desktop looks like that. Use a seed — any
composed profile supplies coherent geometry, measured at screen 1710x1112
against avail 1710x1079 with `devicePixelRatio` 2 — or run headful.

### Other options

```javascript
const browser = await launch({
  fingerprint: 42,
  fingerprintPlatform: "windows",   // present as a Windows desktop
  proxy: "http://user:pass@host:8080",
  headless: false,
  args: ["--fingerprint-hardware-concurrency=8"],
});
```

| Option | Default | Meaning |
|---|---|---|
| `fingerprint` | a fresh random seed | Seed for the whole identity. `"host"` (also `"off"`, `"false"`, `"0"`, `"disable"`, `"disabled"`) inherits the real machine and composes nothing. |
| `fingerprintPlatform` | host's own OS on macOS and Windows; `"windows"` on Linux | `windows`, `macos` or `linux`. Selects the GPU cluster as well as the OS identity. Needs that platform's fonts installed — see below. Cannot be combined with host inheritance. |
| `locale`, `timezone` | from GeoIP; without it, `en-US` and the host's timezone | Set these two yourself. They are never drawn from the seed, because a drawn timezone would not match the exit IP. A single tag such as `de-DE` sets the UI locale, and the language list is then Chrome's own default for it (`de-DE,de,en-US,en`); a comma list such as `de-DE,de` sets the list exactly. GeoIP always gives a single tag. The locale is also written into the browser's environment (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`) so `Intl` formatting follows it. Only host mode inherits your shell's locale. |
| `geoip` | `true` | Best-effort: derive locale and timezone from the effective egress, the proxy exit when one is configured and the direct IP otherwise. The timezone is the provider's; the locale is inferred from the country through `assets/country-locales.json`, which covers every territory CLDR knows (`MY` gives `ms`, `GT` gives `es-419`, `IN` gives `en-IN`). A lookup that fails, or names no country, invents nothing: it adds a warning to `browser.apostateDiagnostics.warnings` and sends no switch for the field it could not answer, so the persona uses `en-US` and the host's timezone there. Behind a proxy the host's timezone is not the exit's, so pass `locale` and `timezone` explicitly when the match has to be guaranteed. |
| `proxy` | none | `http://`, `https://`, `socks5://`; credentials are kept out of the command line. |
| `headless` | `true` | |
| `args` | none | Extra switches passed to the browser. |
| `driver` | first one installed | Force a specific driver by package name. |

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
npx apostate fonts install windows
```

That clones the font set with `git`, copies the files of the Windows core
families into `~/.local/share/fonts/apostate-windows`, runs `fc-cache -f` and
names any core family the host still lacks. Passing
`fingerprintPlatform: "linux"` composes the host's own OS instead and needs
nothing installed. Windows-on-Linux is the default because it is the least bad
cross-OS pairing, not because it is free; `fingerprintPlatform: "macos"` on a
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

```javascript
const browser = await launch({
  fingerprint: 42,
  headless: false,
  proxy: "http://user:pass@residential-host:8080",
});
```

If the host already has a display, the browser uses it. Without Xvfb, a
headed launch on such a host stops with an error that says to install it or
pass `headless: true`.

What a page can still tell on such a host, such as rendering speed and the
rendered pixels, is listed in
[docs/KNOWN_GAPS.md](https://github.com/heretic-tech/apostate/blob/main/docs/KNOWN_GAPS.md).

### Fonts for a cross-platform persona

Asking for a platform other than the one you are running on means that
platform's fonts have to be on the host. Apple and Microsoft fonts cannot be
redistributed, so they come from your own machines; a persona without its
fonts is a common cause of blocks.

```sh
npx apostate fonts install windows                    # Linux or macOS host
npx apostate fonts export-macos ~/mac-fonts           # on a Mac
npx apostate fonts install macos --from ~/mac-fonts   # on the Linux host
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

`launchProcess()` returns the raw child process instead, for when no driver is
installed and you only need the browser running.

## Command line

```sh
npx apostate install        # download, verify and extract, then add Widevine
npx apostate path           # print the executable path
npx apostate info           # install and manifest state as JSON
npx apostate run -- --version
npx apostate clear          # delete the cache
npx apostate fonts install windows [--from DIR]
npx apostate fonts install macos --from DIR
npx apostate fonts export-macos DIR
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

`npx apostate install` does the same ahead of time. If no copy can be had, the
browser launches without DRM and prints one warning. Windows hosts are not
verified yet.

To install a particular copy instead:

```javascript
import { provisionWidevine } from "@heretic-tech/apostate";

await provisionWidevine({ source: "/path/to/WidevineCdm" });
```

Both packages share one browser install and one Widevine copy, so either one
serves both.

Do not pass `--disable-component-update` to the browser. It stops every
preinstalled component from loading, Widevine included. `launch()` removes it
from the driver's default arguments for you. If you drive the binary yourself,
Playwright passes it by default, so use
`ignoreDefaultArgs: ["--disable-component-update"]` (Node) or
`ignore_default_args=["--disable-component-update"]` (Python). Patchright and
Puppeteer do not pass it.

## The browser cache

The install lives under `~/Library/Caches/apostate` on macOS,
`$XDG_CACHE_HOME/apostate` (or `~/.cache/apostate`) on Linux, and
`%LOCALAPPDATA%\apostate\cache` on Windows, keyed by Chromium version and
platform. `APOSTATE_CACHE_DIR` overrides it. The pip package uses the same
layout, so both share one install.

| Variable | Effect |
|---|---|
| `APOSTATE_CACHE_DIR` | Where the browser is installed. |
| `APOSTATE_BINARY` | Use this browser and skip acquisition entirely. An executable, a `.app` bundle or an extracted payload root. |
| `APOSTATE_DOWNLOAD_BASE_URL` | Fetch archives from a mirror. The digest is never taken from the mirror: it comes from the package, or from the release. |
| `APOSTATE_KEEP_ARCHIVE` | Keep the verified archive after extracting, for `gh attestation verify`. |

## Integrity

The archive's SHA-256 is checked **before** the archive is opened, and a
mismatch aborts without extracting anything. Where that digest came from
decides what the check is worth, and the two cases are not the same strength:

- **A manifest baked into this package** (`manifest_trust: pinned`). The
  digest and the bytes come from two different places, so it detects a
  substituted archive as well as a damaged one.
- **A manifest fetched from the release** (`manifest_trust:
  transport-integrity`). The digest travels with the bytes, so it detects a
  corrupted or truncated download and nothing else. A launcher installing a
  release published before it takes this path, and says so on stderr.

For the stronger claim in either case, releases carry GitHub build-provenance
attestations, which bind the archive to the repository, commit and workflow
that produced it. There is no signing key to hold or rotate:

```sh
gh attestation verify apostate-152.0.7977.83-macos-arm64.zip --repo heretic-tech/apostate
```

That needs the archive, so run `npx apostate install --keep-archive` first, or
download it from the release page.

## Licence

GPL-3.0-or-later.
