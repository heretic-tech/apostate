# Known limitations

What this browser does not do, and what a page can still tell about the machine
it is running on. Read this before deciding it is a good fit.

Nothing here is hidden behind a flag or fixed by turning something on. Where a
limitation applies to your launch, `--fingerprint-explain` names it.

## What has been exercised, and how

Read this first. Every behaviour on this page describes a binary that has
been built and run, unless a section says otherwise. The release-candidate
artifacts were measured three ways, and the residuals those runs found are
the ones recorded below.

**Against the reference device (V3).** `capture/derive/conform.py` diffed a
capture taken from the Linux artifact -- launched with a profile derived from
the Windows Intel reference by `capture/derive/to_profile.py` -- against that
reference. 25 of 39 probes conform. None of the 14 that do not is a browser
defect: six are the Windows font set not being installed on the Linux host
(`fonts.detected`, `fonts.metrics`, `fonts.query_api`, `canvas.2d`,
`clientrects`, `worker.parity`, all text-metric consequences of the same
absence -- see **Fonts are yours to install**); three are a Phantom wallet
extension on the reference machine (`chrome.runtime` and ten `window` keys
such as `solana` and `ethereum` exist only when an `externally_connectable`
extension is installed); three are the GPU-less host (`webgpu` adapter null,
`speech.voices` empty, two fewer hardware video codecs); one is Widevine not
provisioned (`apostate drm`); one is the reference having two monitors.

**Against live detectors.** On a GPU-less Linux server with no Windows fonts
installed, Windows persona, through a residential proxy: browserscan 100%
with no deduction and every bot-detection row normal; sannysoft 23 passed, 0
failed; CreepJS 0% headless and 0% stealth, no client-side lie, no
main-thread/worker disagreement; pixelscan's consistency comparison passes,
where stock Chromium fails it. Stock Chromium 153 on the same host and harness
scored 33% headless on CreepJS with `hasSwiftShader: true`. The one deduction
left is iphey's, and it is the missing font files -- see **Fonts are yours to
install** for the control that isolates it. The previous build's one CreepJS
lie and browserscan's "Different operating systems" row were the same defect,
a user agent that named the host's OS beside a `navigator.platform` that
named the persona's, and both are gone.

**Against the behaviours only a running binary shows.**
`scripts/checks/release-smoke.mjs` asserts the user agent agrees with the
platform in all four places including the request header, that a persistent
profile keeps one identity and a bare launch does not, that
`AudioContext.baseLatency` is the persona's, and that a composed launch never
inherits the host's locale -- run with `LANG=th_TH.UTF-8` in the environment
on Linux and `AppleLanguages=th-TH` on macOS. `scripts/checks/gl-caps-check.mjs`
asserts every WebGL limit the anchor measured reaches the page, for each
anchor against its own values rather than launches against each other. Both
were verified to fail against the pre-fix binary before being trusted to pass.

Two things to do before relying on any surface you care about. Run
`--fingerprint-explain` and confirm it resolved the way you expect. Then read
it the way a page would, from a page, and check the value.

## The persona chooses the GPU, and the persona is a real choice

`--fingerprint-platform` sets OS identity, client hints, fonts, screen geometry,
hardware buckets — and the GPU. The claimed operating system selects the
capability cluster, on every host, whatever the host's own graphics stack is
running.

It does not set the locale or the timezone. Those follow the launch, then GeoIP
of the effective egress, then the host, and a persona has no say in any of the
three: a machine's country is a property of its network and not of its
operating system. Nor does it settle the voice list on its own, because the
voices table is keyed on the resolved language list as well as the OS release,
so a launch that names no locale keeps the host's real speech providers whatever
persona it claims.

That works because a capability cluster belongs to a backend rather than to a
machine, and the catalogue holds exactly one backend per platform. Choosing the
persona therefore determines the backend, and the host has nothing left to
decide:

| Persona | Backend | Anchors drawn | Identities |
| --- | --- | --- | --- |
| `windows` | ANGLE/D3D11 | `windows-d3d11-intel-79dfeb5b4f99`, `windows-d3d11-nvidia-0947761dfbe9` | 15: six Intel UHD 630 device ids and nine NVIDIA boards |
| `macos` | ANGLE/Metal | `macos-metal-apple-850a91233555` | 12: M1 through M4 Max |
| `linux` | ANGLE/Vulkan | `linux-vulkan-nvidia-adf287b8f0ee` | 11: RTX 3090 through RTX PRO 4000 Blackwell |

The host column is gone from that table because the host no longer appears in
the answer. A Windows persona presents a Direct3D 11 cluster on a Windows
machine, on a Mac, on a Linux workstation with an NVIDIA card and on a Linux
server with no graphics device at all.

### The default persona depends on the host

A launch that does not pass `--fingerprint-platform` gets:

| Host | Default persona |
| --- | --- |
| macOS | `macos` |
| Windows | `windows` |
| Linux | `windows` |

The first two are the host's own OS, which is the safe case: a macOS host can
natively serve any macOS renderer the catalogue offers, and the same for
Windows. The third is deliberately not the host's own OS, and it is the one to
understand before deploying.

Windows-on-Linux is chosen as the Linux default because it is the least bad
cross-OS pairing and because it is what most deployments want. Least bad, not
free: what it costs is mostly a font question, and fonts are the one part the
operator can fix. See **Fonts are yours to install** below and
[docs/FONTS.md](FONTS.md), because on a Linux host this applies to a default
launch rather than to an opt-in.

`--fingerprint-platform=linux` is the opt-out. It composes the host's own OS on
a Linux machine and draws the Vulkan cluster.

That is the browser's own default, and the pip and npm packages get the same
answer rather than a different one: a default `launch()` passes no persona
switch at all, so the compositor decides. Where a package composes a profile
locally instead, which has no browser to ask, it applies the same table.
[docs/PROFILE_SPEC.md](PROFILE_SPEC.md) names the exported helper that owns it.

### Cross-OS is a risk, not a free move

Serving a persona's GPU does not make every claimed OS equally safe. What the
persona controls is everything composed. What it does not control is everything
a fork cannot reach: which fonts are actually installed, and the kernel's own
timing behaviour. Those stay the host's.

So, in rough order of exposure: the host's own OS is safest; Windows on Linux is
the documented default and needs its fonts; macOS on Linux is the riskier
pairing, because the macOS core font set is 184 families and a Mac cannot be
claimed convincingly without them. A launch is given the pairing it asks for
either way, with the limitation named in `--fingerprint-explain` rather than
refused. On a Windows persona over a Linux host the report prints:

```text
  - the persona is windows on a linux host, which is this project's default
    pairing there and its least bad cross-OS one: the GPU cluster follows the
    persona, but the Windows font set does not install itself, and a Windows
    persona missing Windows faces is measurable in text metrics -- install the
    full set (docs/FONTS.md) or pass --fingerprint-platform=linux to compose
    the host's own OS
```

Any other cross-OS pairing gets the general form, naming installed fonts and
kernel timing as what stays the host's. Neither is a gate and neither is
page-visible.

### What the host still decides

Two things. The first is the subject of **Software rendering is measurable**
below: the rasteriser that actually draws. A claimed GPU's throughput and its
rendered bytes come from the host's backend, not from the claim, and no
selection change touches that.

The second is what happens when a page stops reading a limit and starts using
it. The numbers are the anchor's on every backend, but the operation behind
one is the host's, so a draw, a shader link or an allocation taken to a claim
the host cannot meet is refused by the host. **A limit served to WebGL is
readable everywhere and usable only where the host can**, below, has the
per-limit measurements.

## Fonts are yours to install

The browser can only show a page a font that is really on the machine, because
the moment a page draws text in a font, the shape and the width of that text
have to be real. So a profile subtracts: it hides the fonts the machine has that
the claimed device would not have. It never adds.

That makes installing the claimed platform's fonts a setup step you own, and
[docs/FONTS.md](FONTS.md) is the instructions. The browser assumes you have done
it, and nothing warns you if you have not. A persona running without its fonts
is a common reason a session gets blocked, and the strongest signal is absence,
since Menlo, Monaco, Zapfino, PingFang SC and Helvetica Neue ship with macOS
and cannot be removed.

What remains a limitation, rather than a setup step, is that no amount of
configuration substitutes for the files. A family the machine genuinely lacks
cannot be presented, so a Windows persona on a host without Windows fonts shows
a Windows computer with no Windows fonts.

