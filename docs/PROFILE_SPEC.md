# Profile and launch specification

This document is the public contract for Apostate profiles and launches. The
machine-readable profile schema is [`config/profile.schema.json`](../config/profile.schema.json).
The schema is authoritative for accepted profile fields; this page defines how
those fields are selected, combined, and reported.
[`docs/FINGERPRINTS.md`](FINGERPRINTS.md) is the composition model that produces
them, and this page defers to it.

A profile is a set of native inputs for a browser process. It is not a
JavaScript wrapper, a collection of per-request overrides, or a claim that the
host is physically the device being presented.

## Versions and identity

The release baseline uses these version boundaries:

- package version: `0.3.0`
- Chromium version: `152.0.7977.83`
- profile catalogue version: `2`
- profile schema version: `3`

Catalogue version 2 replaced the fourteen-family catalogue with a composition
model. A profile is no longer selected from a list: it is composed from an
**anchor** (a measured GPU capability cluster, taken atomically) and the
**dispersion** axes under `resources/profiles/dispersion/`, each of which is an
enumerated table of options observed on real systems. The retired families and
their compatibility-acceptance record are gone, and `catalogue.json` records why
in `retired_model`.

A seed is a deterministic selector. It is not a per-call randomizer and does not
invent a physical machine. Composition uses this identity tuple:

```text
(profile_schema_version, catalogue_version, Chromium build,
 fingerprint_platform, fingerprint)
```

The tuple is hashed with SHA-256 under the domain string `apostate/fp/v1`. It
must not use language-runtime hash behavior, process-randomized hashes, or
unordered map iteration. Each axis then draws from a substream keyed by its own
label, so adding an axis or changing one axis's option list cannot shift any
other axis's choice. Weighted selection takes the high 64 bits of a draw as a
`u64` and picks index `(u64 * total_weight) >> 64` against cumulative weights in
file order: no modulo, no rejection loop, no floating point.

The same tuple always produces the same result; changing the schema, catalogue,
or browser build creates a new identity rather than silently remapping an
existing one.

There is no per-call fingerprint randomness. Repeated reads and fresh launches
with the same resolved profile are expected to be stable. Variation happens at
the profile-composition boundary.

## The default launch

A launch with no arguments composes a profile. Where the seed comes from
depends on whether the launch has a persistent profile directory:

| Launch | Seed source | Result |
| --- | --- | --- |
| no `--user-data-dir` | fresh OS entropy, per launch | a new device every launch |
| `--user-data-dir=DIR` | `DIR/apostate/identity`, minted on first use | the same device every launch of that directory |
| `--fingerprint=<seed>` | the argument | the same device anywhere, every launch, over either of the above |
| `--fingerprint=host` | none | no composition; the host's own values |

A persistent profile keeps one identity because that directory holds the
cookies and logged-in sessions a site associates with a machine, and a cookie
jar whose hardware changes between visits is a stronger signal than any single
value. The identity file is one seed and a newline, so it can be read and
passed as `--fingerprint=<seed>` to reproduce the machine elsewhere; copying
the directory copies the identity, which is intended; deleting the file mints
a new one. An explicit `--fingerprint` wins over the file and does not touch
it.

The practical consequence for automation: Playwright's
`launch_persistent_context` reuses one user-data directory by design, and that
reuse is what makes the identity stable, with no flag. A launcher that wants a
fresh device per run uses a fresh directory or no directory.

The profile is fully materialized before the first renderer starts, and nothing
inside the session varies.

## Canonical launch configuration

CLI, Python, and Node integrations share one logical configuration. The shared
wire representation uses snake_case:

```json
{
  "fingerprint": 12345,
  "fingerprint_platform": "windows",
  "profile": null,
  "locale": null,
  "timezone": null,
  "geoip": true,
  "proxy": null,
  "headless": true,
  "user_data_dir": null,
  "args": []
}
```

Field meanings:

