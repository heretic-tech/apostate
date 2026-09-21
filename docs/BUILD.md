# Build contract

Apostate builds Chromium from repository-pinned inputs. Reproducibility means
that independent clean builds of the same target produce the same output
hashes. `scripts/verify-reproducible.sh` performs that comparison; a successful
CI build alone does not establish it.

## Pinned inputs

| File | Input |
| --- | --- |
| `build/CHROMIUM_VERSION` | Exact Chromium tag, `152.0.7977.83` |
| `build/DEPOT_TOOLS_REVISION` | depot_tools commit SHA |
| `build/args/common.gni` | GN settings shared by every target |
| `build/args/linux-x64.gn` | Linux x64 GN settings |
| `build/args/linux-arm64.gn` | Linux arm64 GN settings |
| `build/args/macos-arm64.gn` | macOS arm64 GN settings |
| `build/args/windows-x64.gn` | Windows x64 GN settings |
| `build/MAC_SDK_VERSION` | Exact macOS SDK version, `26.5` |
| `build/MAC_SDK_BUILD` | SDK `ProductBuildVersion`, `25F70` |
| `build/WINDOWS_SDK_INSTALLER_URL` | Version-specific 10.0.26100.7705 SDK installer, for the Debugging Tools feature |
| `build/WINDOWS_SDK_PACKAGES` | The SDK's headers and x64 libraries as digest-pinned NuGet payload packages |
| `build/WINDOWS_SDK_INSTALLER_VERSION` | Which SDK release that URL serves, `10.0.26100.7705` |
| `build/WINDOWS_SDK_INSTALLER_SHA256` | Digest of those installer bytes |
| `build/WINDOWS_SDK_REQUIREMENTS` | SDK requirements a servicing *revision* decides, which no directory name shows |
| `build/WINDOWS_SDK_VERSION` | SDK *directory* version the preflight checks, `10.0.26100.0`; mirrors Chromium's own constant |
| `build/WINDOWS_VS_COMPONENTS` | Visual Studio components the Windows build requires, and the file that proves each |
| `build/linux/Dockerfile` | Linux base image digest and build environment |
| `patches/series` | Patch names and application order |
| `build/MANIFEST.lock` | Generated record of resolved build inputs and outputs |

The patch bytes are inputs too. Editing a patch without changing its name or
its position in the series changes `patch_contents_sha256` in the build
manifest.

Chromium's pinned dependencies supply Clang, LLD, Rust and the Linux sysroots.
The optional Google API environment values read by `build/args/common.gni`
also affect the binary. Reproducibility comparisons must use the same values,
or leave them unset for both builds.

## Determinism requirements

- `scripts/lib.sh` exports `DEPOT_TOOLS_UPDATE=0` so depot_tools stays at the
  pinned revision.
- Linux targets use Chromium's bundled Clang and LLD with `use_sysroot=true`.
  The pinned container excludes host libraries from compilation.
- `build/args/common.gni` selects `is_official_build=true` and keeps PGO at
  Chromium's official-build default. The profiles come from pinned DEPS.
- The common GN settings disable debug symbols and dSYMs. Chromium supplies
  absolute-path stripping for these Linux and macOS configurations; the
  shared arguments do not set an undeclared override.
- macOS uses the exact SDK version and build named in the two pin files,
  rather than whichever SDK the active Xcode supplies.

## Build steps

The build scripts share workspace and tool paths through `scripts/lib.sh`.

| Script | Purpose |
| --- | --- |
| `scripts/resolve-build-targets.sh` | Resolve the CI target list and hosted runner labels |
| `scripts/verify-runner.sh` | Check the target against the runner OS and architecture |
| `scripts/verify-host-tooling.sh` | Report every tool, component and SDK revision the target's build needs and this host lacks |
| `scripts/provision-windows-toolchain.sh` | Install the Visual Studio components and the pinned SDK revision the Windows image lacks |
| `scripts/reclaim-windows-disk.sh` | Remove measured, build-irrelevant software from the Windows runner image |
| `scripts/bootstrap.sh` | Fetch pinned depot_tools and check host prerequisites |
| `scripts/fetch-sources.sh` | Fetch and sync the pinned Chromium revision |
| `scripts/apply-patches.sh` | Apply the patch series without fuzz |
| `scripts/run-chromium-hooks.sh` | Run Chromium hooks in the pinned Linux container |
| `scripts/prepare-linux-sysroot.sh` | Install the pinned Linux target sysroot |
| `scripts/prepare-mac-sdk.sh` | Resolve and link the pinned macOS SDK |
| `scripts/configure.sh` | Assemble target arguments and run GN |
| `scripts/build.sh` | Run Ninja and write the build manifest |
| `scripts/smoke-binary.sh` | Run the native binary's version command, or check the cross-built ARM64 ELF machine type |
| `scripts/package-artifact.sh` | Stage the runtime payload and write the archive and release manifest |
| `scripts/checkfile.sh` | Recompile one translation unit using generated compilation commands |
| `scripts/series-translation-units.py` | List every translation unit the patch series touches, in series order |
| `scripts/checkseries.sh` | Compile all of them for one target and account for every one it produces no object from |
| `scripts/series-gate-report.py` | Classify one gate run's results and decide its verdict |
| `scripts/series_absences.py` | Resolve an include-only file to its includer, and classify a declared absence |
| `scripts/series-absences.tsv` | Declared absences: the files a target legitimately builds no object from, with the GN condition as evidence |
| `scripts/series-symbol-closure.py` | Prove the symbol closure of every library a patch adds GN sources to |
| `scripts/verify-reproducible.sh` | Compare clean-build output hashes |
| `scripts/sign-macos.sh` | Developer ID sign, notarize and staple the macOS bundle into a tree beside the build output |

On Linux, `scripts/fetch-sources.sh` treats a failed Chromium build-dependency
installation as fatal. A missing dependency otherwise tends to surface much
later as a header or linker error.

### Prove the binary contains your change before you measure it

A built `Chromium.app` in the out directory can be arbitrarily older than the
framework beside it, and ninja will not tell you. Measured on
`out/macos-arm64` at 152.0.7977.83: the freshly linked
`out/macos-arm64/Chromium Framework.framework/.../Chromium Framework` was three
days newer than the copy inside
`Chromium.app/Contents/Frameworks/Chromium Framework.framework/...`, and asking
ninja for the app's copy by name answered `no work to do` against the stale
file. Three headless runs and a net-log capture were produced from a binary
that did not contain the patch under test, and nothing in their output said so
— the patch's behaviour was simply absent, which reads exactly like the patch
not working.

So verify the binary before trusting anything it does. The cheapest check is a
string only your change introduces:

```sh
strings -a "out/macos-arm64/Chromium.app/Contents/Frameworks/Chromium Framework.framework/Versions/$(
  )152.0.7977.83/Chromium Framework" | grep -c 'some message only my patch adds'
```

`0` means you are measuring an older build. Recover by deleting the bundle and
letting ninja reassemble it, which takes seconds because every input is already
built:

```sh
rm -rf out/macos-arm64/Chromium.app
( cd out/macos-arm64 && ninja chrome )
```

`scripts/package-artifact.sh` is not the cause: it only reads
`out/$TARGET/Chromium.app` and copies it into a staging directory, and writes
nothing into the out directory. The stale copy was left by something that
overwrote the bundle after ninja recorded it as current, and ninja's freshness
check for a `copy_bundle_data` directory output does not notice. Until that is
tracked down, the string check above is the standard opening move for any local
smoke test, and it is cheap enough that there is no reason to skip it.

### Configure through the script

