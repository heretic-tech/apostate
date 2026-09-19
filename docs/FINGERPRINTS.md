# Fingerprint composition

How a launch gets a fingerprint. `docs/PROFILE_SPEC.md` is the field contract;
this document is the model that produces the fields, and it defers to
`docs/METHODOLOGY.md` on what counts as evidence.

## 1. Why the catalogue model was replaced

The first model shipped a fixed catalogue: fourteen families, each a complete
device contract, selected by seed. Three properties made it unusable as a
product.

**It could not be unique.** Fourteen families is fourteen fingerprints. Two
users who drew the same family were byte-identical to each other, which is a
stronger correlation signal than either user's original host.

**It required a host match.** A profile was only offered when the host could
serve every surface in it, so a macOS user had macOS families and nothing else.
The product requirement is the opposite: a user asks for Windows and gets
Windows, with the cost of that choice stated rather than the choice refused.

**It was not backed by measurement.** All fourteen families carry
`compatibility-capture` provenance derived from one external runtime's output.
The measurement that closes the question: across the fourteen, the WebGL1
capability digest, the WebGL2 capability digest and the canvas pixel digest are
each a single value. One digest, shared by supposedly distinct Intel HD
Graphics, GeForce GTX 960, Radeon RX 9060 XT and Apple M4 machines. They are
identity-string swaps taken on one Mac. The host's real capability tables were
never touched.

That last result is also the most useful thing the old corpus produced, because
it is exactly the failure mode a detector looks for, and it is the reason
section 4 exists.

## 2. Three layers

Every observable belongs to exactly one layer. The layer decides who owns the
value and what may vary it.

| Layer | Owner | Varies | Evidence |
| --- | --- | --- | --- |
| **Invariant** | the binary | never | the build itself |
| **Anchor** | a measured device cluster | per anchor, atomically | T0 capture |
| **Dispersion** | the compositor | per profile, by seed | enumerated real-world options |

**Invariants** are what the binary is: Chromium version, brand list, API
surface, `Function.prototype.toString` output, which codecs are compiled in,
whether a CDM is registered. A profile never varies these. When one is wrong it
is a build or patch defect, not a profile defect. See section 8.

**Anchors** are the clusters that cannot be recombined without contradiction.
The GPU cluster is the canonical one: WebGL1 and WebGL2 extension lists,
parameter tables, shader precision, context attributes, sample counts, the
pixel render digest, and the WebGPU feature and limit sets. These values are
produced by one piece of silicon running one driver through one backend. Any
mixture of two anchors is a device that does not exist.

An anchor is atomic: selecting it takes the whole cluster. Anchors come from
`corpus/anchors/`, each one derived from admitted T0 captures, each carrying
the digests that were compared to establish its membership.

**Dispersion** is everything a real machine varies because of what its owner
installed, plugged in, or configured: which fonts are present, how many
microphones, which voice packs, panel size, taskbar position, core count. This
is where per-profile uniqueness comes from, and it is large enough to carry the
whole product: the dispersion space is combinatorially big while every point in
it remains a machine someone could own.

## 3. The two rules that keep dispersion honest

**Selection, never synthesis.** A dispersion draw chooses among options that
were observed on real systems. It never produces a novel value. Five randomly
chosen font families is synthesis and is a tell, because installed fonts arrive
in bundles: a machine with Myriad Pro has the rest of Creative Cloud, a machine
with Cascadia Code has a developer's toolchain. Dispersion selects bundles.

**Servability: capacity is only ever reduced.** A profile may claim fewer cores
than the host has, never more. Same for memory, GPU limits, codec support, font
families, speech voices, and display area relative to window bounds. The reason
is falsifiability, not modesty: a page can measure parallel throughput,
allocate until it fails, compile a shader at the advertised limit, or ask a
voice to speak. A claim below host capability survives every one of those
probes; a claim above it fails the first. `scripts/check-servable.py` is the
gate.

GPU limits are the exception, and it is a deliberate one. On a backend that
enforces nothing about the numbers it reports — a software rasteriser — the
claim is served upward rather than clamped down, because there the reported
figure is a soft constant and not a ceiling. The price is a claimed maximum that
cannot be allocated at, which
[docs/LIMITATIONS.md](LIMITATIONS.md) states in full with the measurements.

This is the specific respect in which clamping beats spoofing. Reporting the
host's true 14 cores and 32GB identifies one model of laptop. Reporting a
fabricated 20 cores contradicts any timing probe. Reporting 8 cores, a real
bucket that a 14-core host can serve, is both common and unfalsifiable.

## 4. Anchors, identity strings, and the limit of string swapping

