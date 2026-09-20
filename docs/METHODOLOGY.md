# Methodology

The engineering rules this fork is built under, and the measurements behind
them.

This page is for people reading or writing the patches. If you want to use the
browser, read [README](../README.md), [flag reference](FLAGS.md) and
[known limitations](LIMITATIONS.md) instead.

## 1. What correct means

A value is correct when a page cannot distinguish it from the value a real
machine of the claimed kind would have emitted. "Harder to detect" cannot be
measured, so it is not used as a goal. Coherence with the claimed machine is
the whole bar: every surface agrees with every other, and nothing of the host
shows through.

That framing is what makes the work finite. Several hundred observables with
opinions attached never converge. The same list with a target value per row
converges by subtraction.

Where the target value comes from is a separate question, and it is recorded
rather than gated. A capture from a physical machine is the strongest source.
A cloud capture of a GPU family is a source for that family. A documented rule
is a source — D3D11 capability limits are feature-level constants, a card's
PCI id is public, a WASAPI shared-mode buffer is ten milliseconds — and so is a
public corpus of real fingerprints or a competitor that measurably passes. Each
catalogue entry names which it rests on so the next person knows how much to
trust it. None of them decides whether the value may be served; what decides
that is whether serving it makes the composed machine more coherent than
leaving the surface to the host. It almost always does, because a host leaking
through a fabricated identity is the loudest incoherence a page can find.

## 2. Patch the emitter, never the accessor

Every value comes out of the code path that would have produced the real value.
No JavaScript injection. No CDP override. No wrapper objects, no `Proxy`, no
redefined property descriptors.

The reason is practical. A wrapped property is detectable by reading it an
unusual way: `Function.prototype.toString`, a descriptor comparison, a
prototype-chain walk, a re-read from worker scope, a cross-realm check. Patching
the emitter makes all of those agree with the ordinary read, because no wrapper
sits in between.

`navigator.hardwareConcurrency` is the example. Its Blink accessor forwards to
`base::SysInfo`, which also sizes Chromium's thread pools. Patching the accessor
returns the right number while the process keeps scheduling work like the host.

## 3. No new observables

A change must not add anything a page can find that a stock Chromium does not
have. That rules out a new command-line switch whose effect a page can infer, a
new process name, a new JavaScript-visible property, and a new wrapper object.
An anti-detect feature that is itself a fingerprint has cost more than it paid
for.

## 4. Coherence over concealment

Correct means equal to the target. It does not mean noisier, absent, or harder
to read. Blocking a surface is itself a value, and usually a rare one.

Broken relationships between values cause more detections than wrong individual
values. Applying the Client Hints patch on its own produced a browser reporting
macOS through `userAgentData` and Linux through `navigator.platform`. A
half-changed identity is worse than none. User agent, client hints, platform,
GPU renderer, font set, screen geometry and timezone have to agree, so they are
checked in pairs rather than one row at a time.

## 5. No per-call randomness

Real hardware is deterministic. Two identical canvas renders on real silicon are
bit-identical. Two reads of `deviceMemory` agree. An audio graph rendered twice
gives the same samples.

So this fork adds no noise to canvas, WebGL, audio, or client rects. Two reads
of one surface within one launch always agree. Variation happens when the seed
changes, and a different seed is a different device rather than a different
reading of the same device.

Noise is the cheapest thing in this field to detect. It is unstable inside a
session, it breaks returning-visitor consistency between sessions, and no
physical device produces it.

## 6. Capacity only ever goes down

A profile may claim fewer cores than the host has, never more. Same for memory,
codec support, font families, speech voices, and display area against window
bounds. GPU limits are the exception on every backend: what a WebGL context
reports is the composed cluster, and only the operation behind it is held to
the host. §9 has the reasoning and
[docs/LIMITATIONS.md](LIMITATIONS.md) has its cost.

This is falsifiability, not modesty. A page can measure parallel throughput,
allocate until allocation fails, compile a shader at the advertised limit, or
ask a voice to speak. A claim below host capability survives every one of those
probes. A claim above it fails the first one tried.

