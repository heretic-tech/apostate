# How it works

A persona is a profile: a JSON document listing the values a machine shows to
web pages. At startup the browser composes one from a seed. Every process
reads it, and patches in Chromium's C++ serve its values at the places where
Chromium produces them.

## The profile

The browser process composes the profile before it starts any other process.
Every child process, whether renderer, GPU or network, receives the same
profile, base64-encoded, in its `--apostate-profile` switch, so every process
reads the same values and none of them draws its own. Every section is
optional. A missing section leaves that part of the machine to the host.

The schema is `config/profile.schema.json` (schema version 3). Its sections:

| Section | Controls |
| --- | --- |
| `id` | An identifier derived from the seed. No page sees it; readback noise is keyed on it. |
| `source_capture` | Where a profile came from. Removed before the browser reads the profile. |
| `platform` | OS name and version, CPU architecture and bitness, `navigator.platform`, model, WoW64, form factor. The User-Agent and Client Hints are built from it. |
| `browser` | The User-Agent string. The brand and version lists are recorded here, but the build produces them. |
| `cpu` | Logical core count, capped at the host's. |
| `memory` | Installed memory, capped at the host's. `navigator.deviceMemory` and the incognito storage quota follow it. |
| `gpu` | WebGL unmasked vendor and renderer strings. |
| `gl_limits` | WebGL numeric limits, such as `MAX_TEXTURE_SIZE`. WebGL reports them on every host; Chromium's own drawing keeps the lower of these and the host's. |
| `gl_extensions` | The WebGL extensions the GPU has. Extensions it lacks are removed. Five that only add constants are added even when the host lacks them; any other extension shows only if the host supports it. |
| `gl_precisions` | `getShaderPrecisionFormat()` results. |
| `webgpu` | WebGPU adapter vendor, architecture, subgroup sizes, features and limits. |
| `screen` | Size, available area and taskbar or menu-bar insets, pixel ratio, colour depth, gamut, HDR, extra displays. |
| `window` | Window frame sizes. The browser does not use them; the real window's frame shows. |
| `locale` | Timezone, and the UI locale (`application`) or the Accept-Language list. |
| `theme` | Dark mode, highlight colours, and the fonts CSS system font keywords resolve to. |
| `input` | Pointer type and hover. |
| `audio` | Audio output buffer size, which sets `AudioContext.baseLatency`. |
| `media` | Cameras, microphones and speakers a page can list, and which video codecs `MediaCapabilities` reports as power efficient. |
| `speech` | The voices `speechSynthesis.getVoices()` lists. |
| `keyboard` | The layout map `navigator.keyboard.getLayoutMap()` returns. |
| `fonts` | The font families a page can see, and the fonts the generic families resolve to. |
| `network` | `navigator.connection` values and the Save-Data setting. |
| `battery` | What `navigator.getBattery()` reports. |
| `extensions` | Whether pages see `chrome.runtime` from an installed extension. |