`scripts/configure.sh` combines `build/args/common.gni` with the target's GN
file into a self-contained generated `args.gn`. The common settings enable
proprietary codecs and Widevine registration and disable Chromium's field-trial
testing configuration. A target file alone does not contain those settings.
Configuring it by hand changes browser behavior and invalidates comparison
with the reference build.

The build manifest records the generated arguments' `args_sha256`. It covers
both common and target settings, plus the pinned SDK path on macOS.

### Pin the macOS SDK exactly

`build/MAC_SDK_VERSION` and `build/MAC_SDK_BUILD` require SDK `26.5`, build
`25F70`. The SDK is the macOS reproducibility boundary. Chromium supplies the
compiler, but the SDK determines the libraries and headers it links against.

Xcode 27.0's `libSystem.tbd` declares an `arm64e.x1-macos` target that the
bundled LLD cannot parse. LLD then loads no libSystem symbols, and linking
fails on `strlen`. A minimum SDK version cannot prevent this failure because
27.0 satisfies a 15.0 minimum. Do not raise the pin just to match the active
Xcode installation.

`scripts/prepare-mac-sdk.sh` searches the active developer directory, default
and versioned Xcode bundles, and the Command Line Tools SDKs. It checks both
the product version and build, then links the selected SDK into the generated
output directory. `scripts/configure.sh` writes a build-relative
`mac_sdk_path` so the checkout's absolute location does not enter the
arguments hash.

`scripts/bootstrap.sh` resolves the SDK before fetching depot_tools. A
different active Xcode SDK is acceptable when the pinned SDK is installed.
If the pin is missing, bootstrap fails and lists the searched locations and
installed SDKs. Install an Xcode or Command Line Tools package containing the
required SDK from <https://developer.apple.com/download/all/>. A deliberate
SDK update changes both pin files and requires an LLD compatibility check,
reproducibility verification and new reference measurements.

### Signing the macOS bundle

`scripts/sign-macos.sh macos-arm64` signs `Chromium.app` with a Developer ID
Application certificate, submits it to Apple's notary service, staples the
resulting tickets, and verifies the result. Only macOS is signed; the Linux
and Windows archives carry no platform signature and none is expected of
them.

It writes to `$APOSTATE_WORKSPACE/signed/macos-arm64/` and never into
`out/`. That separation is a requirement, not tidiness. `build/MANIFEST.lock`
records a SHA-256 over `out/macos-arm64/Chromium.app`, and
`scripts/verify-reproducible.sh` proves the build by comparing those digests
across two clean builds. A code signature contains a signing timestamp and a
certificate, so it is not reproducible by construction: signing in place
would make every reproducibility comparison fail for a reason that has
nothing to do with the build. The out directory stays exactly as ninja left
it, and the signed tree is a derived artifact beside it.

`scripts/build.sh` builds `chrome/installer/mac` alongside `chrome` on
macos-arm64. That group copies Chromium's own signing driver
(`sign_chrome.py`, the `signing` package, the generated
`build_props_config.py`) and the three entitlements plists into
`out/macos-arm64/Chromium Packaging/`, and builds `dmg_tool` and `hfs_tool`.
It is 61 edges and takes seconds. The packaging directory is not in the
manifest's `[outputs]` list, which enumerates `Chromium.app` and
`chrome_crashpad_handler` by name for this target, so it does not enter the
reproducibility hash set — correctly, since none of it reaches the shipped
payload.

What gets signed is the whole bundle: the outer app, the framework, every
helper app and every nested executable, each with the entitlements Chromium's
own configuration assigns it, under the hardened runtime. After notarization
the script staples a ticket to the outer app and to every nested `.app` and
`.xpc`, deepest first, which is what Chromium's `staple_bundled_parts` does —
a helper left unstapled fails to launch on a machine that is offline the
first time the bundle runs.

`scripts/package-artifact.sh` then stages from the signed tree when it
exists and from `out/` when it does not, and says which in the job log.

#### The six secrets

| Secret | Value |
| --- | --- |
| `APPLE_DEVELOPER_ID_P12_BASE64` | Base64 of the Developer ID Application certificate and its private key, exported as a `.p12` |
| `APPLE_DEVELOPER_ID_P12_PASSWORD` | The export password for that `.p12` |
| `APPLE_SIGNING_IDENTITY` | The certificate's common name, e.g. `Developer ID Application: Example Inc (AB12CD34EF)` |
| `APPLE_NOTARY_KEY_P8_BASE64` | Base64 of the App Store Connect API key `.p8` |
| `APPLE_NOTARY_KEY_ID` | That key's Key ID |
| `APPLE_NOTARY_ISSUER_ID` | The issuer UUID of the App Store Connect API key |

All six or none. With none, the script prints `sign-macos: no signing
identity configured; the bundle stays unsigned` and exits zero, so nightlies
and forks keep building. With some but not all it fails and names the missing
ones, because a release that quietly shipped unsigned is the failure this
script exists to prevent.

Creating the certificate. In Xcode, Settings → Accounts → Manage
Certificates → + → Developer ID Application, or create it at
<https://developer.apple.com/account/resources/certificates>. It has to be a
**Developer ID Application** certificate; Apple Development and Mac App
Distribution certificates do not produce a bundle Gatekeeper accepts outside
the App Store. Export it from Keychain Access together with its private key
as a `.p12` with an export password, then
`base64 -i DeveloperID.p12 | tr -d '\n'` for the secret value.
`security find-identity -v -p codesigning` prints the common name to use for
`APPLE_SIGNING_IDENTITY`. It prints the name inside quotation marks; the
secret is the bare name without them (the script refuses a quoted value at
its first check rather than after the build).

Creating the notary key. At
<https://appstoreconnect.apple.com/access/integrations/api>, Team Keys, add a
key with the **Developer** role — `notarytool` is refused by anything less.
Download the `.p8` once (Apple does not offer it again), and note the Key ID
beside it and the Issuer ID above the table.
`base64 -i AuthKey_XXXX.p8 | tr -d '\n'` for the secret value.

Set all six as repository secrets. `.github/workflows/build-target.yml`
declares them as optional `workflow_call` secrets and both callers pass them
by name rather than with `secrets: inherit`, so the build job receives these
six and nothing else.

#### How the script handles them

The certificate is imported into a keychain created for the run under
`mktemp -d`, added to the front of the user search list, and deleted on exit
along with the decoded `.p12` and `.p8`; the search list is restored to what
it was. The trap covers failures and interrupts, so a failed job leaves no
key material and no keychain behind. The `.p8` is written with mode 0600.
Nothing decoded is ever printed.

Notarization waits for Apple's answer with a 30-minute ceiling
(`APOSTATE_NOTARY_TIMEOUT`). The service usually answers in two to five
minutes; the ceiling is long enough to absorb a queue backlog and short
enough that a wedged submission fails the job instead of holding a paid macOS
runner for hours. When the service rejects the bundle the script prints
`notarytool log` for the submission, because the status alone never says
which binary failed.

Two deviations from Chromium's driver, both forced:

- The driver is invoked through a small in-script wrapper that turns off
  `run_spctl_assess`. Chromium's `signing/parts.py` runs `spctl --assess`
  immediately after signing and before any notarization, and a Developer ID
  signature that has not been notarized yet is always rejected there with
  `source=Unnotarized Developer ID`, so the unmodified driver cannot complete
  a Developer ID run. The assessment is not dropped: the script runs
  `spctl -a -t exec -vv` after stapling, which is the only point at which the
  answer means anything. `--development` would also disable it, but it strips
  the designated requirements and injects `get-task-allow`, which the notary
  service rejects.