Reporting a host's real 14 cores and 36 GB identifies one model of laptop.
Reporting a fabricated 20 cores contradicts any timing probe. Reporting 8 cores,
a real bucket a 14-core host can serve, is both common and unfalsifiable.

### Why memory is clamped and fonts are not

Two opposite rules sit next to each other here, and they follow from one
question rather than from two moods.

> Memory is clamped to the host because the user cannot install RAM to make a
> claim true, so an unbacked claim is falsifiable by allocation. Fonts are
> assumed because the user *can* install them, so the honest move is to tell
> them to. The distinguishing factor is whether the user can change the host,
> not how loud the tell is.

So the browser clamps what the operator cannot fix and documents what they can.
[docs/FONTS.md](FONTS.md) is the documenting half.

Assuming provisioning means we stop checking, not that we start claiming absent
faces. The enumeration filter is still subtractive, so a font the host genuinely
lacks still cannot be made to measure.

## 7. Selection over synthesis, and how a machine is composed

A value is chosen from whole options that real systems exhibit. It is not
assembled field by field to look plausible.

Five randomly chosen font families is synthesis, and it is a tell, because
installed fonts arrive in bundles. A machine with Myriad Pro has the rest of
Creative Cloud. A machine with Cascadia Code has a developer's toolchain. So the
font axis selects bundles, and every other axis selects whole options from a
table rather than inventing a value.

The table is small at its root and large at its leaves, and that is the design
rather than a shortage. A composed machine starts from a **base**: one real
capture that fixes everything a page can cross-check — the WebGL capability
and precision tables, the extension list, the render digests, the WebGPU
cluster, the audio graph. Three physical machines and a handful of cloud GPU
captures are the bases. On a base, the **GPU identity** rotates across every
card in the same capability family: every Apple Silicon chip on the M4 Max
base, because ANGLE's Metal limits are hard-coded and an M1 and an M4 Max
return the same table; every NVIDIA card on the NVIDIA D3D11 base, because
ANGLE's Direct3D 11 limits are derived from the feature level plus a per-vendor
flag set and never from the device id. The strings and PCI ids that rotate are
public facts, and the WebGPU architecture follows the card. Around that, the
axes that genuinely vary from one owner to the next — screen geometry and
furniture, core count and installed memory within what the card is sold with,
font bundles, media devices, battery, network — disperse over options a real
population exhibits, drawn by the seed. A few bases and a few dozen cards
therefore compose a space of coherent machines that is, for practical purposes,
unbounded, and every one of them traces to a measurement or a stated rule.

When no capture exists for an option that operators need, the option is
derived by the rule and labelled authored rather than left out. Leaving it out
does not make the browser more honest; it makes the pool thinner and pushes
every launch onto the same few machines, which is its own signal.

## 8. The browser does not lie about being this Chromium

The binary really is the Chromium version it reports. Claiming another version
would mean behaving like that version, and every feature-detection difference
would contradict the claim.

It follows that a reference measurement is bound to the build it was taken on.
Capability tables move between Chromium releases, so a capture from another
build is evidence about hardware, not a target for this binary. Rebasing onto a
new Chromium means re-measuring the references, not only re-applying the
patches.

## 9. What the measurements established

These four results shaped the design, and two of them are limits rather than
features.

**Fonts have to be installed, not declared.** Chromium on Linux asks fontconfig,
and fontconfig answers from whatever it is pointed at. Running the browser with
`FONTCONFIG_FILE` set to a config naming one directory made it enumerate exactly
the families in that directory, and a config whose font path excluded the system
directories made it enumerate none. So removal works. Addition needs the files.
Of 76 families compared between a reference Mac and an unprovisioned Linux host,
47 already agreed on metrics; of the 29 that differed, 26 were families the host
lacked and 3 were Linux families the Mac lacked. Installing the real files buys
26 fields. Hiding the 3 foreign ones buys 3.

The defensible detection here is absence, not presence. Menlo, Monaco, Zapfino,
PingFang SC and Helvetica Neue ship with macOS and cannot be uninstalled, so a
machine claiming macOS without them is not a Mac. A rule keyed on the presence
of a foreign family would fire on ordinary users, because LibreOffice installs
Liberation and DejaVu everywhere and real machines carry long tails of fonts
their owners installed.