Within an anchor, the identity strings (`unmaskedVendor`,
`unmaskedRenderer`, WebGPU `vendor`/`architecture`) are rotatable, because the
anchor's members measurably agree on everything else. Across anchors they are
not.

Two counts matter here and they are not the same number. The corpus holds 5
anchors and 9 measured members. The catalogue offers more presentable identity
strings than that, each one registered on a specific anchor and carrying that
anchor's measured capability cluster, each labelled `catalogue-value` with its
own source. The table generator hard-fails if an offered identity is not
registered on an anchor, so an identity can never arrive without a capability
cluster behind it. What a page reads is the identity string; what it can probe
is the cluster, and the cluster is measured.

The five anchors in `corpus/anchors/`, measured here, with digests as emitted by
`scripts/build-anchors.py` (first 8 hex of the full SHA-256 in the anchor file):

| Anchor | Backend | Members | WebGL1 caps | WebGL2 caps | WebGL pixels |
| --- | --- | --- | --- | --- | --- |
| `linux-vulkan-nvidia-adf287b8f0ee` | ANGLE/Vulkan | RTX 4070 Ti SUPER, RTX 4080 SUPER, RTX 3090, RTX PRO 4000 Blackwell | `2327f6ae` | `c7aa8b52` | `7e8e3b73` |
| `windows-d3d11-nvidia-0947761dfbe9` | ANGLE/D3D11 | RTX 3070 Ti, RTX A4500 | `ff875414` | `a34893d0` | `a895ab2a` |
| `macos-metal-apple-850a91233555` | ANGLE/Metal | M4 Max | `53ec118e` | `102f6613` | `918f09f6` |
| `windows-d3d11-intel-79dfeb5b4f99` | ANGLE/D3D11 | UHD 630 | `be8acf33` | `e695c2da` | `918f09f6` |
| `linux-swiftshader-google-6922d61bab83` | ANGLE/SwiftShader | SwiftShader Device (Subzero) | `6087e24b` | `9b8362e1` | `918f09f6` |

The last one is the odd one. It is a software rasteriser, captured from a stock
Chromium rather than from hardware, so its evidence class is
`compatibility-capture` and it claims no hardware at all. No seed draws it, on
any host, including a host with no GPU: a GPU-less host serves the profile's
hardware identity like any other, and a renderer string that names a software
rasteriser is a signal handed to a detector for free. What the anchor remains is
the way to ask for stock software rendering deliberately, through
`--fingerprint-anchor` or the two GPU string switches. Serving it claims stock's
limit figures rather than the raised ones patches `0027` and `0038` give this
fork's own SwiftShader, and because the clamp only ever reduces, stock's figures
are what reach the page — so a launch that pins it looks like stock software
rendering rather than like this fork. It is the newest of the five and it has
not been compiled into a binary yet: its id is absent from the shipped
152.0.7977.83 macOS build while the two hardware anchors that build serves are
present. It also offers one identity, because it has one member, so it rotates
nothing.

Four results follow, and they do not all point the same way.

**Within a backend, silicon generation does not matter.** Ada, Ampere and
Blackwell produce byte-identical WebGL1 and WebGL2 capability tables and an
identical WebGL render digest on Linux/Vulkan. The two Ampere-class cards agree
with each other on Windows/D3D11. So one anchor legitimately covers a range of
cards and the renderer string is cosmetic relative to it. This is measured.

**Across backends, the capability tables transfer nothing.** The same NVIDIA
silicon produces a different capability digest through D3D11 than through
Vulkan, and Apple Metal differs from both. Presenting a Windows/D3D11 renderer
string on top of an Apple Metal capability cluster is the same failure section 1
describes.

**The WebGL render digest is not a backend discriminator.** Apple/Metal and
Intel/D3D11 produce the same `918f09f6` pixels. A matching render digest is
therefore necessary but not sufficient evidence of interchangeability; the
capability tables are what discriminate, and the anchor is keyed on them.

**Two surfaces are inside an anchor's members but outside the anchor.** The
WebGPU cluster differs between members of the Linux/Vulkan anchor, because it
belongs to each host's driver stack rather than to the silicon class, so it is
recorded per member rather than per anchor: that anchor's WebGPU is non-uniform,
with two variants across four members. Canvas 2D differs too, and canvas is not
a GPU measurement at all. It is fonts and raster, which is why it is excluded
from the anchor key and recorded separately so the exclusion stays checkable.