- The driver's own `--notarize` is not used. With it, `pipeline.py` puts the
  signed bundle in a temporary work directory that is deleted on exit, and
  copies to `--output` only when a distribution is packaged as a dmg, pkg or
  zip. Chromium branding has one distribution and packages as none of them,
  so `--notarize` combined with `--disable-packaging` produces no artifact at
  all. The script therefore lets the driver sign, and drives
  `notarytool submit --wait` and `stapler staple` itself.

Verification before the script exits: `codesign --verify --deep --strict`,
`spctl -a -t exec -vv`, and `xcrun stapler validate`. Any of the three
failing fails the build.

## Build manifest

`scripts/build.sh` writes `build/MANIFEST.lock` after a successful build. It
records the build target and timestamp, Chromium version and commit,
depot_tools revision, container image identity or `native`, patch-series and
patch-content hashes, generated GN-argument hash, build mode and output hashes.

`patch_series_sha256` hashes the series file. `patch_contents_sha256` hashes
each entry's name and complete patch bytes, in series order with NUL
separators. Any edit to any listed patch changes that digest, even if the
series file is unchanged.

Windows builds record seven more fields, because `windows-x64` is the only
target whose toolchain comes from the runner rather than from a pin:
`visual_studio_version`, `msvc_toolset_version`, `windows_sdk_version`,
`windows_sdk_revision`, `vs_components_sha256`, `sdk_requirements_sha256` and
`sdk_packages_sha256`. They are attribution, not pins — see
[Provisioning](#provisioning). They sit before `[outputs]`, so they change
`manifest_sha256` and leave `outputs_sha256` alone.

The `release-gate` job in `.github/workflows/release.yml` runs
`scripts/validate-release-baseline.py --release` before scheduling any
Chromium build. It checks the committed manifest against the current version,
patch series and patch bytes. A stale digest fails that gate, and the CI build
cannot refresh it because that build has not started yet.

Both digests are functions of the patch files alone, so refreshing them needs
no build: `scripts/validate-release-baseline.py --refresh` rewrites those two
fields in place and nothing else. Run it as the last commit before the tag.
Without `--release` the same script reports the drift as a notice and exits
zero, because between builds those digests are expected to be behind.
[docs/RELEASE.md](RELEASE.md) has the pre-tag sequence and what the refresh
does not claim.

### Fresh builds and local lineage

Both nightly and release CI builds set `APOSTATE_FRESH_BUILD=1` and start in
an empty hosted-job workspace. Local builds can retain output for incremental
compilation. Setting `APOSTATE_FRESH_BUILD=1` locally tells
`scripts/configure.sh` to delete the target output directory before generating
its configuration.

| Manifest field | Meaning |
| --- | --- |
| `build_mode` | `fresh` when `APOSTATE_FRESH_BUILD=1`, otherwise `incremental` |
| `parent_manifest_sha256` | Hash of the manifest replaced by this build, or `none` |
| `fresh_ancestor_sha256` | `self` for a fresh build; for an incremental build, the most recent recorded fresh manifest for this target, or `unknown` |

`scripts/build.sh` appends a sorted compact JSON record to the generated
`.apostate-build-lineage.jsonl` file in the workspace. The record contains
the timestamp, target, build mode, input and output hashes, and manifest
digest. Local history lasts as long as that workspace. In CI the file is
under `$RUNNER_TEMP/apostate-workspace`, lasts only for the job, and is not
uploaded. The parent manifest in a fresh CI checkout is the checked-in
baseline, not a previous hosted job's output.

An incremental build can report `fresh_ancestor_sha256=unknown` when the
workspace has no recorded fresh build for that target. Both CI paths record
`build_mode=fresh` and `fresh_ancestor_sha256=self`.

## CI workflows

`.github/workflows/build-target.yml` defines the reusable build job. It
initializes the workspace, checks host tooling, checks out the requested
revision, verifies build inputs and runner identity, resolves the artifact
name, bootstraps, fetches, applies patches, runs Linux hooks and sysroot
installation, configures, builds, checks, signs and notarizes on macOS,
packages, optionally attests and uploads.

The signing step sits between the smoke check and packaging, and runs only
for `macos-arm64`. The smoke check has to see the tree ninja produced, and
packaging is the step that chooses which tree ships. It takes the six
`APPLE_*` secrets described under
[Signing the macOS bundle](#signing-the-macos-bundle); both callers pass
them by name, and a caller without them still produces an unsigned bundle.

The host-tooling check runs immediately after the repository checkout and
before bootstrap, so it can read the pins in `build/` but not the Chromium
checkout, which does not exist for another twelve minutes. That is the
distinction that decides where an assertion belongs: anything answerable from
the pins and the image goes here, anything needing `$SRC` goes in
`scripts/configure.sh`. A check placed on the wrong side of that line either
cannot run or skips silently, and a silent skip renders as green.

Every target needs Python and Git. Non-Windows targets need `tar` and must
pass a real `tar --zstd` write probe; `windows-x64` needs `7z` instead,
because that is what packages its `.zip`. Linux also needs Docker and a
working daemon; macOS needs `xcodebuild`, `xcrun` and `plutil`; Windows needs
the whole Visual Studio and SDK prerequisite set described under
[Hosted runners and workspaces](#hosted-runners-and-workspaces). Testing
archive support early avoids completing a full Chromium build only to fail at
the final packaging step.

The two callers are `.github/workflows/build-nightly.yml` and
`.github/workflows/release.yml`:

| Setting | Nightly | Release |
| --- | --- | --- |
| Trigger | Daily at 03:17 UTC, or manual dispatch | A `v*` tag push, or manual dispatch of an existing version tag |
| Build mode | Fresh | Fresh |
| `attest` | `false` | `true` |
| Revision | Triggering ref | Semver tag |
| Actions artifact retention | 14 days | 7 days |
| Matrix `fail-fast` | `false` | `false` |
| macOS signing secrets | Passed | Passed |
| Additional jobs | Resolve targets | Version-tag and baseline gate, target resolution, publication |

Each build job has a 600-minute timeout. WarpBuild runners register as
self-hosted, so GitHub's 6-hour cap does not apply and the ceiling is ours;
600 minutes is chosen so the 12-vCPU macOS build has room to finish a full
fetch plus official build rather than being killed at 95% after paying for all
of it. Both callers use the workflow-level
`apostate-build` concurrency group with `cancel-in-progress: false`, so a
nightly and a release cannot overlap. Within a run every matrix leg has its
own VM and runs concurrently, and neither caller cancels the siblings of a
failed leg: a finished leg's artifact stays on the run, and re-running the
failed jobs rebuilds only what failed.

Release builds create provenance with the pinned
`actions/attest-build-provenance` action, using the archive as its subject.
The reusable job and its callers grant `id-token: write` and
`attestations: write`; even the nightly caller grants them to satisfy the
reusable workflow's permission requirements. The nightly attestation step
is skipped. See [Release policy](RELEASE.md) for installer hash checks and
out-of-band provenance verification.

### Pull-request gates

`.github/workflows/check.yml` runs on manual dispatch and on pull requests
touching the paths listed in that workflow. Its hosted jobs need no Chromium
checkout. They validate the ledger, release baseline, patch headers, schemas,
profile catalogue and resolver, GeoIP, and Python and Node packages.

`scripts/validate-patch-headers.py` checks every patch's hunk counts against
its body. Incorrect counts can cause patch application to omit lines, so CI
runs the validator read-only. Use its `--fix` option locally when repairing a
patch, then rebuild to refresh the manifest's patch-content hash.

### Cross-platform compile gate

`scripts/checkfile.sh` gates one translation unit against one configured build
directory. That covers the platform being worked on and nothing else, and the
only build directory on the development machine is `macos-arm64`, so the
series' Linux-only and Windows-only files had never been compiled anywhere.
`base/apostate/explain.cc` is the recorded cost: it called
`base::WriteFileDescriptor(STDOUT_FILENO, ...)`, which is POSIX-only, in a
file `base/BUILD.gn` compiles on every platform, and it was found by reading.

`scripts/series-translation-units.py` derives the gate's coverage from
`patches/series`: every `+++ b/<path>` in every listed patch, filtered to
translation units, de-duplicated, in series order. It is derived rather than
maintained because a list that has to be updated by hand stops covering files
silently, which is the failure this gate exists to remove. At Chromium
152.0.7977.83 the series touches 136 translation units.

The filter is `.c`, `.cc`, `.cpp`, `.mm`, `.m`, so the gate's coverage stops
at files that produce an object of their own. **The series also patches 58
files that do not: 53 headers and 5 `.asm`.** None of them is in the unit
list, so none is verified and none can appear as an absence either — including
the ffmpeg configs' `config.asm` and `config_components.asm`, which are
include-only in exactly the way `codec_list.c` is: asm sources such as
`libavcodec/x86/h264_chromamc.asm` pull them in with `%include`.
`libavcodec/x86/autorename_libavcodec_x86_bswapdsp.asm` is the exception that
patches `0061` and `0109` add to `ffmpeg_asm_sources`, so it does produce an
object and is simply outside the suffix filter. Headers are the old assumption
stated plainly in `series-translation-units.py`: "covered by compiling the
translation units that include it", which is an assumption rather than a
check, and it is the same assumption `codec_list.c` falsified.

`.S` sources are outside the filter too, and patch `0109` shows why that is
not merely theoretical: its arm64 leg adds seven `.S` files to
`ffmpeg_gas_sources`, three of them under `libavcodec/aarch64/h26x`
(`epel_neon.S`, `qpel_neon.S`, `sao_neon.S`) whose names contain no "hevc" at
all. Dropping them leaves 355 undefined `ff_hevc_put_hevc_*` symbols, and
because the gate compiles rather than links, and `.S` is not a gate suffix,
that failure would surface only at link time in a full build. Those files are
not patched, only listed, so they are outside this gate's remit by
construction rather than by oversight — but the gap is real and is recorded
here.

Extending the include-only mechanism over the 58 patched non-unit files is a
coverage change with a bill attached — each includer's object joins a paid
compile set — so it is recorded here rather than made quietly.

`scripts/checkseries.sh` compiles them for one target and classifies each
result. Membership comes from `ninja -t compdb`, a query against the build
graph ninja itself builds from, and the gate never asks ninja to build an
object the graph has not already named — asking and reading the error cannot
work, because `ninja` answers "unknown target" and a failed compile with the
same exit status.

| Classification | Meaning |
| --- | --- |
| `compiles` | ninja produced every object the graph derives from the file |
| `fails` | an object was not produced; the gate reports the error and exits non-zero |
| `include-only` | the file is no translation unit of its own, and an object this run compiled recorded reading it — verified, not skipped |
| `absent-platform` | declared in `scripts/series-absences.tsv` as scoped away from this target by a GN condition |
| `absent-config` | declared there as excluded by this build's configuration |
| `unexplained` | none of the above; the gate exits non-zero |

The last four used to be one word, `absent`, and that folded two unlike claims
into one passing badge. `font_cache_linux.cc` is Linux-only and skipping it on
Windows is correct. But
`third_party/ffmpeg/chromium/config/Chrome/linux/x64/libavcodec/codec_list.c`
was skipped on *every* platform, because it is never a translation unit at
all — `libavcodec/allcodecs.c` includes it textually — so patch `0061`'s codec
and parser registration was compiled by nothing the gate checked while the
gate reported 129 compiles, 3 absent and green.

An include-only file is now resolved to its includer by searching that file's
GN module for an `#include` naming it, the includer's objects join the compile
set, and `ninja -t deps` then has to name the file among what those objects
read. That last step is the one that discriminates: `allcodecs.c` is compiled
on every platform and reads a *different* `codec_list.c` on each, because
`third_party/ffmpeg/BUILD.gn` puts
`chromium/config/$ffmpeg_branding/$os_config/$ffmpeg_arch` on the include path.
Verifying through the includer merely existing would move the unearned badge
one level out instead of removing it.

The two declared categories are human statements, because no build graph can
tell a platform-scoped file from an overlooked one, and they are checked in
both directions: a file nothing accounts for fails the run as `unexplained`,
and a declaration the run disproves fails it as stale.

At 152.0.7977.83 with patch `0109` in the series, each of the three patched
ffmpeg config directories resolves to `include-only` on exactly the one target
whose include path reaches it, and to a declared `absent-platform` on the other
three. `macos-arm64` is measured against its real build graph: 136 units, 113
in the graph, 23 declared absences, 0 unexplained. `linux-x64` reports 129
compiles, 2 include-only and 5 declared absences, and `windows-x64` 116, 2 and
18 — both replayed from their real gate artifacts with `0109`'s four new units
and its `ffmpeg_c_sources` additions applied. `linux-arm64` has no gate
artifact yet, so its numbers are not stated here.

Each run writes a JSON report, and `scripts/checkseries.sh --merge` puts the
per-platform reports side by side. `compiles` and `include-only` are coverage;
the declared absences are not. A file covered by no merged platform is a defect
rather than a platform fact — a patch edits it and nothing compiles it — and
the merge reports that only once the reports it was given cover Linux, macOS
and Windows.

### Symbol closure over a library a patch adds sources to

Compiling proves a translation unit is well formed. It does not prove the
library it joined can still resolve its own symbols, and the gate's unit list
cannot reach the difference: a file **listed** in GN rather than **patched** is
outside the list by construction. Patch `0109` is the live case. Its arm64 leg
adds nine objects for ffmpeg's HEVC SIMD path, three of them under
`libavcodec/aarch64/h26x` with no `hevc` anywhere in their names, and dropping
those three leaves 355 undefined `ff_hevc_put_hevc_*` symbols while every
translation unit still compiles clean. Adding them to the unit list would not
help either: `//third_party/ffmpeg:ffmpeg_internal` is a `static_library`
whenever `is_component_ffmpeg` is false, which `build/args/linux-arm64.gn:12`
makes it, so ninja stops at `alink` and an archive resolves nothing.

`scripts/series-symbol-closure.py` asks one bounded question per affected
library — deliberately not "everything a patch's GN edit pulls in", which is
the whole build:

```text
undefined(objects the patch added)
  - defined(every member of the library)
  - undefined(members the patch did not add)
```

What survives is then split by asking the object files, never by guessing from
source text. A symbol another archive **defines** is supplied by another
library. A symbol other archives **reference** and nothing defines is supplied
by the platform — measured on the real `macos-arm64` output, `__stderrp` is
referenced as undefined by 33 archives, `__stdoutp` by 13 and `fputs` by 7.
A symbol that no archive defines and no other archive references is one this
series' objects are alone in wanting and nothing can supply, which is what a
source missing from a GN list looks like.

An earlier version searched the library's source tree for a textual
definition. It was wrong in both directions — it read `fputs(...)` in
`base/i18n/build_utf8_validator_tables.cc` as a definition when it is a call,
and it would have missed ffmpeg's aarch64 assembly entirely, where definitions
come from a `function ff_hevc_put_hevc_qpel_h4_8_neon` macro and look nothing
like C.

Which library a source lands in comes from the compile database, so no list
name or target mapping is hardcoded. One restriction applies: when the patch
**declares** the target it adds the source to, only that target counts.
Patches `0057` and `0063` add angle's pre-existing `test_utils/ANGLETest.cpp`
into targets they declare themselves, and that source is compiled into three
other angle test libraries on platforms where the new target does not exist;
closing over those would report their unrelated undefined symbols as this
series' fault.

The check runs as phase 1e (plan, and join the archives to the build) and
phase 5 (the closure itself) of `scripts/checkseries.sh`, after the
classification report so that a compile failure is read first — an unresolved
symbol in a library whose sources did not compile is a consequence, not a
finding. Cost is one extra ninja target and two `nm` passes per library,
measured at 13s and 12s over the 2,209 archives of a full `macos-arm64`
output.

It fails when a required object is missing, and equally when it had nothing to
measure: a missing archive, an archive with no members, or a library none of
whose members are the objects we set out to check. A closure that ran on
nothing must never be reportable as a clean closure.

`.github/workflows/series-gate.yml` runs the gate on `linux-x64` and
`windows-x64` by default, weekly and on dispatch. It needs bootstrap, sync,
patch application and `gn gen` before it can compile anything, and compiling is
not the expensive part: on the `macos-arm64` graph the prerequisite closure of
the series' 123 objects is 15,586 other objects and 20,804 generated files,
about a fifth of that graph, because Chromium links a host tool to generate
each family of headers. A run is therefore roughly 80 minutes on Linux and 120
on Windows, about $5 and $15 at Blacksmith's per-vCPU-minute rates, against $42
to build those two platforms outright and about $100 for a four-target build
that finds the same error hours in.

Nothing is cached. The checkout is 28-50 GB against a 10 GB Actions cache
ceiling, and a checkout trimmed with `custom_deps` to fit would change the
build graph the gate reports on, turning a missing dependency into a file
reported as absent.

`macos-arm64` is deliberately not in the default pair, and not only because
the development machine already covers it. The series touches
`chrome/app/chrome_main_delegate.cc`, which on macOS also compiles into
`//chrome/app:test_support`, whose GN hard dependencies include
`phony/chrome/chrome_framework`. Ninja therefore builds the framework bundle
before it will compile that object — 277 planned edges ending in
`SOLINK 'Chromium Framework'`, against 63 codegen edges for the `chrome_dll`
object of the same file — which takes the closure from 53,670 prerequisites to
112,086. A cold macOS gate run costs about what a macOS build costs. Run it
against the local build directory, where that work is already done.

### Packaging

`scripts/package-artifact.sh` stages the runtime payload, writes the archive
and creates a sibling release manifest with the archive's SHA-256. The release
build then attests the archive before uploading it with the manifest.

Packaging fails if required runtime files are absent. Linux needs the browser,
crash handler, ICU data, resource packs, V8 snapshot and English locale pack.
Windows needs the executable, Chromium DLLs and the corresponding runtime
data. Optional graphics libraries are copied when present. On macOS the app
bundle contains the runtime payload. Every target also carries the repository
license, an optional notice, the generated build manifest and the profile
resources. The archive root derives from its filename and has no nightly or
release marker.

The artifact filename and version come from
`.github/release/artifact-policy.json`. The Chromium version must agree with
`build/CHROMIUM_VERSION`. Publication requires exactly one valid manifest
and hash-matching archive for each resolved build target, not every platform
recognized by the policy.

`catalogue_version` comes from `resources/profiles/catalogue.json`. The
publish job checks out the tag and requires the manifest to name the
catalogue at that revision. The catalogue has its own version sequence, so the
release policy defines this relationship instead of pinning a catalogue
integer.

## Hosted runners and workspaces

`scripts/resolve-build-targets.sh` selects these WarpBuild runners:

| Target | Runner label | Build environment |
| --- | --- | --- |
| `linux-x64` | `warp-ubuntu-latest-x64-32x` | Pinned `linux/amd64` container on Linux x64 |
| `linux-arm64` | `warp-ubuntu-latest-x64-32x` | ARM64 cross-build in the same container architecture |
| `macos-arm64` | `warp-macos-26-arm64-12x` | Native macOS ARM64 with the pinned SDK |
| `windows-x64` | `warp-windows-2025-x64-32x` | Native Windows x64 against the runner's own VS Build Tools and SDK |

The macOS and Windows labels are dated, so a runner image change is a visible
edit here rather than a drift under a moving alias. The Linux label is the
`-latest-` alias, currently Ubuntu 24.04, because WarpBuild offers no dated
24.04 label; that is acceptable only because the Linux build runs inside the
pinned `linux/amd64` container and the host image is not its reproducibility
boundary.

Linux arm64 runs on x64 because the pinned `linux/amd64` container is the
reproducibility boundary. `scripts/in-linux-build-container.sh` explicitly
requires a Linux x86_64 host. Chromium's hermetic Clang and LLD plus the
pinned ARM64 sysroot produce the target binary; an ARM64 runner is not a
substitute for this host environment. `scripts/verify-runner.sh` checks both
Linux targets against `RUNNER_OS=Linux` and `RUNNER_ARCH=X64` before bootstrap.
The cross-built binary's smoke check inspects its ELF architecture rather
than executing it on x64.

`windows-x64` builds natively on `warp-windows-2025-x64-32x`. It cannot
use the Linux container. `scripts/lib.sh` exports
`DEPOT_TOOLS_WIN_TOOLCHAIN=0` so `vs_toolchain.py` resolves Visual Studio and
the Windows SDK from the runner's own installation; `build/args/windows-x64.gn`
pins no toolchain path. Pinning `visual_studio_path` there would oblige it to
pin `visual_studio_version`, `windows_sdk_version` and `wdk_path` as well, and
would force `visual_studio_runtime_dirs` empty so the CRT redistributables
never reach the package.

That image excludes the full Visual Studio IDE and provides VS Build Tools
2022 instead. `build/vs_toolchain.py` searches `BuildTools` alongside
`Enterprise`, `Professional`, `Community`, `Preview` and `Insiders`, so
autodetection finds it, which is the reason no path is pinned.

#### What the Windows build needs that Build Tools alone does not give

"VS Build Tools is installed" is not the same claim as "Chromium can build".
The prerequisite set comes from three separate products, and a complete
compiler plus a complete SDK is not evidence that the third is there. Every
entry below is asserted by `scripts/verify-host-tooling.sh` from the
filesystem alone, before bootstrap, and every failure names the component id
that supplies the missing file.

| Prerequisite | Source that requires it | Supplied by |
| --- | --- | --- |
| `VC/Tools/MSVC/<ver>/bin/Hostx64/{x64,x86}/cl.exe` | `setup_toolchain.py` asserts `cl.exe is not found in PATH`, and `win_toolchain_data.gni` runs it for x86 **and** x64 even for an x64-only target | `Microsoft.VisualStudio.Component.VC.Tools.x86.x64` |
| `VC/Auxiliary/Build/vcvarsall.bat` | `setup_toolchain.py` raises `<path> is missing - make sure VC++ tools are installed` | same component |
| `VC/Tools/MSVC/<ver>/atlmfc/include/{atldef.h,atlbase.h}` and `atlmfc/lib/x64` | `base/win/atl_throw.h` includes `<atldef.h>` and `base/BUILD.gn` compiles `base/win/atl_throw.cc` on every Windows build; `base/win/atl.h` pulls eight more ATL headers; Dawn's DXC includes `<atlbase.h>` from `dxc/Support/WinIncludes.h` | `Microsoft.VisualStudio.Component.VC.ATL` |
| `<VS>/DIA SDK/bin/amd64/msdia140.dll` | `vs_toolchain.py` `_CopyDebugger` copies it with no existence check, during `gn gen` | the DIA SDK, shipped with any VS C++ workload |
| `<SDK>/Include/10.0.26100.0/{ucrt,um,shared}` and `<SDK>/Lib/10.0.26100.0/{ucrt,um}/x64` | `setup_toolchain.py` checks every emitted `INCLUDE` and `LIB` entry and raises `Path "..." does not exist. Make sure the necessary SDK is installed.` | Windows 11 SDK 10.0.26100 |
| `<SDK>/Debuggers/x64/dbghelp.dll` | `vs_toolchain.py` marks it non-optional: `You must install Windows 10 SDK version ... including the "Debugging Tools for Windows" feature.` | the SDK feature `OptionId.WindowsDesktopDebuggers` |
| `%windir%/System32/{msvcp140,msvcp140_atomic_wait,vccorlib140,vcruntime140,vcruntime140_1}.dll` | with `DEPOT_TOOLS_WIN_TOOLCHAIN=0`, `vs_toolchain.py` takes its runtime source directories from `System32`, not the VS Redist tree, and `build/toolchain/win/BUILD.gn` runs `copy_dlls` during `gn gen`; `_CopyRuntimeImpl` does not check its source | the machine-wide Visual C++ Redistributable |

`dbgcore.dll` and `symsrv.dll` are optional by name in `vs_toolchain.py`'s own
table, and the CDB bundle (`cdb.exe`, `dbgeng.dll`, `dbgmodel.dll`,
`winext/`, `winxp/`) only matters if `//build/win:copy_cdb_to_output` enters
the graph. Those are reported and never fatal: failing on them would fail a
runner the build works on.

MFC is **not** required, and this is the one place the upstream instructions
are worth contradicting. `docs/windows_build_instructions.md` says to install
`Microsoft.VisualStudio.Component.VC.ATLMFC`, because the IDE checkbox it
names is "MFC/ATL support" and MFC depends on ATL, so that is how a human gets
ATL through the installer UI. No file Chromium compiles includes an `afx*.h`
header; `remoting`'s `atlapp.h` and `atlcrack.h` are WTL, vendored at
`third_party/wtl`, and WTL needs ATL. The ARM64 components the same document
lists are conditioned on building for ARM64 Win32, and `target_cpu` is `x64`.
`build/WINDOWS_VS_COMPONENTS` records both decisions with their sources.

Measured on the image by `.github/workflows/probe-runners.yml`, which runs the
whole provision-then-verify path on a 2 vCPU Windows runner with no Chromium
checkout: `dbghelp.dll` is absent, so the Debugging Tools feature is not
installed, and `atlmfc` is absent, so the ATL component is not installed.
Everything else in the table above is present, including the five `System32`
CRT DLLs, whose absence would have arrived as a bare Python traceback out of
`gn` rather than as anything nameable. The MSVC toolset is `14.44.35207`, the
SDK carries `Include` and `Lib` for `10.0.22621.0` and `10.0.26100.0`, and
`bin` carries six versions — which is exactly why the version the build uses is
not autodetected.

ATL being absent was learned the expensive way: the first Windows run to reach
compilation reached 33,797 edges and failed on `'atldef.h' file not found`.
That is why the check is now a table read from a pin file rather than a
hand-maintained list of the failures seen so far. Whether a component is
present is a property of the image, and the cheapest runner answers it
identically.

#### Revision, not presence

Everything above is a presence question, and there is a second axis that
presence cannot see. The SDK's directories and its registry keys under
`HKLM\Software\Microsoft\Windows Kits\Installed Roots` are named after the
major build — `10.0.26100.0` — and carry no servicing revision. So
`Include/10.0.26100.0/um` existing is a true statement that answers nothing:
revision 4654 and revision 7705 are indistinguishable by presence, ship the
same filenames, and differ in the contents of the headers.

That gap cost a run. With every presence check green, the `windows-x64` gate
reached **26,642 of 33,797 edges** and failed:

```text
FAILED: obj/ui/accessibility/platform/platform/uia_client_info_source_win.obj
../../ui/accessibility/platform/uia_client_info_source_win.cc(30,5):
  error: unknown type name 'IUIAutomationClientInfo'
```

`docs/windows_build_instructions.md:54-56` requires SDK **10.0.26100.7705**;
the image carries 4654. `build/WINDOWS_SDK_REQUIREMENTS` is the revision axis,
asserted by `scripts/verify-host-tooling.sh` with its own mechanism, because
the right instrument differs per requirement:

| Requirement | Kind | Instrument | Source |
| --- | --- | --- | --- |
| `IUIAutomationClientInfo`, `IUIAutomationClientInfoSource` declared under `Include/<version>/um` | symbol | the identifier must appear in some header there | `ui/accessibility/platform/uia_client_info_source_win.cc:24,30,56,66` uses both with no guard; SDK ≥ 10.0.26100.7705 supplies them |
| `CLSID_CUIAutomationClientInfoSource` defined in `Lib/<version>/um/x64/uuid.lib` | libsymbol | a fixed-string search over the archive's bytes | the header only *declares* it `EXTERN_C const CLSID`; `uia_client_info_source_win.cc:144` passes it to `CoCreateInstance`, so an old library compiles clean and fails at link |
| `Debuggers/x64/dbghelp.dll` ≥ `10.0.26100.3323` | version | its `FileVersion`, compared as a dotted version | `docs/windows_build_instructions.md:57-59`, "needed in order to support reading the large-page PDBs that Chrome uses to allow greater-than 4 GiB PDBs" |

Headers carry no version resource, which is why the first is asserted by
symbol rather than by number. Where a version resource does exist the number
wins, because a number can be compared and a symbol has to be chosen.

The library row is the one worth dwelling on, because without it this section
would have shipped a check that passed for the wrong reason. A header can
declare `EXTERN_C const CLSID X` while the library that *defines* `X` is an
older revision: the compile succeeds and the link does not. The series compile
gate never links. So a headers-only check would have turned the gate **green**
and left the four-target build to fail afterwards, at four times the price,
with the preflight still reporting the SDK as satisfactory. Asserting the
header without the library is a check that does not reach the thing that
breaks — the same shape as the earlier mistakes in this section, arrived at
from a different direction. When a header declares what a library defines,
both need a row.

Verified against Microsoft's own bytes rather than reasoned about: the
`Microsoft.Windows.SDK.CPP.x64` package at `10.0.26100.4188` carries an
8410 KB `Uuid.Lib` without that CLSID and the one at `10.0.26100.7705` carries
an 8726 KB `Uuid.Lib` with it. The filename is matched case-insensitively,
because Windows filesystems are and a copy keeps whichever name was already
there — `uuid.lib` and `Uuid.Lib` are one file, and a check that spells only
one of them fails on a tree that is entirely correct.

**Everything else revision-sensitive that was looked for, and what it is.**

- `base/win/windows_version.cc:30-32` has
  `#if !defined(NTDDI_WIN11_GE)` / `#error Windows 10.0.26100.0 SDK or higher
  required.` — Chromium enforces its own NTDDI floor, at compile time, with a
  message naming the SDK. It needs no assertion from us, and it is satisfied on
  4654: the gate compiled that file. It is listed here because it is the same
  class of requirement and it demonstrates that upstream guards the floor it
  cares about and does not guard the UIA types at all.
- `build/config/win/BUILD.gn:294` defines `NTDDI_VERSION=NTDDI_WIN11_GE`, so
  anything the SDK gates above that level is hidden rather than missing. The
  UIA interfaces are not NTDDI-gated, which is why 4654 produces
  "unknown type name" rather than a quieter failure.
- `ui/accessibility/platform/uia_client_info_source_win.h:21` forward-declares
  `struct IUIAutomationClientInfoSource;`, so the header compiles against any
  SDK and only the `.cc` fails. That is why the break was one translation unit
  26,000 edges in rather than an early, obvious error.
- Neither the header nor the implementation uses `__has_include` or any NTDDI
  guard. Verified by search: zero matches. An older SDK is therefore a hard
  compile error, not a silently disabled feature — which for a fingerprinting
  project is the better of the two failures.
- `vs_toolchain.py`'s `SDKIncludesIDCompositionDevice4` checks `dcomp.h` for
  `IDCompositionDevice4` only when the SDK's major build is ≤ 22621. At
  26100 it returns true without opening the file, so it is not a revision
  dependency here.

Adding to the table is the intended response to finding another. A row needs
the consumer cited and, where one exists, the run that found it.

Free space, unlike component presence, is not a property of the image alone.
Measured: a 2 vCPU Windows runner reported 70.8 GB free while
a 32 vCPU Windows runner reported 55 GB, a 14 GB difference on the class that
actually builds, and in the dangerous direction, because the cheap runner looks
roomier. Anything sized in bytes has to be measured on the instance class it
will run on, which is what the `census-windows-build-class` job exists for.
Checkout size is safe to take from the cheap runner, because the sync is
identical.

Measured on the image, `msdia140.dll` and the DIA SDK are present, and Build
Tools sits at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`,
under `Program Files (x86)` rather than `Program Files`. That path is why
`scripts/lib.sh` exports `vs2022_install`: `vs_toolchain.py`'s `MSVC_LOCATION`
table looks for 2022 under `%ProgramFiles%`, so every candidate path misses
and GN reports `No supported Visual Studio can be found` with the toolchain
sitting right there. `vs%YEAR%_install` is checked before the table, and
`vswhere -products '*' -version '[17.0,18.0)'` resolves the real path without
patching Chromium or assuming an edition.

#### Provisioning

`scripts/provision-windows-toolchain.sh` installs what is missing, and the
build, the gate and the probe all run it. It is idempotent in both phases:
with everything present it does nothing and exits 0, so an image that gains a
component later stops paying for it automatically.

The SDK's **headers and x64 libraries** are overlaid from Microsoft's own NuGet
payload packages, `Microsoft.Windows.SDK.CPP` and
`Microsoft.Windows.SDK.CPP.x64`, listed with a SHA-256 each in
`build/WINDOWS_SDK_PACKAGES`. The version comes from
`build/WINDOWS_SDK_INSTALLER_VERSION`, so headers, libraries and Debugging
Tools are one revision by construction with no second version to keep in step,
and the URL is derived from the id and version rather than pinned separately.
A `.nupkg` is a zip, and `7z` is already required on this target for
packaging, so reading one needs nothing new.

This replaced `winsdksetup.exe` for the headers, and the reason is measured.
Asked for `OptionId.DesktopCPPx64`, `OptionId.DesktopCPPx86` and
`OptionId.WindowsDesktopDebuggers` on `blacksmith-2vcpu-windows-2025`, the
installer ran the full 25-minute bound and had reached only package 004 of
many before being killed — exit 124, slow rather than stuck, but a quarter of
the gate's 180-minute budget spent before the first compile on every run. The
overlay does the same job in **15 seconds including the 213 MB download**,
measured on that runner: both packages fetched, digest-checked and overlaid
between 06:26:35 and 06:26:50. The Debugging Tools install that follows took a
further 8 seconds, so the whole SDK phase is 24 seconds against an install that
had not finished in 25 minutes.

It is also *better* provenance, not a compromise for speed.
`winsdksetup.exe` is a 1.4 MB bootstrapper: its bytes can be pinned, and it
then downloads whatever the service hands it. The packages are the payload
itself, pinned by digest, so what lands on the runner is what the repository
names. That is the difference between an unpinned network dependency and a
pinned artifact, which is what this repository requires of every other input.

The **Debugging Tools** still come from the pinned `winsdksetup.exe`, because
they are not in those packages and that one feature installs in about 40
seconds. `build/WINDOWS_SDK_INSTALLER_URL` is the version-specific link rather
than a "latest SDK" link, and `build/WINDOWS_SDK_INSTALLER_SHA256` is checked
before it runs, because a pinned URL only promises a name. Only
`OptionId.WindowsDesktopDebuggers` is requested, and only when `dbghelp.dll` is
actually missing.

One mixed-revision consequence, named rather than hidden:
`Lib/<version>/um/x86` stays at whatever revision the image carries, because
the x86 libraries are not overlaid. Nothing links x86 here — there is no
`windows-x86` target, and the only reason an x86 toolchain is configured at all
is that `build/toolchain/win/BUILD.gn` instantiates `win_toolchains("x86")`
beside `x64`, whose setup only requires those directories to exist.

The Visual Studio components come from the VS installer's `modify --add`. The
installer is located rather than hardcoded — derived from the resolved install
(`<...>/Microsoft Visual Studio/Installer`), with both `Program Files` trees as
fallbacks, accepting either `setup.exe` or `vs_installer.exe` — and driven with
`--quiet --norestart --nocache`: no UI on a headless runner, never reboot the
runner out from under a job, and delete the payloads afterwards, because
Windows is the target with the least free disk.

Three details there are not optional:

- **Exit code 3010 is a success.** Microsoft documents it as "Operation
  completed successfully, but install requires reboot before it can be used",
  and so is 1641. Treating a non-zero status as failure would fail a run that
  installed everything it was asked for.
- **The exit code is read from a file, not from `$?`.** A shell sees only the
  low eight bits of a process status, so 3010 would arrive as 194 and
  `-1073720687` as noise. The install runs under PowerShell, which writes the
  real 32-bit value out.
- **No exit code is trusted anyway.** Both phases re-test the same files they
  tested before installing, and those files are the only success signal.
  Headers and import libraries need no registration — clang-cl only reads
  them — so a 3010 should be harmless here, but "should" is not a check. If ATL
  ever does need the reboot it asked for, provisioning fails naming the
  component instead of handing the gate a broken toolchain, and the message
  says that baking it into the image is then the only option.

Measured, and it settles whether provisioning at job start is viable at all:
the installer returns **0 in 23 seconds** on this image and the ATL headers are
usable immediately, in the same job. It never asked for a reboot. The payload
is 9 MB on disk. The behaviour on 3010, 1641, each documented failure code and
an undocumented one was demonstrated against a stub installer rather than
against a real reboot-required install, so that half is proven code and
reasoned premise, not a measurement — which is why the re-check exists.

One honest limit, and it is now confined to the Visual Studio component. The
ATL payload is fetched from Microsoft at whatever servicing version the
installed Build Tools is on, and there is no digest we can pin — a per-job
network dependency, small at 9 MB and 23 seconds measured, but real. The VS
package cache on the image is 44 MB, so it does come off the network rather
than out of a local cache.

The Windows SDK used to share that limit and no longer does. Moving its
headers and libraries to digest-pinned NuGet packages turned the largest
unpinned input on this target into a pinned artifact, which is the same
treatment every other build input gets. The VS component is what is left.

It does not widen the reproducibility boundary as much as it first appears:
`build/args/windows-x64.gn` deliberately pins no toolchain path, so the entire
MSVC header set, the SDK and the CRT are *already* resolved from the runner's
installation rather than from a pin. ATL joins a set that was never pinned,
rather than opening a new hole. What is pinned is the request —
`build/WINDOWS_VS_COMPONENTS` names the component ids and `scripts/lib.sh`
bounds the VS version to `[17.0,18.0)`.

"Already unpinned" is an argument for recording what we got, not for continuing
not to, so `scripts/build.sh` writes seven fields into `build/MANIFEST.lock` on
Windows builds: the resolved Visual Studio instance version, the MSVC toolset
version, the SDK directory version, the SDK servicing revision, and digests of
`build/WINDOWS_VS_COMPONENTS`, `build/WINDOWS_SDK_REQUIREMENTS` and
`build/WINDOWS_SDK_PACKAGES`. That makes
the input *attributed* rather than pinned: a reproducibility comparison that
disagrees can now be traced to a toolset or SDK upgrade instead of being
blamed on the patches. Pinning it properly means building the runner image
ourselves, which is not done.

`windows_sdk_revision` is the one that had to be added rather than deduced.
This repository's contract is that two builds from the same pins produce
byte-identical output, and a pin naming a major SDK but not a servicing
revision does not satisfy it. That is no longer an argument: revisions 4654
and 7705 both live in a directory called `10.0.26100.0` and differ in whether
a type Chromium requires exists at all, so the same pins demonstrably compiled
different headers. The revision is measured from `dbghelp.dll`'s version
resource, because it is the one SDK file with a version that tracks servicing;
the headers have none, which is the same fact that forces
`build/WINDOWS_SDK_REQUIREMENTS` to assert them by symbol.

Which SDK version the build uses is not decided by any pin of ours, and cannot
drift. `build/vs_toolchain.py` hardcodes `SDK_VERSION = '10.0.26100.0'` and
prints it verbatim as GN's `sdk_version`, with
`build/toolchain/win/setup_toolchain.py` holding a second copy as a
cross-check. There is no version autodetection to mislead, so an SDK
directory appearing alongside can never be selected over the intended one. That
pin travels with `build/CHROMIUM_VERSION`, which is why
`build/args/windows-x64.gn` names no version either.

`build/WINDOWS_SDK_VERSION` is a third copy of it, and exists only because
`scripts/verify-host-tooling.sh` has to check `<SDK>/Include/<version>` before
a checkout exists to read the constant from. A mirror nothing compares is a
mirror that drifts, in the worst direction — the preflight would confirm an SDK
version the build does not use and pass — so `scripts/configure.sh` reads
`SDK_VERSION` out of `build/vs_toolchain.py` and fails if the two disagree,
naming the value to correct. That check sits in `configure.sh` and not in the
preflight for the same reason the preflight needs the mirror at all: it is the
first step where both halves exist.

The provisioning script still logs the `bin`, `Include` and `Lib` version
directories before and after, because a component of the pinned version
*disappearing* is a real failure and the listing is how it would be
recognised.

Measured build sizes:

| Target | Total | Checkout | Output | depot_tools |
| --- | --- | --- | --- | --- |
| `macos-arm64` | 66.6 GB | 50 GB | 16 GB | 0.7 GB |
| `windows-x64` | 44.8 GB | 28.1 GB | 16 GB | 0.7 GB |
| `linux-x64`, `linux-arm64` | unmeasured | unmeasured | 16 GB | 0.7 GB |

Linux is unmeasured because both Linux targets have only ever run on runners
with over 1 TB free, so no run has produced a figure. Do not extrapolate one
from another target. The Windows checkout was extrapolated from the macOS
measurement once, and the guess was 18 GB too high.

macOS is the largest of the three because it fetches
`third_party/swift-toolchain`, 3.9 GB that no other target sees, and carries a
larger `third_party` overall.

Windows has the least storage of the four targets, and the advertised 130 GB is
the volume size rather than free space: measured on the image, 76 GB is the
image itself and 55 GB is free. `scripts/reclaim-windows-disk.sh` removes
measured, build-irrelevant software before the build, which brings free space to
63.5 GB against 44.1 GB of checkout and output. That leaves 19 GB of headroom,
so the Windows build fits. The reclaim candidates came from a disk census on the
image, not from a list of things that sound unnecessary.

The checkout holds 17.5 GB of dependency `.git` directories that look like
reclaimable disk. They are not reclaimed, and the measurements say they do not
need to be.

One trap before anyone reclaims them anyway.
`third_party/angle/src/commit_id.py` reads git to bake `ANGLE_COMMIT_HASH`,
`ANGLE_COMMIT_DATE` and `ANGLE_COMMIT_POSITION` into the binary, and its
`git rev-parse` sits inside a bare `except: pass`, so a missing `.git` silently
yields `"unknown hash"` rather than failing. That string reaches ANGLE's own
`GL_VERSION` through `ANGLE_VERSION_STRING`
(`third_party/angle/src/libANGLE/Context.cpp`), which is what makes it look like
a fingerprint surface. It is not one: the GPU service never forwards ANGLE's
string. `GetServiceVersionString` in
`gpu/command_buffer/service/gl_utils.cc` returns the constant
`"OpenGL ES 2.0 Chromium"` or `"OpenGL ES 3.0 Chromium"`, and Blink wraps that
constant, so `getParameter(VERSION)` reads
`WebGL 1.0 (OpenGL ES 2.0 Chromium)` on every machine. Pruning those
directories would still silently change compiled-in strings in the Windows
binary and not in the others, for disk the build does not need.

The active Python in `hostedtoolcache` is load-bearing, because `python3`
resolves into it, and `C:\Windows\Installer` is needed by the SDK installer this
build runs.

depot_tools on Windows needs a one-time `bootstrap/win_tools.bat` run. A fresh
clone has no `git.bat`, and `gclient_scm` hardcodes `git_exe = "git.bat"` on
win32, so a sync fails without it. `scripts/bootstrap.sh` does this.

`scripts/bootstrap.sh` reports free space on every run, and the workflow reports
disk again after the build even when it fails, so each target's real consumption
ends up in its log. The Windows checkout is measured directly by the
`measure-windows-checkout` job in `.github/workflows/probe-runners.yml`, which
syncs Chromium on a 2 vCPU runner. That job and `census-windows-build-class`
share one `measure_windows_disk` dispatch input, because neither number decides
anything alone and the 32 vCPU class is the most expensive runner here. The
weekly probe must not spin it for a `df`.

Each job sets `APOSTATE_WORKSPACE` to the generated directory
`$RUNNER_TEMP/apostate-workspace`. Chromium, depot_tools, caches, output and
lineage are local to that VM; the workflow restores no build cache and adopts
no existing output directory. Every nightly and release fetches and builds
from scratch. Outside CI, `scripts/lib.sh` defaults to a workspace beneath
the repository and accepts an `APOSTATE_WORKSPACE` override.

### Select the platform set

The repository variable `APOSTATE_BUILD_TARGETS` accepts a comma-separated
list such as `macos-arm64,linux-x64`. When unset or empty, it resolves to all
four targets. The resolver requires both a target GN file and a runner
mapping, so an unknown target fails during setup rather than entering a build
queue.

The nightly workflow's `targets` dispatch input overrides the repository
variable for one run. Release builds use the repository variable and publish
exactly that resolved platform set. A subset release does not need artifacts
for unselected platforms.

Adding Android would make one dormant interaction live. The software HEVC
decoder from `0061` and `0109` sets `IsDecoderBuiltInVideoCodec(kHEVC)`, and
`chrome/services/media_gallery_util/video_thumbnail_parser.cc` branches on
that flag into a switch with no `kHEVC` case, so an HEVC config would take
the `default:` arm and yield a null thumbnail instead of the encoded data the
hardware path expects. Neither patch touches an Android ffmpeg config and
`media_gallery_util/BUILD.gn` compiles that file only `if (is_android)`, so
no shipped target reaches it today. The fix is not a `kHEVC` case beside
`kH264`: `0061` accepts only Main and Main10, so routing HEVC through the
built-in decoder would return null for every other profile, which is
precisely the set Android's hardware decoder handles.

## Keep extensions enabled

The common GN arguments leave `enable_extensions` at Chromium's default.
The extensions layer supplies page-visible `chrome.app` behavior. Disabling
it to reduce binary size changes browser capabilities before a profile is
loaded; runtime profile settings cannot restore compiled-out features.