**Audio renders identify the host CPU.** An `OfflineAudioContext` graph touches
no audio hardware. Its output is fixed by the FFT kernel that CPUID selects at
process start and by the host libm, so the same graph renders differently on
arm64 and x86-64, by about the same margin as deliberately injected noise.
Profiles are partitioned by CPU instruction set for that reason.

**WebGL limit tables identify the backend, not the GPU.** On Metal, ANGLE
hard-codes the whole limit and precision table instead of querying the device,
so an M1 and an M4 Max return identical numbers. The same NVIDIA silicon returns
a different table through D3D11 than through Vulkan. So a limit table is
evidence about a backend, and on a host that is running one the capability
cluster is selected from what that backend can serve. On a host running no
hardware backend at all there is no table for a claim to contradict, and the
cluster follows the claimed platform instead.
[docs/LIMITATIONS.md](LIMITATIONS.md) states the user-visible consequence.

**Software rendering is a throughput fact, not a string fact.** Stock
Chromium's SwiftShader caps `MAX_TEXTURE_SIZE` and `MAX_RENDERBUFFER_SIZE` at
8192, and patches `0027` and `0038` raise the software rasteriser's own limits
to real-hardware values so the numbers are not the tell. Timing still is.
Measured on one M4 Max between the two paths of the same binary, a fixed
fragment-shader workload runs about 230 times slower in software and small draw
calls about 35 times slower, on an identical CPU baseline. Closing that would
mean either making software rendering fast or slowing real hardware down, and a
deliberate timing adjustment is a new observable, which rule 3 forbids. So the
timing gap ships open and the strings do not: the backend a host is running does
not restrict which GPU identity the profile may serve.

The backend a host runs is not a constraint on the identity it presents, and
the reason is deployment shape rather than taste. Almost every host this runs
on is a headless server with no GPU, and `ANGLE (Google, Vulkan 1.3.0
(SwiftShader Device (LLVM ...)))` sorts such a launch out of the ordinary
population on a substring match, while the timing gap it would stay coherent
with costs a page a benchmark to measure. Trading a free signal for an
expensive one is the wrong direction. An earlier revision of this page said the
opposite and some patch commentary written against it is still in the tree;
where the two disagree, this page governs.

What the claimed operating system costs is a separate question with a real
answer: the surfaces a fork cannot reach, installed fonts chief among them,
stay the host's. So the persona is a choice to be made with the trade-off in
view, and the compositor's job is to report the trade-off rather than to
pretend it away or to refuse the choice.

Rule 6 is not weakened by this, but it is now sharper, and the sharpening took
two goes. A renderer string is an identity rather than a capacity, and rule 6
is about capacity. For the capacities, the first answer was to key the decision
on the backend: clamp the claim where the backend enforces what it reports,
serve it whole where the backend enforces nothing. Patch `0103` shipped that
and `0112` corrected which backends enforce.

Patch `0119` replaces the axis, because the backend was never the right one to
ask about. These numbers leave the GPU process through six shared query entry
points, and three unrelated consumers come through them: a WebGL context,
whose numbers a page reads verbatim; the compositor and raster; and Skia
Ganesh, whose `GrGLCaps` reads three of them through the same function
pointer. Only the first is a fingerprint, and gating on the backend applied
one answer to all three — which on ANGLE/Metal meant discarding the whole
anchor on the only surface that mattered, and, had the clamp simply been
dropped, would have meant telling Skia that a four-sample device does eight.
So the gate is now the kind of context asking. A WebGL context is served the
composed cluster on every backend; everything else keeps the host's caps.
Patch `0104` serves the claimed extensions whose objects carry constants
rather than methods, on the same reasoning.

Rule 6 still governs the result, because whatever a launch advertises has to
survive being exercised: a page allocates at the advertised limit, links a
shader at the advertised uniform count, and calls methods on the extension
object it is handed. Where that cannot be made to hold, the residual is
measured and written down rather than assumed away — the claimed
`MAX_TEXTURE_SIZE` is not allocatable on a software backend, and on a backend
that enforces, a point size, a uniform count or a sample count taken to the
claim is refused. [docs/LIMITATIONS.md](LIMITATIONS.md) says so per limit,
with the numbers.

