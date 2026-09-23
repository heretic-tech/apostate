# Build

Apostate is Chromium 152.0.7977.83 with the patches in `patches/` applied in
the order `patches/series` lists them. Every input is pinned in `build/` and
every step is a script in `scripts/`. A full build takes hours and around
70 GB of disk, so check each patch with `scripts/checkfile.sh` first.

## Targets

| Target | Build host | How |
| --- | --- | --- |
| `linux-x64` | Linux x86_64 | in the pinned Docker container |
| `linux-arm64` | Linux x86_64 | cross-built in the same container |
| `macos-arm64` | macOS on Apple silicon | native |
| `windows-x64` | Windows x64 | native, against Visual Studio 2022 |

An arm64 Linux machine cannot build either Linux target: the container needs
an x86_64 host. `linux-arm64` output cannot run on the machine that built it,
so `scripts/smoke-binary.sh` checks its ELF machine type instead of running it.

## What the host needs

- Every host: Python 3, Git, and at least 68 GB free in the workspace (46 GB
  for Windows). `scripts/bootstrap.sh` stops below that and warns below 100 GB.
- Linux: Docker with a running daemon, and `tar` with zstd support.
- macOS: the macOS 26.5 SDK (build 25F70) from an Xcode or Command Line Tools
  install, and `tar` with zstd support. Keep the pinned SDK even when a newer
  Xcode is active; the Xcode 27.0 SDK's `libSystem.tbd` breaks the bundled
  linker.
- Windows: Git for Windows (the scripts run in its bash), 7-Zip, Visual
  Studio 2022 with the C++ x64 toolset and ATL, and Windows SDK 10.0.26100.
  `scripts/provision-windows-toolchain.sh` installs the Visual Studio parts and
  the pinned SDK when they are missing.

`scripts/verify-host-tooling.sh <target>` lists everything a target needs that
the host lacks, in one pass.

## Pins

| File | Pins |
| --- | --- |
| `build/CHROMIUM_VERSION` | Chromium tag `152.0.7977.83` |
| `build/DEPOT_TOOLS_REVISION` | depot_tools commit |
| `build/args/common.gni` | GN args for every target: official build, proprietary codecs, Widevine support (the CDM itself is not bundled), no field-trial testing config |
| `build/args/<target>.gn` | GN args for one target |
| `build/MAC_SDK_VERSION`, `build/MAC_SDK_BUILD` | macOS SDK `26.5`, build `25F70` |
| `build/WINDOWS_SDK_*`, `build/WINDOWS_VS_COMPONENTS` | Windows SDK installer, packages and required revision; Visual Studio components |
| `build/linux/Dockerfile` | Ubuntu base image by digest and apt snapshot for the Linux container |
| `build/widevine-local.json` | Version and hashes of the Widevine CDM `scripts/provision-widevine.py` accepts |
| `patches/series` | Which patches apply, and in what order |

`scripts/lib.sh` exports `DEPOT_TOOLS_UPDATE=0`, so depot_tools stays at its
pin. Do not run `gclient sync` by hand; `scripts/fetch-sources.sh` syncs to the
pinned tag. `build/MANIFEST.lock` is output, not input: `scripts/build.sh`
writes the inputs and output hashes of the last build into it.

## Build step by step

The checkout lives in `.workspace/`; set `APOSTATE_WORKSPACE` to put it
elsewhere. Set the target once, then run the scripts in order:

```sh
export APOSTATE_TARGET=linux-x64

scripts/verify-host-tooling.sh $APOSTATE_TARGET
scripts/bootstrap.sh                                # pinned depot_tools, disk check
scripts/fetch-sources.sh                            # Chromium at the pinned tag, gclient sync, hooks
scripts/apply-patches.sh                            # patches/series in order, no fuzz
scripts/run-chromium-hooks.sh $APOSTATE_TARGET      # Linux targets only
scripts/prepare-linux-sysroot.sh $APOSTATE_TARGET   # Linux targets only
scripts/configure.sh $APOSTATE_TARGET               # common and target GN args, gn gen
scripts/build.sh $APOSTATE_TARGET                   # ninja, then build/MANIFEST.lock
scripts/smoke-binary.sh $APOSTATE_TARGET
scripts/package-artifact.sh $APOSTATE_TARGET "$PWD/apostate-152.0.7977.83-$APOSTATE_TARGET.tar.zst"
```

On Linux, `fetch-sources.sh` also runs Chromium's `install-build-deps.sh`,
which installs packages with apt. On Windows, run
`scripts/provision-windows-toolchain.sh` first.

The browser ends up in `.workspace/src/out/<target>/`: `chrome` on Linux,
`Chromium.app` on macOS, `chrome.exe` on Windows. The package step writes the
archive, a `<archive>.manifest.json` beside it with its SHA-256, and a staging
directory. Linux archives are `.tar.zst`; for macOS and Windows name the
archive `.zip`.