| Field | Meaning |
| --- | --- |
| `fingerprint` | Seed for the deterministic compositor: an integer, or any printable ASCII up to 512 bytes. `null` requests the default, a fresh seed per launch. The literal `host`, or `off`, `false`, `0`, `disable`, `disabled`, turns composition off. |
| `fingerprint_platform` | Platform persona: `windows`, `macos` or `linux`. `null` requests the host-conditional default, which is the host's own OS on macOS and Windows and `windows` on Linux. It changes OS identity, client hints, fonts, voices, locale, screen geometry, hardware buckets and the GPU cluster; see below. |
| `profile` | Explicit profile file or inline profile object. `null` means no explicit profile. |
| `locale` | Explicit locale or Accept-Language policy. `null` means use the precedence rules below. |
| `timezone` | Explicit IANA timezone. `null` means use the precedence rules below. |
| `geoip` | When `true`, resolve locale and timezone from the observed network exit before launch. |
| `proxy` | Proxy URL and credentials, if any. Credentials are used for launch and are never written to diagnostics or logs. |
| `headless` | Whether Chromium is launched headless. |
| `user_data_dir` | Persistent profile directory for cookies, storage, history and, at `DIR/apostate/identity`, the seed the directory is bound to unless `--fingerprint` overrides it. A context option: `launch()` refuses it and `launch_persistent_context` takes it positionally. `null` selects the integration's temporary-directory policy. |
| `args` | Additional Chromium arguments. Integrations must preserve the canonical profile and localization semantics when adding them. |

Package APIs may expose idiomatic camelCase aliases, but aliases map to this
same configuration and must not create a second profile model. For example,
`fingerprintPlatform` maps to `fingerprint_platform` and `userDataDir` maps to
`user_data_dir`.

## Selection precedence

Profile selection is resolved in this order, from strongest to weakest:

```text
explicit profile file
  > inline profile object
  > host mode
  > per-field override switches
  > fingerprint seed plus platform persona
  > a fresh seed drawn for this launch
```

An explicit profile file is validated before launch and bypasses
composition. Its coherence and servability are the author's responsibility,
and the resolver reports that as a warning.

Host mode does not silently outrank the rows under it. Nothing is composed,
so there is no profile for a persona, a pinned anchor or a per-field override
to land on, and combining any of them with host mode refuses the launch on
stderr and exits non-zero. `--fingerprint-explain` still works. The packages
follow the same rule, emitting the per-field locale and timezone switches
only for a natively composed resolution and never for a host-inherited one,
because the binary would refuse the pairing.

Network localization has a separate precedence chain:

```text
explicit locale/timezone
  > GeoIP-derived locale/timezone
  > host values
```

There is no composed row in that chain. A seed cannot see the connection's
exit country, so a timezone drawn by seed would guarantee a mismatch; the
host's own zone matches a direct egress exactly and is at worst wrong the way
a traveller's is.

The chain is per field. A GeoIP lookup that resolves a timezone and no
country contributes the timezone and leaves the language list to the host. A
locale is inferred from the exit country through
`config/country-locales.json`, which both packages ship, so with no country
there is nothing to infer from. No field is ever filled in by something that
cannot answer for where the connection comes out.

A resolved locale or timezone travels as the per-field switches
`--fingerprint-locale` and `--fingerprint-timezone`, not as a profile
envelope, so asking for a locale does not cost the composed fingerprint.
`--apostate-profile` is reserved for a profile the caller authored. Host mode
uses the envelope, because host mode composes nothing either way.

With `geoip: true`, the lookup is performed through the configured proxy, or
through the direct network when no proxy is configured. The result is fixed
for the process before Chromium starts.