That is measured, and it is the one detector deduction the release candidate
still carries on an unprovisioned Linux server. iphey reads a Windows persona
there as "Detected an inconsistent browser fingerprint" and scores it 80
rather than 100, with every hardware and software member reported as fine.
The same binary with a Linux persona on the same host and exit scores 100,
and the pre-fix binary -- whose user agent wrongly said Linux under a Windows
persona -- also scored 100, because its Linux user agent happened to match
the Linux text metrics. Fixing the user agent exposed the fonts rather than
creating a new problem: every other member iphey reports is byte-identical
between the two builds. Install the Windows set and this goes away; run the
Linux persona and it never arises.

All four routes a page has to ask about fonts go through one predicate, so they
cannot disagree: `document.fonts.check()`, `measureText` and CSS width,
`@font-face src:local()`, and `queryLocalFonts()`. A family the profile hides
takes the same branch an absent family already takes, so text still renders in
the next family the page asked for.

The filter serves whatever set the profile states, so the accuracy of the claim
is the accuracy of that list. A pack naming fewer families than the claimed
machine really has presents a machine with too few fonts, and
`queryLocalFonts()` reads the whole set at once. Widening a pack is a catalogue
change rather than a code change.

Generic CSS families, the claimed platform's own core UI faces, last-resort
fallback for glyph coverage, and web fonts a page loads with `@font-face` are
never filtered.

### Text measurement and script fallback

Under a Windows persona, text measurement matches Windows for every font family
installed on the host, because the metrics are read from the real font files
rather than replayed from a capture. Two things do not follow from that.

The generic families, meaning what `monospace`, `serif`, `sans-serif`, `cursive`
and `fantasy` resolve to, are Chrome preferences rather than font properties, so
a persona that does not carry them reports the host's choices. On Windows,
Chrome's `monospace` is Consolas, and a host without Consolas cannot report it.

What happens when a page uses a character no requested family covers depends on
which build you are running, because character fallback is implemented per
platform and only the Linux one consults a table of candidate family names.

On the Linux build, a Windows persona follows Windows' own fallback order, but
only 27 of Windows' 74 script entries name families a Windows font pack can
provide, and those 27 draw on just 11 distinct names: Times New Roman, Segoe UI,
Segoe UI Symbol, Tahoma, and the CJK set of Microsoft YaHei, SimSun, Microsoft
JhengHei, Malgun Gothic, Meiryo, Yu Gothic and MS PGothic. The other 47 name
families such as Nirmala UI, Segoe UI Historic, Leelawadee UI, David and
Sylfaen, so Devanagari, Tamil, Khmer, Hebrew, Georgian and similar scripts fall
back to the host's own font. Where the claimed pack does not carry a script's
families, the fallback is the host's font rather than the claimed platform's, so
a page renders that script slightly differently than the claimed OS would. That
is a difference rather than a contradiction, and it replaces a contradiction:
the font list used to say a family was absent while text measurement used it.

The Windows build already resolves fallback through the real Windows table,
because that is the platform whose behaviour is being described. The macOS build
does not have any of this: macOS fallback goes through CoreText, which never
consults a named candidate table, so a Windows persona on a Mac gets none of the
above rather than a reduced version of it. The visible symptom can still be the
same, a page rendering Han in the host's font, but the cause is different and no
source change to the fallback table would move it.

Installing the claimed platform's faces is the fix on every target, though for
different reasons on each. It is your part of the setup rather than something a
source change can substitute for. A Windows
persona whose Han fallback should pick Microsoft YaHei needs Microsoft YaHei on
the machine. The browser assumes that has been done rather than checking, so a
script whose Windows families the machine lacks keeps the host's own answer.
[docs/FONTS.md](FONTS.md) lists what to install.

## Software rendering is measurable

On a host with no usable GPU the browser renders through SwiftShader, a software
rasteriser, and a page can time that. Measured headed on an M4 Max, one page and
one binary across the two backends back to back: fill runs at 9.5 against 2517
giga-iterations per second, a factor of 264, and draw submission at 65 thousand
against 2703 thousand calls per second, a factor of 42, with the CPU baseline
between 3.9 and 4.1 ms in every run. Across seven runs the envelope was 130 to
264 times on fill and 28 to 42 times on draw submission. Dividing by host speed
does not hide that. Readback differs too: the same WebGL scene and the same 2D
canvas hash to different digests on the two paths.

No source change closes this. The only ways to close a timing gap are to make
software rendering fast or to slow real hardware down, and a deliberate timing
adjustment would itself be a new observable. What the fork does close on the
software path is the limit values, the extension list and the identity: the
backend a host happens to be running does not restrict which identity the
profile may serve. That is a narrower claim than it sounds, and it is worth
keeping narrow — it does not say any identity is safe on any host. Which
operating system the profile claims is a separate choice with its own
trade-offs, set out in **The persona chooses the GPU, and the persona is a real
choice** above. What the fork closes nothing about is render timing and
per-pixel output, and that is a property of the two rasterisers rather than of
this fork.

So the residual is a throughput and pixel question rather than a string
question. A site that times WebGL fill rate, or hashes a WebGL readback against
a corpus of known devices, can tell a software rasteriser from the card the
identity names. A site that reads the identity cannot. Prefer a host with a real
GPU for the workloads where WebGL throughput or canvas bytes are the thing being
scored; everything else runs on the GPU-less server most deployments actually
have.

Headless is not the problem, and never was. `--headless` does not imply software
rendering: on a host with a GPU new headless mode uses it, and on an M4 Max this
binary selects ANGLE/Metal and reports Apple M4 Max in every default
configuration, headed and headless alike.

WebGL numeric limits under software rendering are raised to real-hardware
values: `MAX_TEXTURE_SIZE` and `MAX_RENDERBUFFER_SIZE` 16384,
`MAX_VIEWPORT_DIMS` 32767 by 32767, `ALIASED_POINT_SIZE_RANGE` 1 to 1024. Stock
Chromium's SwiftShader reports 8192, 8192, 8192 and 1 to 1023. Those limits are
a property of the backend rather than of the silicon, so they are not universal:
this Mac's real Metal backend reports `MAX_TEXTURE_SIZE` 16384 but
`MAX_VIEWPORT_DIMS` 16384 by 16384 and `ALIASED_POINT_SIZE_RANGE` 1 to 511. The
32767 viewport and the 1024 point size are Windows Direct3D 11 values.

Raising the render-target limit doubles the rasteriser's span arrays from 64 MiB
to 128 MiB per GPU process: 4 bytes per span, 16384 spans per primitive, 128
primitives per batch, 16 pooled draw calls, and the pool never hands them back,
so resident memory climbs towards the full 128 MiB as a page touches more of it.
Measured GPU-process resident size under software rendering was 357 MB after a
heavy WebGL workload, against 173 MB for the same page on a real GPU and 221 MB
on a light page. None of it is readable by a page, so it is a deployment cost
rather than a fingerprint: budget roughly 128 MB of additional resident memory
per concurrent instance on hosts that render in software. Hosts with a real GPU
do not pay it.

GPU presence is detected from the host's DRM render nodes, which is implemented
for Linux only. On Windows and macOS a host with no usable GPU is not detected
automatically, and the backend has to be stated with `--use-angle`. Even on
Linux the check answers absence reliably and presence only probably: a host that
exposes a render node and still falls back to software rendering, a missing
Vulkan driver for instance, is not detected. `--use-angle` states the backend by
hand for that case too.

What that answer decides has changed with the GPU identity. It is no longer the
difference between claiming hardware and not claiming it: a hardware identity is
served either way. It is which cluster the persona is allowed to select. On a
GPU-less Linux server wrongly believed to have a device, the anchor is filtered
to the platform default, so a Linux persona lands on the same NVIDIA cluster it
would have drawn anyway and only a non-default persona sees a difference —
`--fingerprint-platform=windows` gets the Linux Vulkan cluster instead of the
Windows Direct3D 11 one, and the report says so.

On a host that renders in software and is given no profile to serve, with
`--fingerprint=host` for example, the browser presents its own SwiftShader as it
is. That build reports the raised limits above and, on Linux only, also offers
`KHR_parallel_shader_compile`, which stock Chromium's SwiftShader does not. Both
are enumerable differences from a stock software-rendering browser. Software
rendering on macOS and Windows stays stock in that respect, because the
extension's feature condition is Linux-only. The differences disappear once a
profile is served, because the profile then decides both the limits and the
extension list: see the subsections below.

### A GPU-less host serves a hardware GPU identity

