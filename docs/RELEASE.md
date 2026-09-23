# Releases

A release is a GitHub release holding one archive and one manifest per
platform. Pushing a version tag starts it: `.github/workflows/release.yml`
checks the tree, builds every target and publishes the result. The pip and npm
packages are published by hand afterwards.

## Cut a release

1. Make the last commit before the tag:
   - Set the new version in all five places: `python/pyproject.toml`,
     `python/apostate/config.py`, `npm/package.json`, `npm/src/index.ts` and
     `.github/release/artifact-policy.json`. `scripts/sync-packages.py
     --check` fails while they differ.
   - Refresh the patch digests in `build/MANIFEST.lock` with
     `python3 scripts/validate-release-baseline.py --refresh`. It rewrites
     `patch_series_sha256` and `patch_contents_sha256` and nothing else. Run it
     after the last patch edit; `--release` checks the result.
   - If the composed profiles changed, refresh the golden digests (below).
2. Tag that commit `vMAJOR.MINOR.PATCH` and push the tag:

   ```sh
   git tag -a v0.4.0 -m 'Apostate 0.4.0'
   git push origin v0.4.0
   ```

3. Wait for `release.yml`. Each platform builds on its own machine, all at
   once, and takes hours. If one fails, re-run the failed jobs; the finished
   platforms keep their artifacts and are not rebuilt.
4. The release is created as a draft. Download the archives with
   `gh release download`, launch each on its platform (Linux in a GPU-less
   container as well), then publish it with
   `gh release edit vMAJOR.MINOR.PATCH --draft=false`.
5. [Publish the packages](#publish-the-packages) with the release's digests.

The repository variable `APOSTATE_BUILD_TARGETS` (comma-separated, such as
`macos-arm64,linux-x64`) limits a release to some platforms; unset means all
four. To rebuild an existing tag, run the workflow by hand with that tag as
`release_tag` and `confirm` set to true.

The macOS leg signs and notarizes when the six `APPLE_*` repository secrets are
set ([BUILD.md](BUILD.md#signing-on-macos)). Without them it publishes an
unsigned macOS archive and the job log says so.

## What CI checks

Before any build, a hosted Ubuntu runner checks that the tag has the form
`vMAJOR.MINOR.PATCH` and names the checked-out commit, and runs
`scripts/validate-release-baseline.py --release` (the patch digests in
`build/MANIFEST.lock` match the series), `scripts/test_profile_resolver.py`
with `APOSTATE_REQUIRE_NATIVE_GOLDENS=1`, and the Python and Node package
tests.

Each target then runs `build-target.yml`: every build step from
[BUILD.md](BUILD.md), the resolver and baseline checks again,
`scripts/smoke-binary.sh`, signing on macOS, packaging, upload, and a GitHub
build-provenance attestation for the archive.

Before publishing, `scripts/verify-release-inputs.sh` requires exactly one
archive and one manifest per built platform. It checks each manifest's
versions against the release policy, its catalogue version and
`source_revision` against the tagged commit, and its `sha256` against the
archive. Then `gh release create --verify-tag` publishes them.

## Refreshing the native golden profile digests

`scripts/test_profile_resolver.py` pins one SHA-256 per persona
(`GOLDEN_PROFILES`) over the profile the browser composes for
`--fingerprint=12345`, and its section list (`GOLDEN_SECTIONS`), so the Python
resolver in `scripts/profile_resolver.py` must produce the same bytes as the
browser. Take the digests from a native build: digests recomputed with the
resolver make the test compare it with itself. Use a host like `GOLDEN_HOST`,
macOS on Apple silicon with 14 cores and 36 GiB, because cores and memory are
capped at the host's. Every child process carries the composed profile in its
`--apostate-profile` switch, so read it from `ps`:

```sh
scripts/build.sh macos-arm64
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
data = json.dumps(profile, sort_keys=True, separators=(",", ":"),
                  ensure_ascii=False).encode("utf-8")
print(sorted(profile))
print(hashlib.sha256(data).hexdigest())
PY
```

Repeat with `--fingerprint-platform=macos` and `linux`, and paste each digest
and section list into the test. Without `APOSTATE_REQUIRE_NATIVE_GOLDENS` a
stale digest is skipped with a message; with it the test fails, which is how
the release gate runs it.

## Archives and manifests

Each platform gets `apostate-<chromium version>-<platform>`, as `.tar.zst` for
Linux and `.zip` for macOS and Windows, such as
`apostate-152.0.7977.83-linux-x64.tar.zst`. An archive holds the browser, the
licence, `build/MANIFEST.lock` and the profile resources.

Beside it is `<archive>.manifest.json`, with exactly the fields
`release/manifest.schema.json` allows: `package_version`, `chromium_version`,
`catalogue_version` (from `resources/profiles/catalogue.json` at the tagged
commit), `platform`, `artifact` (the archive name), `sha256` (of the whole
archive), `source_revision` (the commit it was built from),
`patch_series_sha256` (of `patches/series`) and `build_manifest_sha256` (of
the archive's `build/MANIFEST.lock`).

Do not replace a published archive with different bytes. Cut a new version.

## Publish the packages

The packages download the browser and check it against a manifest. To give
them the new release's digests:

```sh
gh release download v0.4.0 -D /tmp/apostate-release
python3 scripts/sync-packages.py --release /tmp/apostate-release --tag v0.4.0
```

`sync-packages.py` hashes every archive again, then writes
`release-manifest.json` into `python/apostate/assets/` and `npm/assets/`.
Commit both files and publish the packages to PyPI and npm.

## Verify a download

Both packages hash the whole archive and compare it with the manifest's
`sha256` before extracting anything. A mismatch stops the install. The digest
comes from the manifest inside the package when it lists the archive.
Otherwise the package fetches `<archive>.manifest.json` from the release
`v<package version>`, then from the latest release, and says so. A fetched
digest catches a broken download, but not a replaced one, because it comes
from the same place as the archive.

To check where an archive came from, verify its build-provenance attestation.
`apostate install --keep-archive` keeps the verified archive in the install
cache, or download it from the release page:

```sh
gh attestation verify apostate-152.0.7977.83-linux-x64.tar.zst --repo heretic-tech/apostate
```

A signed macOS bundle can also be checked with `codesign -dv --verbose=4`,
`spctl -a -t exec -vv` and `xcrun stapler validate` on `Chromium.app`.