The lookup walks four independent sites in a fixed order over plain HTTP --
`ip-api.com/json/`, `ipinfo.io/json`, `ipwho.is/`, `ifconfig.co/json` -- and
stops at the first that answers with both a country and a timezone. Each is
attempted twice, with a fixed 0.5s pause between the two tries and a 5s
ceiling on each, and the whole walk is bounded by `geoip_timeout` /
`geoipTimeoutMs`, which defaults to 20s: no attempt begins that the budget
cannot hold, so a dead network costs a launch the budget rather than the sum
of every attempt it could have made. There is no jitter anywhere; two launches
configured alike take the same path. A site that answers with only half the
pair is kept and the walk continues, because there is no country-to-timezone
table in this project and a derived zone would be an invention. Both packages
carry the same list, order, counts and ceilings.

Behind a SOCKS proxy the endpoint's *name* is handed to the proxy for both the
`socks5://` and `socks5h://` spellings, so the lookup's own DNS query leaves
the exit's network and never this one. Resolving it locally used to pin the
tunnel to an address this host picked -- on a dual-stacked endpoint, the AAAA
record, which a residential exit with no IPv6 route refuses.

A failure is reported and nothing is invented. No timeout, unreachable
provider or malformed response produces a `UTC` or `en-US` the caller did not
ask for. Both packages proceed with no override for the field they could not
answer, so the host's value applies there, and record a warning a caller can
read: `diagnostics.warnings` in Node, reachable from a launched browser as
`browser.apostateDiagnostics.warnings`, and `LaunchPlan.diagnostics["warnings"]`
in Python. A stderr line is written too, but the field is what a caller
should inspect. Node's `diagnostics.geoip` reports `unresolved` for a lookup
that ran and failed.

Neither package raises from `launch()` on a lookup failure, a timeout, an
unavailable provider, or a malformed provider result. `resolve_geoip()` called
directly raises, and a non-positive `geoip_timeout` raises, because that is a
caller bug. `GeoIPError` and `GeoIPUnavailableError` are exported and raised
by `resolve_geoip()`.

Three consequences, each measured through the packages. A partial answer
keeps the field it carries: a provider returning a timezone and no locale
yields `--fingerprint-timezone` alone. A country code resolves through the
project's own table, so a German exit gives `de-DE` rather than `en-DE`. A
non-IANA timezone such as `+02:00` is treated as unresolved, and the launch
keeps the host's timezone while still taking the locale.

`geoip` is best-effort. When a lookup fails behind a proxy, the host's
locale and timezone apply and will not match the exit country. Passing
`locale` and `timezone` explicitly is the deterministic way to guarantee the
match, and it is the strongest row of the chain above.

The Node adapter performs the built-in GeoIP request through the configured
HTTP(S), SOCKS4, or SOCKS5 proxy. Chromium authentication is a separate
native path with two channels: a credential in `--proxy-server`'s userinfo,
which the browser strips off the switch before Chromium's proxy configuration
sees it, and the envelope below, which is what the launcher sends. Supplying
both refuses the launch; `docs/FLAGS.md`, "The proxy", is the operator-facing
contract. The native loader keeps credentials out of the device-profile
schema, diagnostics and persisted artifacts. They do enter Chromium's
in-memory `HttpAuthCache`, as interactively entered proxy credentials do:
that cache is per-`NetworkContext`, never written to disk, and not
page-visible, and populating it is what allows preemptive authentication.
UDP over SOCKS5 UDP ASSOCIATE is supported natively and carries proxied
QUIC/HTTP3 and WebRTC's STUN, TURN and media datagrams. WebRTC gets the
address families the proxy can be reached in and no others; a proxy that
cannot relay a datagram produces no UDP candidate. `docs/FLAGS.md`, "WebRTC",
is the operator-facing contract and `docs/LIMITATIONS.md` has the
measurements.

Proxy credentials are the one envelope user. Credentials have no switch of
their own, because argv is world-readable in `ps` output, so they travel in
an `--apostate-profile` envelope of the shape
`{device_profile: {}, proxy_credentials: {...}}`. The empty key has to stay:
`base/apostate/profile.cc` reads `proxy_credentials` only inside
`FindDict("device_profile")`, so a wrapper without that key loses the
credentials silently. A payload claiming no device, meaning an empty or
absent `device_profile` or a bare payload whose only key is
`proxy_credentials`, composes normally and the composed profile is
re-installed with the credentials attached. A payload carrying any device
content suppresses composition. Authenticating to a proxy does not cost the
composed fingerprint, and
`launch({proxy: "http://user:pass@host", args: ["--fingerprint=12345"]})`
launches.