Built and measured on the deployment target, a headless Linux server with no
GPU. A Windows persona there presented an NVIDIA D3D11 identity, the anchor's
extension list with nothing missing and nothing extra, and every one of the
anchor's numeric limits -- after two rounds of fixes, because the first build
served the host's numbers for thirteen of them and a live detector read the
viewport beside the renderer string and called it. `scripts/checks/gl-caps-check.mjs`
is the regression test and it fails against that first build.

It also replaces an earlier version of this subsection, which said a GPU-less
host would be given a measured software-rasteriser cluster and would "look like
ordinary headless Chrome". That behaviour was withdrawn before it ever shipped,
for the reason two paragraphs down.

A host with no graphics device draws a hardware anchor of the claimed platform.
On a Linux server with no usable GPU a seeded launch composes the whole profile,
and the graphics surfaces report the drawn anchor's renderer and vendor strings.
Under that host's default persona, which is Windows, that means one of the
fifteen Direct3D 11 identities the two Windows anchors offer, rotated by seed;
`--fingerprint-platform=linux` gets the eleven Vulkan NVIDIA ones instead.
Fonts, screen geometry, core count, memory and media topology compose as normal
beside it. Timezone and Accept-Language do not compose at all: they follow the
launch, then GeoIP of the effective egress, then the host, so a server launch
that names neither and whose lookup does not answer serves that server's own
zone and language list. Voices follow from the resolved language list, so on
that launch they stay the host's too.

The alternative was to report the host's own software rasteriser, and it is
worse on the axis that decides the outcome. `ANGLE (Google, Vulkan 1.3.0
(SwiftShader Device (LLVM 10.0...)))` names itself as a software rasteriser in a
field where no consumer machine does, and it is byte-identical on every host
that reports it, so one substring match sorts the launch into a population no
ordinary user is in. Getting the same answer out of a hardware identity running
over a software backend costs a page real work: time a fill, hash a readback,
allocate at the reported maximum. One of those is a string compare and the
others are probes, and the difference between them is the whole reason for this
decision.

`linux-swiftshader-google-6922d61bab83` is therefore not in the drawn candidate
set on any host. It stays in the corpus and stays reachable on request — by
`--fingerprint-anchor` naming it, or by `--fingerprint-gpu-renderer` and
`--fingerprint-gpu-vendor` stating the strings by hand — for the case where
presenting as stock headless Chrome is the thing you actually want.

`--fingerprint=host` is unaffected here as everywhere: it composes nothing, so a
GPU-less machine under it reports its own SwiftShader with the raised limits
described above. Host means host.

### Allocating at the reported maximum fails on a software backend

This is the residual the decision above leaves, it is measured, and it is the
one item on this page that a page can turn into a positive detection rather than
an inference.

On a software backend the reported WebGL limits are the claimed cluster's, and
the claimed maximum is not usable. Measured through the shipped
152.0.7977.83 artifact, allocating with `texStorage2D`, attaching, checking
framebuffer completeness, clearing to green and reading a pixel back:

| Backend | Reports `MAX_TEXTURE_SIZE` | 8192 | 16384 | 32768 |
| --- | --- | --- | --- | --- |
| ANGLE/SwiftShader | 16384 | complete, reads green | `FRAMEBUFFER_UNSUPPORTED`, reads black | same |
| ANGLE/Metal | 16384 | complete, reads green | complete, reads green | `GL_INVALID_VALUE` |

Read the shape rather than the numbers. On hardware the reported maximum is
usable and only sizes above it fail, which is what a real device does. On the
software backend the largest size that actually works is 8192, half what is
reported, and the failure is quiet: `texStorage2D` raises no GL error at all, the
framebuffer reports unsupported, and a texture cleared to green reads back
black. A page that allocates at the reported maximum and checks the pixel it
gets can tell the difference in one probe.

SwiftShader does not enforce any of these numbers. A 3D texture at eight times
the reported `MAX_3D_TEXTURE_SIZE` is accepted without an error, and multisample
renderbuffers at 4, 8 and 16 samples all succeed while granting 0. The reported
figures are soft constants there, not ceilings, which is what makes serving a
claim possible and what makes the claim unbacked at its own maximum.

Two things about the scope of this. It is not a regression from serving a
hardware identity: patch `0027` raised the software rasteriser's own
`MAX_TEXTURE_SIZE` from 8192 to 16384 by editing `OUTLINE_RESOLUTION`,
seventy-five patches before any of this work, and that is what put the reported
figure above the usable one. That patch's own header claims a 16384 texture
allocates and 32768 is correctly refused; the measurement above contradicts the
first half, so read the patch header for the change and this page for the
behaviour. And it is not unique to this fork: the product this one is measured
against reports 16384 on a software backend and fails at 16384 in the same way,
while stock Chrome reports 8192 and fails only above it. Stock is coherent here
because it reports what it can do.

`MAX_RENDERBUFFER_SIZE` is not measured. The renderbuffer arm of the probe
reported `FRAMEBUFFER_UNSUPPORTED` at 8192 as well as above it, so it does not
discriminate and no number for it belongs here.

### A limit served to WebGL is readable everywhere and usable only where the host can

Until v0.2.0 a macOS host served every WebGL limit from its own Apple GPU, no
matter which anchor was drawn. Measured on the shipped v0.1.0 macOS artifact on
an Apple M4 Max by `scripts/checks/gl-caps-check.mjs`: with a Windows NVIDIA
anchor the renderer string was the anchor's while
`ALIASED_POINT_SIZE_RANGE`, `MAX_VIEWPORT_DIMS`, `MAX_VERTEX_UNIFORM_VECTORS`,
`MAX_VERTEX_UNIFORM_COMPONENTS`, `MAX_SAMPLES` and
`UNIFORM_BUFFER_OFFSET_ALIGNMENT` were all the Apple GPU's -- 511, 16384, 1024,
4096, 4, 16 -- where the anchor measured 1024, 32767, 4095, 16380, 8, 256. A
claimed GeForce beside an Apple capability table is a contradiction a page
reads in one `getParameter` call and no GL work at all.

Patch `0119` closes it. The numeric limits are served as composed to WebGL on
every backend, Metal included. What is left is the opposite residual, and it is
the one this page exists for: the numbers are now the anchor's, and on a
backend that enforces them an *operation* taken to the claim can still be
refused by the host. Every row below is falsifiable by a page willing to do the
work, none of them needs a large allocation, and all of them are cheaper for us
than the lookup they replace.

Measured on an Apple M4 Max under the Windows NVIDIA anchor, which is the
widest gap the catalogue can produce on that host:

| Limit | Served to WebGL | ANGLE/Metal host | What a page can still see |
| --- | --- | --- | --- |
| `ALIASED_POINT_SIZE_RANGE` | `[1, 1024]` | `[1, 511]` | A point drawn with `gl_PointSize` above 511 has a 511-pixel footprint. One draw and one `readPixels`. |
| `MAX_VIEWPORT_DIMS` | `[32767, 32767]` | `[16384, 16384]` | `viewport()` at the claim reads back clamped. Two calls, no draw — the table below. |
| `MAX_VERTEX_UNIFORM_VECTORS` | `4095` | `1024` | A vertex shader declaring uniforms at the claim fails to compile or link, with the driver's own info log. One compile. |
| `MAX_VERTEX_UNIFORM_COMPONENTS` | `16380` | `4096` | Same probe, same failure. The two are one limit expressed twice. |
| `MAX_SAMPLES` | `8` | `4` | `renderbufferStorageMultisample` at 8 samples returns `GL_INVALID_OPERATION`. One small renderbuffer. |
| `UNIFORM_BUFFER_OFFSET_ALIGNMENT` | `256` | `16` | `bindBufferRange` at offset 16 succeeds under the default passthrough decoder, where ANGLE validates against its own caps. Under `--use-cmd-decoder=validating` the served 256 is enforced instead, and offset 16 is rejected exactly as a real 256-byte-aligned device rejects it. |

The alignment row is the only one where the fork's own machinery can enforce a
claim the hardware does not, and it does so on the decoder nobody ships. Read
it as a statement about where enforcement lives rather than as a mitigation.

The point-size, uniform and sample rows are all ANGLE's doing and deliberately
left alone: `ApplyProfilePointSizeCaps` and `ApplyProfileIntegerCaps` combine a
profile value with the native cap by taking the lower of the two, so ANGLE's
compiler resources, state validation and format table keep describing the
machine that is really there. Removing that would not close any row above; it
would move the failure from a refused call to a wrong render.

