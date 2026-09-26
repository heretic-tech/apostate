# Flags

The switches Apostate adds to Chromium, and the Python and Node options that
set them. None is required. A launch with no flags presents a whole machine
drawn from a fresh seed.

A refused value prints `apostate: <reason>` on stderr and the browser exits
with status 1. Pages cannot read command-line switches.

## Seed and persona

| Switch | Value | Effect |
| --- | --- | --- |
| `--fingerprint` | printable ASCII, 1 to 512 bytes | Seed for the whole machine. The same seed and persona give the same machine on every launch and every host. |
| `--fingerprint=host` | `host` | No persona. Every value is the real machine's. |
| `--fingerprint-platform` | `windows`, `macos`, `linux` | The operating system the persona presents, with a GPU to match. |
| `--fingerprint-anchor` | GPU family id | Use this GPU family instead of the one the persona picks. |
| `--fingerprint-explain` | none | Print the composed machine to stdout and exit. |

`off`, `false`, `0`, `disable` and `disabled` mean the same as `host`, in any
case. Host mode refuses `--fingerprint-platform`, `--fingerprint-anchor` and
every per-field switch, because nothing is composed for them to change.

The default persona follows the host: `macos` on a Mac, `windows` on Windows
and `windows` on Linux. Any other value of `--fingerprint-platform` is logged
and ignored, so the default applies.

The GPU family ids are the files in `corpus/anchors/`:

| Id | Family |
| --- | --- |
| `windows-d3d11-nvidia-0947761dfbe9` | Windows, NVIDIA, Direct3D 11 |
| `windows-d3d11-intel-79dfeb5b4f99` | Windows, Intel, Direct3D 11 |
| `macos-metal-apple-850a91233555` | macOS, Apple silicon, Metal |
| `linux-vulkan-nvidia-adf287b8f0ee` | Linux, NVIDIA, Vulkan |
| `linux-swiftshader-google-6922d61bab83` | SwiftShader software renderer |

A persona draws from the families of its own platform and never picks
SwiftShader; only `--fingerprint-anchor` selects it. An id that is not in the
list is logged as an error, and the launch shows the host's values.

### How long a machine lasts

| Launched with | The machine | Lasts |
| --- | --- | --- |
| `--fingerprint=SEED` | the one the seed selects | on any host, until the browser version changes |
| `--user-data-dir=DIR` and no seed | bound to `DIR` | until `DIR/apostate/identity` is deleted or the browser version changes |
| neither | drawn from OS entropy | this launch only |

The first launch with a `DIR` writes a new seed to `DIR/apostate/identity` and
later launches read it back. Copying the directory copies the machine, and the
file's content is a seed you can pass as `--fingerprint` anywhere. If the file
cannot be read or written, that launch gets a one-off machine and
`--fingerprint-explain` says so. A file that does not hold a valid seed is
replaced.

The seed is hashed together with the persona and fixed version epochs, not the
running Chromium or catalogue version, so a Chrome update keeps the seed's
machine and moves only the browser version. A different persona gives a
different machine. A release that changes the catalogue tables re-draws the
choices those tables decide (docs/KNOWN_GAPS.md).

## Per-field switches

Each switch sets one value. The seed still draws everything else.

| Switch | Value | Sets |
| --- | --- | --- |
| `--fingerprint-gpu-vendor` | WebGL unmasked vendor string | the GPU vendor |
| `--fingerprint-gpu-renderer` | WebGL unmasked renderer string | the GPU model |
| `--fingerprint-hardware-concurrency` | 1 to 4096 | `navigator.hardwareConcurrency` |
| `--fingerprint-device-memory` | 1 to 4096, in GiB | installed memory |
| `--fingerprint-screen-width` | 1 to 65535, CSS pixels | `screen.width` |
| `--fingerprint-screen-height` | 1 to 65535, CSS pixels | `screen.height` |
| `--fingerprint-timezone` | IANA name, such as `Europe/Berlin` | the timezone |
| `--fingerprint-locale` | One locale tag, such as `de-DE`, or an Accept-Language list, such as `de-DE,de` | UI locale, languages and voices |

- The GPU strings must name a model in the GPU family the launch uses. Any
  other string is refused, and the error lists the models that family has.
  Change the family with `--fingerprint-anchor`.
- More cores or memory than the host has is refused.
- A screen smaller than `--window-size`, or too small to hold the persona's
  taskbar or menu bar, is refused. The available area (`availWidth`,
  `availHeight`) is worked out from the size you set.