## 10. Verification

A change is verified by measuring the surface it changes. Launch the binary,
read the observable the way a page would read it, and compare against the target
value. That is the whole gate.

Three things do not count as verification. A successful compile shows the code
builds. A schema check shows a file is well-formed. A renderer string shows one
string. None of them show the surface emits the right value, and for anything
with a capability behind it the measurement has to exercise the capability:
allocate at the advertised limit, decode with the advertised codec, speak with
the advertised voice.

Repeat reads are part of the measurement, because rule 5 is only observable
across two reads.

One trap is worth naming because it costs a wave's worth of confidence. "This
change applies cleanly" and "this change is applied" are different claims, and
only the second one is about the tree you are going to build. A patch checked
against a scratch copy of the pristine files can pass indefinitely while the
working tree is missing two of its hunks. So the two checks are a pair and
neither substitutes for the other: a forward check against the pristine files
answers "will this apply", a reverse check against the working tree answers "is
this applied", and each is meaningless in the other's situation. A reverse check
against an unpatched tree fails because the content is not there yet, which
looks identical to failing because it was never put there. Prefer whichever of
the two has the noisy failure mode for the question you are actually asking.

### How far something has been checked

The ledger records a short label per surface so that "verified" means something
specific rather than something reassuring. The labels are a description, not a
release gate. Nothing is withheld from a build for lacking one, and a surface
whose label is blank is simply a surface nobody has measured yet.

| Label | What was done |
|---|---|
| V0 | The file is well-formed and the patch applies in series order |
| V1 | The translation unit compiles against the pinned build |
| V2 | The full build succeeds, the binary launches, the smoke run passes |
| V3 | A launch was collected and diffed against a measured physical reference |
| V4 | A launch was run against live detectors |

`V3-C` and `V4-C` are the same two steps against a compatibility target rather
than a physical device, and they stay on their own scoreboard because the
evidence behind them is a different kind of thing.

Most labelled rows sit at V2, and nothing has reached V3 or V4, so no claim on
this page rests on physical-reference conformance or on a detector suite. The
labels are per-patch as well as per-surface: a patch that has been built and
launched can carry V2, and a patch that applies in series but has not been built
yet cannot carry more than V0. The current numbers live in `ledger/`, which is
where they stay accurate.

### Collecting a V3 capture on a headless Linux server

The deployment target is also the machine most likely to be available for
measuring, and it has no display and nobody watching. One command handles it:

```sh
python3 capture/collect-unattended.py --label NAME --out DIR
```

It starts Xvfb on `:99`, reusing a display that is already up, starts the
receiver on a free loopback port with `--once`, launches a **headed** browser at
`?auto=1` — the same code path a person's click takes, with no CDP, no WebDriver
and no injected script — and prints the admission decision. Exit 0 is accepted,
1 rejected, 2 nothing submitted. Measured on a 32-core GPU-less box at roughly
five seconds per capture.

Headed under a virtual display is not a preference, it is the only shape that
passes. The receiver rejects any capture whose user agent contains `Headless`,
so `--headless` cannot produce an admissible capture at all, and rule 6 wants a
normal browser regardless.

Nothing here depends on a patched binary. The whole path is stock Chrome of the
release major plus `capture/server/receive.py`, which is plain Python, so this
runbook is usable today while most of the behaviour described elsewhere on this
page is still waiting on a build. The patched binary appears only as the
*subject* of a later conformance run, and the collection mechanism is the same
either way.

Two host facts need handling, and both are recorded rather than papered over.

**WebGL has to be re-permitted.** Under Xvfb the only GL driver is Mesa
llvmpipe, which Chrome's software-rendering blocklist disables WebGL and WebGPU
for, and the implicit SwiftShader fallback is gone — deprecated from Chrome 130
and progressively removed on Linux and macOS from 139. Measured on that box,
three runs differing only in flags:

| Flags | Result |
| --- | --- |
| none | `getContext('webgl')` returns null, `webgl1` and `webgl2` report `webgl unavailable`, capture rejected |
| `--enable-unsafe-swiftshader` | 44 of 44 probes measured, renderer `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`, accepted |
| `--use-gl=angle --use-angle=swiftshader` | identical renderer string, also accepted |