`humanize: true` is rejected rather than accepted as a no-op.

Geography controls locale and timezone only. Fonts, voices, GPU, rendering,
hardware and codec behaviour remain properties of the composed profile.
`Asia/Bangkok` may validly be paired with `en-US,en` and English fonts.

## The persona selects the GPU cluster

`fingerprint_platform` selects OS identity, client hints, fonts, voices, locale,
screen geometry, hardware buckets and the GPU capability cluster. The anchor is
drawn from the anchors captured on the claimed platform, minus the
software-rasteriser anchor, and the host's own graphics backend is not part of
the draw.

Within one backend, silicon generation does not matter: Ada, Ampere and
Blackwell produce byte-identical WebGL capability tables and an identical
pixel render digest on Linux/Vulkan, so one anchor covers a range of cards.
Across backends nothing transfers: the same NVIDIA silicon produces a
different capability digest through D3D11 than through Vulkan, and Apple
Metal differs from both. An anchor is a statement about a backend, the
catalogue holds one backend per platform, and naming the platform names the
backend.

`null` does not mean the host's platform. The default is host-conditional:

| Host token | Default persona |
| --- | --- |
| `macos` | `macos` |
| `windows` | `windows` |
| `linux` | `windows` |
| anything else, including empty | returned unchanged |

A Linux host claiming Windows is the one default that is not the host's own
OS, and its cost is the Windows font set; see [docs/FONTS.md](FONTS.md) and
the cross-OS section of [docs/LIMITATIONS.md](LIMITATIONS.md). An
unrecognised host token is passed through unchanged so that the browser
fails its own `IsKnownPlatform` check and inherits the host, which is the
right outcome for a platform with no corpus behind it.

That table has one owner per language:

| | Table | Function |
| --- | --- | --- |
| Python | `apostate.DEFAULT_PERSONA_BY_HOST` | `apostate.default_persona_for_host(host_token)` |
| Node | `DEFAULT_PERSONA_BY_HOST` | `defaultPersonaForHost(hostToken)` |

`host_persona()` in Python and `hostPersona()` in Node report the host's
platform. The host's platform and the claimed one differ on a Linux host, so
compose the two for the claimed persona:
`default_persona_for_host(host_persona())` or
`defaultPersonaForHost(hostPersona())`. `normalizePersona(undefined)` returns
`null` and is not a host lookup; it matches Python's
`normalize_platform(None)`.

The resolved platform is reported: `ProfileResolution.platform` in Python
and the `platform` key of `resolveProfile()`'s result in Node both carry it,
alongside the effective `locale` and `timezone`.

The launch path emits `--fingerprint-platform` only when the caller specified
a persona, so a default launch leaves the choice to the browser and the
compositor is the single source of truth. The resolver path has no browser to
ask, so it applies the table itself. Under `fingerprint="host"` the reported
platform is the host's, because host inheritance composes nothing.

Identity strings (`unmaskedVendor`, `unmaskedRenderer`, WebGPU
`vendor`/`architecture`) rotate only among measured members of one anchor,
because those members provably agree on every capability digest. An anchor with
one measured member offers no rotation at all.

## Profile fields and inheritance

Every profile field is optional. An absent field remains host-inherited. This
is deliberate: a missing measurement must not be replaced with a plausible
constant that no consumer can distinguish from a real value.

The schema groups fields as follows:

| Section | Examples | Native limitation |
| --- | --- | --- |
| `platform`, `browser` | Client Hints platform, platform version, architecture, bitness, WoW64, form factors, UA string | The UA browser version must match the Chromium binary. Brand fields are assert-only: the brand list is a build invariant and the loader fails closed on a mismatch instead of rewriting it. |
| `cpu`, `memory` | Logical cores, installed memory | Bucket values only, and clamped down to host capability, never up. |
| `audio` | Output buffer frames | Not clamped to the host, unlike the two above: the loader clears the host device's own `min_frames_per_buffer` and `max_frames_per_buffer` alongside the override, because a backend advertising a 480-frame floor would otherwise clamp a claimed 256 straight back up. Accepted range is `[128, 8192]`, which is `kMinWebAudioBufferSize` to `kMaxWebAudioBufferSize`. The claim is not falsifiable by capacity the way cores and memory are; the browser runs the buffer it reports, but the sample rate it is divided by stays the host's. |
| `screen`, `window` | Panel geometry, DPR, gamut, HDR, work-area insets, window chrome deltas | Impossible display arrangements are rejected. `avail_*` is derived from the panel plus the furniture insets; insets exceeding the panel fail the launch. |
| `gpu`, `gl_limits`, `gl_extensions`, `gl_precisions` | Renderer/vendor identity, WebGL limits, extensions and shader precision | These are native target inputs. A numeric limit is served as composed to a WebGL context on every backend (`0103`, `0112`, `0119`), while the compositor, raster and Skia keep the host's own caps; five claimed extensions whose objects carry only constants are served from Blink's own classes (`0104`) and the rest cannot be added. What the backend enforces is unchanged, so an operation taken to a claim the host cannot meet is refused. Exact equality requires native behavior validation, not renderer identity alone, and the residuals are in [docs/LIMITATIONS.md](LIMITATIONS.md). |
| `webgpu` | Adapter vendor, architecture, features, limits | Served from the same measured anchor member the WebGL cluster comes from, so the two surfaces cannot disagree: pinning `windows-d3d11-nvidia` on a Metal host returns `{nvidia, ampere}` and its 36 measured limits, where an unpinned launch on that host returns `{apple, metal-3}`. A member that recorded no adapter has nothing to serve and leaves the surface host-inherited; [docs/LIMITATIONS.md](LIMITATIONS.md) has that case. |
| `locale`, `theme`, `input` | Timezone, language list, color scheme, pointer/hover | Locale and timezone can be overridden by the launch precedence rules. |
| `media`, `speech` | Hardware decode codecs, device counts, registered voices | Names do not create codecs, devices, or speech providers. `media.hw_decode_codecs` sets what `MediaCapabilities` reports as `powerEfficient` and deliberately does not touch `supported`, which answers from the decoders this build actually has. Device counts are a floor: inputs are added to reach the count and the host's own devices are never removed. Network voices are a build capability, not a profile value. |
| `keyboard`, `fonts` | Layout map, generic family mappings, enumeration allowlist, text render params | Enumeration only ever removes families; adding one needs the font file on the machine, which the operator installs and the browser assumes is done. See [docs/FONTS.md](FONTS.md). Unmeasured font and keyboard data stays inherited. `fonts.render_params` is the exception to "optional per field": all eight fields or none, because a partial tuple leaves the rest to the host, and on Linux the host's half is its own fontconfig. It is projected from the platform persona rather than drawn, and served to every process that draws text (`0117`, `0122`); [docs/LIMITATIONS.md](LIMITATIONS.md) has what a page can see of it per host. |
| `network` | `effective_type`, `http_rtt_ms`, `downlink_mbps`, `save_data` | The emitter clamps `http_rtt_ms` into the band Chromium derives `effective_type` from and floors `downlink_mbps` at that type's own typical throughput, so the pair cannot contradict itself. `save_data` also drives the `Save-Data` request header and the `prefers-reduced-data` media feature. |
| `battery` | `present`, `charging`, `level`, `charging_time_seconds`, `discharging_time_seconds` | `present: false` reports what a real desktop reports: charging true, level 1.0, `chargingTime` 0, `dischargingTime` `Infinity`. The API stays exposed either way, because hiding it is what desktop Chrome does not do. Presence is conditioned on the panel axis, which is what encodes a laptop display. |
| `extensions` | `externally_connectable` | Whether an ordinary http/https page sees `chrome.runtime` and `window.browser`, which is decided by whether any installed extension's `externally_connectable` manifest key matches the page. `false` is fresh Chrome and is served explicitly rather than by omission, so the host's own extension set never answers for the persona; `true` is the MetaMask shape, where `file://` and `about:blank` still stay bare. Never an extension list and never an id: a fixed extension id is a cross-instance correlator. Served from the profile (`0116`, `0121`): a page on an identity that draws it present sees `chrome.runtime` with the eleven properties and the exact rejection a real externally-connectable extension produces. |