- An unknown timezone is logged and the host's timezone stays.
- The first tag of `--fingerprint-locale` becomes the browser's UI locale, so
  `Intl`, date formats and `navigator.language` follow it. A single tag sets
  only that: `navigator.languages` and the `Accept-Language` header then carry
  Chrome's own default list for that UI locale (`de-DE,de,en-US,en` for
  German), as on a real machine. A comma list sets the list exactly. The
  voices follow the first tag. Without the switch a persona uses `en-US`,
  never the host's language. `--lang` has no effect on a persona.
- Without `--fingerprint-timezone` the host's timezone is used. The Python and
  Node packages fill in both switches from GeoIP.

## Reading what a launch chose

`--fingerprint-explain` prints the composed machine and exits without opening
a window. It shows the persona and the host, the seed, one row per value with
where the value came from, and a `limitations` list of anything the launch
could not apply. Run it first when a site blocks you. The seed lines say how
to get the same machine again:

```text
  seed                4f3c8a1e09b7...
  seed source         this profile's identity file (stable for this --user-data-dir)
  identity file       /home/you/work/apostate/identity (read from disk)
  reproduce with      --fingerprint=4f3c8a1e09b7...
```

The report goes to stdout and nowhere else.

## WebRTC

| Switch | Value | Effect |
| --- | --- | --- |
| `--fingerprint-webrtc-ip` | an IP address | Written into the host and server-reflexive ICE candidates in place of the real address. The value is not checked. Packets still go where they would have gone. |
| `--fingerprint-webrtc-udp` | `direct`, `block` | Where WebRTC's UDP goes. |

Without `--fingerprint-webrtc-udp`, a launch with no proxy sends UDP directly,
a launch behind one SOCKS5 proxy relays UDP through that proxy, and any other
proxy setup (HTTP, HTTPS or SOCKS4 proxies, PAC, per-scheme rules, several
proxies) opens no WebRTC UDP socket. `direct` sends UDP from the host even
behind a proxy, so the peer sees the host's address. `block` never opens a UDP
socket. Any other value is logged and treated as `block`.

## Readback noise

`--fingerprint-noise` changes canvas and WebGL pixel readback by at most one
step per colour channel, on pixels at colour edges, so two personas give
different canvas hashes. Solid areas and alpha stay as rendered. The change
depends only on the persona and the pixels: the same seed reads back the same
bytes every time, and `getImageData`, `toDataURL`, `toBlob` and WebGL
`readPixels` agree with each other.

It is off by default and does nothing under `--fingerprint=host`. The switch
takes no value, so `--fingerprint-noise=false` turns it on; the packages refuse
values like that. A page can detect the change, for example by drawing one
image at two scales and comparing them. Leave it off unless canvas hashes must
differ between personas on one host.

## A profile you wrote

`--apostate-profile=BASE64` runs a profile you wrote, as base64-encoded JSON,
instead of composing one. Only the sections you include change. Everything
else keeps the host's value, and seed and persona switches have no effect. A
value that does not decode is refused.

```sh
python3 scripts/validate-release-contract.py --kind profile profile.json
./chrome --apostate-profile="$(base64 < profile.json | tr -d '\n')"
```

