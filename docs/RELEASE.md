# Release policy

`.github/workflows/release.yml` publishes browser artifacts from a semver tag
after its release gate, target builds and artifact checks succeed.
Nightly builds use the same reusable build job but retain their outputs as
Actions artifacts and do not create build-provenance attestations.

## Release identity and platform set

`.github/release/artifact-policy.json` defines the package version, Chromium
version, recognized platforms, artifact filenames and release-manifest fields.
The release version is `0.2.1`; `build/CHROMIUM_VERSION` pins Chromium to
`152.0.7977.83` and must agree with the policy.

The profile catalogue has its own version sequence. Each manifest takes
`catalogue_version` from `resources/profiles/catalogue.json` at its
`source_revision`. Publication checks it against the catalogue at the release
tag, rather than a constant in the release policy.

The CI targets are `linux-x64`, `linux-arm64`, `macos-arm64` and
`windows-x64`. `scripts/resolve-build-targets.sh` selects all four when the
repository variable `APOSTATE_BUILD_TARGETS` is unset or empty. Set that
variable to a comma-separated subset to narrow a release. The publish job
requires exactly the resolved targets, with one manifest and archive per
target.

Linux arm64 is a cross-build on the x64 runner inside the pinned
`linux/amd64` container. Windows x64 builds natively against the runner's own
Visual Studio and Windows SDK.
See [Build contract](BUILD.md) for runner mappings and toolchain requirements.