**Skia, raster and the compositor still see the host.** The same six GL query
entry points serve the GPU process as a whole:
`ui/gl/init/create_gr_gl_interface.cc` binds Skia's `get_integerv` straight to
`gl::GLApi`, and `GrGLCaps` reads `MAX_TEXTURE_SIZE`, `MAX_RENDERBUFFER_SIZE`
and `MAX_SAMPLES` through it. `0119` gates on the kind of GL context that is
asking, so only a WebGL-compatibility context is served the claim. Everything
else -- compositor, raster, canvas2D through Ganesh -- gets the intersection of
claim and host, which can lower a cap below the host's but never raise it. This
is by design and it is not a gap: nothing there is page-visible, and telling
Skia that a 4-sample device does 8 would break canvas2D and raster
multisampling for every page, fingerprinted or not.

**The viewport row in full.** `MAX_VIEWPORT_DIMS` shows no residual on a
software backend: SwiftShader reports 32767 by 32767, which is what the Windows
anchors measured, and a viewport at 32767 raises no error. On ANGLE/Metal it is
different, and it is the cheapest of the residuals above, so it is worth the
measurement in full. Taken through the shipped 152.0.7977.83 macOS artifact on
an Apple M4 Max, on both the WebGL1 and WebGL2 paths, while the host's 16384
was still being served:

| Call | Result |
| --- | --- |
| `getParameter(MAX_VIEWPORT_DIMS)` | `[16384, 16384]` |
| `viewport(0, 0, 32767, 32767)` | no GL error |
| `getParameter(VIEWPORT)` | `[0, 0, 16384, 16384]` |
| `scissor(0, 0, 32767, 32767)` | no GL error |
| `getParameter(SCISSOR_BOX)` | `[0, 0, 32767, 32767]` |

The viewport state is silently clamped to the driver's real maximum and the
clamped value is readable. So a page that reads the 32767 claim, sets a
viewport to exactly that and reads the viewport back gets 16384 and a
contradiction in two calls, with no allocation and no rendering. The
`SCISSOR_BOX` row is the control: it is not clamped, so the clamp tracks
`MAX_VIEWPORT_DIMS` specifically rather than being a generic bound on integer
state, and a page can compare the two to isolate it.

v0.1.0's note here argued from that table that an honest 16384 beats a claim a
page can break in two calls, and served the host's number on purpose. That
reasoning is withdrawn, for two reasons. It priced one limit and ignored the
set: the same gate was discarding six others at once, and six host values under
a claimed GeForce is a lookup, which is cheaper for a detector than any probe.
And it left the table internally split, one row honest and the rest of the
cluster the anchor's, which is a shape no machine produces. A whole anchor with
a measured probe cost beats a mixture.

The pairing is still what carries the cost, and it has not changed: on a
Windows or Linux host with the corresponding silicon the anchor's 32767 is both
claimed and real, and none of the rows above exist.

**`ALIASED_LINE_WIDTH_RANGE` is not in the table, and `[1, 64]` is not an
error.** The Windows D3D11 anchors and the Apple anchor all measure `[1, 1]`,
so on a Mac under a Windows persona the claim and the host agree and there is
nothing to falsify. The `linux-vulkan-nvidia` anchor measures `[1, 64]`, in all
four of its captures, and that is the real value a Vulkan NVIDIA machine
reports -- Direct3D and Metal both cap line width at 1 and Vulkan does not.
Under that anchor on a Mac the claim is served and a 64-pixel line rasterises
one pixel wide, so it belongs with the rows above; it is listed separately only
because a reader who knows the Windows numbers will otherwise read 64 as a
typo.

### Pinning across platforms

`--fingerprint-anchor` is the one way to get a cluster from a platform the
persona does not claim, and it is honoured rather than refused, because a pin is
an explicit request. The launch records that it happened. Nothing else produces
that combination any more: a drawn launch takes its anchor from the claimed
platform, so the persona and the cluster always agree unless a pin makes them
disagree.

## Capabilities that depend on the machine, not the profile

A name in a profile does not create a capability. Where the machine cannot do
something, the browser reports that it cannot, because a name that does not do
what it promises is worse than the absence.

Two things commonly assumed missing from a Chromium fork are not missing here,
though one of them has a condition you have to know about.

**HEVC/H.265 is supported and decodes.**
`canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"')` returns `probably`,
`MediaSource.isTypeSupported` returns `true`, `MediaCapabilities.decodingInfo`
reports supported, smooth and power-efficient, WebCodecs
`VideoDecoder.isConfigSupported` returns true, and 8-bit Main and 10-bit Main10
files both decode with zero dropped and zero corrupted frames. Every value is
identical to stock Chrome 152 on the same machine. No GN argument is needed:
`enable_hevc_parser_and_hw_decoder` already defaults to true from
`proprietary_codecs` in `media/media_options.gni`, which makes
`enable_platform_hevc` true on macOS, Windows and Linux. That was measured on
macos-arm64, and it holds on macOS and Windows through the platform decoder and
on linux-x64 through patch `0061`'s software decoder. On linux-arm64 there is no
HEVC decoder unless the host exposes one, and Chromium reports that honestly
either way.

**Widevine DRM works, but the CDM is not part of the download.** Chromium
fetches it from Google at runtime, because the Widevine CDM is Google-licensed
proprietary software this project is not permitted to redistribute. Once the CDM
is present, `navigator.requestMediaKeySystemAccess('com.widevine.alpha', ...)`
resolves and `createMediaKeys()` succeeds, byte-identical to stock Chrome
including the `SW_SECURE_CRYPTO` and `SW_SECURE_DECODE` robustness levels, the
rejection of all three `HW_SECURE_*` levels, and the rejection of
persistent-license sessions. Until it is present, Widevine is absent and every
`requestMediaKeySystemAccess` call for it rejects with `NotSupportedError`,
which a detector can trivially distinguish from a real Chrome.

Three things follow, and the first is the one that bites.

**DRM needs a persistent `--user-data-dir`.** The CDM lands in
`<user-data-dir>/WidevineCdm/<version>/`, so a throwaway profile starts with no
CDM and no Widevine. The fetch is also not something you can count on: across
five fresh profiles with full network access, watched for 5 to 20 minutes each,
the CDM arrived exactly once. So "it will download on first run" is not a
promise, and the first profile to obtain it may take an unpredictable amount of
time.

**There is no race and nothing to retry.** Once the CDM is on disk it registers
before the first page paints. The first `requestMediaKeySystemAccess` call of
the first page succeeds, measured 9 to 40 ms after page load, on the first
attempt, and it works with no network to Google at all. So it is either there
from startup or not there for that launch, and a page that gets
`NotSupportedError` should not retry.

**The fetch honours `--proxy-server`.** The component update request goes
through the configured proxy and fails closed when the proxy blocks it, so it
does not leak the real address.

What a profile does control is `MediaCapabilities.decodingInfo()`'s
`powerEfficient`, which is a statement about the claimed GPU's fixed-function
decoder set. Whether a codec is `supported` is a statement about this machine
and answers from the decoders actually present, which is why the profile does
not touch it.

**Network speech voices.** The `localService: false` voices are served by
Chromium's network speech component against a Google endpoint that needs API
keys at build time. Without keys those voices cannot speak, so they are not
listed.

**Capture devices.** A profile describes how many microphones and cameras the
machine has and what they are called. Those devices appear in
`enumerateDevices()` and `getUserMedia()` opens them: a claimed camera delivers
video at the resolution and frame rate it advertises, and a claimed microphone
delivers audio at the sample rate and channel count it advertises. Real devices
the host has are never removed or replaced; the profile only tops the list up.
Labels still require a granted camera or microphone permission before a page can
read them, and `deviceId` and `groupId` are still per-origin values that differ
between sites. The residual is what is in the frames: a claimed camera delivers
a synthetic dim scene rather than a real room, and a claimed microphone delivers
a room-tone noise floor. A site that requires recognisable video of a person
will not get it.

Audio outputs are the exception inside that paragraph. `media.audiooutput_count`
is accepted by the schema and composed into every profile, and nothing reads it:
`enumerateDevices()` reports the host's real speakers, and a profile claiming a
different number of them changes nothing. Synthesising an output device would
mean synthesising one a page can actually play through, because a device that
enumerates and then fails to open is a worse signal than a truthful count, so
the count stays inert until that is built rather than being half-served.

**Web Share.** Linux has no platform share backend, so under a Windows or macOS
persona `share()` rejects with `AbortError` and the same message a share the
user dismissed produces. The residual is timing: no share sheet appears, so the
rejection is prompt where a real one waits for the user. That is inside the real
distribution rather than outside it, since a user can dismiss a sheet
immediately. Web Share also needs transient user activation, so a page that has
not been clicked gets `NotAllowedError` on every platform identically.