So the one flag does the whole job under Xvfb, and the script passes no other
GL-related switch. Prefer it anyway over the pair that also works, because the
two are different mechanisms: Chromium's own documentation separates the SwANGLE
*driver* mode from the WebGL *fallback* opt-in, so
`--enable-unsafe-swiftshader` permits the software fallback while
`--use-angle=swiftshader` selects a backend. On a host that does have a GPU the
documented behaviour of the opt-in is therefore to change nothing, while the
driver switch would yield a software capture from hardware without saying so —
the same class of error as the `--disable-gpu` trap in
[docs/FLAGS.md](FLAGS.md). That last comparison is the switches' documented
division of labour rather than a measurement, because the box this was taken on
has no GPU to prove it with. What is measured is the backstop: the script prints
the renderer it actually measured, so a software capture cannot be mistaken for
a hardware one.

For more than one box there is an enterprise policy,
`EnableUnsafeSwiftShader`, added for the managed-VM case.

**The window-management permission has to exist before the run.**
`screen.details` will not prompt during a capture and nobody is present to click
Allow, so the grant is written into the fresh profile. One trap for anyone doing
it by hand: Chrome still stores this under the API's old name,
`window_placement`, in `Default/Preferences`. Seeding `window_management` is
silently ignored.

A capture taken this way describes SwiftShader, not the host's GPU, and the
script prints the renderer it actually measured. That is a usable capture, and
the corpus policy around it is deliberate:
`scripts/import-capture.py` admits it only with `--allow-software-renderer`, and
`scripts/build-anchors.py` refuses to build a hardware anchor from it.

Two operational notes. The box needs a browser of the release major: Google's
apt repository carries only current stable, but older stable debs remain in the
pool and `dpkg-deb -x` unpacks one beside the installed browser without
disturbing it. And a rejected capture's body is never written to disk, so
diagnose from the admission record alone — it carries `probe_errors`, every
probe that did not complete with its error. For the browser's own GPU messages,
pass `-- --enable-logging=stderr` and read the log in the printed run directory.

### A claim outlives the thing it described

This is the failure mode that cost this project the most, and it is invisible to
anyone reading the claim. Three instances, all found in one pass:

- A ledger row marked resolved, resting on a probe that could not see the
  surface it was resolving.
- Four patches asserting that Windows evidence was unavailable, while the
  capture had been checked into the tree for a week.
- A servability column in this documentation that promised a font-provisioning
  path no C++ read, next to a page that correctly said the field was inert. Both
  are gone: the field was deleted from the schema and the browser now assumes
  the operator installed the fonts.

None was written dishonestly. Each was true when written, and then the thing it
described changed and the sentence did not. The reader cannot tell the
difference, which is what makes it expensive: a stale claim reads exactly like a
current one.

Two habits follow. Name the symbol a claim depends on rather than the line
number, so that a reader can check it and so that moving code does not silently
invalidate the citation. And when a claim says something is absent,
unimplemented or unavailable, check the tree before repeating it, because that is
the class of claim that goes stale in the direction nobody notices.

## 11. Where the parts live

```
patches/     The fork, one patch per concern, ordered by patches/series
config/      profile.schema.json, the accepted profile fields
resources/   The dispersion tables that a seed draws from
corpus/      Measured GPU capability clusters
build/       Every pinned build input. See docs/BUILD.md
scripts/     Operational steps, including the reference resolver
capture/     Measures a device and diffs a launch against it
```

The profile itself lives in `base/apostate/profile.h`, in `base/` because
`base::SysInfo` is its first consumer and `base` cannot depend on `content` or
`chrome`. It arrives on the command line because field trials initialise before
mojo, so a value delivered over a mojo interface cannot serve a read that
happens earlier. Command-line switches are not readable by web content.

Every getter returns `nullopt` when the profile does not carry the field, and
callers fall through to the host value. An absent field stays inherited. A
default invented in the loader would be wrong in a way no consumer can detect.

[docs/ARCHITECTURE.md](ARCHITECTURE.md) has the process topology and the
surfaces that are computed in more than one process.