Useful settings:

- `APOSTATE_JOBS` sets ninja's job count (default: 75% of the CPUs).
- `APOSTATE_FRESH_BUILD=1` deletes `out/<target>` before configuring.
- `scripts/apply-patches.sh --check` reports whether the series is applied
  without changing anything. A normal run does nothing when the applied series
  is current, and otherwise resets the checkout and applies the whole series
  again.

## The Linux container

On Linux, `configure.sh`, `build.sh`, `checkfile.sh`, `checkseries.sh`,
`run-chromium-hooks.sh` and `verify-reproducible.sh` re-run themselves inside
the container, so you call them the same way on every host.
`scripts/in-linux-build-container.sh` builds the `linux/amd64` image from
`build/linux/Dockerfile` and runs one repository script in it with the network
off and only the repository and workspace mounted:

```sh
scripts/in-linux-build-container.sh --prepare
scripts/in-linux-build-container.sh scripts/build.sh linux-x64
```

## Check a patch before a full build

A full build is hours. Compiling the files a patch touches is minutes.

```sh
scripts/checkfile.sh third_party/blink/renderer/core/frame/navigator.cc
scripts/checkfile.sh base/apostate/compose.cc macos-arm64
```

`checkfile.sh` takes a path inside the Chromium checkout and an optional
target, and asks ninja to build just that file's objects, with any generated
headers they need. It needs a configured output directory, so run
`configure.sh` once first.

`scripts/checkseries.sh` does the same for every file the series touches on
one target, and reports any file that produced no object. `--list` prints the
files without compiling. A patch that adds sources to a library can also be
checked with `scripts/series-symbol-closure.py`.

On macOS, ninja can leave an older framework inside `Chromium.app` after a
rebuild. Before you measure a change, check the binary contains it:

```sh
cd .workspace/src/out/macos-arm64
strings -a "Chromium.app/Contents/Frameworks/Chromium Framework.framework/Versions/152.0.7977.83/Chromium Framework" \
  | grep -c 'a string only your patch adds'
```

`0` means an old build. Delete `Chromium.app` and run `ninja chrome` in that
directory again; it takes seconds.

## Writing a patch

Edit the checkout under `.workspace/src`, then save the change as a file in
`patches/`: a `Subject: [PATCH] area: what it does` line, a short description,
and the output of `git -C .workspace/src diff` for the files you changed. Add
the file name to `patches/series` after every patch it depends on. The series
is ordered by dependency, not by number.

`apply-patches.sh` resets the checkout and reapplies the series whenever a
patch or `patches/series` changes, so an edit that no patch records is lost.
The checkout is shared by everyone working on patches; leave it as you found
it.

`scripts/validate-patch-headers.py` checks the hunk counts of every listed
patch and `--fix` repairs them. `scripts/validate-release-baseline.py
--series-only` checks the series order.

## Signing on macOS

`scripts/sign-macos.sh macos-arm64` signs `Chromium.app` with a Developer ID
certificate, notarizes and staples it, and writes the result to
`$APOSTATE_WORKSPACE/signed/macos-arm64/`, leaving `out/` as built.
`package-artifact.sh` packages the signed copy when there is one. The script
reads six variables: `APPLE_DEVELOPER_ID_P12_BASE64`,
`APPLE_DEVELOPER_ID_P12_PASSWORD`, `APPLE_SIGNING_IDENTITY`,
`APPLE_NOTARY_KEY_P8_BASE64`, `APPLE_NOTARY_KEY_ID` and
`APPLE_NOTARY_ISSUER_ID`. With none set it leaves the bundle unsigned and exits
0; with only some set it fails. Linux and Windows archives are not signed.

## Reproducibility

`scripts/verify-reproducible.sh <target>` builds twice from clean and compares
the output hashes. With `--against-manifest` it compares one fresh build with
the hashes in `build/MANIFEST.lock`. The optional Google API key variables read
by `build/args/common.gni` change the binary, so give both builds the same
values or leave them unset.

## CI

| Workflow | Runs | Does |
| --- | --- | --- |
| `check.yml` | pushes to `main`, pull requests, or by hand | Patch headers, series order, schemas, catalogue, resolver, GeoIP and package tests. No Chromium build. |
| `series-gate.yml` | Mondays, or by hand | Compiles every file the series touches for `linux-x64` and `windows-x64`, without linking. |
| `build-target.yml` | called by the two below | One target from a fresh workspace: every step above, then upload of the archive and manifest. |
| `build-nightly.yml` | daily, or by hand | Builds every target; keeps the artifacts 14 days. |
| `release.yml` | a `v*` tag push | Builds, checks and publishes a release. See [RELEASE.md](RELEASE.md). |
| `probe-runners.yml` | Wednesdays, or by hand | Checks the hosted runners' disk and Windows toolchain without building. |

CI builds start from an empty workspace and restore no build cache: the
checkout alone is larger than the Actions cache limit.