**Installed memory.** `navigator.deviceMemory` is not a limitation and is worth
stating because it is easy to assume otherwise. The profile's installed-memory
figure feeds Chromium's own rounding rather than overriding the result, so the
reported value is always one of 2, 4, 8, 16 or 32 GiB, the same set stock Chrome
can produce, and it always agrees with the `Device-Memory` request header
because both come from the same input. On a 36 GiB Mac, stock Chrome 152 and
this binary both report 32. V8 sizes its JavaScript heap from the same figure,
clamped to the host's real installed memory rather than from the raw claim, so
`performance.memory.jsHeapSizeLimit` and `console.memory.jsHeapSizeLimit` agree
with the claim and the machine can always actually back the ceiling it reports.
A page cannot falsify it by allocating. The honest consequence is that a profile
claiming 4 GiB gets a genuine 2 GiB heap ceiling, exactly as a real 4 GiB device
does, so a heavy page on that seed can run out of heap the way it would on that
machine. That is intended.

Until that change has compiled, the heap ceiling still comes from the host, and
the mismatch is worst exactly where this browser is most often run. On a 36 GiB
workstation nothing shows. On a 2 or 4 GiB VM the heap ceiling contradicts any
memory claim above it, and the contradiction is one property read away.

## Brand list

A Chromium-branded build emits two entries in `navigator.userAgentData.brands`
where Google Chrome emits three, because upstream adds the product brand only
under a Chrome-branded build. The GREASE brand and the version are correct,
since both derive from the major version. Sites that count brand entries can see
the difference.

## Capacity is presented downward only

A profile can claim fewer cores, less memory, a smaller screen, fewer codecs,
fewer fonts and fewer voices than the host has. It cannot claim more. A host
with 4 cores cannot present as a 16-core workstation, and requesting a screen
larger than the host's panel is refused rather than served.

Options the host cannot serve are dropped before the seed draws, so a small host
draws from a smaller set of identities than a large one.

WebGL limits are the exception, on every backend. What a WebGL context reports
is the anchor's cluster whatever the host is running, because a claimed GPU
beside the host's own capability table is a contradiction a page reads in one
call, where the over-claim that avoids costs it a draw, a shader link or an
allocation. Two sections state the price. **Allocating at the reported maximum
fails on a software backend** above has the software case; **A limit served to
WebGL is readable everywhere and usable only where the host can** has the
per-limit table for a backend that does enforce. Nothing outside a WebGL
context is affected: the compositor, raster and Skia keep the host's caps.

## The extension list, and the five names that are served

A profile whose GPU cluster matches the host's backend is served exactly.
Measured through the shipped binary on an Apple M4 Max running real ANGLE/Metal,
the `macos-metal-apple` anchor claimed 39 WebGL1 and 36 WebGL2 extensions and
delivered all of them, with nothing missing and nothing extra.

Everywhere else there is a gap between what a cluster claims and what the host's
GL stack implements, and it is measured. A SwiftShader host — the deployment
target — offers 36 WebGL1 and 30 WebGL2 names, byte-identical between this
project's macOS SwiftShader and the Linux SwiftShader capture in the corpus.
Against that list:

| Claimed cluster | WebGL1 short by | WebGL2 short by |
| --- | --- | --- |
| `linux-vulkan-nvidia` | 1 | 3 |
| `windows-d3d11-intel`, `windows-d3d11-nvidia` | 2 | 5 |
| `macos-metal-apple` | 3 | 7 |

The names involved are seven in total: `EXT_render_snorm`,
`EXT_texture_norm16`, `WEBGL_blend_func_extended`, `WEBGL_render_shared_exponent`,
`WEBGL_compressed_texture_pvrtc`, `WEBGL_provoking_vertex` and
`KHR_parallel_shader_compile`.

Five of those seven are served rather than dropped — every one whose extension
object exposes constants, internal formats or blend factors and no methods.
Blink has a complete implementation class for each; the only thing that was
refusing them on a software backend is a driver-support lookup. So a page
enumerates the list, reads the constants and hashes the result, and all of that
succeeds.

The remaining two of the seven are not served, and two further names that could
have been added are not either. Each for a reason rather than a size limit:

- `WEBGL_provoking_vertex`, and `OVR_multiview2` outside this list, carry
  methods. A page calls `getExtension()` and then calls methods on the object it
  gets back, so a name advertised without an implementation behind it fails at
  first use — a functional break rather than a tell.
- `EXT_disjoint_timer_query_webgl2` carries methods too, and serving it would be
  actively self-defeating: a working GPU timer on a software rasteriser hands a
  page the throughput ratio directly, and that ratio is the one GPU
  contradiction no string can cover.
- `KHR_parallel_shader_compile` has no methods, but its `COMPLETION_STATUS_KHR`
  is read through `getProgramParameter`, and a page polling it would never see a
  program finish. It is also unnecessary on the deployment target, where patch
  `0052` enables it natively on Linux SwiftShader — which is why the gap above
  is one name smaller on a Linux build for the three clusters that claim it.

The residual after that is narrow and worth stating precisely: a page that
*renders through* one of the five served names fails where a real device would
not. An R16 texture on a stack without `GL_EXT_texture_norm16` raises
`GL_INVALID_ENUM`. Every detector reads the extension list; almost none renders
through a norm16 format.

The removal direction is unchanged and still subtractive. A profile that does
not claim a name the host has still loses it, which is what keeps
`WEBGL_compressed_texture_astc`, `_etc` and `_etc1` off a Windows persona. An
empty profile list still disables both directions.

All of this is patch `0104`, which is in the series, is compile-unverified and is
in no binary. Read `getSupportedExtensions()` from a page on the host you deploy
on and compare it against what `--fingerprint-explain` says the profile claimed,
rather than trusting this page.

## WebGPU and WebGL cannot disagree

This one is a property rather than a limitation, and it is here because the
obvious worry is reasonable: a browser claiming a GeForce on WebGL while
`navigator.gpu` names a software rasteriser would be caught by one property
read.

That cannot happen on a drawn launch. `adapter.info` is profile-driven, measured
on a live binary: pinning `windows-d3d11-nvidia` on a Metal host returns
`{vendor: "nvidia", architecture: "ampere"}` where an unpinned launch on the same
machine returns `{vendor: "apple", architecture: "metal-3"}`.

The reason it holds is worth stating, because the reasoning is what protects it.
Vendor, architecture and the whole 36-entry limit table come from the same
measured anchor member the WebGL capability cluster comes from. One member, both
surfaces. They cannot contradict each other because there is no second source
for either to disagree with, and no code enforces that — it falls out of the
selection. So anyone who later adds an independently authored WebGPU table
breaks a guarantee that currently holds by construction, and this paragraph is
the warning.

The measured comparison is favourable and is worth having on the record. The
product this one is measured against serves `{nvidia, lovelace}` beside a
GeForce RTX 3070 — Lovelace is Ada and the 3070 is Ampere, so its own pair
contradicts itself. This one serves `ampere` beside an RTX 3070 Ti, with 36
measured limits rather than the host's.

### The one residual, off the default path

Two of the eleven Linux Vulkan identities — the RTX 3090 and the RTX PRO 4000
Blackwell — are measured members whose machines returned no WebGPU adapter at
all. The anchor records that faithfully: its WebGPU cluster is non-uniform, with
one variant carrying the Lovelace adapter pair and one carrying nulls for both
`high-performance` and `low-power`.

A profile drawing one of those two therefore has nothing to serve, so
`navigator.gpu` stays the host's. On a GPU-less host that means
`{vendor: "google", architecture: "swiftshader"}` beside a GeForce claim, which
is the contradiction this section otherwise rules out.

It takes `--fingerprint-platform=linux` to reach, since the default persona on a
Linux host is Windows and the Windows anchors' WebGPU is uniform. Patch `0105`
serves *no* adapter for those two members instead of falling through to the
host's, which reproduces what was measured on those machines rather than
contradicting it. It is in the built series.

## Network quality and battery are profile values

Network information and battery state come from the profile rather than the
machine. `navigator.connection` reports the profile's `network.effective_type`,
`network.http_rtt_ms`, `network.downlink_mbps` and `network.save_data`, and the
Battery Status API reports the profile's `battery` values. A profile describing a
chassis with no battery reports what a real desktop reports: charging true, level
1.0, `chargingTime` 0, `dischargingTime` `Infinity`. The API is still there and
still resolves in that case. Neither section is invented when the profile omits
it: an absent section leaves that surface on the host's own value.