Being outside the anchor key does not make WebGPU independent of the identity.
A launch serves `adapter.info` and the WebGPU limit table from the same measured
member it takes the WebGL cluster from, so the two surfaces agree by
construction. [docs/LIMITATIONS.md](LIMITATIONS.md) has that as a property, with
the one case where a member has no adapter to serve.

Therefore: **`--fingerprint-platform` selects the GPU cluster.** It changes OS
identity, client hints, fonts, voices, locale, screen geometry and hardware
buckets, and the anchor draw is filtered by the claimed platform, on every host.
The host's own graphics backend is not consulted. `windows` draws one of the two
Direct3D 11 anchors, `macos` the Metal one, `linux` the Vulkan one, and the
identity rotates within whichever is drawn.

This is only sound because of the result above it: within a backend, silicon
generation does not matter, so an anchor is a statement about a backend rather
than about a machine. One backend per platform in the catalogue means the
persona determines the backend outright, and there is nothing left for the
host's to decide.

The default persona is the host's own OS on macOS and Windows, and `windows` on
Linux. The software anchor is excluded from every draw.

The consequence for deployment is a font question rather than a GPU one. A
coherent Windows fingerprint wants its Windows fonts installed, wherever it
runs; what it no longer needs is Windows hardware.
[docs/LIMITATIONS.md](LIMITATIONS.md) has the cross-OS risk ordering and the
font prerequisite.

### Three anchors were measured on another build

Capability tables move between Chromium releases, so an anchor measured on one
build is not an exact target for a binary of another. Three of the five were not
measured on 152.0.7977.83, and the anchor files record it:

- `windows-d3d11-nvidia-0947761dfbe9` was measured on Chromium 153.0.8010.37.
  It is evidence about Ampere-class D3D11 silicon, and its version-bearing
  fields belong to a different major.
- `linux-vulkan-nvidia-adf287b8f0ee` and `linux-swiftshader-google-6922d61bab83`
  were measured on 152.0.7977.82, the patch release before the pin, so their
  version-bearing fields differ in that field only.

`macos-metal-apple-850a91233555` and `windows-d3d11-intel-79dfeb5b4f99` are on
the pinned build. Closing the other three means re-capturing them on the pinned
binary.

## 5. Seed, and what a seed is worth

A seed is a deterministic selector over the dispersion space. It is not a
randomizer and it never reaches a value at read time.

| Launch | Seed source | Result |
| --- | --- | --- |
| no `--user-data-dir` | fresh OS entropy, per launch | a new device every launch |
| `--user-data-dir=DIR` | `DIR/apostate/identity`, minted on first use | the same device every launch of that directory |
| `--fingerprint=<seed>` | the argument | the same device anywhere, every launch, over either of the above |
| `--fingerprint=host` | none | no composition; the host's own values |

A persistent profile keeps one identity because the cookies and sessions in
that directory are what a site ties to a machine; the file holds one seed and
a newline, so `--fingerprint=<seed>` reproduces the same machine on a second
host, and an explicit `--fingerprint` wins over the file without touching it.
[docs/FLAGS.md](FLAGS.md) has the precedence and the failure cases.

Either way the profile is fully materialized before the first renderer starts,
and nothing inside the session varies.

### Derivation

```text
root = SHA-256( "apostate/fp/v1" 0x00
                profile_schema_version 0x00
                catalogue_version      0x00
                chromium_version       0x00
                fingerprint_platform   0x00
                seed )

draw(label, i) = SHA-256( root 0x00 label 0x00 uint32_be(i) )
```

Every axis draws from a substream keyed by its own label. This is the property
that lets the catalogue grow: adding an axis, or changing the option list of
one axis, cannot shift any other axis's choice, so an old seed keeps producing
the same profile for everything that did not change.

Weighted selection from an option list uses the high 64 bits of `draw` as
`u64`, and picks index `(u64 * total_weight) >> 64` by cumulative weight. No
modulo, no rejection loop, no floating point.

### Resolution order

Axes resolve in dependency order, and a dependent axis draws from an option set
that is a function of its parents' resolved values. There is no rejection
sampling and no re-draw, so every profile is coherent by construction rather
than by validation.

```text
platform persona -> os release -> anchor (claimed platform)
  -> identity string -> cpu bucket -> memory bucket -> panel -> furniture
  -> font packs -> media topology -> audio buffer -> voice table
  -> locale/timezone
```

A coherence-graph violation after resolution is a defect in the option tables,
never something the compositor works around at runtime.

## 6. The dispersion axes

Each axis has an option table under `resources/profiles/dispersion/`, and each
option carries its evidence class and a weight. Weights encode real-world
prevalence; they are not uniform.