A profile does not replay opaque canvas or audio bytes. Those surfaces are
served by native Chromium emitters using validated inputs. Where the native
implementation cannot honor a profile value, the value remains constrained by
native capability or is inherited; it is not fabricated.

### Anchors and what selecting one claims

Selecting an anchor supplies the GPU capability inputs. It does not claim that
every resulting observable matches the reference device on a host that is not
that device. Native limits, feature intersections and inherited values can all
leave a surface below the anchor's own measurement, and `--fingerprint-explain`
names those.

Three anchors in the current catalogue were measured on a Chromium other than
152.0.7977.83, and each carries a `build_caveat` recording it. Capability tables
move between builds, so those three clusters can differ from this binary in
version-bearing fields. The resolver reports the caveat as a warning.

## Where a value came from

Every dispersion option, anchor and profile report carries one of these labels,
and `--fingerprint-explain` prints it per surface:

- `physical-ground-truth`: a consented capture from a physical device.
- `catalogue-value`: a value authored from platform release history and
  constrained by Chromium and platform rules. Authored rather than measured, and
  each option says so in its own `note`.
- `native-derived`: a value this build emits from its own source inputs.
- `proxy-derived`: a value resolved at launch from the network exit.
- `host-inherited`: a value left to the host.
- `compatibility-capture`: a value our collector measured from another
  runtime's output rather than from a physical device. Exactly one entry carries
  it, the software-rasteriser anchor `linux-swiftshader-google-6922d61bab83`,
  which was captured from a stock Chromium and claims no hardware.

A composed profile mixes several of these. The report keeps them apart rather
than presenting the whole profile as a measured machine. The catalogue holds
normalized values authored here; it does not redistribute third-party
fingerprint corpora.

## Composition limitations

The dispersion space is large and every point in it is a machine someone could
own, but composition has hard limits. [docs/LIMITATIONS.md](LIMITATIONS.md) is
the user-facing version of this list.

1. A `catalogue-value` option is authored from platform release history. Drawing
   it does not turn it into a measurement.
2. A composed profile does not claim the host owns the corresponding GPU,
   display, fonts, audio stack or codec hardware.
3. Native capability is a ceiling for what an operation can do, not for what a
   limit reports. A media claim cannot create a decoder or provider that is
   absent. WebGL and WebGPU limits are reported as composed to a WebGL context
   on every backend (`0103`, `0112`, `0119`), because a claimed GPU beside the
   host's capability table is a one-call contradiction, and `0104` serves the
   claimed extensions whose objects carry constants rather than methods. The
   residuals are measured and stated in
   [docs/LIMITATIONS.md](LIMITATIONS.md): the claimed `MAX_TEXTURE_SIZE`
   cannot be allocated at on a software backend, and on a backend that
   enforces, a point size, a uniform count or a sample count taken to the
   claim is refused by the driver.
4. Capacity only ever goes down, with the GPU limits in item 3 as the
   exception. A profile may claim fewer cores than the host has, never
   more, and the same holds for memory, codec support, font families, speech
   voices, and display area against window bounds. A page can measure parallel
   throughput, allocate until allocation fails, compile a shader at the
   advertised limit, or ask a voice to speak; a claim below host capability
   survives all of those and a claim above it fails the first. Options the host
   cannot serve are dropped before the draw, so a small host draws from a
   smaller set than a large one.