Those numbers do not track the real connection or the real battery, and there
are two residuals.

`navigator.connection.rtt` never equals a site's measured request timing, even
on stock Chrome: it is a network-quality percentile over recent observations,
multiplied by a per-origin salted factor between 0.90 and 1.10, rounded to the
nearest 50 ms and capped at 3 s. So a mismatch is not the tell. A persistent
gross mismatch is, a claimed 100 ms beside consistently measured 400 ms, and
nothing here addresses it because the browser does not measure the proxy exit's
round trip. That is the largest open residual on this surface.

The battery level is fixed for the launch. The dispatcher delivers one status
and never re-queries, so two reads agree and no `chargingchange`, `levelchange`
or `dischargingtimechange` event ever fires. That is deliberate, because
per-read drift would be a far stronger signal than a static level. The cost is
that a real laptop on battery does fire `levelchange` over a long session, so a
multi-hour session with a perfectly static level is itself a weak signal. A
fresh seed per launch means the level differs next launch.

## Every persona reports a desktop form factor

Chromium's form-factors client hint has no `Laptop` value, so
`Sec-CH-UA-Form-Factors` and `navigator.userAgentData` report `Desktop` for every
persona. A profile claiming a laptop panel while that header says `Desktop` is a
contradiction readable from one header. Closing it is a catalogue question rather
than something a patch can fix, since the vocabulary is Chromium's.

## The window chrome delta is the host's, not the profile's

`outerHeight - innerHeight` is the height of the browser's own frame, tabstrip
and toolbar, and `outerWidth - innerWidth` its side frame plus any classic
scrollbar. Both are platform-specific: 87 CSS pixels of height on macOS 26, 121
on Windows 11, 143 on both Linux reference hosts, and a zero width delta on all
four. A macOS persona served from a Linux host contradicts itself in that
subtraction with no screen value patched at all.

`window.outer_inner_delta_width` and `window.outer_inner_delta_height` are
accepted by the schema and composed into every profile, and nothing reads
either of them. That is not an oversight in the loader: the delta is the real
furniture of the window the host draws, so serving it from the profile would
mean reporting an `outerHeight` the window does not have, which then
contradicts `screenY` against the claimed available rect. The way to make the
subtraction true is to size the real window at launch so the host's own chrome
lands on the claimed delta, and neither the Python nor the Node launcher does
that yet. Until one of them does, the two fields record the reference
measurement and change nothing a page can read.

## The window rect is the host's and the work area is the profile's

They are never reconciled, and on most compositions they contradict each other.

Measured on the shipped `linux-x64` artifact, headed under Xvfb at 1920x1080,
with a composed 1920x1080 panel and the `taskbar-bottom` furniture option:
`screen.availHeight` is 1032 — the panel less the 48 px taskbar the profile
claims — while `outerHeight` is 1060 at `screenY` 10. The window's bottom edge
is therefore at 1070, which is 38 px inside the taskbar the same profile says is
there. Stock Chromium on the same host reports the identical 1060 at 10 and no
violation at all, because its `availHeight` is the full 1080. Apostate creates
the contradiction by deducting the furniture from one of the two rects.

The mechanism is a single unpatched read. `WindowSizer::GetDefaultWindowBounds`
sizes the first window from `display.work_area()` — the browser-side
`display::Display`, filled in by the platform screen — as
`work_area.height() - 2 * kWindowTilePixels` tall, half the work area less
`1.5 * kWindowTilePixels` wide on a 16:9 screen, offset by `kWindowTilePixels`
from the origin. On a 1920x1080 host that is exactly 945x1060 at (10,10), which
is what was measured. The profile is applied somewhere else entirely:
`DisplayUtil::DisplayToScreenInfo` builds the renderer-facing `ScreenInfo`, and
that is where patches 0010, 0016 and 0018 write the claimed screen and work
area. The browser sizes its window against the host's rect and the page reads
the profile's, and nothing in Chromium relates the two — they arrive in the
renderer over different mojo channels from different sources.

**It is not one furniture option.** Against that measured window rect, over
every furniture option in the catalogue and every panel its platform offers,
131 of 171 combinations break at least one clause of
`coh.window-within-avail-rect`:

| Persona | Combinations breaking a clause | `screenX >= availLeft` | `screenY >= availTop` | right edge | bottom edge |
| --- | --- | --- | --- | --- | --- |
| macOS | 84 of 84 | 21 | 84 | 0 | 33 |
| Windows | 31 of 63 | 7 | 7 | 0 | 21 |
| Linux | 16 of 24 | 6 | 12 | 0 | 6 |

Sixteen of the twenty-five furniture options break on every panel their platform
offers, and not one holds on all of them. A further 46 combinations compose a
panel *smaller* than the real window, so `outerHeight` exceeds `screen.height`
outright rather than merely the work area; the panel axis only filters on window
bounds when the launch passes `--window-size`, and a bare launch passes none.
The right-edge clause survives only because Chrome's default window is half the
screen wide.

**The bottom edge the sweep found is the mild half.** Of the 22 captures in
`resources/fingerprints/raw`, 21 satisfy all four clauses and the one that does
not is a real GNOME desktop whose window overhangs the work area's right edge by
7 px, so a small overhang is something real machines do. The clauses no
reference ever breaks are the origin ones: every macOS capture reports
`screenX == availLeft == 0` and `screenY == availTop == 33`, the window sitting
exactly under the menu bar. A macOS persona here reports `screenY` 10 against
`availTop` 33 — a window 23 px *above* a menu bar that is always on top — on
every panel and every dock option. That is the unmistakable version of the same
defect, and it is the one to fix first.

No target detector charges for any of it today: sannysoft's
`PHANTOM_WINDOW_HEIGHT` passes on these exact numbers, CreepJS leaves its Screen
section unflagged, and browserscan does not deduct. It shows up under
Playwright's default window size, which is the common automation path.

**Why it is not fixed rather than fixed badly.** Two repairs suggest
themselves and both are worse than the defect.

Serving `outerWidth`, `outerHeight`, `screenX` and `screenY` from the profile
satisfies the arithmetic and introduces a sharper tell: `MouseEvent.screenX` is
the real screen coordinate of a real event, so one `mousemove` recovers the
true window origin and catches the browser disagreeing with itself. That is the
argument to remember before anyone "fixes" this by writing values, and it is
why `window.outer-dimensions` and `window.screen-position` are `inherit` rather
than `spoof` in the ledger.

Filtering the furniture axis against the host window — offering only insets
whose work area can contain the real window, the way `cpu` and `memory` are
filtered against host capacity — trades one fatal violation for another. On
Windows 11 the catalogue offers exactly two options, and dropping
`taskbar-bottom` leaves `taskbar-autohide` as the only survivor — so every
Windows persona on that host would claim an auto-hidden taskbar, a
configuration no capture in this tree measures, presented uniformly. Its zero
insets also make the work area the whole panel, which is the one case
`coh.screen-avail-inset-vs-claimed-os` has to carve out rather than assert. It
also cannot work on a bare launch at all: the compositor learns the window
bounds only from `--window-size`, which is absent, and the window does not
exist yet when composition runs.

The repair that works is the one the ledger has always named: place and size the
real OS window inside the work area the profile claims, so `screenY` and
`outerHeight` stay the window's own true values and the relation holds because
it is true. That means the browser-side `display::Display` work area has to
carry the profile, not just the renderer-facing `ScreenInfo`, and it brings a
second requirement with it — the claimed panel must then fit inside the host's
real screen, or the browser places windows the window manager immediately moves,
which breaks the same relation from the other side. Neither the Python nor the
Node launcher sizes the window either, and a launcher-side `--window-size` would
leave the bare binary — the headline case, a fingerprint with no arguments —
unfixed.

So it is a display-placement change in a subsystem the profile does not
currently touch, and it is only verifiable headed, with a window on a screen.
Until that lands, treat window geometry as the host's: a page that compares the
window rect against the claimed work area can tell.

## The keyboard layout map is replayed, not composed

`navigator.keyboard.getLayoutMap()` is served from `keyboard.layout_map` when a
profile carries one, replaced whole rather than merged. Nothing composes one: no
dispersion table emits a keyboard section, so a launch that draws its identity
from a seed inherits the host's map, and the surface has no per-seed variation at
all. A profile derived from a capture does replay it, which is the path the
field exists for.

