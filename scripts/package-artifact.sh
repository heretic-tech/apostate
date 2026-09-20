#!/usr/bin/env bash
# Stage a runnable payload for one target, archive it, and describe it.
#
# "Runnable" is the point. Copying chrome plus a couple of shared libraries
# produces an archive that cannot start: Chromium loads its resource paks, ICU
# data and V8 snapshot from beside the executable at runtime, and without them
# the binary aborts before it draws anything. The required set below is
# therefore checked, not best-effort copied, and a missing member fails the
# build instead of shipping a broken archive.
#
# The manifest written beside the archive is the release manifest: exactly the
# field set pinned by .github/release/artifact-policy.json. It carries no
# signature -- build provenance comes from GitHub artifact attestations, which
# are keyless and verified out of band with `gh attestation verify`.
source "$(dirname "$0")/lib.sh"

usage() {
  cat >&2 <<'EOF'
usage: scripts/package-artifact.sh <target> <archive-path>

  target        macos-arm64 | linux-x64 | linux-arm64 | windows-x64
  archive-path  .tar.zst or .zip path to write. The payload manifest is
                written beside it as <archive-path>.manifest.json and the
                staging directory as <archive-path-without-extension>/.
EOF
  exit 2
}

TARGET="${1:-}"
ARCHIVE="${2:-}"
[ -n "$TARGET" ] || usage
[ -n "$ARCHIVE" ] || usage

POLICY="$REPO_ROOT/.github/release/artifact-policy.json"
[ -f "$POLICY" ] || die "missing release policy: $POLICY"

# Windows path separators are written with '/' here. The runner's POSIX layer
# accepts them, and the staged layout is identical either way.
case "$TARGET" in
  macos-arm64)
    # The app bundle is the whole payload; everything Chromium needs at runtime
    # is already inside Contents/.
    required='Chromium.app'
    optional=''
    ;;
  linux-x64|linux-arm64)
    required='chrome
chrome_crashpad_handler
icudtl.dat
resources.pak
chrome_100_percent.pak
chrome_200_percent.pak
v8_context_snapshot.bin
locales'
    optional='libEGL.so
libGLESv2.so
libvulkan.so.1
libvk_swiftshader.so
vk_swiftshader_icd.json
headless_lib_data.pak
MEIPreload'
    ;;
  windows-x64)
    required='chrome.exe
chrome.dll
chrome_elf.dll
icudtl.dat
resources.pak
chrome_100_percent.pak
chrome_200_percent.pak
v8_context_snapshot.bin
locales'
    optional='libEGL.dll
libGLESv2.dll
vk_swiftshader.dll
vulkan-1.dll
d3dcompiler_47.dll
mojo_core.dll'
    ;;
  *) die "unsupported target: $TARGET" ;;
esac

archive_dir="$(dirname "$ARCHIVE")"
[ -d "$archive_dir" ] || die "archive directory does not exist: $archive_dir"
archive_dir="$(cd "$archive_dir" && pwd)"
archive_name="$(basename "$ARCHIVE")"
ARCHIVE="$archive_dir/$archive_name"

case "$archive_name" in
  *.tar.zst) stage_name="${archive_name%.tar.zst}" ;;
  *.zip) stage_name="${archive_name%.zip}" ;;
  *) die "unsupported archive format (expected .tar.zst or .zip): $archive_name" ;;
esac
# The payload root carries no nightly/release marker: a nightly and a release
# built from the same pins must produce the same tree, or comparing them means
# nothing.
[ -n "$stage_name" ] || die "archive name has no stem: $archive_name"
stage="$archive_dir/$stage_name"

OUT="$SRC/out/$TARGET"

# Where the payload is copied FROM. Normally the build output; on macOS the
# Developer ID signed, notarized and stapled tree when scripts/sign-macos.sh
# produced one. Signing writes outside out/ so the reproducibility hashes in
# build/MANIFEST.lock keep describing what ninja built, which means the
# shipped bundle and the hashed bundle live in different directories and
# packaging is the step that has to choose. Same path as sign-macos.sh.
# Announced either way: "was this release signed" must be answerable from the
# job log alone.
PAYLOAD="$OUT"
if [ "$TARGET" = macos-arm64 ]; then
  signed="${APOSTATE_SIGNED_ROOT:-$WORKSPACE/signed}/$TARGET"
  if [ -d "$signed/Chromium.app" ]; then
    PAYLOAD="$signed"
    say "staging the signed and notarized bundle from $signed"
  else
    say "staging the unsigned bundle from $OUT (no signed tree at $signed)"
  fi
fi
rm -rf "$stage" "$ARCHIVE" "$ARCHIVE.manifest.json" "$ARCHIVE.sig"
mkdir -p "$stage"

stage_entry() {
  local relative="$1" kind="$2" from="$PAYLOAD/$1" to
  if [ ! -e "$from" ]; then
    if [ "$kind" = required ]; then
      die "missing required payload file for $TARGET: $relative (in $PAYLOAD)"
    fi
    return 0
  fi
  to="$stage/$relative"
  mkdir -p "$(dirname "$to")"
  # -R, never -L: the macOS framework bundle is held together by symlinks.
  cp -R "$from" "$to"
}

while IFS= read -r entry; do
  [ -n "$entry" ] || continue
  stage_entry "$entry" required
done <<EOF
$required
EOF

while IFS= read -r entry; do
  [ -n "$entry" ] || continue
  stage_entry "$entry" optional
done <<EOF
$optional
EOF