5. Canvas, text, audio, font metrics and speech providers can stay
   host-inherited when the native resources are missing. The profile reports
   that rather than adding synthetic output.
6. Geography selects locale and timezone only. A proxy-derived timezone does not
   imply regional fonts, voices or hardware.

## Where composition runs

**The compositor lives in the browser process, in C++, and is the only
implementation.** The bare binary must produce a fingerprint with no arguments,
which puts the compositor inside the binary by necessity. Reimplementing it in
the Python and Node packages would create three sources of truth for one
deterministic function, and the drift between them would be silent.

So the packages do not compose profiles. They do what only they can do: CLI
ergonomics, schema validation, launch orchestration, and GeoIP. A code path that
used to compose and now has no model fails closed with an explicit error rather
than guessing.

`scripts/profile_resolver.py` is the repository's reference implementation of the
same deterministic function. It exists so the tables can be validated, listed
and resolved outside a build, and so the C++ implementation has golden vectors
to agree with. Determinism across implementations is pinned by those vectors
rather than by cross-language byte comparison.

## Native and process contract

Profile values are consumed by native Chromium emitters. A conforming launch
must deliver the composed profile consistently to every process that owns a
selected surface, including browser, renderer, GPU, and network paths where
applicable. The transport is `--apostate-profile=<base64 JSON>`, parsed by
`base::apostate::Profile`. JavaScript injection, CDP overrides, redefined
accessors, and wrapper objects are outside this contract.

Diagnostics are local-only and may identify `profile_id`, `catalogue_version`,
`profile_schema_version`, `browser_build`, platform, anchor, the chosen option
per axis, locale source, timezone source, and warnings. Diagnostics must not
expose profile contents as a page-visible API or log proxy credentials.

## Examples

### Python

```python
from apostate import launch

browser = launch(
    fingerprint=12345,
    fingerprint_platform="windows",
    proxy="http://user:pass@proxy:8080",
    geoip=True,
)
```

### Node.js

```javascript
import { launch } from "apostate";

const browser = await launch({
  fingerprint: 12345,
  fingerprintPlatform: "windows",
  proxy: "http://user:pass@proxy:8080",
  geoip: true,
});
```

### Direct CLI

The binary takes the same configuration as switches:

```bash
./chrome \
  --fingerprint=12345 \
  --fingerprint-platform=windows \
  --fingerprint-locale=en-US,en \
  --fingerprint-timezone=America/New_York \
  --proxy-server=http://proxy:8080
```

[docs/FLAGS.md](FLAGS.md) is the full switch reference.

The package entry points are:

| Shared operation | Python | Node.js |
| --- | --- | --- |
| Launch | `launch` | `launch` |
| Context | `launch_context` | `launchContext` |
| Persistent context | `launch_persistent_context` | `launchPersistentContext` |
| Binary download/cache | `ensure_binary` | `ensureBinary` |
| Binary metadata | `binary_info` | `binaryInfo` |
| Cache maintenance | `clear_cache` | `clearCache` |

The package surface is Patchright-compatible Playwright behaviour. There are no
.NET bindings, no Puppeteer adapter, no GUI profile manager and no cloud profile
sync. `humanize: true` is rejected rather than accepted as a no-op.

## Validation

Validate profile files against the schema before launch:

```bash
python3 scripts/validate-profile.py profile.json
```

Validate the catalogue, every dispersion table and every anchor, and compose a
profile from a seed:

```bash
python3 scripts/profile_resolver.py --catalogue
python3 scripts/profile_resolver.py --list
python3 scripts/profile_resolver.py --resolve --fingerprint=12345 \
  --fingerprint-platform=windows
```

Switches are documented in [docs/FLAGS.md](FLAGS.md) and the residuals a profile
cannot close are in [docs/LIMITATIONS.md](LIMITATIONS.md).