The catalogue used to compose a five-entry map — `KeyA`, `KeyQ`, `KeyW`, `KeyY`,
`KeyZ`, the letters that separate QWERTY from AZERTY and QWERTZ — on every
locale option. Because the loader replaces the host map whole, a profile carrying
that stub reported a keyboard with five keys, which no keyboard has, and the
reference machines report 48. The stubs are gone. An absent map inherits a real
one; a truncated map invents a device that cannot exist, and that is the worse
of the two.

Serving it per platform is not available yet, and the measurements say why the
obvious version of it would be wrong. The reference maps differ by platform in
one entry — `IntlBackslash` is `§` on macOS, `\` on Windows and `<` on Linux —
so a map is not interchangeable across a claimed platform even when the key count
matches. The 49-entry variant, which adds `IntlYen`, turned up on both Linux and
Intel-macOS captures of one host, so that key follows the physical keyboard
rather than the OS and no rule from a claimed platform can produce it.

## Canvas and audio are rendered, not replayed

There is no stored canvas bitmap or audio buffer to hand back. Those surfaces
come out of Chromium's own rasteriser and audio graph, running against the
profile's fonts, metrics, screen and GPU inputs. Two reads in one launch are
identical, which is what real hardware does, and the output is what this host
actually renders under those inputs rather than a recording of another machine.

An `OfflineAudioContext` render is fixed by the FFT kernel CPUID selects and by
the host libm, so it differs between arm64 and x86-64 hosts. Profiles are
partitioned by instruction set for that reason, and a profile does not move an
audio render across architectures.

**The audio device's own numbers are the host's, except the buffer.**
`AudioContext.sampleRate` and `destination.maxChannelCount` come out of the
audio service from the real output device, and the schema declares no key for
either, so a profile cannot move them. `audio.hardware_buffer_frames` is served,
and since the audio dispersion axis exists it is also populated on every
composed profile: 256 frames under a macOS persona, 480 under Windows, 512 under
Linux. Until that axis existed the field was declared, consumed by patch 0019
and supplied by nothing, so `baseLatency` was the build host's own buffer under
every persona — 0.042666 on the shipped linux-x64 artifact, identical to stock
Chrome, which is 2048 frames of Linux ALSA behind whatever OS the rest of the
profile claimed.

`baseLatency` is `max(framesPerBuffer, 128) / sampleRate`, so the numerator now
follows the profile while the denominator still follows the host. On a 48 kHz
host that is exact: a macOS persona reports 0.005333333333333333 and a Windows
persona 0.01, both equal to their reference captures. On a 44.1 kHz host it is
not, and the error is not the same shape on every platform — CoreAudio and
PulseAudio pin the frame count, while WASAPI shared mode pins a 10 ms period, so
a real Windows machine at 44.1 kHz reports 441 frames and this build would
report 480. The sample rate is recorded as unresolved in the ledger
(`audio.context-sample-rate`, verdict `escalate`), and closing it means deciding
per platform which of the two is pinned rather than declaring one rate.

The Linux number is authored rather than measured, and is the one value here
that a capture would replace. It is Chromium's own Pulse floor
(`kMinimumOutputBufferSize`, 512). Every admitted Linux reference in the corpus
enumerates zero audio devices, so their 44100 Hz / 441 frames is the
no-sound-card path rather than a Linux desktop, and the only Linux capture in
the tree holding a real output device is a non-admissible v1 file reporting
ALSA's compiled default of 2048 — which is also this project's build host.

### Readback noise is available, off, and detectable

`--fingerprint-noise` is the one switch that puts something between the
rasteriser and the page. It is off by default and it is not part of any
profile, because what it buys is not coherence. With it, a page-visible canvas
or WebGL readback comes back with each colour channel of an edge pixel moved by
at most one step; without it, the bytes are what this build rendered.

What it gets right is everything about a *machine*: the same profile perturbs
the same pixels the same way on every launch and every host, two reads agree, a
1:1 canvas-to-canvas copy reads back identically, `getImageData`,
`toDataURL`, `toBlob`, `convertToBlob`, a transferred `ImageBitmap` and WebGL
`readPixels` all agree with each other, and a solid fill is byte-exact because a
pixel with a flat 3x3 neighbourhood is never moved. A detector that renders
twice, reads twice, fills a known colour, votes across many reads, or compares
two routes finds a consistent device.

What gets it is scale. Draw a scene, draw the same scene eight times larger,
downsample it and compare: a perturbation keyed on a pixel and its immediate
neighbours does not survive being averaged with 63 of them, so the two images
disagree in a way no single machine's rasteriser does. Stock Chromium does not
match itself exactly across that test either, but it mismatches differently.
That residual is inherent to a per-pixel intervention rather than a defect in
this one, and it is why the switch is off: a profile is meant to be a machine,
and this makes it a machine with a policy.

Three narrower gaps, for completeness. `captureStream` and the canvas-to-video
frame path are out of scope and stay exact, so a page can compare a captured
frame against `getImageData` of the same canvas. A readback whose format the
policy does not describe — a canvas colour type no canvas output uses, or an
alpha-only `readPixels` — stays exact rather than being guessed at. And a
pixel-pack buffer that script has partly overwritten loses the rows the write
touched, so `getBufferSubData` returns those exactly.

## Platform support

Four targets are the contract: `linux-x64`, `linux-arm64`, `macos-arm64` and
`windows-x64`. All four build green on CI. The Linux and macOS archives have
been run through the detector suite and the post-build checks; the Windows
archive has been built and packaged but its packages' acquisition path has
been exercised only against a planted archive, not on a Windows machine.

There is no Intel macOS build, no 32-bit Windows build, and no Android or iOS
build. Personas are `windows`, `macos` and `linux`; there is no mobile persona,
and presenting as a phone would need touch input, mobile viewport behaviour and
a mobile GPU cluster that this catalogue does not have.

## How long an identity lasts

A launch with no `--user-data-dir` draws a fresh seed from OS entropy and is a
new device; nothing is written anywhere. A launch with `--user-data-dir=DIR`
mints a seed on first use, stores it at `DIR/apostate/identity`, and every
later launch of that directory reads it back and is the same device. An
explicit `--fingerprint=<seed>` wins over both and leaves the file untouched.

That directory holds the cookies and logged-in sessions a site ties to a
machine, so a cookie jar whose hardware changed between visits would be a
stronger signal than any single value. Playwright's
`launch_persistent_context` reuses one directory by design, and that reuse is
what keeps the identity stable, with no flag. Copying the directory copies
the identity, which is intended; deleting the file mints a new one; a fresh
directory per run is how to get a fresh device per run.

An incognito or off-the-record context derives its identity from the same
profile. It does not get a second fingerprint.

## WebRTC

**Measured through a real SOCKS5 proxy, on Linux and macOS.** With a
residential exit configured, the only server-reflexive candidate the browser
gathered carried the exit's address, the host candidate was mDNS-obfuscated,
`raddr` was masked, and the SDP connection line named the exit. Without the
proxy the same page gathered the host's real IPv4 and IPv6 addresses, so the
suppression is the relay's, not the network's. QUIC rides the same UDP
ASSOCIATE and a probe saw its datagrams arrive from the exit.

What the code does. WebRTC carries two separate things, the candidate text a
page reads over SDP and the packets themselves, and they used to disagree.
`--fingerprint-webrtc-ip` rewrites the candidate text and nothing else, so on
its own it left a cooperating peer reading the host's real public address off
the source of the arriving packets while the SDP said something else.

The packets are now meant to follow the proxy. `--fingerprint-webrtc-udp`
decides how, and with the switch absent the behaviour is automatic:

| Configured proxy | Result |
| --- | --- |
| none | direct UDP, as any browser does |
| single-hop SOCKS5 | every datagram relayed through the UDP association, so peers see the proxy |
| HTTP, HTTPS, SOCKS4, a proxy chain, a PAC script, per-scheme rules | no UDP socket is created |

**A relayed socket has no address family of its own, so the families are
decided before any port exists.** The only address such a socket ever sends to
is its association's relay endpoint, and the ICE candidate it produces is that
endpoint, so the families WebRTC is offered are the families the configured
proxy can be reached in and nothing to do with this host's interfaces. The
browser resolves the proxy once per network change and publishes the answer with
the interface list; the renderer offers WebRTC only the families that are left,
and refuses a socket in any other family before one is created. Through an
IPv4-only exit that yields one mDNS host candidate and one IPv4
server-reflexive candidate, which is what an IPv4-only desktop emits; through a
dual-stack exit it yields the usual pair per family.

This replaces the earlier behaviour, where a family the proxy could not carry
was discovered only after the socket had been requested. That refusal was
correct about the packets and wrong about everything else: a port whose socket
dies before it binds never reports an address, so it never completes, and
`iceGatheringState` stayed at `gathering` for the life of the peer connection.
A detector reading candidates at its own timeout then saw a short list, and one
waiting for the end-of-candidates event saw none at all. No stock Chrome
configuration reaches that state.

**Gathering always completes.** That holds for every outcome above, including a
proxy that grants no UDP ASSOCIATE at all, a proxy that stops answering
mid-handshake, and `--fingerprint-webrtc-udp=block`: the socket failure reaches
the port, the port reports an error, and gathering finishes. With no UDP egress
the result is a peer connection that completes with no UDP candidate and
`c=IN IP4 0.0.0.0` on the connection line, which is exactly what stock Chrome
produces under the `WebRTCIPHandling=disable_non_proxied_udp` enterprise policy
with no TURN server configured.

A proxy that cannot relay datagrams still has a real cost. WebRTC gets no host
candidate, no srflx candidate and no UDP relay candidate, so a page that offers
no TURN server over `turn:...?transport=tcp` or `turns:` gets no working media.
TCP is unaffected, and a TURN server reached over TCP or TLS still produces a
relay candidate through the proxy.

`--fingerprint-webrtc-udp=direct` forces direct UDP under a proxy, which
publishes the host's real address. `block` never creates the socket.

Two residuals while the relay is in use. The host candidate carries the
association's ingress address, the address the proxy told the browser to send
to, and whether that is the same port a peer observes as the packet source
depends on the proxy implementation; the address peers actually see reaches the
page as the srflx candidate its own STUN server produces over the same
association. And the enterprise `WebRtcUdpPortRange` constraint no longer
applies, because the port a page sees is the proxy's rather than one this host
chose.

One more residual belongs to the proxy rather than to the browser. A proxy that
resolves in both families but relays only IPv4 destinations answers ASSOCIATE in
both, so both families produce a host candidate, and the IPv6 one gets no
server-reflexive partner because the STUN request is dropped on the way out.
That is what a machine with IPv6 configured and no IPv6 route looks like, and it
is common enough to be unremarkable; closing it would need an ASSOCIATE probe per
family before any candidate is offered.

**The interface topology is the host's, whatever the address says.** Nothing in
the profile describes the machine's network interfaces, so the network service
still enumerates the real ones and every candidate carries their arithmetic. A
candidate's `priority` encodes which interface it came from and how that
interface ranks, `network-id` counts them, and `network-cost` is a direct readout
of the adapter type — 0 ethernet, 10 wifi, 50 unknown, 250 to 980 cellular, plus
one if the adapter is a VPN. mDNS does not cover any of it: the sanitiser
rewrites the address and leaves priority, foundation, `network-id` and
`network-cost` untouched. So a page that never learns an IP can still read how
many interfaces the machine has, what kind each is, and whether one of them is a
VPN.

## Proxies

HTTP, HTTPS, SOCKS4 and SOCKS5 all work, with authentication. UDP over SOCKS5
UDP ASSOCIATE carries proxied QUIC and HTTP/3.

`humanize: true` is rejected by the packages rather than accepted as a no-op.
There is no synthetic mouse or keyboard behaviour in this fork.

## Automation and remote debugging

The packages expose a Playwright-compatible launch API for Python and Node.
There are no .NET bindings, no Puppeteer adapter, no GUI profile manager and no
cloud profile sync.

**Use `--remote-debugging-pipe`, not `--remote-debugging-port`.** A page can
detect an open debugging port today, with no timing tricks:

```js
fetch('http://127.0.0.1:9222/json/version', { mode: 'no-cors' })
```

That promise resolves when the port is open and rejects when it is not, four out
of four trials. Two qualifications. It only works from a document in the local
or private address space; Local Network Access blocks it from an ordinary public
HTTPS page at this Chromium revision, where open and closed ports measured
indistinguishable. And `--remote-debugging-pipe` opens no socket at all and
never produced the signal.

Playwright uses the pipe by default and needs no change. Puppeteer defaults to a
TCP port, so pass the pipe explicitly.

The change is in the built series. Its boundary matters more than its
existence, because people will assume more than it does: it makes the port
unreachable from a **page**, not from a **process**. Any program on the
machine that opens a socket still connects, which is exactly why Playwright
and Puppeteer keep working.

**A CDP session used to be detectable by timing, in ten lines of JavaScript.**
This was the largest automation tell. Once a client sent `Runtime.enable`,
exception handling and console calls got measurably slower, as single-page
ratios a detector needs no baseline for. Both causes are in V8's debugger,
and patch `0087` gates them on the delegate that only `Debugger.enable`
installs. Measured on the shipped artifact, before and after the change:

| Probe | Unattached | After `Runtime.enable`, before | After `Runtime.enable`, now |
| --- | --- | --- | --- |
| `try{throw 1}catch{}` at depth 240 over depth 2 | 1.0x | 26.5x | 0.97x |
| `console.log` | 1.0x | 10.8x | 0.94x |
| `console.trace` over `console.log` | 8.0x | 1.009x | 6.67x |

The third row was the sharpest because it inverted -- `console.trace` is
normally much more expensive than `console.log`, and under an attached session
the two cost the same -- and it is the one that now reads as unattached.
CreepJS and browserscan both report no automation on a Playwright-driven
session of this build, which is what the numbers predict. Patchright is
therefore not required for stealth; the package ships it because it is what
the launcher drives, not because the binary needs it.

Three tells survive whatever happens to the port, and all three are real.

**JS coverage re-exposes the timing signal.** `page.coverage.startJSCoverage()`
sends `Debugger.enable`, which is the condition the change keys on, so a
coverage run puts the signal back. Do not collect coverage in a run you want to
be unremarkable.

**Heap size rises when a client attaches.** `usedJSHeapSize` and
`totalJSHeapSize` genuinely grow, because the inspector allocates in the
renderer's heap. Bucketing hides it on most pages, and a site-locked page reads
precise values. Nothing fixes this without lying about the heap.

**One loopback round trip remains.** With the port open the kernel completes the
TCP handshake before the browser hangs up, so an open port and a never-open one
differ by roughly 0.1 ms at the median. That is one tick of `performance.now`'s
resolution in a page that is not cross-origin isolated, which is why forty
samples did not separate them: the distributions overlap fully. Small, not zero.

The widely repeated console probes do not work, on this build or on stock
Chrome, and all of these measured identical attached and unattached: a getter on
an `Error`'s own `stack` property, `Proxy` traps, plain-object getters,
`console.table` and `console.dir`, `toString`, `valueOf` and
`Symbol.toPrimitive`, `Error.prepareStackTrace`, `Error.captureStackTrace`,
`Error.stackTraceLimit`, and `Function.prototype.toString`. What did leak was one
step past upstream's own guard: a `stack` accessor installed on
`Error.prototype` rather than on an instance. So the real signals were timing
and that one accessor, neither of which is what the folklore describes.

## Two measurement gaps ship open

Both need hardware nobody here has, so they are stated rather than closed.

**Cross-OS WebGL is unmeasured.** Every WebGL comparison behind the
cross-platform numbers was taken between two software rasterisers, so how much
of the difference is the operating system and how much is the test environment
is not known. Settling it needs a Linux host with a discrete GPU and a capture
from a Windows machine with the same GPU. Until then, treat a cross-OS WebGL
claim as untested rather than verified. That case is now the ordinary one rather
than the exotic one, because the persona picks the cluster on every host: a
Windows persona on a Linux server presents a Direct3D 11 cluster, and how that
composes against a real Windows machine is exactly what has not been measured.
It is the largest unmeasured thing behind the current default.

**Three GPU clusters were measured on another Chromium.**
`windows-d3d11-nvidia-0947761dfbe9` was captured on 153.0.8010.37, and
`linux-vulkan-nvidia-adf287b8f0ee` and `linux-swiftshader-google-6922d61bab83`
on 152.0.7977.82, against a 152.0.7977.83 binary. Capability tables move between
releases, so those three can differ from this binary in version-bearing fields.
The other two, `macos-metal-apple-850a91233555` and
`windows-d3d11-intel-79dfeb5b4f99`, are on the pinned build.
`--fingerprint-explain` reports the caveat when a launch draws one of the three.

## Reporting a limitation

`--fingerprint-explain` prints, per surface, the resolved value, where it came
from, and any limitation that applies to this host. It writes to stdout and is
not readable by a page. If a surface is wrong and this page does not explain it,
that is a bug worth filing.