| Axis | Option unit | Conditioned on | Servability limit |
| --- | --- | --- | --- |
| `os_release` | OS build + client-hint `platformVersion` + the platform's UA string | platform | none |
| `anchor` | GPU capability cluster | platform | the claimed platform's anchors, minus the software one; the host's backend is not consulted |
| `gpu_identity` | vendor + renderer string pair | anchor | must be registered on the anchor |
| `cpu` | core-count bucket | platform, device class | `<=` host logical cores |
| `memory` | `deviceMemory` bucket | device class | `<=` host physical memory |
| `panel` | width x height x DPR | platform, device class | `>=` window bounds |
| `furniture` | taskbar/dock edge, size, autohide | platform, os release | none |
| `font_packs` | an installed-software bundle | platform, os release | none |
| `media_topology` | input/output/camera counts + labels + group pairing | platform | none |
| `audio` | output buffer size in frames | platform | none |
| `voices` | voice table for an OS release and language set | platform, os release, languages | provider must be able to speak |
| `locale` | language list + timezone | launch precedence, then GeoIP, then the host | none; never drawn |

Notes that matter per axis:

**OS release.** This axis owns every surface that names the operating system:
`navigator.platform`, the `Sec-CH-UA-Platform` and `-Platform-Version` hints,
and the complete `navigator.userAgent` string. They live in one option because
a browser that contradicts itself about its OS is more detectable than one
claiming nothing, and one draw cannot contradict itself. Splitting the UA into
its own axis would let two draws pick a Windows `navigator.platform` and a macOS
user agent, which is worse than shipping neither.

The UA string is stored as a physical device sent it, never assembled: the OS
token, the AppleWebKit version and the trailing Safari token vary together, and
only a capture records them together. Chrome's UA reduction freezes the OS token
per platform, so one measured string covers every release of that platform --
every Windows capture in `resources/fingerprints/raw` carries
`Windows NT 10.0` whatever the real build, every macOS one
`Intel Mac OS X 10_15_7` whatever the real version.

The browser version inside it is not the profile's. It is an invariant this
binary owns, so the major is stamped in from the pinned build --
`user_agent_for_build` in `scripts/profile_resolver.py`, called once by
`scripts/generate-dispersion-tables.py` when the tables are compiled and again
on the reference path, so both implementations serve one string. A Chromium bump
therefore moves the UA without a data edit, and a table string the rule cannot
stamp fails the build rather than shipping a stale major.

**Font packs.** The mandatory core set for an OS release is not optional. A
machine claiming macOS without Menlo, Monaco, Zapfino, PingFang SC and
Helvetica Neue is not a Mac, and that absence is the defensible detection. On
top of the core set, packs model what software installs: Office, Creative
Cloud, the CJK language packs, LibreOffice's Liberation/DejaVu, a developer's
Cascadia. Enumeration is subtractive: it removes families the claimed device
would not have and never adds one, so a face the machine lacks cannot be
presented. Installing the claimed platform's fonts is the operator's job and the
compositor assumes it has been done, rather than checking the filesystem and
warning. [docs/FONTS.md](FONTS.md) is the instructions.

**Media topology.** `deviceId` and `groupId` are already per-origin HMACs in
Chromium, so the fingerprint content is the count, the kind mix, the group
pairing, and the labels once permission is granted. Labels are drawn from
per-platform vendor tables: a Realtek codec name is a Windows artifact and must
never appear on macOS.

**Audio.** One number, `audio.hardware_buffer_frames`, and it is the whole of
`AudioContext.baseLatency`: Blink computes
`max(framesPerBuffer, renderQuantumSize) / sampleRate` once per context and
caches it, so a page reads the claimed machine's audio pipeline length in a
single property access with no permission. The values are per-backend and
measured where a capture exists — CoreAudio 256, WASAPI shared mode 480 — with
Linux carrying Chromium's own Pulse floor of 512 because every admitted Linux
reference turns out to have no audio device at all. It is conditioned on the
platform and not on `media_topology`, because no capture measures one machine
with two output devices and dispersing on an unmeasured axis is synthesis.

The denominator is not ours. `audio.sample_rate` is undeclared
(`audio.context-sample-rate`, verdict `escalate`), so the claimed frame count is
served over the host's real rate. On a 48 kHz host every persona lands exactly
on its reference capture. On a 44.1 kHz host it does not, and not uniformly:
CoreAudio and PulseAudio pin the frame count, while WASAPI shared mode pins the
10 ms period, so a real Windows machine at 44.1 kHz reports 441 frames rather
than 480. Declaring a rate is therefore not sufficient to close that row; it has
to decide which of the two each platform pins.