# The whole `locales` directory, not `locales/en-US.pak`, and the difference is
# a fingerprint property rather than a packaging preference.
#
# Chromium's build produces every locale pak. Staging only en-US.pak left
# l10n_util with one UI resource bundle to resolve the application locale
# against, so it could only ever answer en-US -- and Chromium sets ICU's default
# locale from the application locale, which is what every Intl constructor
# resolves its default against (v8/src/execution/isolate.cc:8123). The effect on
# the shipped artifact: LANG, LC_ALL and --lang all failed to move
# Intl.DateTimeFormat, Intl.NumberFormat, Intl.Collator or any toLocaleString
# off en-US, while navigator.languages followed the profile. A page could read
# that disagreement in one call, with no network request. Stock Chrome with its
# full pak set moves all of them together from LANGUAGE alone, so the lever was
# always there and this is the thing it operates.
#
# Shipping one pak was also a divergence from stock in its own right: real
# Chrome and real Chromium both ship the complete set, 220 files.
#
# en-US.pak is asserted separately because it is the fallback the binary needs
# to start at all, and requiring the directory does not by itself promise a
# member. macOS is not in this check: it stages Chromium.app wholesale and the
# bundle already carries its .lproj directories.
case "$TARGET" in
  linux-x64|linux-arm64|windows-x64)
    [ -f "$stage/locales/en-US.pak" ] ||
      die "staged payload has no locales/en-US.pak: the fallback UI bundle is what the binary starts on"
    paks="$(find "$stage/locales" -name '*.pak' -type f | wc -l | tr -d ' ')"
    [ "$paks" -gt 1 ] ||
      die "staged payload has $paks locale pak(s): one bundle pins the application locale, and with it ICU's default and every Intl constructor's, to en-US whatever the profile resolved"
    ;;
esac

[ -f "$REPO_ROOT/LICENSE" ] || die "missing LICENSE"
cp "$REPO_ROOT/LICENSE" "$stage/"
if [ -f "$REPO_ROOT/NOTICE" ]; then cp "$REPO_ROOT/NOTICE" "$stage/"; fi
[ -f "$REPO_ROOT/build/MANIFEST.lock" ] || die "missing build/MANIFEST.lock; run scripts/build.sh $TARGET"
mkdir -p "$stage/build"
# Keeps the repository path. Flattened to build-MANIFEST.lock it reads like a
# mangled path rather than a file, and a reader cannot tell which it is.
cp "$REPO_ROOT/build/MANIFEST.lock" "$stage/build/MANIFEST.lock"
mkdir -p "$stage/resources/profiles"
cp -R "$REPO_ROOT/resources/profiles/." "$stage/resources/profiles/"

say "packaging $TARGET into $archive_name"
case "$archive_name" in
  *.tar.zst)
    ( cd "$archive_dir" && tar --zstd -cf "$archive_name" "$stage_name" )
    ;;
  *.zip)
    # ditto where it exists, which is macOS, and not merely as a preference:
    # the framework bundle is held together by symlinks, `zip -r` stores them
    # as regular files containing the target path, and the resulting .app does
    # not launch. Measured on a fixture with two symlinks: ditto preserved
    # 2 of 2, `zip -r` preserved 0 of 2. 7z is the Windows path, where the
    # payload has no symlinks to lose.
    if command -v ditto >/dev/null 2>&1; then
      ( cd "$archive_dir" && ditto -c -k --keepParent "$stage_name" "$archive_name" )
    elif command -v 7z >/dev/null 2>&1; then
      ( cd "$archive_dir" && 7z a -tzip "$archive_name" "$stage_name" >/dev/null )
    else
      die "writing $archive_name needs ditto or 7z"
    fi
    ;;
esac

python3 - "$ARCHIVE" "$ARCHIVE.manifest.json" "$TARGET" "$REPO_ROOT" "$POLICY" <<'PY'
import hashlib, json, pathlib, subprocess, sys

artifact, manifest, target, root, policy_path = (
    pathlib.Path(sys.argv[1]),
    pathlib.Path(sys.argv[2]),
    sys.argv[3],
    pathlib.Path(sys.argv[4]),
    pathlib.Path(sys.argv[5]),
)
document = json.loads(policy_path.read_text(encoding="utf-8"))
policy = document["manifest_contract"]
artifacts = document["artifacts"]
chromium_version = (root / "build/CHROMIUM_VERSION").read_text(encoding="utf-8").strip()

if target not in policy["platforms"]:
    raise SystemExit(f"{target} is not a release platform in {policy_path}")
if chromium_version != policy["chromium_version"]:
    raise SystemExit(
        "build/CHROMIUM_VERSION is "
        f"{chromium_version} but the release policy pins {policy['chromium_version']}"
    )
if artifact.name not in artifacts["filenames"]:
    raise SystemExit(f"{artifact.name} is not a release artifact name in {policy_path}")

catalogue = json.loads((root / "resources/profiles/catalogue.json").read_text(encoding="utf-8"))
data = {
    "package_version": policy["package_version"],
    "chromium_version": chromium_version,
    "catalogue_version": catalogue["catalogue_version"],
    "platform": target,
    "artifact": artifact.name,
    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    "source_revision": subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip(),
    "patch_series_sha256": hashlib.sha256((root / "patches/series").read_bytes()).hexdigest(),
    "build_manifest_sha256": hashlib.sha256((root / "build/MANIFEST.lock").read_bytes()).hexdigest(),
}
if set(data) != set(policy["required_fields"]):
    raise SystemExit("manifest fields do not match the release contract")
manifest.write_bytes(
    (json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
)
print(f"{artifact.name}  {data['sha256']}")
PY

say "wrote $ARCHIVE and $archive_name.manifest.json"