The macOS archive is Developer ID signed, notarized and stapled from v0.2.0
onward; the release runs with the six signing secrets configured (see
[Signing the macOS bundle](BUILD.md#signing-the-macos-bundle)), and a run
without them says so and ships the unsigned bundle rather than failing. The
signed bundle is not the bundle whose hashes
`build/MANIFEST.lock` records: signing runs after the build, writes to a
separate tree, and adds a signature carrying a timestamp and a certificate,
which is the one part of a release that cannot be reproducible. Everything
else is byte-identical to the reproducible build output, and the archive's
`sha256` in the release manifest is computed over the signed archive, so the
digest a user checks is the digest of what they downloaded. The three other
platforms carry no platform signature.

To verify a macOS release:

```sh
codesign -dv --verbose=4 Chromium.app
spctl -a -t exec -vv Chromium.app
xcrun stapler validate Chromium.app
```

`codesign` reports the authority chain, the team identifier and the hardened
runtime flag; `spctl` answers `accepted` with `source=Notarized Developer
ID`; `stapler` confirms the notarization ticket travels with the bundle, so
a first launch works with no network. Provenance is a separate claim with a
separate check — see [Verify a download](#verify-a-download).

The six `APPLE_*` repository secrets that make this happen, and how to
create the certificate and the notary key, are documented under
[Signing the macOS bundle](BUILD.md#signing-the-macos-bundle). A release
built without them still succeeds and produces an unsigned macOS archive;
check the `Sign and notarize (macOS)` step in the build log, which says
`no signing identity configured` when that happened, and the packaging step,
which names the tree it staged from.

## Cut a release

For a source revision that satisfies the release gate:

1. Set the repository variable `APOSTATE_BUILD_TARGETS` only if narrowing the
   default three-target set. This is a repository variable, not a shell variable
   on the maintainer's machine.
2. Refresh the two artefacts the release gate requires to be current, in this
   order and as the last commit before the tag. Both are described under
   [Two pre-tag refresh steps](#two-pre-tag-refresh-steps).
3. Create and push a tag in the exact form `vMAJOR.MINOR.PATCH`.

For example, when publishing package version `0.1.0`:

```sh
git tag -a v0.1.0 -m 'Apostate 0.1.0'
git push origin v0.1.0
```

The tag push triggers the rest. There is no release secret to configure and
no maintainer-held artifact key to manage. Annotated and lightweight tags both
work; a tag signature is not required or checked.

Creating a tag in this repository requires push access, the same authority
that permits changing its workflow files. Requiring a tag signature would
add another key to manage without adding an independent trust root. Artifact
provenance comes from GitHub's keyless attestation, which binds each archive
to the repository, commit and workflow that produced it.

Manual dispatch accepts an existing `release_tag` and requires `confirm=true`
to guard against starting a multi-hour paid build accidentally. It executes
the same gate and build path; the confirmation is not an authentication check.

### Two pre-tag refresh steps

Two checked-in artefacts describe a state the repository has moved past, and
neither is repaired by a CI build: the release gate runs before any build is
scheduled, so an input it rejects stays rejected. Both refreshes belong to
the last commit before the tag.

Everyday runs of `scripts/validate-release-baseline.py` and
`scripts/test_profile_resolver.py` report both as a notice and a skip and exit
zero, because between releases they are expected to be behind and a check that
is permanently red is a check nobody reads. The release gate turns both into
hard failures.

#### The patch digests in `build/MANIFEST.lock`

`patch_series_sha256` is the SHA-256 of `patches/series`.
`patch_contents_sha256` covers every listed patch's name and complete bytes,
in series order with NUL separators, so it moves when a patch is edited even
though the series file did not change.

Both are functions of files in this repository, so no build is needed:

```sh
python3 scripts/validate-release-baseline.py --refresh
```

It is idempotent, prints the digests it moved, and refuses to run over a
series that does not validate. It worked when
`python3 scripts/validate-release-baseline.py --release` exits zero.

Run it **last**, once `patches/series` and the patch files have stopped
moving; every subsequent patch edit invalidates it again.

It rewrites those two fields and nothing else. The rest of `MANIFEST.lock` is
evidence from a build, and after a digest-only refresh its `[outputs]` hashes
still describe binaries built from the earlier patch set. That binding is not
faked here: the authoritative output-to-patch record is the append-only build
lineage beside the manifest, which `scripts/build.sh` writes with the
`patch_contents_sha256` and `outputs_sha256` of each build together. A release
build regenerates the whole file from its own inputs.

#### The golden profile digests in `scripts/test_profile_resolver.py`

`CompositionTests.GOLDEN_PROFILES` pins one SHA-256 per persona over the
composed profile. Those digests were produced by the C++ compositor in the
browser process, and comparing them against this repository's Python resolver
is the only evidence that the two implementations agree byte for byte. **Do
not recompute them from `scripts/profile_resolver.py`**: that replaces a
cross-implementation comparison with the module agreeing with itself, and
reports it as success.

So this one does need a build, on the host the vector was taken on — macOS
arm64, ANGLE/Metal, 14 logical cores, 36 GiB — because a capability table is
bound to the backend serving it.

```sh
bash scripts/build.sh macos-arm64

# No switch prints the composed profile. Patch 0005 copies
# --apostate-profile onto every child process, so a child's command line is
# the readout, and `ps` must be given -ww or it truncates the base64.
.workspace/src/out/macos-arm64/Chromium.app/Contents/MacOS/Chromium \
  --fingerprint=12345 --fingerprint-platform=windows \
  --user-data-dir="$(mktemp -d)" about:blank &
sleep 5
ps -Awwo args= | tr ' ' '\n' | grep -m1 '^--apostate-profile=' \
  | cut -d= -f2- | base64 -d > native-windows.json
kill %1

python3 - native-windows.json <<'PY'
import hashlib, json, sys
profile = json.load(open(sys.argv[1]))
encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
print(len(profile), sorted(profile))
print(hashlib.sha256(encoded).hexdigest())
PY
```

Repeat with `--fingerprint-platform=macos` and `=linux`, then paste each
digest and its section list into `GOLDEN_PROFILES` and `GOLDEN_SECTIONS`.
`ensure_ascii=False` is not a detail: Chromium's JSON writer emits raw UTF-8
for non-ASCII, and the macOS persona's `speech.voices` names are what make
that digest evidence about escaping rather than only about field values.

It worked when

```sh
APOSTATE_REQUIRE_NATIVE_GOLDENS=1 python3 scripts/test_profile_resolver.py
```

exits zero with no skips. Without that variable the two golden tests skip and
print which sections moved; with it they fail, which is how the release gate
runs them. A profile whose section set still matches the pinned vector is
compared outright, so the skip cannot hide a drift inside the shape the
digests cover.

## What the workflow checks

The release gate runs on a hosted Ubuntu runner. It validates the exact tag
form, fetches tags, resolves the requested tag to a commit and requires the
checkout to match that commit. It then validates the checked-in build baseline
with `--release` and runs the resolver checks with
`APOSTATE_REQUIRE_NATIVE_GOLDENS=1`, followed by the Python and Node package
tests. Those two settings are what make a stale build record and a stale
cross-implementation digest fail here rather than at a tag nobody can undo.

Target resolution follows the gate. Each selected target then runs
`.github/workflows/build-target.yml` on a fresh Blacksmith VM. The job checks
host tools and runner identity, fetches the pinned sources, applies patches,
builds, checks the resolver and generated baseline, performs a binary smoke
check, packages, attests and uploads the result. The Linux arm64 smoke check
checks the ELF machine type because the x64 build host cannot execute it.

The release matrix runs every target concurrently with `fail-fast: false`.
Each leg is its own VM, so serialization saves nothing; and a leg that
finishes keeps its artifact on the run, so when another leg fails for a
reason of its own, re-running the failed jobs rebuilds only those. v0.2.0
paid twice for that: two finished Windows builds were cancelled by
`fail-fast: true` when the macOS leg failed on a signing secret and then on
a lost runner. Both release and nightly workflows share the
`apostate-build` concurrency group with `cancel-in-progress: false`,
preventing overlapping full-build runs.

The publish job checks the expected platform set, manifest schema and exact
field set, package and Chromium versions, catalogue version at the tag,
artifact filename and platform binding, source revision, and every archive's
SHA-256. Missing, duplicate or unexpected platforms fail publication. It then
creates the GitHub release with the archives and their manifests. Provenance
verification through `gh attestation verify` is an out-of-band operation,
not an additional check performed by the publish job.

Publication uses `gh release create --verify-tag`. That flag requires the tag
to exist before creating the release; it does not verify a tag signature.

## Archives and manifests

`scripts/package-artifact.sh` writes a `.tar.zst` archive for each available
target. The policy reserves `.zip` for Windows. Each archive has the runtime
files needed by its target, the repository license, an optional notice, the
generated build manifest and the profile resources. Missing required runtime
files fail packaging. The release manifest is generated beside the archive
with a `.manifest.json` suffix.

`release/manifest.schema.json` permits exactly these fields:

| Field | Value |
| --- | --- |
| `package_version` | Package version from the artifact policy |
| `chromium_version` | Pinned Chromium version |
| `catalogue_version` | Catalogue version at the source revision |
| `platform` | Target built for this archive |
| `artifact` | Exact archive filename from the policy |
| `sha256` | SHA-256 of the complete archive bytes |
| `source_revision` | Repository commit that produced the archive |
| `patch_series_sha256` | SHA-256 of the patch-series file |
| `build_manifest_sha256` | SHA-256 of the generated build manifest |

Packaging writes UTF-8 JSON with sorted keys, compact separators and exactly
one trailing newline. Each release identity binds the source and build inputs
to its archive; do not replace published artifacts with different bytes.

The package version is pinned once, in
`.github/release/artifact-policy.json`; the schema requires the shape and the
publish job requires the manifest to state the policy's number. The schema
pinned the number too until v0.2.0, whose publish job failed on it after
every other version site had been bumped. The v0.2.0 artifacts were built
from `6d30e25`, the commit their manifests name; the tag was moved one
commit forward, to the schema fix, so that the tagged tree accepts its own
manifests. That commit changes no build input: the manifests'
`patch_series_sha256` and `build_manifest_sha256` are those of the tagged
tree.

## After the binaries publish

The packages are published separately from the binaries and are not blocked
on them. Each launcher can install a release it was built before, by fetching
the per-artifact manifest the release published beside each archive:
`releases/download/v<package version>/<archive>.manifest.json` first, then
`releases/latest/download/<archive>.manifest.json`. So `pip install apostate`
and `npm install @heretic-tech/apostate` work the moment a binary release
exists, whatever the launcher's own version is, and a launcher-only fix
ships without rebuilding Chromium.

That fallback is a transport-integrity check: it detects a corrupted or
truncated download, because the digest and the bytes come from the same
place. A digest baked into the package is the stronger claim, and this is how
to bake it in once a release exists:

```sh
gh release download vX.Y.Z -D /tmp/apostate-release
scripts/sync-packages.py --release /tmp/apostate-release --tag vX.Y.Z
```

`sync-packages.py` re-hashes every archive rather than trusting the manifest
that travelled with it, then writes one package manifest into
`python/apostate/assets/` and `npm/assets/`. Bump the launcher version in
`python/pyproject.toml`, `python/apostate/config.py` and `npm/package.json`
-- the script requires all three to agree and to be at or above the policy's
`package_version` -- commit both assets, and publish the packages.

The two version lines are deliberately separate. The manifest's `tag` binds a
package to a binary release; the package's own version moves on its own. A
0.1.1 launcher that installs the v0.1.0 binaries is the ordinary case, not a
mistake, and `.github/release/artifact-policy.json` keeps naming the binary
release's identity rather than the launcher's.

Neither provenance claim changes here: run
`gh attestation verify <archive> --repo heretic-tech/apostate` for that, from
either path.

## Verify a download

The Python and Node installers read the manifest and validate its artifact
binding before extraction. They compute SHA-256 over the complete downloaded
archive and require it to equal `sha256` in the manifest. A hash mismatch
fails installation before the archive is extracted. This integrity check
needs no additional network request or external verification tool.

SHA-256 detects bytes that differ from the manifest. Build provenance is a
separate check. Release builds call `actions/attest-build-provenance` to
create a GitHub artifact attestation for each archive. Sigstore mints an
ephemeral keypair for that workflow run and discards it; the attestation binds
the artifact to the repository, commit and workflow.

Verify that provenance against the repository with GitHub CLI:

```sh
gh attestation verify <artifact> --repo <owner>/<repo>
```

Replace the placeholders with the downloaded archive and the repository that
published it. Run this separately when checking provenance. Installers enforce
the archive hash before extraction; they do not invoke GitHub CLI or query
GitHub for attestations.

## Release-candidate evidence

`scripts/release-candidate-check.py` runs the local validators and tests
available in a checkout and reports missing evidence as `blocked`. It does
not download, build or publish artifacts. Its artifact-presence checks do not
verify archive hashes or attestations, and it does not exercise installation
from built package archives.

The checker inventories all four contract targets, including Windows, and
records unprovided native and conformance evidence as blocked. It is not called
by the release workflow and is not the target-selection gate for a subset
release.

A passing release workflow proves its listed checks, not native process
propagation, full browser conformance, installation on every platform or an
independent reproducibility result. Keep runtime and reproducibility evidence
separate from CI status. `scripts/verify-reproducible.sh` compares clean-build
output hashes when collecting that evidence; the release workflow does not
invoke it.

Physical-device and compatibility claims follow [Methodology](METHODOLOGY.md)
and [Profile specification](PROFILE_SPEC.md). Report only claims supported by
the corresponding captures and conformance runs. A schema check, successful
compile, renderer string or single detector result does not establish those
milestones.