**Voices.** The table is a function of OS release and installed language packs,
keyed on the resolved language list. A launch that names no locale keys onto the
empty set, which carries no `speech` section, so the host's real providers stay
in effect — the only coherent answer when the language list is the host's too.
Network voices (`localService: false`) are a build-level capability, not a
profile value. See section 8.

**Locale.** The one row in the table above that is not drawn, and the only
surface whose correct value is a property of the network rather than of the
machine. Precedence, strongest first:

1. `--fingerprint-locale` and `--fingerprint-timezone`, one field each.
2. GeoIP of the effective egress — the proxy exit when `--proxy-server` is set,
   the direct IP otherwise. The Python and Node packages perform that lookup
   before launch and pass the answer as the switches above, so the compositor
   sees layers 1 and 2 as one; the packages keep them apart in their own
   diagnostics, and `locale_source` / `timezone_source` say which answered.
3. The host, expressed as absence. No `locale` section is composed, which
   leaves the host's own zone in ICU and its own list in the Accept-Language
   pref. Both fields fall back together, because a field nobody named is never
   written by a source that cannot also answer for the other.

A seed is not a layer here, and used to be: four catalogue policies were drawn
from the composition root, so a bare launch on an `Asia/Bangkok` host served
`America/New_York`, `Europe/London` or `Australia/Sydney` by seed. That is
worse than inheriting the host, not better. A drawn timezone cannot correlate
with an exit IP the draw never saw, so it guarantees the mismatch that
`coh.timezone-network-position` is about, where the host's own zone at least
matches a direct connection. Patch 0111 removed the draw and the compiled
table it drew from; the four policies remain in the catalogue as the set
`scripts/profile_resolver.py --locale-policy` selects from, each an internally
consistent language-list-and-timezone pair.

A failed or partial GeoIP lookup invents nothing. The field it could not answer
for falls to the host and the launcher warns; `AL`, for instance, has a
timezone in every provider and no entry in `scripts/geoip.py`'s country-locale
policy, so an Albanian exit resolves `Europe/Tirane` and leaves the language
list to the host.

**Panel and furniture.** `availLeft`/`availTop`/`availWidth`/`availHeight`
follow from the panel plus the furniture model; `outerWidth`/`outerHeight`
follow from the OS's window chrome for that release. A forced device scale
factor makes a claimed DPR real at rasterization time rather than only at the
accessor.

## 7. Where composition runs

The compositor lives in the browser process, in C++, and is the only
implementation.

The bare binary must produce a fingerprint with no arguments, which puts the
compositor inside the binary by necessity. Reimplementing it in the Python and
Node packages would create three sources of truth for one deterministic
function, and the drift between them would be silent. So the packages stop
composing: they call the binary to materialize a profile, and keep doing what
only they can do: CLI ergonomics, schema validation, launch orchestration and
GeoIP.

Determinism across languages is then a property of the binary, and is pinned by
golden seed vectors rather than by cross-language byte comparison.

## 8. What belongs to the build, not the profile

Three surfaces in the current gap list are invariants, and no profile field can
fix them:

- **Brand list.** `components/embedder_support/user_agent_utils.cc` adds the
  product brand only under `#if !BUILDFLAG(CHROMIUM_BRANDING)`, so a Chromium
  build emits two brands where Chrome emits three. The GREASE brand and version
  are already correct, because both derive from the major version.
- **Codecs.** `proprietary_codecs` and `ffmpeg_branding = "Chrome"` live in
  `build/args/common.gni`. A build configured without that file has no H.264
  at all. HEVC follows the platform decoder on macOS and Windows and patch
  `0061` on Linux x64.
- **Network speech voices.** The `localService: false` voices are served by
  Chromium's network speech synthesis component against a Google endpoint, which
  requires API keys at build time. Without keys the voices cannot be listed,
  because a listed voice that cannot speak is worse than an absent one. Patch
  `0043` enforces that.

A fourth looked like one and is not. It is recorded here because it was
documented as an invariant on one measurement and turned out to be a defect of
ours on the next.