The schema is `config/profile.schema.json` and
[HOW_IT_WORKS.md](HOW_IT_WORKS.md#the-profile) lists its sections.

## The proxy

Put the credential in Chromium's own switch:

```sh
./chrome --proxy-server=socks5://user:pass@proxy.example:1080
```

Stock Chromium rejects a credential there. Apostate takes it off the switch
before Chromium parses it, keeps it in memory for the launch, and answers the
proxy's authentication with it (HTTP `407` or SOCKS5 username and password).
It does not appear in NetLog, error messages, `chrome://version` or
`--fingerprint-explain`. Child processes get it inside the `--apostate-profile`
value on their command line, so `ps` on the machine shows it base64-encoded.

- Schemes: `http`, `https`, `socks`, `socks4`, `socks5`, and a bare
  `host:port`, which means HTTP. `socks5h://` does not work; `socks5://`
  already resolves names on the proxy.
- Percent-encode `/`, `%` and spaces in the credential (`%2F`, `%25`, `%20`).
  An `@` or `:` in the password needs no escape.
- Refused: a credential on `direct://` or `quic://`, a malformed escape, a
  NUL byte, invalid UTF-8, a username or password over 4096 bytes, two proxies
  with different credentials, and a credential in both the URL and
  `--apostate-profile`.

## Precedence

Strongest first:

1. `--apostate-profile` describing a device: your profile, nothing composed.
2. `--fingerprint=host`: the real machine, nothing composed.
3. Per-field switches: one value each, on top of the seed's machine.
4. `--fingerprint=SEED`.
5. The seed in `DIR/apostate/identity`, when `--user-data-dir=DIR` is set.
6. A fresh seed from OS entropy.

`--fingerprint=host` refuses to launch together with a persona, a GPU family
or a per-field switch.

## Chromium switches that matter

| Switch | Note |
| --- | --- |
| `--user-data-dir=DIR` | Keeps cookies and storage, and binds the machine to `DIR`. |
| `--headless=new` | Works, and leaves `HeadlessChrome` out of the User-Agent. |
| `--lang` | No effect on a persona. Use `--fingerprint-locale`. |
| `--window-size=W,H` | Sets the window size. A screen override smaller than it is refused. |
| `--use-angle` | Changes how the host renders, not the GPU a page is told about. |
| `--remote-debugging-pipe` | Opens no port. Playwright uses it. A page served from a local or private network address can detect an open `--remote-debugging-port`. |

## Python and Node options

`launch()` returns a browser whose `new_page()` opens pages in a normal
profile, a temporary one deleted when the browser closes. Its `new_context()`
is off-the-record, as in Playwright, and sites can tell.
`launch_persistent_context(user_data_dir, ...)` (Node:
`launchPersistentContext(userDataDir, ...)`) returns a context bound to that
directory, and is how a machine is kept across runs; `launch()` refuses a user
data directory. `launch_context()` (Node: `launchContext()`) returns the
temporary profile's own context. Python also has async versions of all three
(`launch_async()`, `launch_context_async()`,
`launch_persistent_context_async()`). Node also has `launchProcess()`, which
starts the browser without a driver.

| Python | Node | Default | Effect |
| --- | --- | --- | --- |
| `fingerprint` | `fingerprint` | fresh seed | `--fingerprint`. An integer or a string. |
| `fingerprint_platform` | `fingerprintPlatform` | host default | `--fingerprint-platform`. |
| `proxy` | `proxy` | none | Proxy URL. The endpoint goes to `--proxy-server` and the credential inside `--apostate-profile`. |
| `geoip` | `geoip` | on | Look up the exit's locale and timezone before launch. |
| `geoip_timeout` | `geoipTimeoutMs` | 20 s | Time limit for the whole lookup. |
| `locale` | `locale` | from GeoIP | `--fingerprint-locale`. GeoIP gives the exit country's locale tag; pass a list to set the language list yourself. |
| `timezone` | `timezone` | from GeoIP | `--fingerprint-timezone`. |
| `headless` | `headless` | on | `--headless=new`. When off on a Linux machine with no display, the package starts Xvfb. |
| `args` | `args` | none | Extra switches. An unknown or misspelt `--fingerprint*` switch is refused. |
| `profile` | `profile` | none | A profile you wrote, sent as `--apostate-profile`. A dict or a JSON file path in Python, an object in Node. |
| `binary_path` | `executablePath` | found or downloaded | The browser to run. `APOSTATE_BINARY` does the same. |
| `cache_dir` | `cacheDir` | per-OS cache | Where the browser is installed. `APOSTATE_CACHE_DIR` does the same. |
| `manifest` | `manifest`, `manifestPath`, `manifestUrl` | the package's own | The release manifest that holds the archive's SHA-256. |
| `driver` | `driver` | first installed | `patchright` or `playwright`. Node also takes `playwright-core`, `puppeteer` and `puppeteer-core`. |

In Python, any other keyword goes to Playwright's `launch_persistent_context`,
so its launch options and its context options, such as `viewport`, both work;
the context entry points also take `context_options`. Node passes `env` and
`ignoreDefaultArgs` through to the driver.

GeoIP asks `ip-api.com`, `ipinfo.io`, `ipwho.is` and `ifconfig.co` over plain
HTTP, through the proxy when there is one, and stops at the first answer with
a country and a timezone. The country maps to a locale through
`config/country-locales.json`, so a German exit gives `de-DE`. A failed lookup
adds a warning and leaves out the switch it could not fill, so the persona uses
`en-US` or the host's timezone there. Pass `locale` and `timezone` yourself to
be sure.

Behind a proxy the Node package also passes the exit address from GeoIP as
`--fingerprint-webrtc-ip` and adds Chromium's
`--force-webrtc-ip-handling-policy=disable_non_proxied_udp`. The Python package
does neither.
