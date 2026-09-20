# Flag reference

Every switch the browser accepts for fingerprint control, what it takes, and
what it changes. None of them are required: a launch with no flags composes a
complete device and presents it.

Command-line switches are not readable from a page. Passing one of these does
not add an observable.

## The seed

```sh
./chrome --fingerprint=12345
```

`--fingerprint` selects the whole identity. The same seed produces the same
device on any host that can serve it, on every launch, with nothing stored on
disk. Without it, a launch that names a `--user-data-dir` presents the
identity bound to that directory, and a launch that names none draws a fresh
seed and presents a different device. [How long an identity
lasts](#how-long-an-identity-lasts) is the whole rule.

The value is any printable ASCII up to 512 bytes. Integers are the usual choice.
An empty, over-long or non-printable value refuses the launch on stderr and
exits non-zero. It does not fall back to anything, because a typo that quietly
presents the operator's real machine is indistinguishable from success until a
site has already clustered the real device.

The seed feeds a SHA-256 derivation over the profile schema version, the
catalogue version, the Chromium build, the persona and the seed itself. Changing
the browser build or the catalogue therefore changes what a seed selects, by
design: a seed names a device drawn from one specific table, and silently
remapping it onto a new table would be worse than a new identity.

## Turning composition off

```sh
./chrome --fingerprint=host
```

Every surface reports the machine's own value:

```text
apostate fingerprint composition

  composition         disabled
  seed source         none: the host is inherited

Every surface is the host's own value, which is what --fingerprint=host
asks for. No layer below it is active, so there is nothing to report per
surface: the profile is the host.
```

Six spellings mean this, all case-insensitive: `host`, `off`, `false`, `0`,
`disable` and `disabled`. `host` is the canonical one.

Nothing is composed under any of them, so there is no profile for a persona, a
pinned anchor or a per-field override to land on. Combining one of those with
host mode refuses the launch rather than half-applying it. `--fingerprint-explain`
still works.

Use it to find out whether a problem is this browser or the environment. On a
genuine Windows machine it is the fastest way to tell those two apart.

## The persona

```sh
./chrome --fingerprint=12345 --fingerprint-platform=windows
```

`--fingerprint-platform` takes `windows`, `macos` or `linux`. It sets
`navigator.platform`, the User Agent, the Client Hints platform and version, the
OS release, the font set, the voice table, the screen geometry pool, the window
chrome deltas, the hardware buckets — and the GPU.

It moves the GPU on every host. The claimed platform selects the capability
cluster, and the host's own graphics backend is not consulted: `windows` draws a
Direct3D 11 cluster, `macos` an Apple Metal one, `linux` an NVIDIA Vulkan one,
whatever the machine underneath is running.

The default is host-dependent:

| Host | Default persona |
| --- | --- |
| macOS | `macos` |
| Windows | `windows` |
| Linux | `windows` |

A Linux host therefore claims Windows unless told otherwise, which is the one
default in this browser that is not the host's own OS. It is chosen as the least
bad cross-OS pairing and because it is what most deployments want, and its cost
is the Windows font set: install it ([docs/FONTS.md](FONTS.md)) or pass
`--fingerprint-platform=linux` to compose the host's own OS. The report prints
that as a limitation on every such launch.

[docs/LIMITATIONS.md](LIMITATIONS.md) has the per-persona cluster table, the
cross-OS risk ordering, and this behaviour's status: it is patch `0102` and has
not been built or run.

## Per-field overrides

An explicit switch sets one field and the seed fills in the rest, so the result
is one device with a correction rather than two partial identities. The two
locale switches are the exception to the second half of that: the seed fills in
nothing for them, because the seed does not reach that surface. What they do not
set is served by the host. See below.

| Flag | Value | Field it sets |
| --- | --- | --- |
| `--fingerprint-gpu-vendor` | exact `UNMASKED_VENDOR_WEBGL` string | WebGL unmasked vendor |
| `--fingerprint-gpu-renderer` | exact `UNMASKED_RENDERER_WEBGL` string | WebGL unmasked renderer |
| `--fingerprint-hardware-concurrency` | positive integer | `navigator.hardwareConcurrency` |
| `--fingerprint-device-memory` | positive integer, GiB | Installed memory |
| `--fingerprint-screen-width` | positive integer, CSS px | `screen.width` |
| `--fingerprint-screen-height` | positive integer, CSS px | `screen.height` |
| `--fingerprint-timezone` | IANA name, such as `America/New_York` | Timezone for `Intl` and `Date` |
| `--fingerprint-locale` | `Accept-Language` list, such as `en-US,en` | Accept-Language, and the voice table keyed from it |

Every refusal writes to stderr and exits non-zero. Nothing is silently ignored,
because an operator who mistypes a switch and is not told runs a whole session
believing a field was spoofed when it was their own hardware.

The two GPU strings narrow the choice before the seed draws, rather than
overwriting a finished profile. The limits, extensions, shader precisions and
WebGPU adapter all come from the anchor those strings were measured on, so a
renderer written over a finished profile would sit on a capability table
belonging to different silicon. A name the resolved anchor never measured is
refused, with the servable identities listed.

`--fingerprint-hardware-concurrency` is refused above the host's real logical
core count and `--fingerprint-device-memory` above the host's installed memory.
A claim below the machine is unfalsifiable and a claim above it is not: a page
can measure parallel throughput and can allocate until allocation fails.

The screen switches are applied before the available rectangle is derived, so
`availWidth`, `availHeight`, `availLeft` and `availTop` follow from the same
desktop furniture the seed drew. A size smaller than a `--window-size` the same
launch asked for is refused, and so is one the drawn insets cannot fit inside.
There is no display yet at composition time, so `--window-size` is the only host
bound available and the host's real panel size is not checked.

`--fingerprint-locale` and `--fingerprint-timezone` set exactly the field each
names, and what neither names is the host's own — not the seed's, which never
had a value here. So `--fingerprint-timezone=Europe/Berlin` alone gives a
Berlin zone over the host's own language list, which is an ordinary combination
on a real machine and is the reason the switch is one field and not two:
resolving `--fingerprint-locale=en-GB` to the catalogue's `en-gb` policy would
quietly set `Europe/London` as well, and on the path where a GeoIP lookup
resolved a country but no locale that would be a timezone invented out of the
launcher's own answer. To pin both, pass both.

`--fingerprint-locale` is applied where the locale surface resolves, not over
the finished profile, because the speech-voice table is keyed on the resolved
Accept-Language list. That also means it is the switch that brings the measured
voice list back: a launch that names no locale keys onto the empty language set,
which carries no speech section, so `speechSynthesis` reports the host's real
providers.

It moves no keyboard layout, because the locale policy no longer carries one:
the maps it used to set were a five-key stub identical on
every option, and the layout a machine reports follows its physical keyboard
rather than its Accept-Language list. A locale that does not match the physical
layout is an ordinary thing on a real machine, so the map stays the host's unless
a capture-derived profile replays one. See
[known limitations](LIMITATIONS.md).

A value that is not one of the catalogue's own buckets is honoured and reported.
`--fingerprint-explain` names the field, the value, the command line as the
layer that decided it, and the drawn value it displaced, and says that a
hand-chosen value has no prevalence data behind it and may be more distinctive
than a drawn one. The two locale switches displace nothing and carry no such
caveat: there is no drawn locale to be more distinctive than, and naming a zone
that matches the connection's exit country is the correct use of the surface
rather than a risk taken. Their report rows say instead that the value came from
the command line — which is also where the Python and Node packages put the
answer from their prelaunch GeoIP lookup — and, when a field is unset, that the
host serves it and which switch would pin it.

## Inspecting a launch

```sh
./chrome --fingerprint=12345 --fingerprint-explain
```

Prints the composition report to stdout and exits without opening a window. The
report is three parts: what the launch resolved, one row per surface, and the
limitations that apply on this host.

```text
apostate fingerprint composition

  chromium            152.0.7977.83
  profile schema      3
  catalogue           v2 (tables 0dff1dd6658e...)
  platform persona    macos
  host platform       macos
  host cores          14
  host memory         38654705664 bytes
  host backend        metal (platform default, not probed)
  seed                12345
  seed source         --fingerprint (pinned by flag, identical on any machine)
  reproduce with      --fingerprint=12345
  root                60eab51485a4...

surface                  layer             evidence               value
anchor                   anchor            physical-ground-truth  macos-metal-apple-850a91233555
os_release               dispersion        catalogue-value        macos-26-5-0
gpu_identity             dispersion        physical-ground-truth  apple-m4-max
cpu                      dispersion        physical-ground-truth  cores-14
memory                   dispersion        catalogue-value        gib-8
panel                    dispersion        catalogue-value        mba13-default
audio                    dispersion        physical-ground-truth  coreaudio-256
extensions               dispersion        physical-ground-truth  absent
locale.application       composed-default  native-derived         en-US
locale.accept_languages  composed-default  native-derived         (the en-US bundle's default)
locale.timezone          host-inherited    host-inherited         (inherited)
fonts.render_params      platform-projection  native-derived      macos text rendering

limitations
  - anchor macos-metal-apple-850a91233555 has one measured member, so no
    identity rotation is offered on its capability cluster
  - no launch layer named locale.timezone, so the host's own is served
    unchanged and nothing is composed for it. On a direct egress that
    matches; behind a proxy it is the host's and not the exit's. Pass
    --fingerprint-timezone to pin it, or launch through the Python or Node
    package, which resolves it from GeoIP of the effective egress and passes
    that same switch
```

The `layer` column says who decided a surface. `dispersion` is a weighted draw
from an option table and a different seed can move it; `anchor` is a measured
capability cluster taken whole; `command-line` is an override the launch
named; `host-inherited` is a surface left alone; `composed-default` is neither
the operator's choice nor the host's value; and `platform-projection` is a
value the claimed OS determines outright, with no table behind it and nothing
for a seed to vary. Text rasterisation is the only surface on that last layer,
because Chromium computes it per platform and only per platform.

The three locale rows are the surface an operator checks against their exit IP,
so the report names the layer that decided each one.

`locale.application` is the application locale the launch presents, and it is
the row to read first because the other two resolve against it: it decides
`Intl.DateTimeFormat`, `NumberFormat` and `Collator`, the calendar and hour
cycle they report, the default `Accept-Language` list, collation order, and
locale-dependent font fallback — and the rendered width of an
`<input type=date>`, which is how a page reads it with no `Intl` call at all.

It is pinned twice, because one pin per platform is not enough.
`l10n_util` resolves it ahead of every platform candidate — ahead of glib's
`LANGUAGE`/`LC_*`/`LANG` on Linux, of `NSBundle`'s `preferredLocalizations` on
macOS, and of both `--lang` and the Windows preferred-UI-language list on
Windows — and `LANGUAGE`, `LC_ALL`, `LC_MESSAGES` and `LANG` are written to it
in the browser before the first child is forked, so the C library agrees with
ICU and every child agrees with the browser. Both read the same value, so they
cannot disagree.

`command-line` means `--fingerprint-locale` named it — which is also how a
GeoIP answer from the Python or Node package arrives — and `composed-default`
means nothing did, so `en-US` is presented rather than the operator's shell or
system language. It is never `host-inherited`, except under
`--fingerprint=host`, which reports no surfaces at all.

`locale.accept_languages` is `command-line` when a switch named the list, and
`composed-default` otherwise: no override is composed, and the list a page reads
is the one the application locale's resource bundle declares. `locale.timezone`
is the one of the three that really can be `host-inherited`, because ICU's zone
is a separate producer from the application locale.

The `evidence` column says where each value came from:
`physical-ground-truth` is a measurement from a real device, `catalogue-value`
is authored from platform release history, and a surface left to the host says
so.

The `seed source` line is the one to read when an identity is not the one you
expected. It names which of the three lifetimes this launch is in, and
`reproduce with` carries the exact argument that recreates it.

A launch with no `--fingerprint` and no `--user-data-dir` reports the seed it
drew, and that line is the only record of it:

```text
  seed                616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
  seed source         drawn from OS entropy for this launch only (ephemeral)
  reproduce with      --fingerprint=616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
```

A launch that named a `--user-data-dir` reports the file the identity came
from, and whether this launch is the one that created it. `(created this
launch)` on a profile you believed was established is the whole diagnosis:

```text
  seed                4f3c8a1e09b7d2650c3ab8f41d7e5920ac6b13f8e04d7a29bb5c1e6370d8f425
  seed source         this profile's identity file (stable for this --user-data-dir)
  identity file       /home/you/profiles/one/apostate/identity (read from disk)
  reproduce with      --fingerprint=4f3c8a1e09b7d2650c3ab8f41d7e5920ac6b13f8e04d7a29bb5c1e6370d8f425
```

The report goes to stdout and is never exposed to a page.

## Troubleshooting a block

Run `--fingerprint-explain` first. It is the only thing that will tell you what
this launch actually claimed and what this host could not serve, and most blocks
turn out to be named in its `limitations` block.

On a Linux host with the default persona, which claims Windows, the report
prints the pairing as a limitation with its own remedy:

```text
limitations
  - the persona is windows on a linux host, which is this project's default
    pairing there and its least bad cross-OS one: the GPU cluster follows the
    persona, but the Windows font set does not install itself, and a Windows
    persona missing Windows faces is measurable in text metrics -- install the
    full set (docs/FONTS.md) or pass --fingerprint-platform=linux to compose
    the host's own OS
```

Each line has an action:

- **A cross-OS persona line** names what stays the host's on that pairing:
  installed fonts, and the kernel's own timing behaviour. For Windows-on-Linux
  the action is to install the font set, or to pass
  `--fingerprint-platform=linux`. Every cross-OS pairing gets a line; the
  host's own OS gets none.
- **An identity-rotation line** means this anchor has one measured member, so
  every launch on this host presents the same GPU strings regardless of seed.

The report names the font prerequisite but cannot confirm you have met it. It
does not read the filesystem, so nothing in it tells you whether the claimed
platform's faces are actually present. That half is yours:
[docs/FONTS.md](FONTS.md) says what to install and how to read which packs this
launch drew.

If the report looks right and the block persists, check the two things it does
not cover: whether the site needs WebRTC, and whether the site is timing WebGL
or hashing canvas bytes on a host that renders in software. A software backend
does not change what the identity claims; it does change render throughput and
per-pixel output. Both are in [docs/LIMITATIONS.md](LIMITATIONS.md).

## Pinning the GPU cluster

```sh
./chrome --fingerprint-anchor=macos-metal-apple-850a91233555
```

`--fingerprint-anchor` pins the measured GPU capability cluster instead of
letting the seed draw one. A drawn launch always takes its cluster from the
claimed platform, so the persona and the cluster agree by construction; a pin is
the only way to make them disagree. It is accepted rather than refused, because
an explicit request is worth honouring, and the launch records that it happened.

It is also the only way to ask for the software-rasteriser cluster
`linux-swiftshader-google-6922d61bab83`, which no seed draws on any host: pin it
by id, or state its strings with `--fingerprint-gpu-renderer` and
`--fingerprint-gpu-vendor`. Anchor ids are listed in
[corpus/anchors/README.md](../corpus/anchors/README.md).

## WebRTC

```sh
./chrome --fingerprint-webrtc-ip=203.0.113.7
```

`--fingerprint-webrtc-ip` takes an IP literal and replaces the address in the
host and srflx ICE candidates with it. It rewrites the candidate text and
nothing else: the packets still leave from wherever they were going to leave
from. There is no `auto` value; passing one puts the literal string `auto` in
the SDP.

```sh
./chrome --fingerprint-webrtc-udp=block
```

`--fingerprint-webrtc-udp` decides where the packets go.

| Value | Behaviour |
| --- | --- |
| absent | Automatic. Relay through the configured proxy in the address families that proxy can be reached in, go direct in both families when no proxy is configured, and offer WebRTC no family at all when the configured proxy cannot relay. |
| `direct` | Force direct UDP even under a proxy. An explicit opt-in to publishing the host's real address. |
| `block` | Never create a WebRTC UDP socket. Candidate gathering still completes, with no UDP candidate. |

The families are the decision, not the sockets. A relayed datagram is only ever
sent to the relay endpoint of its association, and the ICE candidate it produces
is that endpoint, so a family the proxy has no address in cannot produce a
candidate however many interfaces this host has in it. The browser resolves the
proxy once per network change and tells the renderer which families are left,
and the renderer creates ports only in those. Through an IPv4-only exit that
means one host candidate and one server-reflexive candidate, both IPv4, which is
what an IPv4-only desktop emits; through a dual-stack exit it means the usual
pair per family.

[docs/LIMITATIONS.md](LIMITATIONS.md) has what WebRTC does and does not hide.

## Readback noise

```sh
./chrome --fingerprint=12345 --fingerprint-noise
```

`--fingerprint-noise` is off by default and is the one switch in this file that
trades coherence away rather than buying it. With it, every page-visible canvas
and WebGL pixel readback comes back one step from the bytes this build
rendered. Without it, and under `--fingerprint=host`, and under a profile with
no identity to key on, the readback is byte-for-byte what stock Chromium of
this version produces.

What it changes, and nothing else:

| Route | Effect |
| --- | --- |
| `getImageData` | The returned `ImageData`, in all three pixel formats. |
| `toDataURL`, `toBlob`, `convertToBlob` | The raster handed to the encoder. The encoders themselves are untouched, so the byte stream is still a PNG, a JPEG or a WebP produced by this build's encoder from those pixels. |
| `transferToImageBitmap`, `createImageBitmap` of a canvas | The structured clone of the bitmap. Drawing it back into a canvas and reading that is perturbed once, at the read. |
| WebGL and WebGL2 `readPixels` into a typed array | The caller's view, in every format and type pair `readPixels` accepts, under any `PACK_*` state. |
| WebGL2 `readPixels` into a `PIXEL_PACK_BUFFER` | The bytes `getBufferSubData` hands back. The buffer itself is never written, because a GPU command may still read it. |

What it does not change: the canvas's backing store, a WebGL drawing buffer,
`captureStream` and the canvas-to-video frame path, audio, client rects,
`measureText`, WebGL parameters, or any image, video frame or `ImageData` that
did not come out of a canvas.

The perturbation is a function of the profile identity and the clean pixels,
and of nothing else — not a clock, not a call count, not a per-page token. So
the same seed gives the same bytes on the next launch and on another machine,
a different seed gives different bytes, two reads of one canvas agree, and
every route above agrees with every other for the same pixels. A pixel whose
3x3 neighbourhood is one colour is left exactly as it was, which makes a solid
fill byte-exact and keeps the change to the edges a rasteriser signs its name
on. Alpha is never moved.

The tamper checks a detector actually runs, and how this answers them:

| Check | Result |
| --- | --- |
| render the same scene twice, compare | identical |
| read the same canvas twice, compare | identical |
| fill a known solid colour, compare to the expected bytes | exact |
| draw a server-chosen pixel grid, read it back, compare | exact where the grid is flat, off by at most one per channel at its edges |
| read through two routes and compare | agrees |
| return across sessions with the same profile | identical |
| average many reads to recover the true pixel | recovers nothing; there is one answer, not a distribution |
| draw at 1x, draw the same thing at 8x, downsample and compare | **detects it.** A perturbation keyed on the pixel and its neighbourhood does not survive being averaged with 63 neighbours, so the scaled-down image disagrees with the small one. Stock Chromium's own scaling is not exact either, but it is not exact in a different way. |

That last row is the cost, stated rather than papered over: this is an
intervention, and a detector that looks for an intervention can find it. The
trade it buys is unlinkability — two profiles render the same page to different
bytes, so a canvas hash stops joining their sessions. Whether that is worth a
detectable intervention depends on what is reading the page, which is why the
switch exists rather than the behaviour.

[docs/LIMITATIONS.md](LIMITATIONS.md) has the same trade from the other side,
and `docs/METHODOLOGY.md` §5 records why it is an exception to a rule this
project otherwise keeps.

## Supplying a profile directly

```sh
./chrome --apostate-profile="$(base64 < profile.json | tr -d '\n')"
```

`--apostate-profile` takes a base64-encoded profile JSON object and skips
composition entirely. Coherence and servability are then yours to get right, not
the catalogue's. A value that does not decode refuses the launch and exits
non-zero rather than composing something else, and the message names the form
above, because passing a path or raw JSON used to log one line and then present
a different device.

Validate the file first:

```sh
python3 scripts/validate-profile.py profile.json
```

`resources/fingerprints/` holds valid example profiles that load as they are.
The field contract is [docs/PROFILE_SPEC.md](PROFILE_SPEC.md) and the schema is
[config/profile.schema.json](../config/profile.schema.json).

## Precedence

Strongest first:

```text
--apostate-profile        a profile you supply; nothing is recomposed
--fingerprint=host        no composition at all; every surface is the host's
--fingerprint-*           one field each, on top of the seed's values
--fingerprint=<seed>      the identity that seed selects
the profile identity      the identity bound to the --user-data-dir you named
fresh OS entropy          the default when you named no --user-data-dir
```

### How long an identity lasts

Three cases, and the report tells you which one you are in.

| You launched with | The identity is | It lasts |
| --- | --- | --- |
| `--fingerprint=<seed>` | the one that seed selects | forever, on any machine, in any container — the seed is the identity |
| `--user-data-dir=DIR` and no `--fingerprint` | bound to `DIR` | until `DIR` is deleted; it survives renaming and moving `DIR` |
| neither | drawn fresh from OS entropy | this launch only, recorded nowhere |

A named `--user-data-dir` keeps its identity because it keeps everything else.
That directory holds cookies, localStorage and logged-in sessions, so a
different GPU, core count, installed memory and panel on every launch shows a
site one account whose hardware changes between visits — which no real machine
does, and which is a stronger signal than any single fingerprint value.

If you want a fresh machine every run and have been reusing one directory out
of habit, say so, because you will otherwise get the same machine every time.
Drop `--user-data-dir` — that is the ephemeral default, and the right answer
if you did not need the cookies either — or give each run its own directory,
or delete `DIR/apostate/identity` between runs. Neither choice is the correct
one in general; they are different sessions and the report tells you which
one you are running.

The identity lives in `DIR/apostate/identity`: one seed and one newline. Read
it to pin the same machine elsewhere, write it to choose one by hand, delete it
to take a new machine on the next launch.

```sh
cat ~/profiles/one/apostate/identity
# 4f3c...  -> pass as --fingerprint=4f3c... anywhere
```

Copying a profile directory clones its identity, deliberately: a copy of a
profile is a copy of its logged-in sessions, and an identity that changed under
them would defeat the point. Two copies used at once look like one machine in
two places, because that is what they are. Use `--fingerprint=<seed>` when you
want the same profile on a different machine.

Incognito and guest windows share the browser process, so they share its
identity. A window cannot report different hardware from the browser running
it.

If the directory cannot be written — read-only, full, or on a medium that
refuses — the launch still gets a coherent device, but an ephemeral one, and
says so in its limitations. If the identity file is unreadable it is left
alone and the launch is ephemeral; if it is present but is not a seed, it is
replaced once and that is reported too.

## The proxy

```sh
./chrome --proxy-server=socks5://user:pass@proxy.example:1080
```

The credential goes in the URL, which is the syntax every other proxy tool
accepts. It used to fail instantly with `net::ERR_NO_SUPPORTED_PROXIES` on
every request, because Chromium's proxy URI parser has no userinfo concept and
one `@` makes the whole chain unparseable, and this document claimed otherwise.
It works now.

The credential never reaches Chromium's proxy configuration. It is taken off
the value before anything parses it, held in memory for the launch, and given
only to the network stack; Chromium is handed the `scheme://host:port` it has
always wanted. That is not cosmetic — proxy identity is serialized into
NetLog, net-export, socket-pool group keys, session and cache keys, error
strings and telemetry, and a credential inside it would be in all of them.

Percent-encode with the usual URL rules, and nothing more than the usual:

| in the password | write | why |
| --- | --- | --- |
| `p@ss` | `p@ss` or `p%40ss` | the **last** `@` separates the credential, so a literal one needs no escape |
| `p:ss` | `p:ss` or `p%3Ass` | the **first** `:` separates username from password, so a later one needs none either |
| `p/ss` | `p%2Fss` | |
| `p%ss` | `p%25ss` | |
| a space | `%20` | `+` stays a literal `+`, as in any URL path |

A malformed escape refuses the launch instead of being read as a literal `%`,
so `%zz` is an error rather than a password you did not type. So is a decoded
credential over 4096 bytes, one containing a NUL, or one that is not valid
UTF-8: nothing is truncated, because half a password authenticates nothing
while looking like it should.

Schemes that take a credential: `http`, `https`, `socks`, `socks4`, `socks5`,
and the scheme-less `host:port` form, which Chromium reads as HTTP. A
credential on `direct://` or `quic://` refuses the launch — the first has no
peer to authenticate to and the second is Chromium's own MASQUE path, so in
both a credential is a typo, and dropping it silently would authenticate
nothing while reporting success. `socks5h://` is not a Chromium proxy scheme
at all; use `socks5://`, which already resolves the destination proxy-side.

The full proxy-rules grammar works, not just a single URL, so
`http=http://user:pass@a:8080;https=http://user:pass@b:8443` is fine. Two
proxies naming *different* credentials refuses the launch: one credential is
held for the whole launch, and applying one proxy's to another is not something
to do quietly.

**Against the profile envelope.** An `--apostate-profile` envelope can also
carry a `proxy_credentials` block, which is how the Node package sends one.
There is no precedence between them: supplying both refuses the launch. They
are two sources of truth for one store, so picking either would authenticate
with a credential you did not name. Use the URL by hand and the envelope from
the packages, and never both in one launch.

The two channels are otherwise identical, and that is worth saying rather than
leaving you to infer it. Both end up in the same in-memory store, both are read
by the same code, and neither is a different kind of credential once it is
there. So the lifetime is the same — the launch, and no longer; nothing is
written to disk by either — and the reuse is the same: against an HTTP or HTTPS
proxy, answering a `407` puts the entry in Chromium's in-memory `HttpAuthCache`
by way of `HttpAuthController::ResetAuth`, which is what stops the next request
paying for another challenge. That cache is per network context and is never
persisted, so it does not outlive the browser. Choose the channel that suits
how you launch, not for any difference in what happens afterwards.

**Where the credential is not.** Not in `ProxyServer`, `ProxyChain`, NetLog,
net-export, socket-pool group keys, session or cache keys, error strings, crash
keys or `--fingerprint-explain`. Six things print the browser's command line
verbatim, and after the credential is lifted off it there is nothing on it for
any of them to print:

| surface | what prints it |
| --- | --- |
| `chrome://version` | the command-line row |
| `chrome://gpu` | the same row in its client-info block |
| `chrome://net-export` | the capture's `clientInfo.command_line` |
| `--log-net-log` | the same field, written at startup |
| DevTools `SystemInfo.getInfo` | the `commandLine` field it returns |
| `chrome://tracing` | Perfetto metadata, unless privacy filtering is on |

An envelope credential is lifted the same way, which is a change — before this
it was visible in `chrome://version`, base64-encoded, for the whole session.

**Where it still is, and why that is not new.** A child process's argv, inside
the profile envelope, which is what `ps` shows for the renderers and the
network process. This is inherent to the design rather than something accepting
a credential in the URL introduced: the code that spends the credential — the
one that answers a proxy's `407`, and the one that sends the RFC 1929
sub-negotiation — runs in the network service, not in the browser, and the
envelope is the only channel a child process has. `--proxy-server` is not
copied to children at all. It has worked this way since the envelope existed;
what changed is that the browser's own command line is now clean too.

Two consequences worth acting on.

**A trace is a credential.** `chrome://tracing` and any Perfetto capture taken
without privacy filtering record *every* process's command line, so a trace
taken while a proxy is configured contains the base64 envelope, and base64 is
not encryption. So "send me a trace so I can look at this" is a request to send
a proxy password. Rotate the credential, or capture with privacy filtering on,
or take the trace with no proxy configured. This is reported rather than
mitigated: the field is there to record the command line, and a browser that
quietly wrote a different command line into a diagnostic than the one it was
launched with would be a worse trade.

**A crash report is not.** `--apostate-profile` is on the crash-key ignore
list, so a crash report from a child does not carry the envelope. Without that
it would: the 64-byte crash-key bound cuts a base64 payload only three bytes
short of the username, which is arithmetic and not a guarantee.

And the obvious one: a machine you share with users you do not trust was never
a place to put a proxy password on a command line.

To confirm a live proxy end to end, including that the exit is the proxy's:

```sh
./chrome --headless --proxy-server=socks5://user:pass@proxy.example:1080 \
  --dump-dom https://ip.decodo.com/json
```

`proxy.ip` is the exit address, `isp.isp` the exit network, `country.name` and
`city.time_zone` the geography — which is also how you check that a
`--fingerprint-timezone` you passed agrees with where the traffic actually
leaves from. A credential-free `--proxy-server` against a proxy that requires
one fails the connection outright rather than falling back to the direct
network, so a result at all is proof the credential was accepted, and the
address in it is proof the proxy's network fetched it. Compare against the same
URL with no proxy: a different `proxy.ip` is the whole point.

## Chromium flags worth knowing

These are upstream switches that interact with the identity, unchanged except
where a row says otherwise.

| Flag | Why it matters |
| --- | --- |
| `--user-data-dir=DIR` | Keeps cookies, storage and history — and the identity, which is bound to `DIR` and stable across launches. See [How long an identity lasts](#how-long-an-identity-lasts). |
| `--proxy-server=URL` | HTTP, HTTPS, SOCKS4 and SOCKS5. UDP over SOCKS5 UDP ASSOCIATE carries proxied QUIC and HTTP/3. Not unchanged: a credential in the URL is accepted, which upstream refuses. [The proxy](#the-proxy) is the detail. |
| `--use-angle=BACKEND` | Selects the backend the GPU process actually renders through. It no longer decides which capability cluster is drawn — the claimed platform does that — so what it changes is throughput and rendered bytes, not the identity. |
| `--headless` | Supported, and it does not imply software rendering. On a Mac this binary selects ANGLE/Metal in every default configuration including `--headless=new`. A headless Linux server with no GPU is the primary deployment and needs no further switch. |
| `--lang=TAG` | **Inert under a composed profile.** The composed application locale resolves ahead of it on every platform, because a UI language that disagrees with the persona's locale is a contradiction a page reads in one `toLocaleString` call. `--fingerprint-explain` names it in the limitations when it had no effect. Use `--fingerprint-locale` instead: that moves the UI locale, `Intl`, the calendar and the `Accept-Language` list together. Still honoured under `--fingerprint=host`, which composes nothing. |
| `--remote-debugging-pipe` | Opens no socket. Use this rather than a port: a page in the local or private address space can detect an open debugging port. Playwright uses the pipe by default; Puppeteer defaults to a port. |
| `--window-size=W,H` | Sets the window, not the viewport. The viewport is smaller by the browser chrome and it settles shortly after load rather than immediately. |

That last row matters if you assert on it. Reading `window.innerHeight`,
`visualViewport.height` or `documentElement.clientHeight` in the first script of
a page gives a provisional number that is corrected within about a second: with
`--window-size=1280,800`, twelve launches out of twelve reported 684, 685 or 692
first and 657 once settled, and the early value varied run to run while the
settled one did not. So read viewport height after load, and treat an early
reading as noise if you are recording a fingerprint. This is ordinary browser
behaviour and a real Chrome restoring a window does the same thing.

Two to avoid:

`--disable-gpu` makes Chromium's GPU info report the literal strings `Disabled`
for vendor, renderer and version, because `CollectGraphicsInfoGL` returns early
without calling `glGetString`. WebGL reaches the driver through the command
buffer and keeps reporting a real ANGLE string, so the two contradict each
other. This has already produced a wrong result here once.

It no longer affects the identity either way. Which capability cluster a launch
serves comes from the claimed platform, so forcing the software rasteriser
changes what the machine renders — throughput and pixels — and changes nothing
about what it claims. There is no longer a reason to reach for this switch.

`--no-sandbox` is a deviation from a normal launch in its own right. It is
unavoidable as root, so run as a normal user instead.

## The packages

The Python and Node packages take the same values through named arguments, and
anything else through `args`:

```python
from apostate import launch

browser = launch(
    fingerprint=12345,
    fingerprint_platform="windows",
    proxy="http://user:pass@proxy:8080",
    geoip=True,
)
```

```javascript
import { launch } from "@heretic-tech/apostate";

const browser = await launch({
  fingerprint: 12345,
  fingerprintPlatform: "windows",
  proxy: "http://user:pass@proxy:8080",
  geoip: true,
});
```

`geoip: true` resolves the locale and timezone from the network exit before the
browser starts, through the proxy when one is configured, and the result travels
as `--fingerprint-locale` and `--fingerprint-timezone` rather than as a profile
envelope, so asking for a locale does not cost you the composed fingerprint.

A failed or partial lookup never invents a locale. It leaves off the switch for
each field it could not answer for, so the host's own value applies there — not
a drawn one, because the seed does not reach that surface. The consequence is
worth being explicit about: behind a proxy the host's zone is the host's and not
the exit's, so `geoip` is best-effort geo-matching and passing `locale` and
`timezone` explicitly is the way to guarantee it.

Both packages behave identically here and it is exercised. Neither raises from
`launch()` and neither substitutes a value: they warn into the resolution's own
warnings list, which a caller can read unlike a console line, and send no
override. `resolve_geoip()` called directly still raises, and so does a
non-positive `geoip_timeout`, because that is a caller bug rather than a network
failure. A Python launch that used to raise `GeoIPError` on a failed lookup now
succeeds, so a caller who relied on that exception to abort reads the warnings
instead.

Three details that follow: a partial answer keeps the field it carries, so a
timezone with no locale sets `--fingerprint-timezone` alone; a country code
resolves through this project's own table, so a German exit gives `de-DE` rather
than `en-DE`; and a provider timezone that is not an IANA identifier, such as
`+02:00`, is treated as unresolved rather than passed to the switch.

A `proxy` passed to either package is split the same way the browser splits
`--proxy-server` by hand: the endpoint goes on the command line and the
credential travels in the profile envelope. [The proxy](#the-proxy) has what
happens to it after that, and it is the same either way.

`humanize: true` is rejected rather than accepted as a no-op. There is no
synthetic input behaviour in this fork.

[docs/PROFILE_SPEC.md](PROFILE_SPEC.md) documents the full launch configuration
and the package entry points.
