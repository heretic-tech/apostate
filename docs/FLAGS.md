# Flag reference

Every switch the browser accepts for fingerprint control, what it takes, and
what it changes. None is required. A launch with no flags composes a complete
device and presents it.

Command-line switches are not readable from a page. Passing one adds no
observable.

## The seed

```sh
./chrome --fingerprint=12345
```

`--fingerprint` selects the whole identity. The same seed produces the same
device on any host that can serve it, on every launch, with nothing stored on
disk. Without it, a launch that names a `--user-data-dir` presents the
identity bound to that directory, and a launch that names neither draws a
fresh seed. [How long an identity lasts](#how-long-an-identity-lasts) has the
rule.

The value is any printable ASCII up to 512 bytes. An empty, over-long or
non-printable value refuses the launch on stderr and exits non-zero. There is
no fallback, because a typo that presented the host's real machine would look
like success until a site had already clustered it.

The seed feeds a SHA-256 derivation over the profile schema version, the
catalogue version, the Chromium build, the persona and the seed itself. A new
browser build or catalogue therefore changes what a seed selects. A seed names
a device drawn from one specific table, and remapping it onto a new table
without saying so would be worse than a new identity.

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

Six spellings mean this, case-insensitive: `host`, `off`, `false`, `0`,
`disable` and `disabled`. `host` is canonical.

Nothing is composed, so there is no profile for a persona, an anchor pin or a
per-field override to land on. Combining one of those with host mode refuses
the launch. `--fingerprint-explain` still works.

Use it to tell whether a problem is this browser or the environment.

## The persona

```sh
./chrome --fingerprint=12345 --fingerprint-platform=windows
```

`--fingerprint-platform` takes `windows`, `macos` or `linux`. It sets
`navigator.platform`, the User-Agent, the Client Hints platform and version,
the OS release, the font set, the voice table, the screen geometry pool, the
window chrome deltas, the hardware buckets and the GPU.

The claimed platform selects the GPU capability cluster on every host.
`windows` draws a Direct3D 11 cluster, `macos` an Apple Metal one and `linux`
an NVIDIA Vulkan one, whatever the machine underneath runs.

The default is host-dependent:

| Host | Default persona |
| --- | --- |
| macOS | `macos` |
| Windows | `windows` |
| Linux | `windows` |

A Linux host claims Windows unless told otherwise. This is the only default
that is not the host's own OS. Its cost is the Windows font set. Install it
([docs/FONTS.md](FONTS.md)) or pass `--fingerprint-platform=linux`. The
report prints the pairing as a limitation on every such launch.

[docs/LIMITATIONS.md](LIMITATIONS.md) has the per-persona cluster table and
the cross-OS risk ordering.

## Per-field overrides

An explicit switch sets one field and the seed fills in the rest, so the
result is one device with a correction. The two locale switches are the
exception: the seed fills in nothing for them, and what they do not set is
served by the host.

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

Every refusal writes to stderr and exits non-zero. Nothing is silently
ignored.

The two GPU strings narrow the choice before the seed draws. The limits,
extensions, shader precisions and WebGPU adapter all come from the anchor
those strings were measured on, so a renderer written over a finished profile
would sit on a capability table from different silicon. A name no anchor
measured is refused, with the servable identities listed.

`--fingerprint-hardware-concurrency` is refused above the host's real logical
core count, and `--fingerprint-device-memory` above the host's installed
memory. A page can measure parallel throughput and can allocate until
allocation fails, so a claim above the machine is testable and a claim below
it is not.

The screen switches apply before the available rectangle is derived, so
`availWidth`, `availHeight`, `availLeft` and `availTop` follow from the same
desktop furniture the seed drew. A size smaller than a `--window-size` the
same launch asked for is refused, and so is one the drawn insets cannot fit
inside. There is no display at composition time, so `--window-size` is the
only host bound checked.

`--fingerprint-locale` and `--fingerprint-timezone` each set exactly the field
named. `--fingerprint-timezone=Europe/Berlin` alone gives a Berlin zone over
the host's own language list, which is an ordinary combination on a real
machine. Resolving a locale to a timezone would invent a zone the launcher
never measured. To pin both, pass both.

`--fingerprint-locale` applies where the locale surface resolves, because the
speech-voice table is keyed on the resolved Accept-Language list. A launch
that names no locale keys onto the empty language set, which carries no
speech section, so `speechSynthesis` reports the host's real providers.

Neither locale switch moves the keyboard layout. A machine's layout follows
its physical keyboard, and a locale that does not match it is ordinary, so
the map stays the host's unless a capture-derived profile replays one.

A value that is not one of the catalogue's own buckets is honoured and
reported. `--fingerprint-explain` names the field, the value, the command line
as the deciding layer, and the drawn value it displaced, and notes that a
hand-chosen value has no prevalence data behind it and may be more distinctive
than a drawn one. The two locale switches displace nothing and carry no such
note. Their rows say the value came from the command line, which is also
where the Python and Node packages put their GeoIP answer, and when a field is
unset, that the host serves it and which switch would pin it.

## Inspecting a launch

```sh
./chrome --fingerprint=12345 --fingerprint-explain
```

Prints the composition report to stdout and exits without opening a window.
The report has three parts: what the launch resolved, one row per surface,
and the limitations that apply on this host.

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

The `layer` column says who decided a surface. `dispersion` is a weighted
draw from an option table that a different seed can move. `anchor` is a
measured capability cluster taken whole. `command-line` is an override the
launch named. `host-inherited` is a surface left alone. `composed-default` is
neither the operator's choice nor the host's value. `platform-projection` is a
value the claimed OS determines outright, with no table behind it; text
rasterisation is the only surface on that layer, because Chromium computes it
per platform.

The three locale rows are the ones to check against your exit IP.

`locale.application` is the application locale. Read it first, because the
other two resolve against it. It decides `Intl.DateTimeFormat`, `NumberFormat`
and `Collator`, the calendar and hour cycle they report, the default
`Accept-Language` list, collation order, locale-dependent font fallback, and
the rendered width of an `<input type=date>`, which a page can read with no
`Intl` call at all.

It is pinned in two places. `l10n_util` resolves it ahead of every platform
candidate, ahead of glib's `LANGUAGE`, `LC_*` and `LANG` on Linux, of
`NSBundle`'s `preferredLocalizations` on macOS, and of both `--lang` and the
Windows preferred-UI-language list. `LANGUAGE`, `LC_ALL`, `LC_MESSAGES` and
`LANG` are also written into the browser's environment before the first child
process is forked, so the C library agrees with ICU and every child agrees
with the browser.

`command-line` on that row means `--fingerprint-locale` named it, which is
also how a GeoIP answer from the packages arrives. `composed-default` means
nothing did, so `en-US` is presented rather than the operator's shell or
system language. It is never `host-inherited` except under
`--fingerprint=host`, which reports no surfaces at all.

`locale.accept_languages` is `command-line` when a switch named the list, and
`composed-default` otherwise, in which case the list a page reads is the one
the application locale's resource bundle declares. `locale.timezone` is the
one of the three that can be `host-inherited`, because ICU's zone is a
separate producer from the application locale.

The `evidence` column says where each value came from. `physical-ground-truth`
is a measurement from a real device. `catalogue-value` is authored from
platform release history. A surface left to the host says so.

The `seed source` line names which of the three lifetimes this launch is in,
and `reproduce with` carries the exact argument that recreates it. A launch
with no `--fingerprint` and no `--user-data-dir` reports the seed it drew, and
that line is the only record of it:

```text
  seed                616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
  seed source         drawn from OS entropy for this launch only (ephemeral)
  reproduce with      --fingerprint=616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
```

A launch that named a `--user-data-dir` reports the file the identity came
from, and whether this launch created it. `(created this launch)` on a
profile you believed was established is the diagnosis:

```text
  seed                4f3c8a1e09b7d2650c3ab8f41d7e5920ac6b13f8e04d7a29bb5c1e6370d8f425
  seed source         this profile's identity file (stable for this --user-data-dir)
  identity file       /home/you/profiles/one/apostate/identity (read from disk)
  reproduce with      --fingerprint=4f3c8a1e09b7d2650c3ab8f41d7e5920ac6b13f8e04d7a29bb5c1e6370d8f425
```

The report goes to stdout and is never exposed to a page.

## Troubleshooting a block

Run `--fingerprint-explain` first. Most blocks are named in its `limitations`
block.

On a Linux host with the default persona, the report prints the pairing as a
limitation with its remedy:

```text
limitations
  - the persona is windows on a linux host, which is this project's default
    pairing there and its least bad cross-OS one: the GPU cluster follows the
    persona, but the Windows font set does not install itself, and a Windows
    persona missing Windows faces is measurable in text metrics -- install the
    full set (docs/FONTS.md) or pass --fingerprint-platform=linux to compose
    the host's own OS
```

A cross-OS persona line names what stays the host's on that pairing:
installed fonts, and the kernel's timing behaviour. For Windows-on-Linux the
action is to install the font set or pass `--fingerprint-platform=linux`.
Every cross-OS pairing gets a line; the host's own OS gets none.

An identity-rotation line means this anchor has one measured member, so every
launch on this host presents the same GPU strings regardless of seed.

The report names the font prerequisite but does not read the filesystem, so
it cannot confirm the claimed platform's faces are present.
[docs/FONTS.md](FONTS.md) says what to install and how to check.

If the report looks right and the block persists, check two things it does
not cover: whether the site needs WebRTC, and whether the site is timing
WebGL or hashing canvas bytes on a host that renders in software. A software
backend changes render throughput and per-pixel output, not what the identity
claims. Both are in [docs/LIMITATIONS.md](LIMITATIONS.md).

## Pinning the GPU cluster

```sh
./chrome --fingerprint-anchor=macos-metal-apple-850a91233555
```

`--fingerprint-anchor` pins the measured GPU capability cluster instead of
letting the seed draw one. A drawn launch always takes its cluster from the
claimed platform, so persona and cluster agree by construction; a pin is the
only way to make them disagree. It is accepted, and the launch records that it
happened.

It is also the only way to get the software-rasteriser cluster
`linux-swiftshader-google-6922d61bab83`, which no seed draws on any host. Pin
it by id, or state its strings with `--fingerprint-gpu-renderer` and
`--fingerprint-gpu-vendor`. Anchor ids are listed in
[corpus/anchors/README.md](../corpus/anchors/README.md).

## WebRTC

```sh
./chrome --fingerprint-webrtc-ip=203.0.113.7
```

`--fingerprint-webrtc-ip` takes an IP literal and replaces the address in the
host and srflx ICE candidates. It rewrites the candidate text and nothing
else; the packets still leave from wherever they were going to leave from.
There is no `auto` value. Passing one puts the literal string `auto` in the
SDP.

```sh
./chrome --fingerprint-webrtc-udp=block
```

`--fingerprint-webrtc-udp` decides where the packets go.

| Value | Behaviour |
| --- | --- |
| absent | Automatic. Relay through the configured proxy in the address families that proxy can be reached in; go direct in both families with no proxy; offer WebRTC no family at all under a proxy that cannot relay. |
| `direct` | Force direct UDP even under a proxy. An explicit opt-in to publishing the host's real address. |
| `block` | Never create a WebRTC UDP socket. Candidate gathering still completes, with no UDP candidate. |

A relayed datagram is only ever sent to its association's relay endpoint, and
the ICE candidate it produces is that endpoint, so a family the proxy has no
address in cannot produce a candidate however many interfaces the host has in
it. The browser resolves the proxy once per network change and tells the
renderer which families are left, and the renderer creates ports only in
those. Through an IPv4-only exit that is one host candidate and one
server-reflexive candidate, both IPv4, as an IPv4-only desktop emits. Through
a dual-stack exit it is the usual pair per family.

[docs/LIMITATIONS.md](LIMITATIONS.md) has the measurements.

## Readback noise

```sh
./chrome --fingerprint=12345 --fingerprint-noise
```

`--fingerprint-noise` is off by default, and a composed identity does not need
it to draw its own canvas: canvas text is rendered at a profile-derived
sub-pixel phase, always, which is described in
[docs/LIMITATIONS.md](LIMITATIONS.md). What the switch adds is coverage of
what that does not reach, a canvas with no text and a WebGL readback, and it
buys that coverage at a price a page can read.

Measured on the shipped binary: with the switch on, a canvas exported to a
data URL, imported and exported again drifts by one step every trip, because
each egress route applies the policy once more; and a linear gradient the page
specified analytically comes back with 32 of its 255 steps going backwards,
which is not something hardware does. Both checks are a few lines of
JavaScript and need no reference sample. Use the switch when unlinkability on
a text-free canvas matters more than those two answers.

With it, every page-visible canvas and WebGL pixel readback comes back one
step from the bytes this build rendered. Without it, under
`--fingerprint=host`, and under a profile with no identity to key on, the
readback is byte-for-byte what stock Chromium of this version produces.

The switch is presence-gated: it takes no value, and any value it is given is
ignored. `--fingerprint-noise=false` therefore turns noise **on**, which is
the opposite of what it reads like. Omitting the switch is how you turn it
off. Both packages refuse an off-looking value rather than let it through.

What it changes:

| Route | Effect |
| --- | --- |
| `getImageData` | The returned `ImageData`, in all three pixel formats. |
| `toDataURL`, `toBlob`, `convertToBlob` | The raster handed to the encoder. The encoders are untouched, so the byte stream is still a PNG, JPEG or WebP produced by this build's encoder from those pixels. |
| `transferToImageBitmap`, `createImageBitmap` of a canvas | The structured clone of the bitmap. Drawing it back into a canvas and reading that perturbs once, at the read. |
| WebGL and WebGL2 `readPixels` into a typed array | The caller's view, in every format and type pair `readPixels` accepts, under any `PACK_*` state. |
| WebGL2 `readPixels` into a `PIXEL_PACK_BUFFER` | The bytes `getBufferSubData` returns. The buffer itself is never written, because a GPU command may still read it. |

What it does not change: the canvas's backing store, a WebGL drawing buffer,
`captureStream` and the canvas-to-video frame path, audio, client rects,
`measureText`, WebGL parameters, and any image, video frame or `ImageData`
that did not come out of a canvas.

`captureStream` carries clean pixels, but reading them back needs a canvas,
and that read perturbs them exactly as it perturbs the source canvas, so the
two agree. The gap is reachable only through an egress that never touches a
canvas: `MediaRecorder`, or a WebRTC track, whose bytes leave the page and can
be compared against a `toDataURL` of the same canvas elsewhere.

The perturbation is a function of the profile identity and the clean pixels,
and of nothing else. The same seed gives the same bytes on the next launch and
on another machine, a different seed gives different bytes, two reads of one
canvas agree, and every route above agrees with every other for the same
pixels. A pixel whose 3x3 neighbourhood is one colour is left as it was, so a
solid fill is byte-exact and the change stays on the edges. Alpha is never
moved.

"One step" is one unit of what the canvas holds. The step is decided from the
colour rather than the bytes, because a canvas is read out through more than
one memory layout and keying on the layout would make two routes disagree
about one image. It is applied to the pixels the canvas holds rather than to
a converted copy, so every route's copy is produced from the same perturbed
pixels by the browser's own conversion. Measured, `getImageData` and a decoded
`toDataURL` of one canvas return identical bytes, as they do in stock
Chromium.

A canvas stores colour multiplied by alpha and `getImageData` divides that
back out. On an opaque pixel the two are the same and one step is one unit of
the value you read. On a translucent pixel the canvas has less resolution than
the value you read, so the step becomes zero or one in the store and reads
back as two units at half alpha, more as alpha approaches zero. What a page
sees composited moves by one at most.

The tamper checks a detector runs, and the result under this switch:

| Check | Result |
| --- | --- |
| render the same scene twice, compare | identical |
| read the same canvas twice, compare | identical |
| fill a known solid colour, compare to the expected bytes | exact |
| draw a server-chosen pixel grid, read it back, compare | exact where the grid is flat, off by at most one per channel at its edges |
| read through two routes and compare | agrees |
| return across sessions with the same profile | identical |
| average many reads to recover the true pixel | recovers nothing; there is one answer |
| draw at 1x, draw the same thing at 8x, downsample and compare | detects it. A perturbation keyed on a pixel and its neighbourhood does not survive averaging with 63 neighbours, so the scaled-down image disagrees with the small one. |

That last row is the cost. A detector that looks for an intervention can find
this one. What it buys is unlinkability: two profiles render the same page to
different bytes, so a canvas hash stops joining their sessions. Whether that
trade is worth it depends on what is reading the page, which is why it is a
switch.

`docs/METHODOLOGY.md` section 5 records why this is an exception to a rule the
project otherwise keeps.

## Supplying a profile directly

```sh
./chrome --apostate-profile="$(base64 < profile.json | tr -d '\n')"
```

`--apostate-profile` takes a base64-encoded profile JSON object and skips
composition. Coherence and servability are then yours. A value that does not
decode refuses the launch and exits non-zero, and the message names the form
above.

Validate the file first:

```sh
python3 scripts/validate-profile.py profile.json
```

`resources/fingerprints/` holds valid example profiles. The field contract is
[docs/PROFILE_SPEC.md](PROFILE_SPEC.md) and the schema is
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

Three cases. The report says which one you are in.

| You launched with | The identity is | It lasts |
| --- | --- | --- |
| `--fingerprint=<seed>` | the one that seed selects | forever, on any machine |
| `--user-data-dir=DIR` and no `--fingerprint` | bound to `DIR` | until `DIR` is deleted; renaming or moving it changes nothing |
| neither | drawn from OS entropy | this launch only, recorded nowhere |

A named `--user-data-dir` keeps its identity because it keeps everything
else. The directory holds cookies, localStorage and logged-in sessions, and a
different GPU, core count and panel on every launch would show a site one
account whose hardware changes between visits.

If you want a fresh machine every run and have been reusing one directory,
drop `--user-data-dir`, give each run its own directory, or delete
`DIR/apostate/identity` between runs.

The identity lives in `DIR/apostate/identity`: one seed and one newline. Read
it to pin the same machine elsewhere, write it to choose one by hand, delete
it to take a new machine on the next launch.

```sh
cat ~/profiles/one/apostate/identity
# 4f3c...  -> pass as --fingerprint=4f3c... anywhere
```

Copying a profile directory copies its identity. Two copies used at once look
like one machine in two places. Use `--fingerprint=<seed>` when you want the
same machine on a different profile.

Incognito and guest windows share the browser process, so they share its
identity.

If the directory cannot be written, the launch still gets a coherent device,
but an ephemeral one, and says so in its limitations. An unreadable identity
file is left alone and the launch is ephemeral. A file that is present but not
a seed is replaced once, and that is reported too.

## The proxy

```sh
./chrome --proxy-server=socks5://user:pass@proxy.example:1080
```

The credential goes in the URL. Upstream Chromium rejects this with
`net::ERR_NO_SUPPORTED_PROXIES`, because its proxy URI parser has no userinfo
concept and one `@` makes the whole chain unparseable. Apostate takes the
credential off the value before anything parses it, holds it in memory for
the launch, and gives it only to the network stack. Chromium's proxy
configuration receives the `scheme://host:port` it expects. Proxy identity is
serialised into NetLog, net-export, socket-pool group keys, session and cache
keys, error strings and telemetry, and a credential left inside it would be in
all of them.

Percent-encode with the usual URL rules:

| in the password | write | why |
| --- | --- | --- |
| `p@ss` | `p@ss` or `p%40ss` | the last `@` separates the credential, so a literal one needs no escape |
| `p:ss` | `p:ss` or `p%3Ass` | the first `:` separates username from password, so a later one needs none |
| `p/ss` | `p%2Fss` | |
| `p%ss` | `p%25ss` | |
| a space | `%20` | `+` stays a literal `+`, as in any URL path |

A malformed escape such as `%zz` refuses the launch. So does a decoded
credential over 4096 bytes, one containing a NUL, or one that is not valid
UTF-8. Nothing is truncated.

Schemes that take a credential: `http`, `https`, `socks`, `socks4`, `socks5`,
and the scheme-less `host:port` form, which Chromium reads as HTTP. A
credential on `direct://` or `quic://` refuses the launch; the first has no
peer to authenticate to and the second is Chromium's MASQUE path. `socks5h://`
is not a Chromium proxy scheme. Use `socks5://`, which already resolves the
destination proxy-side.

The full proxy-rules grammar works, so
`http=http://user:pass@a:8080;https=http://user:pass@b:8443` is fine. Two
proxies naming different credentials refuses the launch, because one
credential is held for the whole launch.

An `--apostate-profile` envelope can also carry a `proxy_credentials` block,
which is how the Node package sends one. There is no precedence between the
two channels: supplying both refuses the launch. Use the URL by hand and the
envelope from the packages.

Both channels end in the same in-memory store, read by the same code. The
lifetime is the launch, and nothing is written to disk by either. Against an
HTTP or HTTPS proxy, answering a `407` puts the entry in Chromium's in-memory
`HttpAuthCache`, so the next request does not pay for another challenge. That
cache is per network context and never persisted.

Where the credential is not: `ProxyServer`, `ProxyChain`, NetLog, net-export,
socket-pool group keys, session or cache keys, error strings, crash keys or
`--fingerprint-explain`. Six places print the browser's command line
verbatim, and after the credential is lifted there is nothing on it for them
to print:

| surface | what prints it |
| --- | --- |
| `chrome://version` | the command-line row |
| `chrome://gpu` | the same row in its client-info block |
| `chrome://net-export` | the capture's `clientInfo.command_line` |
| `--log-net-log` | the same field, written at startup |
| DevTools `SystemInfo.getInfo` | the `commandLine` field it returns |
| `chrome://tracing` | Perfetto metadata, unless privacy filtering is on |

Where it still is: a child process's argv, inside the profile envelope, which
is what `ps` shows for the renderers and the network process. The code that
spends the credential, answering a proxy's `407` or sending the RFC 1929
sub-negotiation, runs in the network service, and the envelope is the only
channel a child process has. `--proxy-server` is not copied to children at
all.

Two consequences. A Perfetto trace taken without privacy filtering records
every process's command line, so a trace taken while a proxy is configured
contains the base64 envelope, and base64 is not encryption. Rotate the
credential, capture with privacy filtering on, or take the trace with no proxy
configured. A crash report does not carry it: `--apostate-profile` is on the
crash-key ignore list.

To confirm a live proxy end to end:

```sh
./chrome --headless --proxy-server=socks5://user:pass@proxy.example:1080 \
  --dump-dom https://ip.decodo.com/json
```

`proxy.ip` is the exit address, `isp.isp` the exit network, `country.name`
and `city.time_zone` the geography, which is also how to check that a
`--fingerprint-timezone` you passed agrees with where the traffic leaves
from. A credential-free `--proxy-server` against a proxy that requires one
fails the connection outright rather than falling back to the direct network,
so a result is proof the credential was accepted.

## Chromium flags worth knowing

Upstream switches that interact with the identity.

| Flag | Why it matters |
| --- | --- |
| `--user-data-dir=DIR` | Keeps cookies, storage, history, and the identity, which is bound to `DIR`. See [How long an identity lasts](#how-long-an-identity-lasts). |
| `--proxy-server=URL` | HTTP, HTTPS, SOCKS4 and SOCKS5. UDP over SOCKS5 UDP ASSOCIATE carries proxied QUIC and HTTP/3. A credential in the URL is accepted; see [The proxy](#the-proxy). |
| `--use-angle=BACKEND` | Selects the backend the GPU process renders through. It does not decide which capability cluster is drawn; the claimed platform does. It changes throughput and rendered bytes, not the identity. |
| `--headless` | Supported, and it does not imply software rendering. On a Mac this binary selects ANGLE/Metal in every default configuration including `--headless=new`. A headless Linux server with no GPU is the primary deployment and needs no further switch. |
| `--lang=TAG` | Inert under a composed profile. The composed application locale resolves ahead of it on every platform. `--fingerprint-explain` names it in the limitations when it had no effect. Use `--fingerprint-locale`, which moves the UI locale, `Intl`, the calendar and the `Accept-Language` list together. Still honoured under `--fingerprint=host`. |
| `--remote-debugging-pipe` | Opens no socket. A page in the local or private address space can detect an open debugging port. Playwright uses the pipe by default; Puppeteer defaults to a port. |
| `--window-size=W,H` | Sets the window, not the viewport. The viewport is smaller by the browser chrome and settles shortly after load. |

On that last row: `window.innerHeight`, `visualViewport.height` and
`documentElement.clientHeight` read in the first script of a page give a
provisional number that is corrected within about a second. With
`--window-size=1280,800`, twelve launches reported 684, 685 or 692 first and
657 once settled. Read viewport height after load. A real Chrome restoring a
window does the same thing.

Two to avoid. `--disable-gpu` makes Chromium's GPU info report the literal
string `Disabled` for vendor, renderer and version, because
`CollectGraphicsInfoGL` returns early without calling `glGetString`, while
WebGL keeps reporting a real ANGLE string through the command buffer. The two
contradict each other. It changes nothing about the identity either way,
since the cluster comes from the claimed platform, so there is no reason to
pass it. `--no-sandbox` is a deviation from a normal launch in its own right.
It is unavoidable as root, so run as a normal user.

## The packages

The Python and Node packages take the same values through named arguments,
and anything else through `args`:

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

`geoip: true` resolves the locale and timezone from the network exit before
the browser starts, through the proxy when one is configured. The result
travels as `--fingerprint-locale` and `--fingerprint-timezone` rather than as
a profile envelope, so asking for a locale does not cost you the composed
fingerprint.

A failed or partial lookup invents nothing. It leaves off the switch for each
field it could not answer, so the host's own value applies there. Behind a
proxy that is the wrong country, so `geoip` is best-effort and passing
`locale` and `timezone` explicitly is how to guarantee the match. Neither
package raises from `launch()` on a failed lookup; both add a warning to the
resolution's warnings list, which a caller can read. `resolve_geoip()` called
directly still raises, and so does a non-positive `geoip_timeout`.

A partial answer keeps the field it carries, so a timezone with no locale sets
`--fingerprint-timezone` alone. A country code resolves through this project's
own table, so a German exit gives `de-DE` rather than `en-DE`. A provider
timezone that is not an IANA identifier, such as `+02:00`, is treated as
unresolved.

A `proxy` passed to either package is split the same way the browser splits
`--proxy-server`: the endpoint goes on the command line and the credential
travels in the profile envelope. [The proxy](#the-proxy) has what happens
after that.

`humanize: true` is rejected rather than accepted as a no-op. There is no
synthetic input behaviour in this fork.

[docs/PROFILE_SPEC.md](PROFILE_SPEC.md) documents the full launch
configuration and the package entry points.