- **The `Intl` default locale**, and with it `navigator.languages`,
  `navigator.language`, `DateTimeFormat`, `NumberFormat`, `Collator` and every
  `toLocaleString`. Every `Intl` constructor resolves its default against
  `Isolate::DefaultLocale()`, which is ICU's process default converted to a
  language tag (`v8/src/execution/isolate.cc:8123`). Chromium sets that default
  from the *application* locale, which `l10n_util` resolves against the UI
  resource bundles the build actually ships. It was shipping exactly one.
  [`scripts/package-artifact.sh`](../scripts/package-artifact.sh) listed
  `locales/en-US.pak` as the only pak in the required set for linux-x64,
  linux-arm64 and windows-x64, while Chromium's build produced all 220 —
  packaging discarded them. That is fixed and the record of it stays, because
  the *consequences* of fixing it are the rest of this entry.

  While one pak shipped, nothing moved the locale: `LANG=de_DE.UTF-8`,
  `LC_ALL=fr_FR.UTF-8`, `LC_ALL=ja_JP.UTF-8`, `--lang=de-DE` and `--lang=fr-FR`
  all left every member at en-US. That is not a property of Chromium. Stock
  Google Chrome 151 with its full pak set, same host, same probe:

  ```text
  --lang=de-DE         en-US,en / en-US / en-US / en-US / en-US   unchanged
  LANGUAGE=de          de-DE,de,en-US,en / de-DE / de / de / de
                       Date 1.1.1970, 00:00:00   Number 1.234.567,89
  ```

  Two things follow. The lever is the process **environment**, not `--lang`,
  which moves nothing even on stock. And the application locale feeds the
  Accept-Language default as well as ICU's, so one lever drives both producers
  — which is what makes agreement achievable rather than a coincidence to be
  maintained.

  It was never missing ICU data. The same shipped binary formats any locale
  named explicitly: `new Intl.NumberFormat("de-DE")` gives `1.234.567,89`,
  `toLocaleString("de-DE", {month: "long"})` gives `Januar`,
  `supportedLocalesOf` accepts de, fr, ja, ar, zh, th, ru, pt, es and it, and
  `icudtl.dat` is the full 10.8 MB set. Only the default was pinned, and the pak
  list pinned it.

  Do not test this by mixing paks across versions: Chromium 153's paks beside a
  152 binary core-dump, because resource ids are version-specific. macOS is
  unaffected — `package-artifact.sh` ships `Chromium.app` wholesale and the
  bundle already carries 55 `.lproj` directories.

  **That build has landed, and it turned the lever on.** The artifact now ships
  440 paks, so the environment moves the application locale for real — and the
  first thing it moved was the host's own value into a composed persona. On a
  host with `LANG=th_TH.UTF-8` and `LANGUAGE=th`, a Windows persona served Thai
  `navigator.languages`, Thai `Intl` formatting, and `calendar: "buddhist"`,
  which restates every year it touches as `1/1/2513`. That is not inheritance,
  it is the host showing through a composed profile, and it was worse than the
  en-US the wider pak set replaced.

  So the environment is written, never inherited, in three places that agree:
  the Python package, the Node package, and — since patch `0114` — the browser
  itself, for the bare launch that goes through neither. Only
  `--fingerprint=host` inherits the host's locale environment, because only
  there is the host the thing being presented. A composed persona gets
  `LANGUAGE`, `LC_ALL`, `LC_MESSAGES` and `LANG` written explicitly — to the
  resolved locale when a launch layer named one, and to the composed default
  `en-US` when none did. All four, because each moves the application locale on
  its own, so leaving any one of them at the operator's value lets the host win
  through a variable nobody wrote. `LANGUAGE` takes the BCP-47 tag as written
  and the other three take the POSIX spelling; a locale the host has not
  generated therefore still works, because `LANGUAGE` needs nothing generated.

  Writing them is necessary and not sufficient, and this is the part worth
  remembering. `content/app/content_main.cc` runs `setlocale(LC_ALL, "")` before
  any embedder hook exists, so the C library has already adopted the operator's
  environment; glib reads `LANGUAGE` live but falls back to the C library when
  it is unset. Measured: with `LANG=en_US.UTF-8` inherited and the composed
  environment written, `LC_MESSAGES` was still `en_US.UTF-8`. Patch `0114`
  therefore re-runs `content`'s two `setlocale` calls from `"C"`, the locale a
  fresh process starts in, which reproduces exactly the state a launcher-set
  environment produces. It runs from `InstallComposedProfile` at
  `PreSandboxStartup`, after the compositor and before both the zygote fork and
  the `PreCreateThreadsImpl` resolution, so every child inherits the answer
  rather than deciding again.

  **The environment is the Linux lever and only the Linux lever**, so `0114`
  closed Linux and only Linux. The other two resolve the application locale
  from somewhere the environment does not reach, and on macOS that was not a
  regression waiting for a build — it was live in the artifact then shipping.
  `package-artifact.sh` ships `Chromium.app` wholesale and the locale data is
  inside it: not the 55 `.lproj` directories in `Contents/Resources`, which
  hold `InfoPlist.strings` and nothing else, but **220 `locale.pak` files in
  `Chromium Framework.framework/.../<locale>.lproj/`**, which is where
  `ResourceBundle::GetLocaleFilePath` looks on Apple. The same full set the
  Linux build produces and packaging used to discard, present all along.

  Measured on that bundle, system language varied per launch, composed Windows
  persona — and read the last column with the first two, because the defect is
  not one leak but a *contradiction*:

  ```text
  system language   Intl   calendar   month     navigator.languages
  (host, English)   en-US  gregory    January   en-SG,en
  fr-FR             fr     gregory    janvier   en-GB,en
  th-TH             th     buddhist   มกราคม    en-SG,en
  ja-JP             ja     gregory    1月       en-SG,en
  de-DE             de     gregory    Januar    en-SG,en
  ```

  The persona announced English-Singapore language preferences and formatted
  every date in Thai on a Buddhist calendar. `--fingerprint-locale=de-DE,de`
  moved `navigator.languages` to de-DE and left `Intl` on the host's Thai, so
  the contract's strongest locale layer was honoured on one half of the surface
  and ignored on the other. `--lang` moved neither.

  Patch `0115` resolves the composed locale in `GetApplicationLocaleInternal`,
  ahead of every platform candidate: ahead of glib on Linux, of `NSBundle`'s
  `preferredLocalizations` on macOS, and of both `--lang` and the Windows
  preferred-UI-language list on Windows. One point rather than three, because
  pre-filling the macOS and Windows override slots would still sit *below*
  Windows' `pref_locale` — where a `kApplicationLocale` already stored in an
  existing `--user-data-dir` would outrank a composed persona through a
  persisted channel. One decision too: `base::apostate::ComposedApplicationLocale()`
  is what `0114`'s environment write reads as well, so the resolution-side pin
  and the environment-side pin cannot disagree.

  Windows was described here as falling through to `GetUserDefaultLocaleName`.
  That is where the ICU fallback comes from and it is not what answers:
  `content` fills the override from `GetThreadPreferredUILanguageList()` right
  after `PreBrowserMain`, so the leak is the user's *preferred UI language
  list*. The fix is the same; the claim was wrong.

  `--lang` is inert under a composed profile on all three platforms as a
  result. It already was on Linux and macOS and said nothing; on Windows it was
  the first candidate, so that is a real change. It is *reported* rather than
  honoured — a UI language that contradicts the persona is the disagreement
  this surface exists to prevent — and rather than refused, since a launch may
  pass it for reasons unrelated to the fingerprint.

  What else the 440 paks turned on, since the paks were dead weight and are now
  live, and all of it page-readable off one application locale: the calendar and
  hour cycle `Intl.DateTimeFormat` reports; `getWeekInfo().firstDay`;
  `Intl.Collator`'s default order, which sorts `aäoz` under en-US and `aozä`
  under sv; number and date grouping; `DisplayNames`, `RelativeTimeFormat`,
  `ListFormat` and long time-zone names; the *shape* of `navigator.languages`,
  since `LANGUAGE=th` yields `["th-TH","th"]` with no English fallback while
  `de` yields four entries, so the list length is a second-order signal; the
  rendered width of `<input type=date>`, 125.33px under en-US against 106.33
  (th), 108.33 (ja) and 141.33 (ar) at one font size, which is the host's locale
  read off a UA form control with no `Intl` call at all; and locale-dependent
  CJK font fallback, visible in `measureText`. The UI's RTL flip under
  `LANGUAGE=ar` was tested and does *not* reach
  `getComputedStyle(documentElement).direction`.

  The check, against a fresh artifact on a host whose shell is deliberately
  foreign:

  ```sh
  LANG=th_TH.UTF-8 LANGUAGE=th \
    node scripts/checks/release-smoke.mjs <artifact>/chrome
  ```

  Before `0114` that printed three failures, `LOCALE windows`, `LOCALE macos`
  and `LOCALE linux`, each `languages=th-TH,th intl=th`. After it, all twelve
  checks pass and each LOCALE line reads `languages=en-US,en intl=en-US`.

  The macOS half of the check needs the host's *system language* foreign rather
  than its shell, because that is the lever there — and note that
  `release-smoke.mjs` sets `LANG` and `LANGUAGE` in the child environment,
  which on macOS moves nothing, so run against a macos-arm64 artifact on an
  English-language Mac its `LOCALE` lines pass whether or not the leak is
  fixed. Set the system language for the app instead:

  ```sh
  defaults write org.chromium.Chromium AppleLanguages -array th-TH
  node scripts/checks/release-smoke.mjs \
    <artifact>/Chromium.app/Contents/MacOS/Chromium
  defaults delete org.chromium.Chromium AppleLanguages
  ```

  Before `0115` that reports `intl=th` with `calendar: "buddhist"` on every
  composed persona, and — on any artifact built after `0111` removed the locale
  draw — `languages=th-TH,th` too, so the `LOCALE` assertion fails. After
  `0115`, `languages=en-US,en intl=en-US`, while `--fingerprint=host` still
  reports `intl=th`, which is the arm that must not change: only there is the
  host the thing being presented.

  `ledger/surfaces.jsonl`'s `intl.resolved-locale` row carries every measurement
  above and the one question still open: whether stock on a host whose system
  language is not English reports its own locale or en-US, which decides whether
  that combination is a defect or the correct reproduction of a real device.