You can write a profile yourself and launch it with `--apostate-profile`
([FLAGS.md](FLAGS.md#a-profile-you-wrote)). The browser then composes nothing.

## Composing a machine from a seed

The catalogue says what a machine can be:

- `resources/profiles/catalogue.json`: the catalogue version, the GPU
  families, and the order of the choices.
- `resources/profiles/dispersion/*.json`: one table per choice, each a list of
  real options with weights.
- `corpus/anchors/*.json`: the measured GPU families (below).

`scripts/generate-dispersion-tables.py` compiles these into the binary during
the build. The browser does not read them from disk.

A machine is a series of weighted choices, made in this order: platform, OS
release, GPU family, GPU model, machine class, CPU cores, memory, screen,
taskbar or menu bar, font packs, media devices, audio, network, battery,
voices, extensions. Later choices depend on earlier ones. The machine class,
core count and memory depend on the GPU model, and the screen on the machine
class, so a persona never gets, say, an Apple M4 Max with a MacBook Air screen.
The persona picks the GPU family: a Windows persona draws a Direct3D 11
family, macOS the Metal one and Linux the Vulkan one, whatever GPU the host
has.

The seed decides every choice. The browser hashes it (SHA-256) together with
the profile schema version, the catalogue version, the Chromium version and
the persona, then draws each choice from its own hash of that result. The
same seed, persona and build give the same machine on every host with enough
cores and memory for it. A new Chromium version or catalogue version gives
every seed a new machine.

A drawn core count or memory size above the host's is lowered to the host's.
Language and timezone are never drawn from the seed, because a drawn timezone
would not match the proxy exit. They come from `--fingerprint-locale` and
`--fingerprint-timezone`, which the packages fill from GeoIP. Without them the
persona uses `en-US` and the host's timezone.

Without a seed, the browser draws a new one for each launch. With
`--user-data-dir`, it stores the seed in `DIR/apostate/identity` on the first
launch and reads it on every launch after, so a persistent profile keeps its
machine. `--fingerprint-explain` prints every choice a launch made and where
it came from.

`scripts/profile_resolver.py` is a Python copy of the compositor, used to
check the tables without a build. Its output is compared byte for byte with
the browser's through golden digests ([RELEASE.md](RELEASE.md#refreshing-the-native-golden-profile-digests)).

## Where the values come from

Our own captures of real machines are in `resources/fingerprints/raw/`. The
collector page in `capture/` records what a browser shows a web page, and the
captures were taken with it in headed Chrome on:

- macOS on an Apple M4 Max,
- Windows with an NVIDIA RTX 3070 Ti, an NVIDIA RTX A4500 and Intel UHD
  Graphics 630,
- Linux with an NVIDIA RTX 3060, RTX 3090, RTX 4070 Ti SUPER, RTX 4080 SUPER
  and RTX PRO 4000.

Two more captures show stock headless Chrome on Linux and macOS, for
comparison.

The GPU values come from four GPU families measured on that hardware:

| Family | Measured on | Models a persona can name |
| --- | --- | --- |
| Windows, NVIDIA, Direct3D 11 | RTX 3070 Ti, RTX A4500 | 45 GeForce and RTX models, GTX 10 series to RTX 50 series |
| Windows, Intel, Direct3D 11 | UHD Graphics 630 | 12: UHD 620, 630, 730, 770 and Iris Xe |
| macOS, Apple, Metal | M4 Max | 12: M1 to M4, base, Pro and Max |
| Linux, NVIDIA, Vulkan | RTX 4070 Ti SUPER, RTX 4080 SUPER, RTX 3090, RTX PRO 4000 | 11: RTX 3090, RTX 40 series, RTX PRO 4000 |

Within a family, WebGL limits, extensions and shader precision are the same
for every model, and so is the WebGPU adapter apart from its architecture
name and, on Intel's 12th-generation graphics, its smallest subgroup size,
which follow the model's chip generation. A persona can therefore name
any model of the family by changing the renderer string. The models are
listed in `resources/profiles/dispersion/gpu_identity.json`. A fifth family,
the SwiftShader software renderer, is only used when pinned with
`--fingerprint-anchor`.

The other tables (screens, cores, memory, fonts, voices and so on) take their
options from the captures and from what real machines of each kind ship with.
We also use a large public collection of older Windows fingerprints as
reference. It is someone else's data, so it stays outside the repository and
no build step reads it.

## How a value gets changed

Each change is a patch in `patches/` that edits the Chromium code producing a
value, so the value is right everywhere Chromium uses it, and no JavaScript is
injected. For example, the core count is changed in
`base::SysInfo::NumberOfProcessors()` rather than in
`navigator.hardwareConcurrency`, so the thread pools Chromium sizes from it
agree with what a page reads.

`patches/series` lists the patches in the order they apply. The order follows
dependencies, not numbers: a patch that edits code another patch added comes
after it. A patch file is a short description followed by the diff;
[BUILD.md](BUILD.md#writing-a-patch) has the details.

Proxy support is patched the same way. SOCKS5 proxies take a username and
password, a proxy URL can carry its credential, and UDP (QUIC and WebRTC) goes
through a single SOCKS5 proxy with UDP ASSOCIATE. The credential stays out of
logs and error messages.

## Host mode

`--fingerprint=host` (or `fingerprint="host"` in the packages) turns
composition off. The browser builds no profile, and every value a persona
would change is the host's own. Use it to tell whether a problem comes from
the persona or from the machine and network underneath.

## Repository layout

| Path | Holds |
| --- | --- |
| `patches/` | The Chromium changes, and `series`, their order |
| `build/` | Pinned build inputs and `MANIFEST.lock`, the record of the last build |
| `scripts/` | Build, release and check scripts, the table generator, the Python resolver, `measure-fpjs.py` |
| `resources/profiles/` | The catalogue and its option tables |
| `resources/fingerprints/raw/` | Our captures of real machines |
| `corpus/anchors/` | The measured GPU families |
| `config/` | The profile and launch schemas, and the country to locale table GeoIP uses |
| `capture/` | The collector page and the tools for taking a capture |
| `python/`, `npm/` | The Python and Node packages |
| `release/` | The release manifest schema |
| `.github/` | CI workflows and the release policy |
| `docs/` | These documents |
| `.workspace/` | Not committed: the Chromium checkout, depot_tools and build output |
