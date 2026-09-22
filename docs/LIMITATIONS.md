# Known limitations

What this browser does not do, and what a page can still tell about the
machine it runs on. Read this before deciding it is a good fit.

Nothing here is hidden behind a flag or fixed by turning something on. Where
a limitation applies to your launch, `--fingerprint-explain` names it.

## What has been exercised, and how

Every behaviour on this page describes a binary that has been built and run,
unless a section says otherwise. The release artifacts were measured three
ways, and the residuals those runs found are the ones recorded below.

Against the reference device (V3). `capture/derive/conform.py` diffed a
capture taken from the Linux artifact, launched with a profile derived from
the Windows Intel reference by `capture/derive/to_profile.py`, against that
reference. 25 of 39 probes conform. None of the 14 that do not is a browser
defect. Six are the Windows font set not being installed on the Linux host
(`fonts.detected`, `fonts.metrics`, `fonts.query_api`, `canvas.2d`,
`clientrects`, `worker.parity`, all text-metric consequences of the same
absence; see [Fonts are yours to install](#fonts-are-yours-to-install)).
Three are a Phantom wallet extension on the reference machine
(`chrome.runtime` and ten `window` keys such as `solana` and `ethereum` exist
only when an `externally_connectable` extension is installed). Three are the
GPU-less host (`webgpu` adapter null, `speech.voices` empty, two fewer
hardware video codecs). One is Widevine not provisioned. One is the reference
having two monitors.

Against live detectors. On a GPU-less Linux server with no Windows fonts
installed, Windows persona, through a residential proxy: browserscan 100%
with no deduction and every bot-detection row normal; sannysoft 23 passed, 0
failed; CreepJS 0% headless and 0% stealth, no client-side lie, no
main-thread/worker disagreement; pixelscan's consistency comparison passes,
where stock Chromium fails it. Stock Chromium 153 on the same host and
harness scored 33% headless on CreepJS with `hasSwiftShader: true`. The one
deduction left is iphey's, and it is the missing font files.

Against the behaviours only a running binary shows.
`scripts/checks/release-smoke.mjs` asserts the user agent agrees with the
platform in all four places including the request header, that a persistent
profile keeps one identity and a bare launch does not, that
`AudioContext.baseLatency` is the persona's, and that a composed launch never
inherits the host's locale, run with `LANG=th_TH.UTF-8` in the environment on
Linux and `AppleLanguages=th-TH` on macOS. `scripts/checks/gl-caps-check.mjs`
asserts every WebGL limit the anchor measured reaches the page, for each
anchor against its own values. Both checks were verified to fail against a
binary without the change before being trusted to pass.

What the v0.2.0 build was measured against before tagging, on the macOS
arm64 build of the full series. WebGL limits: `gl-caps-check.mjs` 5 of 5
anchors on a Metal host; canvas 2D and WebGL antialiasing still render,
because the claim is served only to WebGL contexts and Skia keeps the host's
caps. WebRTC: through an IPv4-only residential relay, one mDNS host candidate
plus one server-reflexive IPv4 candidate and `iceGatheringState` reaching
`complete` in under five seconds; `--fingerprint-webrtc-udp=block` completes
with zero candidates; direct gathering is unchanged at four. `chrome.runtime`:
a seed that draws the extensions axis present serves `chrome.runtime` and
`window.browser` on https pages with the eleven properties, the undefined
`id`, and the exact "Could not establish connection" rejection a real
externally-connectable extension produces, and nothing on `file://`; a seed
that draws it absent is field-for-field a fresh stock Chrome, 26 of 26
assertions. Readback noise: 38 of 38 harness checks with the switch on, and
the switch off is byte-identical to the switch never having existed. Text
rendering: the persona's tuple reaches every process; on a Mac host the
Windows and macOS tuples differ only in two fields CoreText discards, so
those two personas still rasterise alike there (see
[Text rendering follows the persona](#text-rendering-follows-the-persona)),
while the Linux persona's tuple visibly moves canvas and DOM text and
quantises advances exactly as an unscaled Linux desktop does.

Before relying on any surface you care about, run `--fingerprint-explain`
and confirm it resolved the way you expect. Then read the value from a page.

## The persona chooses the GPU

`--fingerprint-platform` sets OS identity, client hints, fonts, screen
geometry, hardware buckets and the GPU. The claimed operating system selects
the capability cluster on every host, whatever the host's own graphics stack
runs.

It does not set the locale or the timezone. Those follow the launch, then
GeoIP of the effective egress, then the host; a machine's country is a
property of its network. The voice list is keyed on the resolved language
list as well as the OS release, and a launch that names no locale is served
that platform's own default set rather than the host's providers.

A capability cluster belongs to a backend, and the catalogue holds one
backend per platform, so choosing the persona determines the backend:

| Persona | Backend | Anchors drawn | Identities |
| --- | --- | --- | --- |
| `windows` | ANGLE/D3D11 | `windows-d3d11-intel-79dfeb5b4f99`, `windows-d3d11-nvidia-0947761dfbe9` | 15: six Intel UHD 630 device ids and nine NVIDIA boards |
| `macos` | ANGLE/Metal | `macos-metal-apple-850a91233555` | 12: M1 through M4 Max |
| `linux` | ANGLE/Vulkan | `linux-vulkan-nvidia-adf287b8f0ee` | 11: RTX 3090 through RTX PRO 4000 Blackwell |

A Windows persona presents a Direct3D 11 cluster on a Windows machine, on a
Mac, on a Linux workstation with an NVIDIA card, and on a Linux server with
no graphics device at all.

### The default persona depends on the host

A launch that does not pass `--fingerprint-platform` gets:

| Host | Default persona |
| --- | --- |
| macOS | `macos` |
| Windows | `windows` |
| Linux | `windows` |

The first two are the host's own OS. A macOS host can natively serve any
macOS renderer the catalogue offers, and the same for Windows. The third is
not the host's own OS, and it is the one to understand before deploying.

Windows-on-Linux is the Linux default because it is the least bad cross-OS
pairing and what most deployments want. Its cost is the Windows font set,
which the operator can install. See
[Fonts are yours to install](#fonts-are-yours-to-install) and
[docs/FONTS.md](FONTS.md). `--fingerprint-platform=linux` composes the host's
own OS and draws the Vulkan cluster.

The pip and npm packages get the same default. A default `launch()` passes no
persona switch, so the compositor decides. Where a package composes a profile
locally, with no browser to ask, it applies the same table.
[docs/PROFILE_SPEC.md](PROFILE_SPEC.md) names the exported helper.

### Cross-OS is a risk

The persona controls everything composed. It does not control which fonts
are installed or the kernel's timing behaviour. Those stay the host's.

In rough order of exposure: the host's own OS is safest; Windows on Linux is
the default and needs its fonts; macOS on Linux is riskier, because the macOS
core font set is 184 families. A launch is given the pairing it asks for,
with the limitation named in `--fingerprint-explain`. On a Windows persona
over a Linux host the report prints:

```text
  - the persona is windows on a linux host, which is this project's default
    pairing there and its least bad cross-OS one: the GPU cluster follows the
    persona, but the Windows font set does not install itself, and a Windows
    persona missing Windows faces is measurable in text metrics -- install the
    full set (docs/FONTS.md) or pass --fingerprint-platform=linux to compose
    the host's own OS
```

Any other cross-OS pairing gets the general form, naming installed fonts and
kernel timing as what stays the host's. Neither line is a gate and neither is
page-visible.

Measured against FingerprintJS Pro at `demo.fingerprint.com/playground`
(agent 4.1.5), on the shipped `macos-arm64` artifact, headed, through
`launch_persistent_context` with a fresh profile directory per run, over
residential SOCKS5 exits with the exit's own timezone passed explicitly, so
no run carries an incognito, returning-visitor or timezone-mismatch
contribution. One run per row.

| Persona | Suspect score | Tampering | Anomaly score | Anti-detect | Virtual machine |
| --- | --- | --- | --- | --- | --- |
| macOS, the host's own (`--fingerprint=42`) | 5 | false | 0.0021 | false | false |
| Windows (`--fingerprint=42 --fingerprint-platform=windows`) | 36 | true, ML 0.997 | 1 | true | true |

The macOS row is the quiet one, and its 5 is the exit's residential-proxy
verdict. The Windows row is a cross-OS persona on a host that is not that
OS. Seven candidate causes were forced to the value a real Windows machine
reports and measured again, one run each:

| Forced to match | Result |
| --- | --- |
| Full Windows 11 English font set installed on the host | Served list `[]` to `Calibri, Segoe UI Light`; every verdict unchanged |
| Font allowlist widened to all 78 installed Windows families | `Marlett` and `MS UI Gothic` now answer yes, `font_hash` changes; every verdict unchanged |
| Window placed and sized inside the claimed work area | Every verdict unchanged |
| Panel equal to the window plus a taskbar gap | Score rises and `rare_device` becomes true: the panel exists in no population; verdicts unchanged |
| Panel and 40 px inset set to the commonest real combination | Every verdict unchanged |
| `--fingerprint-noise` on, perturbing the canvas readback | Canvas hashes change, stable per profile; verdicts unchanged |
| User agent set to the previous major version | Score moves by 2; every verdict unchanged |

One further field could not be forced, and that is the design working.
`cpu.logical_cores` above the host's count is served as the host's, because
[capacity is presented downward only](#capacity-is-presented-downward-only).

The voice list was the second, and it was not the design working: naming
Windows voices on a Mac yielded an empty list, and naming none yielded the
host's own 180 Apple voices under a Win32 persona. Both were measured, and
[the voice list is the persona's](#the-voice-list-is-the-personas-spoken-by-a-provider-that-exists)
describes what replaced them. The runs in this section predate that change.

Varying the persona and the claimed GPU independently separates the two
signals. Four runs, same host, same protocol, each through an explicit
profile that differs only in those two fields:

| Persona | Claimed GPU | Anomaly | Anti-detect | Virtual machine | Suspect |
| --- | --- | --- | --- | --- | --- |
| macOS, the host's own | Apple Metal, the host's own | 0.0021 | false | false | 5 |
| macOS | Intel Direct3D 11 | 1 | false | false | 12 |
| Windows | Intel Direct3D 11 | 1 | true | true | 36 |
| Windows | Apple Metal | 1 | true | true | 36 |

The anomaly score fires on any departure from the host's native pairing, by
either axis. Only the fully native row is quiet: claiming a GPU the host
cannot back scores 1 under the host's own operating system, and so does
claiming the host's own GPU under another operating system, which is its own
contradiction because no Windows machine runs an Apple Metal renderer. No
profile edit reaches a quiet anomaly score on this host.

The anti-detect and virtual-machine verdicts are the separable pair. They
track the operating system claim and stay true under either GPU, including
the one the host really has. Nothing served from a profile moved them, which
places them on the surfaces a profile does not reach: the canvas and emoji
rasterisation and the audio render, which are
[rendered, not replayed](#canvas-and-audio-are-rendered-not-replayed) and
are the host's under any user agent. Whether that is structural to a
cross-OS persona or particular to this host is not settled here; the same
profile on an x86_64 Linux host would separate those.

### What the host still decides

The rasteriser that draws. A claimed GPU's throughput and rendered bytes come
from the host's backend, not from the claim. See
[Software rendering is measurable](#software-rendering-is-measurable).

And what happens when a page stops reading a limit and starts using it. The
numbers are the anchor's on every backend, but the operation behind one is
the host's, so a draw, a shader link or an allocation taken to a claim the
host cannot meet is refused by the host. See
[A limit served to WebGL is readable everywhere and usable only where the host can](#a-limit-served-to-webgl-is-readable-everywhere-and-usable-only-where-the-host-can).

## Fonts are yours to install

The browser can only show a page a font that is on the machine, because the
moment a page draws text the shape and width of that text have to be real. A
profile subtracts: it hides the fonts the machine has that the claimed device
would not have. It never adds.

Installing the claimed platform's fonts is therefore a setup step you own,
and [docs/FONTS.md](FONTS.md) is the instructions. The browser assumes you
have done it and does not warn if you have not. A persona running without
its fonts is a common reason a session is blocked, and absence is the
strongest signal: Menlo, Monaco, Zapfino, PingFang SC and Helvetica Neue
ship with macOS and cannot be removed.

No configuration substitutes for the files. A Windows persona on a host
without Windows fonts shows a Windows computer with no Windows fonts.

That is the one detector deduction the release carries on an unprovisioned
Linux server. iphey reads a Windows persona there as "Detected an
inconsistent browser fingerprint" and scores it 80 rather than 100, with
every hardware and software member reported as fine. The same binary with a
Linux persona on the same host and exit scores 100. Install the Windows set
and the deduction goes away.

All four routes a page has to ask about fonts go through one predicate, so
they cannot disagree: `document.fonts.check()`, `measureText` and CSS width,
`@font-face src:local()`, and `queryLocalFonts()`. A family the profile hides
takes the same branch an absent family takes, so text still renders in the
next family the page asked for.

The filter serves whatever set the profile states, so the accuracy of the
claim is the accuracy of that list. A pack naming fewer families than the
claimed machine has presents a machine with too few fonts, and
`queryLocalFonts()` reads the whole set at once. Widening a pack is a
catalogue change, and the Windows core pack needs one. Its 35 families are
the set a 76-name probe detected on the reference Windows host, not the
host's full enumerable set; the macOS core pack was rebuilt from the full
set for exactly this reason and is 184 families. Against
[Microsoft's list of the families Windows 11 ships](https://learn.microsoft.com/en-us/typography/fonts/windows_11_font_list),
63 base families before the optional language features, the core pack
lacks 39. Twenty-eight are in no pack, among them Segoe UI Emoji, Segoe UI
Variable, Symbol, Webdings, Sylfaen, Marlett, Bahnschrift, Ebrima, Gadugi
and Nirmala UI, so a Windows persona with every pack installed still
answers no to a family no Windows 11 lacks. Eleven, Candara, Constantia,
Corbel, Gabriola and the CJK set among them, sit in the Office and language
packs as if they were add-ons, so a seed that draws neither hides families
the OS ships. Of the 52 names a FingerprintJS probe tests, the pack answers
yes to Calibri and Segoe UI Light, the Office and Adobe packs add three, and
Marlett and MS UI Gothic are answered no. Completing the pack needs the
full family set enumerated through Chrome on a Windows host, the
measurement the macOS pack rests on, and it compiles into the browser, so
it ships with a release.

Generic CSS families, the claimed platform's own core UI faces, last-resort
fallback for glyph coverage, and web fonts a page loads with `@font-face` are
never filtered.

### Text measurement and script fallback

Under a Windows persona, text measurement matches Windows for every font
family installed on the host, because the metrics are read from the real
font files. Two things do not follow from that.

The generic families, what `monospace`, `serif`, `sans-serif`, `cursive` and
`fantasy` resolve to, are Chrome preferences rather than font properties, so
a persona that does not carry them reports the host's choices. On Windows,
Chrome's `monospace` is Consolas, and a host without Consolas cannot report
it.

What happens when a page uses a character no requested family covers depends
on which build you run, because character fallback is implemented per
platform and only the Linux one consults a table of candidate family names.

On the Linux build, a Windows persona follows Windows' own fallback order,
but only 27 of Windows' 74 script entries name families a Windows font pack
can provide, and those 27 draw on 11 distinct names: Times New Roman, Segoe
UI, Segoe UI Symbol, Tahoma, and the CJK set of Microsoft YaHei, SimSun,
Microsoft JhengHei, Malgun Gothic, Meiryo, Yu Gothic and MS PGothic. The
other 47 name families such as Nirmala UI, Segoe UI Historic, Leelawadee UI,
David and Sylfaen, so Devanagari, Tamil, Khmer, Hebrew, Georgian and similar
scripts fall back to the host's own font. A page renders that script slightly
differently than the claimed OS would.

The Windows build resolves fallback through the real Windows table. The
macOS build has none of this: macOS fallback goes through CoreText, which
never consults a named candidate table, so a Windows persona on a Mac gets
the host's fallback outright. The visible symptom can be the same, a page
rendering Han in the host's font, but no change to the fallback table would
move it.

Installing the claimed platform's faces is the fix on every target. A
Windows persona whose Han fallback should pick Microsoft YaHei needs
Microsoft YaHei on the machine. [docs/FONTS.md](FONTS.md) lists what to
install.

## Text rendering follows the persona

Chromium reads the settings that turn a glyph into pixels from the operating
system it was built for. The persona decides them instead, in every process
that draws text. Six settings move with it:

- antialiasing, and whether it is LCD subpixel coverage or grayscale
- the subpixel order, RGB or BGR, horizontal or vertical
- hinting level, and the autohinter
- embedded bitmap strikes
- subpixel positioning, whether a glyph may sit between two pixels
- Skia's text contrast and gamma, the curve applied to glyph coverage

A Windows identity gets Windows' ClearType defaults and Skia's Windows gamma
constants. A macOS identity gets macOS's, a Linux identity gets what an
ordinary Linux desktop produces, and none of the three reads the machine the
browser runs on.

How much of that a page can see depends on the host, and on a Mac it is less
than the list suggests. Four of the six are delivered and then dropped by
Skia's own CoreText code before a glyph is drawn: text contrast and gamma are
discarded because CoreGraphics applies its own dilation, hinting is collapsed
to on-or-off, and the autohinter is a FreeType setting CoreText has no
equivalent for. What is left on a Mac is antialiasing, the subpixel order,
embedded bitmaps and subpixel positioning. On a Linux host all six are live,
because FreeType honours the contrast curve whenever LCD text is on and takes
the hinting level as given.

Measured on a Mac: the tuples for a Windows identity and a macOS identity
differ only in text contrast and embedded bitmaps, so a Windows identity's
canvas text on a Mac is byte-identical to a Mac's. A Linux identity on the
same host is not, because subpixel positioning survives. On a Mac host,
choose the macOS persona if canvas text matters to you.

The glyph masks are the host's whatever the settings say. The six above are
what a real Chrome sets on the same Skia font object, and underneath them the
coverage bytes come from whatever text engine the host has: CoreText on a
Mac, FreeType on Linux. DirectWrite's masks are not obtainable on either at
any setting. That is the same kind of residual as the font files, a property
of the machine which no source change reaches.

Rasterisation settings decide how a glyph is inked, never which glyphs the
machine has. Segoe UI Emoji under a Windows persona still needs the Segoe UI
Emoji file.

Text measurement is untouched by the rasterisation curve. `measureText`, the
width of a client rect and a font's metrics come from shaping the real font
file, before a glyph is inked, and the advances a Windows persona and a macOS
persona report for the same family on one host are identical to the digit.

Subpixel positioning is the one setting on both sides of that line, and it is
derived the way each claimed platform derives it: Linux from the hinting
level, Windows from antialiasing, macOS always on. Linux Blink force-enables
subpixel positioning unless hinting is full, and an ordinary desktop hints
slight, so a stock Chrome on a Linux desktop measures 316.6015625 for a 16px
Helvetica pangram, the same fraction a Mac does, and a Linux identity on a
Mac reports the same. `measureText` and a client rect around the same text
are produced by one shaping run, so they agree; the client rect's last bits
follow the device pixel ratio's layout snapping, on every persona and on
stock Chrome alike.

## Software rendering is measurable

On a host with no usable GPU the browser renders through SwiftShader, a
software rasteriser, and a page can time that. Measured headed on an M4 Max,
one page and one binary across the two backends back to back: fill runs at
9.5 against 2517 giga-iterations per second, a factor of 264, and draw
submission at 65 thousand against 2703 thousand calls per second, a factor
of 42, with the CPU baseline between 3.9 and 4.1 ms in every run. Across
seven runs the envelope was 130 to 264 times on fill and 28 to 42 times on
draw submission. Dividing by host speed does not hide that. Readback differs
too: the same WebGL scene and the same 2D canvas hash to different digests
on the two paths.

No source change closes this. The only ways to close a timing gap are to
make software rendering fast or to slow real hardware down, and a deliberate
timing adjustment would itself be a new observable. What the fork closes on
the software path is the limit values, the extension list and the identity:
the backend a host runs does not restrict which identity the profile may
serve. That does not make any identity safe on any host. Which operating
system the profile claims is a separate choice, set out in
[The persona chooses the GPU](#the-persona-chooses-the-gpu).

The residual is a throughput and pixel question. A site that times WebGL
fill rate, or hashes a WebGL readback against a corpus of known devices, can
tell a software rasteriser from the card the identity names. A site that
reads the identity cannot. Prefer a host with a real GPU where WebGL
throughput or canvas bytes are being scored; everything else runs on the
GPU-less server most deployments have.

`--headless` does not imply software rendering. On a host with a GPU new
headless mode uses it, and on an M4 Max this binary selects ANGLE/Metal and
reports Apple M4 Max in every default configuration, headed and headless.

WebGL numeric limits under software rendering are raised to real-hardware
values: `MAX_TEXTURE_SIZE` and `MAX_RENDERBUFFER_SIZE` 16384,
`MAX_VIEWPORT_DIMS` 32767 by 32767, `ALIASED_POINT_SIZE_RANGE` 1 to 1024.
Stock Chromium's SwiftShader reports 8192, 8192, 8192 and 1 to 1023. Those
limits are a property of the backend, so they are not universal: this Mac's
real Metal backend reports `MAX_TEXTURE_SIZE` 16384 but `MAX_VIEWPORT_DIMS`
16384 by 16384 and `ALIASED_POINT_SIZE_RANGE` 1 to 511. The 32767 viewport
and the 1024 point size are Windows Direct3D 11 values.

Raising the render-target limit doubles the rasteriser's span arrays from
64 MiB to 128 MiB per GPU process: 4 bytes per span, 16384 spans per
primitive, 128 primitives per batch, 16 pooled draw calls, and the pool never
hands them back. Measured GPU-process resident size under software rendering
was 357 MB after a heavy WebGL workload, against 173 MB for the same page on
a real GPU and 221 MB on a light page. None of it is readable by a page.
Budget roughly 128 MB of additional resident memory per concurrent instance
on hosts that render in software.

GPU presence is detected from the host's DRM render nodes, which is
implemented for Linux only. On Windows and macOS a host with no usable GPU is
not detected automatically, and the backend has to be stated with
`--use-angle`. On Linux the check answers absence reliably and presence only
probably: a host that exposes a render node and still falls back to software
rendering, from a missing Vulkan driver for instance, is not detected.
`--use-angle` states the backend by hand for that case too.

That answer decides which cluster the persona may select. A hardware
identity is served either way. On a GPU-less Linux server wrongly believed to
have a device, the anchor is filtered to the platform default, so a Linux
persona lands on the same NVIDIA cluster it would have drawn anyway, and
`--fingerprint-platform=windows` gets the Linux Vulkan cluster instead of
the Windows Direct3D 11 one. The report says so.

On a host that renders in software and is given no profile to serve, under
`--fingerprint=host` for example, the browser presents its own SwiftShader.
That build reports the raised limits above and, on Linux only, also offers
`KHR_parallel_shader_compile`, which stock Chromium's SwiftShader does not.
Both are enumerable differences from a stock software-rendering browser.
Software rendering on macOS and Windows stays stock in that respect, because
the extension's feature condition is Linux-only. Once a profile is served it
decides both the limits and the extension list.

### A GPU-less host serves a hardware GPU identity

Built and measured on the deployment target, a headless Linux server with no
GPU. A Windows persona there presented an NVIDIA D3D11 identity, the anchor's
WebGL1 extension list complete, its WebGL2 list one name short, and every one
of the anchor's numeric limits. `scripts/checks/gl-caps-check.mjs` is the
regression test for the limits. The one missing WebGL2 name is
`WEBGL_provoking_vertex`, which both Windows anchors claim and which is
deliberately not served; see
[The extension list, and the five names that are served](#the-extension-list-and-the-five-names-that-are-served).

A host with no graphics device draws a hardware anchor of the claimed
platform. Under that host's default persona, Windows, that is one of the
fifteen Direct3D 11 identities the two Windows anchors offer, rotated by
seed; `--fingerprint-platform=linux` gets the eleven Vulkan NVIDIA ones.
Fonts, screen geometry, core count, memory and media topology compose as
normal beside it. Timezone and Accept-Language do not compose: they follow
the launch, then GeoIP of the effective egress, then the host, so a server
launch that names neither and whose lookup does not answer serves that
server's own zone and language list. Voices follow from the resolved
language list, so on that launch they stay the host's too.

The alternative, reporting the host's own software rasteriser, is worse on
the axis that decides the outcome. `ANGLE (Google, Vulkan 1.3.0 (SwiftShader
Device (LLVM 10.0...)))` names itself as a software rasteriser in a field
where no consumer machine does, and it is byte-identical on every host that
reports it, so one substring match sorts the launch into a population no
ordinary user is in. Getting the same answer out of a hardware identity over
a software backend costs a page real work: time a fill, hash a readback,
allocate at the reported maximum.

`linux-swiftshader-google-6922d61bab83` is therefore not in the drawn
candidate set on any host. It stays in the corpus and reachable on request,
by `--fingerprint-anchor` naming it or by `--fingerprint-gpu-renderer` and
`--fingerprint-gpu-vendor` stating the strings, for the case where presenting
as stock headless Chrome is what you want.

`--fingerprint=host` composes nothing, so a GPU-less machine under it reports
its own SwiftShader with the raised limits described above.

### Allocating at the reported maximum fails on a software backend

This is the residual the decision above leaves. It is measured, and it is the
one item on this page that a page can turn into a positive detection rather
than an inference.

On a software backend the reported WebGL limits are the claimed cluster's,
and the claimed maximum is not usable. Measured through the shipped
152.0.7977.83 artifact, allocating with `texStorage2D`, attaching, checking
framebuffer completeness, clearing to green and reading a pixel back:

| Backend | Reports `MAX_TEXTURE_SIZE` | 8192 | 16384 | 32768 |
| --- | --- | --- | --- | --- |
| ANGLE/SwiftShader | 16384 | complete, reads green | `FRAMEBUFFER_UNSUPPORTED`, reads black | same |
| ANGLE/Metal | 16384 | complete, reads green | complete, reads green | `GL_INVALID_VALUE` |

On hardware the reported maximum is usable and only sizes above it fail. On
the software backend the largest size that works is 8192, half what is
reported, and the failure is quiet: `texStorage2D` raises no GL error, the
framebuffer reports unsupported, and a texture cleared to green reads back
black. A page that allocates at the reported maximum and checks the pixel it
gets can tell the difference in one probe.

SwiftShader does not enforce any of these numbers. A 3D texture at eight
times the reported `MAX_3D_TEXTURE_SIZE` is accepted without an error, and
multisample renderbuffers at 4, 8 and 16 samples all succeed while granting
0. The reported figures are soft constants there, which is what makes serving
a claim possible and what leaves the claim unbacked at its own maximum.

Patch `0027` raises the software rasteriser's own `MAX_TEXTURE_SIZE` from
8192 to 16384 by editing `OUTLINE_RESOLUTION`, and that is what puts the
reported figure above the usable one. It is not unique to this fork: the
product this one is measured against reports 16384 on a software backend and
fails at 16384 in the same way. Stock Chrome reports 8192 and fails only
above it, so it is coherent here because it reports what it can do.

`MAX_RENDERBUFFER_SIZE` is not measured. The renderbuffer arm of the probe
reported `FRAMEBUFFER_UNSUPPORTED` at 8192 as well as above it, so it does
not discriminate.

### A limit served to WebGL is readable everywhere and usable only where the host can

The numeric limits are served as composed to WebGL on every backend, Metal
included. The residual is that on a backend which enforces its own limits, an
operation taken to the claim can still be refused by the host. Every row
below is falsifiable by a page willing to do the work, and none needs a large
allocation.

Measured on an Apple M4 Max under the Windows NVIDIA anchor, the widest gap
the catalogue can produce on that host:

| Limit | Served to WebGL | ANGLE/Metal host | What a page can still see |
| --- | --- | --- | --- |
| `ALIASED_POINT_SIZE_RANGE` | `[1, 1024]` | `[1, 511]` | A point drawn with `gl_PointSize` above 511 has a 511-pixel footprint. One draw and one `readPixels`. |
| `MAX_VIEWPORT_DIMS` | `[32767, 32767]` | `[16384, 16384]` | `viewport()` at the claim reads back clamped. Two calls, no draw. |
| `MAX_VERTEX_UNIFORM_VECTORS` | `4095` | `1024` | A vertex shader declaring uniforms at the claim fails to compile or link, with the driver's own info log. One compile. |
| `MAX_VERTEX_UNIFORM_COMPONENTS` | `16380` | `4096` | Same probe, same failure. The two are one limit expressed twice. |
| `MAX_SAMPLES` | `8` | `4` | `renderbufferStorageMultisample` at 8 samples returns `GL_INVALID_OPERATION`. One small renderbuffer. |
| `UNIFORM_BUFFER_OFFSET_ALIGNMENT` | `256` | `16` | `bindBufferRange` at offset 16 succeeds under the default passthrough decoder, where ANGLE validates against its own caps. Under `--use-cmd-decoder=validating` the served 256 is enforced instead. |

The alignment row is the only one where the fork's own machinery can enforce
a claim the hardware does not, and it does so on the decoder nobody ships.

The point-size, uniform and sample rows are ANGLE's doing and are left alone:
`ApplyProfilePointSizeCaps` and `ApplyProfileIntegerCaps` combine a profile
value with the native cap by taking the lower of the two, so ANGLE's compiler
resources, state validation and format table keep describing the machine
that is there. Removing that would move the failure from a refused call to a
wrong render.

Skia, raster and the compositor still see the host. The same GL query entry
points serve the GPU process as a whole: `ui/gl/init/create_gr_gl_interface.cc`
binds Skia's `get_integerv` to `gl::GLApi`, and `GrGLCaps` reads
`MAX_TEXTURE_SIZE`, `MAX_RENDERBUFFER_SIZE` and `MAX_SAMPLES` through it.
Patch `0119` gates on the kind of GL context asking, so only a
WebGL-compatibility context is served the claim. Compositor, raster and
canvas2D through Ganesh get the intersection of claim and host, which can
lower a cap below the host's but never raise it. Nothing there is
page-visible, and telling Skia that a 4-sample device does 8 would break
canvas2D and raster multisampling for every page.

The viewport row in full. `MAX_VIEWPORT_DIMS` shows no residual on a software
backend: SwiftShader reports 32767 by 32767, which is what the Windows anchors
measured, and a viewport at 32767 raises no error. On ANGLE/Metal it is the
cheapest residual above. The table was taken through the 152.0.7977.83 macOS
artifact on an Apple M4 Max, on both the WebGL1 and WebGL2 paths, on a build
that still served the host's 16384 for the first row; patch `0119` now serves
the anchor's 32767 there, and the clamp in the third row is the driver's and
is unchanged by it:

| Call | Result |
| --- | --- |
| `getParameter(MAX_VIEWPORT_DIMS)` | `[16384, 16384]` on that build; the anchor's `[32767, 32767]` since `0119` |
| `viewport(0, 0, 32767, 32767)` | no GL error |
| `getParameter(VIEWPORT)` | `[0, 0, 16384, 16384]` |
| `scissor(0, 0, 32767, 32767)` | no GL error |
| `getParameter(SCISSOR_BOX)` | `[0, 0, 32767, 32767]` |

The viewport state is silently clamped to the driver's real maximum and the
clamped value is readable, so a page that reads the 32767 claim, sets a
viewport to exactly that and reads it back gets 16384 and a contradiction in
two calls, with no allocation and no rendering. The `SCISSOR_BOX` row is the
control: it is not clamped, so the clamp tracks `MAX_VIEWPORT_DIMS`
specifically.

The pairing carries the cost. On a Windows or Linux host with the
corresponding silicon the anchor's 32767 is both claimed and real, and none
of the rows above exist.

`ALIASED_LINE_WIDTH_RANGE` is not in the table. The Windows D3D11 anchors and
the Apple anchor all measure `[1, 1]`, so on a Mac under a Windows persona
the claim and the host agree. The `linux-vulkan-nvidia` anchor measures
`[1, 64]` in all four of its captures, which is the real value a Vulkan
NVIDIA machine reports; Direct3D and Metal cap line width at 1 and Vulkan
does not. Under that anchor on a Mac the claim is served and a 64-pixel line
rasterises one pixel wide.

### Pinning across platforms

`--fingerprint-anchor` is the one way to get a cluster from a platform the
persona does not claim. It is honoured, and the launch records that it
happened. A drawn launch takes its anchor from the claimed platform, so the
persona and the cluster agree unless a pin makes them disagree.

## Capabilities that depend on the machine, not the profile

A name in a profile does not create a capability. Where the machine cannot do
something, the browser reports that it cannot.

HEVC/H.265 is supported and decodes.
`canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"')` returns `probably`,
`MediaSource.isTypeSupported` returns `true`, `MediaCapabilities.decodingInfo`
reports supported, smooth and power-efficient, WebCodecs
`VideoDecoder.isConfigSupported` returns true, and 8-bit Main and 10-bit
Main10 files both decode with zero dropped and zero corrupted frames. Every
value is identical to stock Chrome 152 on the same machine.
`enable_hevc_parser_and_hw_decoder` defaults to true from `proprietary_codecs`
in `media/media_options.gni`, which makes `enable_platform_hevc` true on
macOS, Windows and Linux. Measured on macos-arm64; it holds on macOS and
Windows through the platform decoder and on linux-x64 through patch `0061`'s
software decoder. On linux-arm64 there is no HEVC decoder unless the host
exposes one, and Chromium reports that either way.

Widevine DRM works, but the CDM is not part of the download. It is
Google-licensed proprietary software this project may not redistribute, so
Chromium fetches it from Google at runtime, into
`<user-data-dir>/WidevineCdm/<version>/`. Once present,
`navigator.requestMediaKeySystemAccess('com.widevine.alpha', ...)` resolves
and `createMediaKeys()` succeeds, byte-identical to stock Chrome including the
`SW_SECURE_CRYPTO` and `SW_SECURE_DECODE` robustness levels, the rejection of
all three `HW_SECURE_*` levels, and the rejection of persistent-license
sessions. Until it is present, every `requestMediaKeySystemAccess` call for it
rejects with `NotSupportedError`, which a detector reads in one call.

Three things follow.

A throwaway profile has no CDM. The fetch is also not something to count on:
across five fresh profiles with full network access, watched for 5 to 20
minutes each, the CDM arrived once. The pip package's `provision-drm` command
copies a CDM already on the machine into the browser's preinstalled-component
directory, where it registers at startup for every profile, ephemeral ones
included, with no network access. Nothing is redistributed; the CDM travels
from Google to the operator's machine as it does for Chrome.

There is no race and nothing to retry. Once the CDM is on disk it registers
before the first page paints. The first `requestMediaKeySystemAccess` call of
the first page succeeds, measured 9 to 40 ms after page load, with no network
to Google at all. A page that gets `NotSupportedError` should not retry.

The fetch honours `--proxy-server`. The component update request goes
through the configured proxy and fails closed when the proxy blocks it.

What a profile does control is `MediaCapabilities.decodingInfo()`'s
`powerEfficient`, which is a statement about the claimed GPU's fixed-function
decoder set. Whether a codec is `supported` answers from the decoders
present on this machine, so the profile does not touch it.

Network speech voices. The nineteen `localService: false` voices are served
by Chromium's network speech component against a Google endpoint that needs
API keys at build time. A build without keys cannot reach it, so those voices
are spoken by a local provider instead, and what a page can tell from that is
in
[the voice list is the persona's](#the-voice-list-is-the-personas-spoken-by-a-provider-that-exists).

Capture devices. A profile describes how many microphones and cameras the
machine has and what they are called. Those devices appear in
`enumerateDevices()` and `getUserMedia()` opens them: a claimed camera
delivers video at the resolution and frame rate it advertises, and a claimed
microphone delivers audio at the sample rate and channel count it
advertises. Real devices the host has are never removed or replaced; the
profile only tops the list up. Labels still require a granted permission
before a page can read them, and `deviceId` and `groupId` are still
per-origin values. The residual is what is in the frames: a claimed camera
delivers a synthetic dim scene, and a claimed microphone a room-tone noise
floor. A site that requires recognisable video of a person will not get it.

Audio outputs are the exception. `media.audiooutput_count` is accepted by the
schema and composed into every profile, and nothing reads it:
`enumerateDevices()` reports the host's real speakers. A device that
enumerates and then fails to open is a worse signal than a truthful count,
so the count stays inert until an output a page can play through is built.

Web Share. Linux has no platform share backend, so under a Windows or macOS
persona `share()` rejects with `AbortError` and the same message a share the
user dismissed produces. The residual is timing: no share sheet appears, so
the rejection is prompt where a real one waits for the user. A user can
dismiss a sheet immediately, so this is inside the real distribution. Web
Share also needs transient user activation, so a page that has not been
clicked gets `NotAllowedError` on every platform identically.

Installed memory. `navigator.deviceMemory` is not a limitation. The profile's
installed-memory figure feeds Chromium's own rounding, so the reported value
is always one of 2, 4, 8, 16 or 32 GiB, the same set stock Chrome produces,
and it always agrees with the `Device-Memory` request header because both
come from the same input. On a 36 GiB Mac, stock Chrome 152 and this binary
both report 32. V8 sizes its JavaScript heap from the same figure, clamped to
the host's real installed memory, so `performance.memory.jsHeapSizeLimit`
and `console.memory.jsHeapSizeLimit` agree with the claim and the machine can
back the ceiling it reports. A page cannot falsify it by allocating. A
profile claiming 4 GiB gets a genuine 2 GiB heap ceiling, as a real 4 GiB
device does, so a heavy page on that seed can run out of heap the way it
would on that machine.

## Brand list

A Chromium-branded build emits two entries in `navigator.userAgentData.brands`
where Google Chrome emits three, because upstream adds the product brand only
under a Chrome-branded build. The GREASE brand and the version are correct,
since both derive from the major version. Sites that count brand entries can
see the difference.

## Capacity is presented downward only

A profile can claim fewer cores, less memory, a smaller screen, fewer codecs,
fewer fonts and fewer voices than the host has. It cannot claim more. A host
with 4 cores cannot present as a 16-core workstation, and a screen larger
than the host's panel is refused.

Options the host cannot serve are dropped before the seed draws, so a small
host draws from a smaller set of identities than a large one.

WebGL limits are the exception, on every backend. What a WebGL context
reports is the anchor's cluster whatever the host runs, because a claimed GPU
beside the host's own capability table is a contradiction a page reads in
one call, where the over-claim costs it a draw, a shader link or an
allocation. [Allocating at the reported maximum fails on a software backend](#allocating-at-the-reported-maximum-fails-on-a-software-backend)
has the software case and
[A limit served to WebGL is readable everywhere and usable only where the host can](#a-limit-served-to-webgl-is-readable-everywhere-and-usable-only-where-the-host-can)
has the per-limit table. Nothing outside a WebGL context is affected.

### One parameter a Direct3D 11 claim cannot carry on a Metal host

The limits are one part of what a page hashes; the rest of the parameter
set is the other. Measured through the shipped binary on an Apple M4 Max
serving the `windows-d3d11-intel` cluster, 54 parameters were compared
against two references: this project's own T0 capture of that GPU family,
taken on Windows hardware with `capture/` at the same browser version the
binary is built from, and a third-party corpus of 10,000 Windows captures
containing 26 machines reporting `Intel(R) UHD Graphics 770`. That corpus
is not ours and is not in this tree; its records carry Chrome 115 version
strings, which dates them to around mid-2023.

Against the T0 capture, 53 of the 54 agree exactly. One does not:

| Parameter | T0 capture | Served on a Metal host |
| --- | --- | --- |
| `MAX_UNIFORM_BLOCK_SIZE` | 65536 | 16384 |

That is the clamp in
[Capacity is presented downward only](#capacity-is-presented-downward-only)
doing its job: the profile carries the captured 65536, ANGLE/Metal offers
16384, and serving the larger number would hand a page a 64 KB allocation
that fails on use. It is a readable difference from a real Windows machine
and it is the price of never over-claiming.

Six further parameters differ from the third-party corpus and not from the
capture: `STENCIL_BITS` on both context versions, which the corpus reports
as 8 and both the capture and this build report as 0, and four stencil mask
values, which the corpus reports as `2147483647` where both report
`4294967295`. A capture of the claimed hardware at the shipped browser
version outranks a three-year-old corpus, so those six are dated corpus
behaviour rather than a leak here, and the apparent `STENCIL_BITS` defect
was withdrawn on that evidence rather than patched.

## The extension list, and the five names that are served

A profile whose GPU cluster matches the host's backend is served exactly.
Measured through the shipped binary on an Apple M4 Max running ANGLE/Metal,
the `macos-metal-apple` anchor claimed 39 WebGL1 and 36 WebGL2 extensions and
delivered all of them, with nothing missing and nothing extra. The serving
path for a mismatched backend, patch `0104`, is what the GPU-less Linux
measurement in
[A GPU-less host serves a hardware GPU identity](#a-gpu-less-host-serves-a-hardware-gpu-identity)
exercises: SwiftShader does not offer `WEBGL_blend_func_extended`, both
Windows anchors claim it on WebGL1, and the WebGL1 list arrived complete. The
same run's WebGL2 list was short by `WEBGL_provoking_vertex`, which is the
unserved case below behaving as documented.

Everywhere else there is a gap between what a cluster claims and what the
host's GL stack implements. A SwiftShader host, the deployment target, offers
36 WebGL1 and 30 WebGL2 names, byte-identical between this project's macOS
SwiftShader and the Linux SwiftShader capture in the corpus. Against that
list:

| Claimed cluster | WebGL1 short by | WebGL2 short by |
| --- | --- | --- |
| `linux-vulkan-nvidia` | 1 | 3 |
| `windows-d3d11-intel`, `windows-d3d11-nvidia` | 2 | 5 |
| `macos-metal-apple` | 3 | 7 |

The names involved are seven: `EXT_render_snorm`, `EXT_texture_norm16`,
`WEBGL_blend_func_extended`, `WEBGL_render_shared_exponent`,
`WEBGL_compressed_texture_pvrtc`, `WEBGL_provoking_vertex` and
`KHR_parallel_shader_compile`.

Five of those seven are served rather than dropped: every one whose extension
object exposes constants, internal formats or blend factors and no methods.
Blink has a complete implementation class for each, and the only thing
refusing them on a software backend was a driver-support lookup. A page
enumerates the list, reads the constants and hashes the result, and all of
that succeeds.

The remaining two, and two further names that could have been added, are not
served:

- `WEBGL_provoking_vertex`, and `OVR_multiview2` outside this list, carry
  methods. A page calls `getExtension()` and then calls methods on the object,
  so a name advertised without an implementation fails at first use.
- `EXT_disjoint_timer_query_webgl2` carries methods too, and a working GPU
  timer on a software rasteriser would hand a page the throughput ratio
  directly.
- `KHR_parallel_shader_compile` has no methods, but its `COMPLETION_STATUS_KHR`
  is read through `getProgramParameter`, and a page polling it would never see
  a program finish. Patch `0052` enables it natively on Linux SwiftShader,
  which is why the gap above is one name smaller on a Linux build for the
  three clusters that claim it.

The residual is narrow: a page that renders through one of the five served
names fails where a real device would not. An R16 texture on a stack without
`GL_EXT_texture_norm16` raises `GL_INVALID_ENUM`. Every detector reads the
extension list; almost none renders through a norm16 format.

The removal direction is subtractive. A profile that does not claim a name
the host has still loses it, which is what keeps
`WEBGL_compressed_texture_astc`, `_etc` and `_etc1` off a Windows persona. An
empty profile list disables both directions.

Read `getSupportedExtensions()` from a page on the host you deploy on and
compare it against what `--fingerprint-explain` says the profile claimed.

## WebGPU and WebGL cannot disagree

A browser claiming a GeForce on WebGL while `navigator.gpu` names a software
rasteriser would be caught by one property read. That cannot happen on a
drawn launch. `adapter.info` is profile-driven, measured on a live binary:
pinning `windows-d3d11-nvidia` on a Metal host returns
`{vendor: "nvidia", architecture: "ampere"}` where an unpinned launch on the
same machine returns `{vendor: "apple", architecture: "metal-3"}`.

Vendor, architecture and the whole 36-entry limit table come from the same
measured anchor member the WebGL capability cluster comes from. One member,
both surfaces, so there is no second source for either to disagree with.
Anyone who later adds an independently authored WebGPU table breaks a
guarantee that holds by construction. The failure it prevents is real: the
product this one is measured against serves `{nvidia, lovelace}` beside a
GeForce RTX 3070, and Lovelace is Ada where the 3070 is Ampere, so its own
pair contradicts itself.

### The one residual, off the default path

Two of the eleven Linux Vulkan identities, the RTX 3090 and the RTX PRO 4000
Blackwell, are measured members whose machines returned no WebGPU adapter at
all. The anchor records that: its WebGPU cluster is non-uniform, with one
variant carrying the Lovelace adapter pair and one carrying nulls for both
`high-performance` and `low-power`. Patch `0105` serves what was measured, so
on those two identities `navigator.gpu.requestAdapter()` resolves `null`.
That is what a GPU-less machine reports, and it is what those two machines
reported; on a host that does have a GPU it is unusual beside a WebGL
GeForce claim, which is the residual. Without `0105` the surface would fall
through to the host, which on a GPU-less server is
`{vendor: "google", architecture: "swiftshader"}` beside a GeForce, the
contradiction this section otherwise rules out. It takes
`--fingerprint-platform=linux` to reach, since the default persona on a Linux
host is Windows and the Windows anchors' WebGPU is uniform.

## Network quality and battery are profile values

Network information and battery state come from the profile.
`navigator.connection` reports the profile's `network.effective_type`,
`network.http_rtt_ms`, `network.downlink_mbps` and `network.save_data`, and
the Battery Status API reports the profile's `battery` values. A profile
describing a chassis with no battery reports what a real desktop reports:
charging true, level 1.0, `chargingTime` 0, `dischargingTime` `Infinity`. An
absent section leaves that surface on the host's own value.

Those numbers do not track the real connection or the real battery. Two
residuals follow.

`navigator.connection.rtt` never equals a site's measured request timing,
even on stock Chrome: it is a network-quality percentile over recent
observations, multiplied by a per-origin salted factor between 0.90 and
1.10, rounded to the nearest 50 ms and capped at 3 s. A persistent gross
mismatch is the tell, a claimed 100 ms beside consistently measured 400 ms,
and nothing here addresses it because the browser does not measure the proxy
exit's round trip. That is the largest open residual on this surface.

The battery level is fixed for the launch. The dispatcher delivers one
status and never re-queries, so two reads agree and no `chargingchange`,
`levelchange` or `dischargingtimechange` event ever fires. Per-read drift
would be a stronger signal than a static level. The cost is that a real
laptop on battery does fire `levelchange` over a long session, so a
multi-hour session with a perfectly static level is itself a weak signal. A
fresh seed per launch means the level differs next launch.

## Every persona reports a desktop form factor

Chromium's form-factors client hint has no `Laptop` value, so
`Sec-CH-UA-Form-Factors` and `navigator.userAgentData` report `Desktop` for
every persona. A profile claiming a laptop panel beside that header is a
contradiction readable from one header. The vocabulary is Chromium's, so
closing it is a catalogue question.

## chrome.runtime on a page is a persona, present on about a quarter of them

Some detectors read a missing `chrome.runtime` on an ordinary page as a
headless tell. Fresh stock Chrome has no `chrome.runtime` there either. What
decides the surface is whether any installed extension names the page in its
`externally_connectable` manifest key, and a browser with no such extension
answers no.

Because the real population splits, the answer is drawn. About a quarter of
identities carry the wallet-user state and the rest carry fresh Chrome's,
deterministic per seed. The same `--fingerprint` always lands on the same
state and `--fingerprint-explain` prints it on the `extensions` line. The
present state is MetaMask's shape: every http and https page gets
`chrome.runtime` and `window.browser` with `browser.runtime ===
chrome.runtime`, and `file://` and `about:blank` get neither.
`chrome.runtime.id` reads `undefined`, and a message or a port to any
extension id comes back "Could not establish connection. Receiving end does
not exist." from the browser process, which is what a page gets for an
extension it cannot reach. No extension is installed and no extension id is
ever named, because a fixed id would correlate every launch that showed it.

The cost is a visibly different page. A site that branches on
`chrome.runtime` takes its extension path on those identities: a
wallet-connect flow offers the injected-provider button, some SSO widgets
try the extension handshake before falling back. That is what those sites do
for a real user whose wallet extension is disabled. If your target site is
one of them, pin a seed that draws the state you want and check it with
`--fingerprint-explain`.

If you install an extension into the user-data-dir yourself, its own
`externally_connectable` still answers too. The profile widens the gate and
never narrows it.

## The window chrome delta is the host's, not the profile's

`outerHeight - innerHeight` is the height of the browser's own frame,
tabstrip and toolbar, and `outerWidth - innerWidth` its side frame plus any
classic scrollbar. Both are platform-specific: 87 CSS pixels of height on
macOS 26, 121 on Windows 11, 143 on both Linux reference hosts, and a zero
width delta on all four. A macOS persona served from a Linux host contradicts
itself in that subtraction with no screen value patched at all.

`window.outer_inner_delta_width` and `window.outer_inner_delta_height` are
accepted by the schema and composed into every profile, and nothing reads
either. The delta is the real furniture of the window the host draws, so
serving it from the profile would report an `outerHeight` the window does
not have, which then contradicts `screenY` against the claimed available
rect. The way to make the subtraction true is to size the real window at
launch so the host's own chrome lands on the claimed delta, and neither
launcher does that. The two fields record the reference measurement and
change nothing a page can read.

## The window rect is the host's and the work area is the profile's

They are never reconciled, and on most compositions they contradict each
other.

Measured on the shipped `linux-x64` artifact, headed under Xvfb at 1920x1080,
with a composed 1920x1080 panel and the `taskbar-bottom` furniture option:
`screen.availHeight` is 1032, the panel less the 48 px taskbar the profile
claims, while `outerHeight` is 1060 at `screenY` 10. The window's bottom edge
is at 1070, 38 px inside the taskbar the same profile says is there. Stock
Chromium on the same host reports the identical 1060 at 10 and no violation,
because its `availHeight` is the full 1080.

The mechanism is a single unpatched read. `WindowSizer::GetDefaultWindowBounds`
sizes the first window from `display.work_area()`, the browser-side
`display::Display` filled in by the platform screen, as
`work_area.height() - 2 * kWindowTilePixels` tall, half the work area less
`1.5 * kWindowTilePixels` wide on a 16:9 screen, offset by `kWindowTilePixels`
from the origin. On a 1920x1080 host that is 945x1060 at (10,10), which is
what was measured. The profile is applied elsewhere:
`DisplayUtil::DisplayToScreenInfo` builds the renderer-facing `ScreenInfo`,
and that is where patches 0010, 0016 and 0018 write the claimed screen and
work area. The browser sizes its window against the host's rect and the page
reads the profile's; they arrive in the renderer over different mojo channels
from different sources.

It is not one furniture option. Against that measured window rect, over
every furniture option in the catalogue and every panel its platform offers,
131 of 171 combinations break at least one clause of
`coh.window-within-avail-rect`:

| Persona | Combinations breaking a clause | `screenX >= availLeft` | `screenY >= availTop` | right edge | bottom edge |
| --- | --- | --- | --- | --- | --- |
| macOS | 84 of 84 | 21 | 84 | 0 | 33 |
| Windows | 31 of 63 | 7 | 7 | 0 | 21 |
| Linux | 16 of 24 | 6 | 12 | 0 | 6 |

Sixteen of the twenty-five furniture options break on every panel their
platform offers, and none holds on all of them. A further 46 combinations
compose a panel smaller than the real window, so `outerHeight` exceeds
`screen.height` outright; the panel axis only filters on window bounds when
the launch passes `--window-size`, and a bare launch passes none. The
right-edge clause survives only because Chrome's default window is half the
screen wide.

Of the 22 captures in `resources/fingerprints/raw`, 21 satisfy all four
clauses and the one that does not is a real GNOME desktop whose window
overhangs the work area's right edge by 7 px, so a small overhang is
something real machines do. The clauses no reference breaks are the origin
ones: every macOS capture reports `screenX == availLeft == 0` and
`screenY == availTop == 33`, the window sitting exactly under the menu bar.
A macOS persona here reports `screenY` 10 against `availTop` 33, a window
23 px above a menu bar that is always on top, on every panel and every dock
option.

No target detector charges for any of it today: sannysoft's
`PHANTOM_WINDOW_HEIGHT` passes on these exact numbers, CreepJS leaves its
Screen section unflagged, and browserscan does not deduct. It shows up under
Playwright's default window size, which is the common automation path.

Two repairs suggest themselves and both are worse than the defect. Serving
`outerWidth`, `outerHeight`, `screenX` and `screenY` from the profile
satisfies the arithmetic and introduces a sharper tell: `MouseEvent.screenX`
is the real screen coordinate of a real event, so one `mousemove` recovers
the true window origin and catches the browser disagreeing with itself. That
is why `window.outer-dimensions` and `window.screen-position` are `inherit`
rather than `spoof` in the ledger. Filtering the furniture axis against the
host window, offering only insets whose work area can contain the real
window the way `cpu` and `memory` are filtered against host capacity, trades
one violation for another: on Windows 11 the catalogue offers two options,
and dropping `taskbar-bottom` leaves `taskbar-autohide` as the only survivor,
so every Windows persona on that host would claim an auto-hidden taskbar, a
configuration no capture in this tree measures. Its zero insets also make
the work area the whole panel, the one case
`coh.screen-avail-inset-vs-claimed-os` has to carve out rather than assert.
It also cannot work on a bare launch, because the compositor learns the
window bounds only from `--window-size`.

The repair that works is to place and size the real OS window inside the
work area the profile claims, so `screenY` and `outerHeight` stay the
window's true values and the relation holds because it is true. That means
the browser-side `display::Display` work area has to carry the profile, not
only the renderer-facing `ScreenInfo`, and the claimed panel must then fit
inside the host's real screen, or the window manager immediately moves the
window. It is a display-placement change in a subsystem the profile does not
currently touch, and it is only verifiable headed. Until it lands, treat
window geometry as the host's: a page that compares the window rect against
the claimed work area can tell.

## The keyboard layout map is replayed, not composed

`navigator.keyboard.getLayoutMap()` is served from `keyboard.layout_map` when
a profile carries one, replaced whole rather than merged. Nothing composes
one: no dispersion table emits a keyboard section, so a launch that draws its
identity from a seed inherits the host's map, and the surface has no per-seed
variation. A profile derived from a capture does replay it.

A truncated map invents a device that cannot exist. The reference machines
report 48 keys, and the loader replaces the host map whole, so a partial map
would report a keyboard with a handful of keys. An absent map inherits a real
one.

Serving it per platform is not available, and the measurements say why the
obvious version would be wrong. The reference maps differ by platform in one
entry, `IntlBackslash` is `§` on macOS, `\` on Windows and `<` on Linux, so a
map is not interchangeable across a claimed platform even when the key count
matches. The 49-entry variant, which adds `IntlYen`, turned up on both Linux
and Intel-macOS captures of one host, so that key follows the physical
keyboard rather than the OS and no rule from a claimed platform can produce
it.

## Canvas and audio are rendered, not replayed

There is no stored canvas bitmap or audio buffer to hand back. Those surfaces
come out of Chromium's own rasteriser and audio graph, running against the
profile's fonts, metrics, screen and GPU inputs. Two reads in one launch are
identical, as on real hardware, and the output is what this host renders
under those inputs.

An `OfflineAudioContext` render is fixed by the FFT kernel CPUID selects and
by the host libm, so it differs between arm64 and x86-64 hosts. Profiles are
partitioned by instruction set for that reason, and a profile does not move
an audio render across architectures.

The audio device's own numbers are the host's, except the buffer.
`AudioContext.sampleRate` and `destination.maxChannelCount` come out of the
audio service from the real output device, and the schema declares no key
for either. `audio.hardware_buffer_frames` is served and populated on every
composed profile: 256 frames under a macOS persona, 480 under Windows, 512
under Linux.

`baseLatency` is `max(framesPerBuffer, 128) / sampleRate`, so the numerator
follows the profile while the denominator follows the host. On a 48 kHz host
that is exact: a macOS persona reports 0.005333333333333333 and a Windows
persona 0.01, both equal to their reference captures. On a 44.1 kHz host it
is not, and the error differs by platform: CoreAudio and PulseAudio pin the
frame count, while WASAPI shared mode pins a 10 ms period, so a real Windows
machine at 44.1 kHz reports 441 frames and this build would report 480. The
sample rate is recorded as unresolved in the ledger
(`audio.context-sample-rate`, verdict `escalate`), and closing it means
deciding per platform which of the two is pinned.

The Linux number is authored rather than measured. It is Chromium's own
Pulse floor (`kMinimumOutputBufferSize`, 512). Every admitted Linux reference
in the corpus enumerates zero audio devices, so their 44100 Hz / 441 frames
is the no-sound-card path, and the only Linux capture in the tree holding a
real output device is a non-admissible v1 file reporting ALSA's compiled
default of 2048.

### The voice list is the persona's, spoken by a provider that exists

`speechSynthesis.getVoices()` is a list a page can read in one call, and a
composed identity used to have two answers to it, both of which described the
host. Measured on this Mac under a Windows persona: 180 voices, every one
`localService: true`, beginning Albert, Alice, Alva, Amelie, byte-identical to
what the same binary returns with no profile at all. Give that profile a
Windows voice list instead and the answer became 0 voices, because the list
was a filter over real providers and no provider here is called "Microsoft
David - English (United States)". Real Windows Chrome returns 22, and no
desktop Chrome returns none.

So the profile's list is served, and a real provider is found to speak it.
Where this host has the voice the profile names, its own record is used
unchanged, native identifiers and event set included. Where it does not, the
page sees the persona's voice and synthesis runs on the closest provider by
language: the same language tag first, then the same primary subtag, then the
first voice the platform offered. A voice with no provider at all is not
listed, so a machine with no speech stack still enumerates nothing.

What a page reads is the persona's throughout. `SpeechSynthesisVoice` exposes
name, `voiceURI`, `lang`, `localService` and `default`, all of which come from
the profile; the engine id and native identifier that route the request are
not surfaced by Blink. Speaking works, and the start, word, sentence and end
events are a real engine's rather than a timer's. A page cannot hear the
difference, because speech synthesis output does not reach Web Audio or any
capture path.

The residual is timing. A voice the profile marks remote is spoken by a local
engine, so `onstart` arrives without the network round trip a real remote
voice needs. That is measurable by a page that times it, it applies only to
the nineteen network voices, and it is the price of listing what a real Chrome
lists on a build with no speech API key.

### Canvas text sits at the identity's own sub-pixel phase

Two profiles launched from one machine would otherwise draw one canvas. The
bytes come out of this host's Skia and this host's driver, the catalogue has
no second rasteriser to offer, and a site that stores that hash joins the two
sessions without caring what `navigator.platform` said.

So a composed identity draws canvas text at its own sub-pixel offset: a value
in [-0.25, 0.25] device-independent pixels on x, in 1/64 steps, derived from
the profile id and applied to the text origin once, before the draw. It is on
wherever a profile is on, and zero under `--fingerprint=host` and for a launch
with no profile, where there is no identity to key on and a host-derived
offset would be a host fact in the pixels.

A quarter pixel is the bound because every persona this fork claims renders
text with sub-pixel positioning enabled, so each glyph lands in one of Skia's
quarter-pixel bins and where a run starts decides which. Shifting the start
inside one bin moves some glyphs of a run and leaves the others, which is the
coverage difference two machines produce when their hinting disagrees, and
every frame it draws is a frame the claimed platform draws for some origin.

Because the offset is applied before rasterisation, the perturbed pixels are
the canvas. `getImageData`, `toDataURL`, a `drawImage` copy, a WebGL
`texImage2D` upload, an `OffscreenCanvas` in a worker and any route Chromium
adds later all read one surface, there is nothing applied at egress to compose
with itself, and `measureText` is untouched because metrics come from the font
rather than from where the ink landed.

What it does not reach is a canvas that draws no text, and a WebGL readback.
Those are identical across profiles on one host, which is the honest ceiling
of perturbing at raster time and the reason the switch below still exists.

### Readback noise is available, off, and detectable

`--fingerprint-noise` is the one switch that puts something between the
rasteriser and the page. It is off by default and is not part of any
profile, because what it buys is not coherence. With it, a page-visible
canvas or WebGL readback comes back with each colour channel of an edge
pixel moved by at most one step. Without it, the bytes are what this build
rendered.

What it gets right is everything about a machine being one machine: the same
profile perturbs the same pixels the same way on every launch and every
host, two reads agree, a 1:1 canvas-to-canvas copy reads back identically,
and a solid fill is byte-exact because a pixel with a flat 3x3 neighbourhood
is never moved. Measured on the shipped `macos-arm64` binary, seed 42
windows: `toDataURL` twice identical, `getImageData` twice identical, a
`drawImage` copy read back with zero differing bytes, and the interior of a
flat rectangle at variance zero on all four channels with alpha untouched
everywhere. A detector that renders twice, reads twice, fills a known colour
or votes across many reads finds a consistent device.

What a page can see is that the perturbation is applied where bytes leave
the canvas rather than where they are made, and two consequences of that
need no reference sample to check. Both measured in the same session:

| Check | Switch off | Switch on |
| --- | --- | --- |
| Export to a data URL, import, export, import: four generations | all four byte-identical | every generation differs from the last, each differing byte by one, two generations apart by two |
| A 256px `#000000` to `#ffffff` gradient, read back against the ramp the caller asked for | monotone, deviation in [-1, 0] | 32 of 255 steps go backwards, deviation in [-2, +1] |
| `toDataURL` decoded through an image and read again, against `getImageData` | identical | 28428 of 108000 bytes differ |

The first is the route table composing with itself: a page's own canvas,
exported and re-imported, is a new canvas holding already-perturbed pixels,
and reading it perturbs them again. The drift accumulates, and a real
browser hands back the bytes it was given. The second is arithmetic against
an analytic value: a step of one on a pixel near a rounding boundary is a
step against the ramp, and no hardware produces a gradient that goes
backwards. Neither is fixed by making the step idempotent, and neither
exists for a perturbation applied before the pixels are drawn, which is what
[the section above](#canvas-text-sits-at-the-identitys-own-sub-pixel-phase)
does by default.

A third gap is scale. Draw a scene, draw it eight times larger, downsample
and compare: a perturbation keyed on a pixel and its immediate neighbours
does not survive being averaged with 63 of them, so the two images disagree
in a way no single machine's rasteriser does. Stock Chromium does not match
itself exactly across that test either, but it mismatches differently.

Four narrower gaps.

`captureStream` and the canvas-to-video frame path stay exact. The frame
carries clean pixels, but a page can only read them back through a canvas,
and that read perturbs them exactly as it perturbs the source canvas, so the
two land on the same bytes. The gap is reachable only through an egress that
never touches a canvas, `MediaRecorder` or a WebRTC track, whose bytes leave
the page and can be compared elsewhere against a `toDataURL` of the same
canvas.

A translucent pixel moves by one unit of what the canvas holds, not one unit
of what `getImageData` returns. A canvas stores colour multiplied by alpha,
so it has less resolution than the value read back: the step becomes zero or
one in the store, reads out as two at half alpha and more as alpha
approaches zero, and what the page sees composited moves by one at most. The
noise is weaker on translucent pixels than on opaque ones, and opaque pixels,
which is every pixel of any canvas a fingerprinter draws, take the full step
every time.

Every route agrees on the number because the step is applied to the pixels
the canvas holds and each route's copy is then produced from those by the
browser's own conversion. Perturbing each route's converted copy instead
would mean reproducing that conversion, including its rounding, and being
wrong by one there changes the key, so one route moves a pixel the other
leaves alone. Measured: perturbing converted copies made a translucent canvas
differ on 2359 of 57600 bytes between `getImageData` and a decoded
`toDataURL`; perturbing the stored pixels makes it differ on none, which is
stock Chromium's answer too.

A readback whose format the policy does not describe, a canvas colour type
no canvas output uses or an alpha-only `readPixels`, stays exact. A
pixel-pack buffer that script has partly overwritten loses the rows the
write touched, so `getBufferSubData` returns those exactly.

## Platform support

Four targets ship: `linux-x64`, `linux-arm64`, `macos-arm64` and
`windows-x64`. The Linux and macOS archives have been run through the
detector suite and the post-build checks. The Windows archive builds and
packages, but the packages' Windows acquisition path has been exercised only
against a planted archive, not on a Windows machine.

There is no Intel macOS build, no 32-bit Windows build, and no Android or
iOS build. Personas are `windows`, `macos` and `linux`. There is no mobile
persona; presenting as a phone would need touch input, mobile viewport
behaviour and a mobile GPU cluster this catalogue does not have.

## How long an identity lasts

A launch with no `--user-data-dir` draws a fresh seed from OS entropy and is
a new device; nothing is written anywhere. A launch with `--user-data-dir=DIR`
mints a seed on first use, stores it at `DIR/apostate/identity`, and every
later launch of that directory reads it back. An explicit
`--fingerprint=<seed>` wins over both and leaves the file untouched.

That directory holds the cookies and logged-in sessions a site ties to a
machine, so a cookie jar whose hardware changed between visits would be a
stronger signal than any single value. Playwright's
`launch_persistent_context` reuses one directory, and that reuse is what
keeps the identity stable. Copying the directory copies the identity;
deleting the file mints a new one; a fresh directory per run gives a fresh
device per run.

An incognito or off-the-record context derives its identity from the same
profile. It does not get a second fingerprint.

## WebRTC

Measured through a real SOCKS5 proxy, on Linux and macOS. With a residential
exit configured, the only server-reflexive candidate the browser gathered
carried the exit's address, the host candidate was mDNS-obfuscated, `raddr`
was masked, and the SDP connection line named the exit. Without the proxy the
same page gathered the host's real IPv4 and IPv6 addresses, so the
suppression is the relay's. QUIC rides the same UDP ASSOCIATE and a probe saw
its datagrams arrive from the exit.

WebRTC carries two separate things, the candidate text a page reads over SDP
and the packets themselves. `--fingerprint-webrtc-ip` rewrites the candidate
text and nothing else, so on its own it leaves a cooperating peer reading the
host's real public address off the source of the arriving packets.

The packets follow the proxy. `--fingerprint-webrtc-udp` decides how, and
with the switch absent the behaviour is automatic:

| Configured proxy | Result |
| --- | --- |
| none | direct UDP, as any browser does |
| single-hop SOCKS5 | every datagram relayed through the UDP association, so peers see the proxy |
| HTTP, HTTPS, SOCKS4, a proxy chain, a PAC script, per-scheme rules | no UDP socket is created |

A relayed socket has no address family of its own, so the families are
decided before any port exists. The only address such a socket sends to is
its association's relay endpoint, and the ICE candidate it produces is that
endpoint, so the families WebRTC is offered are the families the configured
proxy can be reached in. The browser resolves the proxy once per network
change and publishes the answer with the interface list; the renderer offers
WebRTC only the families that are left, and refuses a socket in any other
family before one is created. Through an IPv4-only exit that yields one mDNS
host candidate and one IPv4 server-reflexive candidate, as an IPv4-only
desktop emits; through a dual-stack exit it yields the usual pair per family.

Gathering always completes. That holds for a proxy that grants no UDP
ASSOCIATE, a proxy that stops answering mid-handshake, and
`--fingerprint-webrtc-udp=block`: the socket failure reaches the port, the
port reports an error, and gathering finishes. With no UDP egress the result
is a peer connection that completes with no UDP candidate and
`c=IN IP4 0.0.0.0` on the connection line, which is what stock Chrome
produces under the `WebRTCIPHandling=disable_non_proxied_udp` enterprise
policy with no TURN server configured.

A proxy that cannot relay datagrams still has a cost. WebRTC gets no host
candidate, no srflx candidate and no UDP relay candidate, so a page that
offers no TURN server over `turn:...?transport=tcp` or `turns:` gets no
working media. TCP is unaffected, and a TURN server reached over TCP or TLS
still produces a relay candidate through the proxy.

`--fingerprint-webrtc-udp=direct` forces direct UDP under a proxy, which
publishes the host's real address. `block` never creates the socket.

Two residuals while the relay is in use. The host candidate carries the
association's ingress address, the address the proxy told the browser to
send to, and whether that is the same port a peer observes as the packet
source depends on the proxy implementation; the address peers see reaches
the page as the srflx candidate its own STUN server produces over the same
association. And the enterprise `WebRtcUdpPortRange` constraint does not
apply, because the port a page sees is the proxy's.

One residual belongs to the proxy. A proxy that resolves in both families but
relays only IPv4 destinations answers ASSOCIATE in both, so both families
produce a host candidate, and the IPv6 one gets no server-reflexive partner
because the STUN request is dropped on the way out. That is what a machine
with IPv6 configured and no IPv6 route looks like, which is common. Closing
it would need an ASSOCIATE probe per family before any candidate is offered.

The interface topology is the host's, whatever the address says. Nothing in
the profile describes the machine's network interfaces, so the network
service still enumerates the real ones and every candidate carries their
arithmetic. A candidate's `priority` encodes which interface it came from and
how that interface ranks, `network-id` counts them, and `network-cost` is a
direct readout of the adapter type: 0 ethernet, 10 wifi, 50 unknown, 250 to
980 cellular, plus one if the adapter is a VPN. mDNS does not cover any of
it. A page that never learns an IP can still read how many interfaces the
machine has, what kind each is, and whether one of them is a VPN.

## Proxies

HTTP, HTTPS, SOCKS4 and SOCKS5 all work, with authentication. UDP over SOCKS5
UDP ASSOCIATE carries proxied QUIC and HTTP/3.

`humanize: true` is rejected by the packages rather than accepted as a
no-op. There is no synthetic mouse or keyboard behaviour in this fork.

## Automation and remote debugging

The packages expose a Playwright-compatible launch API for Python and Node.
There are no .NET bindings, no Puppeteer adapter, no GUI profile manager and
no cloud profile sync.

Use `--remote-debugging-pipe`, not `--remote-debugging-port`. A page can
detect an open debugging port with no timing tricks:

```js
fetch('http://127.0.0.1:9222/json/version', { mode: 'no-cors' })
```

That promise resolves when the port is open and rejects when it is not, four
of four trials. It only works from a document in the local or private address
space; Local Network Access blocks it from an ordinary public HTTPS page at
this Chromium revision, where open and closed ports measured
indistinguishable. `--remote-debugging-pipe` opens no socket and never
produced the signal. Playwright uses the pipe by default. Puppeteer defaults
to a TCP port, so pass the pipe explicitly.

The change makes the port unreachable from a page, not from a process. Any
program on the machine that opens a socket still connects, which is why
Playwright and Puppeteer keep working.

A CDP session was detectable by timing. Once a client sent `Runtime.enable`,
exception handling and console calls got measurably slower, as single-page
ratios a detector needs no baseline for. Both causes are in V8's debugger,
and patch `0087` gates them on the delegate that only `Debugger.enable`
installs. Measured on the shipped artifact:

| Probe | Unattached | After `Runtime.enable`, stock | After `Runtime.enable`, this build |
| --- | --- | --- | --- |
| `try{throw 1}catch{}` at depth 240 over depth 2 | 1.0x | 26.5x | 0.97x |
| `console.log` | 1.0x | 10.8x | 0.94x |
| `console.trace` over `console.log` | 8.0x | 1.009x | 6.67x |

The third row was the sharpest because it inverted: `console.trace` is
normally much more expensive than `console.log`, and under an attached
session the two cost the same. CreepJS and browserscan both report no
automation on a Playwright-driven session of this build. Patchright is
therefore not required for stealth; the package ships it because it is what
the launcher drives.

Three tells survive whatever happens to the port.

JS coverage re-exposes the timing signal. `page.coverage.startJSCoverage()`
sends `Debugger.enable`, which is the condition the change keys on. Do not
collect coverage in a run you want to be unremarkable.

Heap size rises when a client attaches. `usedJSHeapSize` and
`totalJSHeapSize` grow, because the inspector allocates in the renderer's
heap. Bucketing hides it on most pages, and a site-locked page reads precise
values.

One loopback round trip remains. With the port open the kernel completes the
TCP handshake before the browser hangs up, so an open port and a never-open
one differ by roughly 0.1 ms at the median. That is one tick of
`performance.now`'s resolution in a page that is not cross-origin isolated,
and forty samples did not separate them.

The widely repeated console probes do not work, on this build or on stock
Chrome. All of these measured identical attached and unattached: a getter on
an `Error`'s own `stack` property, `Proxy` traps, plain-object getters,
`console.table` and `console.dir`, `toString`, `valueOf` and
`Symbol.toPrimitive`, `Error.prepareStackTrace`, `Error.captureStackTrace`,
`Error.stackTraceLimit`, and `Function.prototype.toString`. What did leak was
one step past upstream's own guard: a `stack` accessor installed on
`Error.prototype` rather than on an instance.

## Two measurement gaps ship open

Both need hardware nobody here has.

Cross-OS WebGL is unmeasured. Every WebGL comparison behind the
cross-platform numbers was taken between two software rasterisers, so how
much of the difference is the operating system and how much is the test
environment is not known. Settling it needs a Linux host with a discrete GPU
and a capture from a Windows machine with the same GPU. Treat a cross-OS
WebGL claim as untested. This is the ordinary case, because the persona picks
the cluster on every host: a Windows persona on a Linux server presents a
Direct3D 11 cluster, and how that composes against a real Windows machine is
what has not been measured.

Three GPU clusters were measured on another Chromium.
`windows-d3d11-nvidia-0947761dfbe9` was captured on 153.0.8010.37, and
`linux-vulkan-nvidia-adf287b8f0ee` and `linux-swiftshader-google-6922d61bab83`
on 152.0.7977.82, against a 152.0.7977.83 binary. Capability tables move
between releases, so those three can differ from this binary in
version-bearing fields. The other two, `macos-metal-apple-850a91233555` and
`windows-d3d11-intel-79dfeb5b4f99`, are on the pinned build.
`--fingerprint-explain` reports the caveat when a launch draws one of the
three.

## Reporting a limitation

`--fingerprint-explain` prints, per surface, the resolved value, where it
came from, and any limitation that applies to this host. It writes to stdout
and is not readable by a page. If a surface is wrong and this page does not
explain it, file a bug.