## 9. Verification

A composition change is done when all five pass:

1. Determinism. The same tuple yields byte-identical profiles, across processes
   and hosts, checked against golden seed vectors.
2. Coherence. Every pair that has to agree still agrees, for N seeds.
3. Servability. Every claim is at or below host capability, checked by
   `scripts/check-servable.py` for N seeds.
4. Dispersion. For N seeds, no value falls outside its option table and the
   realized distribution matches the table weights.
5. Measurement. Launch with the profile, read the surfaces the way a page reads
   them, and diff against the anchor's own captures.

## 10. The launch contract

One switch family, all resolved before the first renderer starts.

| Switch | Meaning |
| --- | --- |
| `--fingerprint=<seed>` | Deterministic seed. An integer or any printable ASCII up to 512 bytes. |
| `--fingerprint=host` | Compose nothing. `off`, `false`, `0`, `disable` and `disabled` are synonyms. |
| `--fingerprint-platform=<windows\|macos\|linux>` | Platform persona, which also selects the GPU cluster. Defaults to the host's own OS on macOS and Windows, and to `windows` on Linux. |
| `--fingerprint-anchor=<id>` | Pin the GPU anchor instead of drawing one. |
| `--fingerprint-explain` | Write the composition report to stdout and exit. |
| `--fingerprint-gpu-vendor`, `--fingerprint-gpu-renderer`, `--fingerprint-hardware-concurrency`, `--fingerprint-device-memory`, `--fingerprint-screen-width`, `--fingerprint-screen-height` | Override one field each; the seed fills the rest. |
| `--fingerprint-timezone`, `--fingerprint-locale` | Override one field each. The seed fills in nothing here: what neither names is served by the host. See section 6. |
| `--apostate-profile=<base64>` | An already-composed profile. |

Precedence, strongest first: `--apostate-profile`, then host mode, then the
per-field overrides, then `--fingerprint`, then the fresh seed a bare launch
draws. Host mode is not a layer that quietly outranks the ones below it: since
nothing is composed, there is no profile for a persona, a pinned anchor or a
per-field override to land on, so combining any of them with host mode refuses
the launch on stderr and exits non-zero rather than half-applying.
`--fingerprint-explain` is the exception and still works.

[docs/FLAGS.md](FLAGS.md) is the user-facing reference for all of these.

`--fingerprint-explain` prints, per surface, the resolved value, the layer that
owns it (invariant, anchor, dispersion, command-line, host-inherited,
composed-default), the evidence class, and any limitation. It answers what the
profile claims and what this host can actually serve, and on a cross-OS launch
it is where the pairing's cost is reported rather than hidden: which fonts the
persona needs, and what stays the host's whatever the profile says.

The locale surface reports as three rows, `locale.application`,
`locale.accept_languages` and `locale.timezone`, each naming its own layer and
carrying the resolved value rather than a policy id. It printed one row naming
the policy id before, which is how a Bangkok host serving `America/New_York`
produced a report with nothing wrong in it: the drawn id `en-us` was accurate,
and the zone it implied was never printed. A report that cannot show the value
an operator is comparing against an exit IP cannot be used to debug the
comparison. `locale.application` was added for the same reason one step later:
the report said `locale.accept_languages (inherited)` on a launch that was
serving Thai under a Windows persona, because the application locale those rows
resolve against was named nowhere. `composed-default` is the layer that exists
to be unmistakable — not the operator's choice, not the host's value.
It writes to stdout, never to a page-visible API.

### Table transport

The dispersion tables and anchors are compiled into the binary by a GN action
that generates C++ from `resources/profiles/`. They are not read from disk at
runtime: a data directory beside the executable would be one more thing to
lose, to mismatch against the binary, and to diverge per install. The compiled
digest of the tables is part of the composition identity in §5, so a binary
and a profile cannot silently disagree about what the catalogue said.
