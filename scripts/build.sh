#!/usr/bin/env bash
# Full build, then write build/MANIFEST.lock recording exactly what produced it.
source "$(dirname "$0")/lib.sh"

if [ "${1:-$(target_default)}" = macos-arm64 ] && [ "$(uname -s)" != Darwin ]; then
  die "macos-arm64 builds require a native macOS runner"
fi
if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/build.sh "$@"
fi

TARGET="${1:-$(target_default)}"
OUT="$SRC/out/$TARGET"
[ -f "$OUT/args.gn" ] || die "not configured; run scripts/configure.sh $TARGET"

# Leave headroom by default. On a shared machine an unconstrained Chromium build
# starves everything else, and where the box also runs latency-sensitive work a
# saturated scheduler distorts its measurements.
JOBS="${APOSTATE_JOBS:-$(python3 -c "import os;print(max(1,int(os.cpu_count()*0.75)))")}"

[ -x "$NINJA" ] || die "no ninja at $NINJA; the checkout is incomplete"

# chrome/installer/mac carries the signing driver: the group copies
# sign_chrome.py, the signing package, the generated build_props_config.py and
# the three entitlements plists into "$OUT/Chromium Packaging/", and builds
# dmg_tool and hfs_tool. 61 edges, seconds, and without it
# scripts/sign-macos.sh has nothing to run. It is not added to [outputs]
# below: that list is an explicit per-target enumeration, macos-arm64 names
# Chromium.app and chrome_crashpad_handler only, and the packaging directory
# is therefore already outside the reproducibility hash set. It has to stay
# outside it -- these are copies of checked-in scripts and two host tools that
# never reach the shipped payload, so hashing them would widen the
# reproducibility claim to files the artifact does not contain.
ninja_targets=(chrome)
if [ "$TARGET" = macos-arm64 ]; then
  ninja_targets+=(chrome/installer/mac)
fi

say "building $TARGET with $JOBS jobs"
( cd "$SRC" && nice -n 10 "$NINJA" -j "$JOBS" -C "out/$TARGET" "${ninja_targets[@]}" )

manifest="$REPO_ROOT/build/MANIFEST.lock"
lineage="$WORKSPACE/.apostate-build-lineage.jsonl"

# Hosted CI builds start fresh; local builds may reuse an out/ directory. An
# incremental manifest records its parent and most recent fresh build because
# its outputs can depend on earlier builds in that directory.
#
#   parent_manifest_sha256  digest of the manifest this build replaced, or
#                           "none" when no manifest existed
#   fresh_ancestor_sha256   "self" for a fresh build; otherwise the digest of
#                           the most recent fresh build's manifest for this
#                           target, or "unknown" when this workspace has no
#                           recorded fresh build
if [ "${APOSTATE_FRESH_BUILD:-}" = "1" ]; then
  build_mode="fresh"
else
  build_mode="incremental"
fi
if [ -f "$manifest" ]; then
  parent_manifest_sha256="$($SHA256 "$manifest" | cut -d' ' -f1)"
else
  parent_manifest_sha256="none"
fi
fresh_ancestor_sha256="$(python3 - "$lineage" "$TARGET" "$build_mode" <<'PY'
import json, pathlib, sys

path, target, mode = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if mode == "fresh":
    print("self")
    raise SystemExit(0)
ancestor = "unknown"
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("target") == target and record.get("build_mode") == "fresh":
            ancestor = record.get("manifest_sha256") or "unknown"
print(ancestor)
PY
)"

built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
args_sha256="$($SHA256 "$OUT/args.gn" | cut -d' ' -f1)"
patch_contents_sha256="$(python3 - "$REPO_ROOT" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for line in (root / "patches/series").read_text().splitlines():
    name = line.partition("#")[0].strip()
    if name:
        digest.update(name.encode() + b"\0" + (root / "patches" / name).read_bytes() + b"\0")
print(digest.hexdigest())
PY
)"

# The Windows toolchain is the one build input nothing pins. build/args/
# windows-x64.gn deliberately names no visual_studio_path, because pinning it
# obliges you to pin visual_studio_version, windows_sdk_version and wdk_path
# too and forces visual_studio_runtime_dirs empty, so the CRT redistributables
# never reach the package. The consequence is that the MSVC headers, the SDK,
# the CRT and ATL all come from whatever the runner image happens to carry,
# and two builds from the same pins would differ across a runner-image
# refresh with nothing in the record naming the change.
#
# So record what was resolved. This does not make the input pinned; it makes it
# ATTRIBUTED, which is the honest intermediate state between here and building
# the image ourselves. A reproducibility comparison that disagrees can now be
# told from a toolset upgrade instead of being blamed on the patches.
#
# Flat scalar keys, not a [section]: scripts/validate-release-baseline.py
# parses every line before [outputs] and rejects anything that is not a quoted
# scalar assignment.
windows_toolchain_lines=()
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    _vs_version="$(windows_vs_property installationVersion || true)"
    _toolset="$(windows_msvc_toolset_root || true)"
    # SDK_VERSION out of Chromium's own source rather than our mirror of it.
    # scripts/configure.sh has already failed the build if the two disagree, so
    # this records the value that was actually used.
    _sdk_version="$(sed -n "s/^SDK_VERSION = '\([^']*\)'.*/\1/p" \
      "$SRC/build/vs_toolchain.py" | head -1)"
    # The SDK's servicing revision, measured rather than assumed. This is the
    # field the sixth Windows defect proves is load-bearing: revisions 4654 and
    # 7705 both live in a directory called 10.0.26100.0 and differ in whether a
    # type Chromium requires exists at all, so windows_sdk_version alone cannot
    # distinguish two builds that compiled different headers. dbghelp.dll is
    # used because it is the one SDK file with a version resource that tracks
    # servicing; the headers have none, which is why
    # build/WINDOWS_SDK_REQUIREMENTS asserts them by symbol instead.
    _sdk_revision="$(windows_file_version "$(windows_sdk_root)/Debuggers/x64/dbghelp.dll" || true)"
    windows_toolchain_lines=(
      "visual_studio_version = \"${_vs_version:-unknown}\""
      "msvc_toolset_version = \"$([ -n "$_toolset" ] && basename "$_toolset" || echo unknown)\""
      "windows_sdk_version  = \"${_sdk_version:-unknown}\""
      "windows_sdk_revision = \"${_sdk_revision:-unknown}\""
      "vs_components_sha256 = \"$($SHA256 "$REPO_ROOT/build/WINDOWS_VS_COMPONENTS" | cut -d' ' -f1)\""
      "sdk_requirements_sha256 = \"$($SHA256 "$REPO_ROOT/build/WINDOWS_SDK_REQUIREMENTS" | cut -d' ' -f1)\""
      "sdk_packages_sha256 = \"$($SHA256 "$REPO_ROOT/build/WINDOWS_SDK_PACKAGES" | cut -d' ' -f1)\""
    )
    unset _vs_version _toolset _sdk_version _sdk_revision
    ;;
esac

# Staged, then moved into place. MANIFEST.lock is the checked-in baseline that
# scripts/validate-release-baseline.py gates a release on, and redirecting
# straight into it truncates it the instant the group opens. Any failure
# inside -- a missing output list, an unreadable artifact -- would then leave a
# committed file holding a header and no [outputs], failing the next gate run
# and poisoning the parent_manifest_sha256 lineage with a corrupt parent.
manifest_tmp="$manifest.tmp.$$"
trap 'rm -f "$manifest_tmp"' EXIT
{
  echo "# Generated by scripts/build.sh. Records what this build resolved."
  echo "built_at             = \"$built_at\""
  echo "target               = \"$TARGET\""
  echo "chromium_version     = \"$CHROMIUM_VERSION\""
  echo "chromium_commit      = \"$(git -C "$SRC" rev-parse HEAD)\""
  echo "depot_tools_revision = \"$DEPOT_TOOLS_REVISION\""
  echo "build_container_image = \"${APOSTATE_BUILD_IMAGE_ID:-native}\""
  echo "patch_series_sha256  = \"$($SHA256 "$REPO_ROOT/patches/series" | cut -d' ' -f1)\""
  echo "patch_contents_sha256 = \"$patch_contents_sha256\""
  echo "args_sha256          = \"$args_sha256\""
  echo "build_mode           = \"$build_mode\""
  echo "parent_manifest_sha256 = \"$parent_manifest_sha256\""
  echo "fresh_ancestor_sha256 = \"$fresh_ancestor_sha256\""
  if [ "${#windows_toolchain_lines[@]}" -gt 0 ]; then
    echo "# Resolved from the runner's own installation, not from a pin. See"
    echo "# docs/BUILD.md, \"Provisioning\". Windows targets only."
    for line in "${windows_toolchain_lines[@]}"; do echo "$line"; done
  fi
  echo
  echo "[outputs]"
  # The executables and libraries that carry the fingerprint, per target. A
  # target missing from this list produces an empty [outputs] section, which
  # makes its outputs_sha256 the hash of nothing and silently turns
  # scripts/verify-reproducible.sh into a no-op for that platform.
  case "$TARGET" in
    macos-arm64) artifacts='Chromium.app chrome_crashpad_handler' ;;
    linux-x64|linux-arm64) artifacts='chrome chrome_crashpad_handler libEGL.so libGLESv2.so' ;;
    windows-x64) artifacts='chrome.exe chrome.dll chrome_elf.dll libEGL.dll libGLESv2.dll' ;;
    *) die "no output list for target: $TARGET" ;;
  esac
  hashed=0
  for f in $artifacts; do
    [ -e "$OUT/$f" ] || continue
    hashed=$((hashed + 1))
    if [ -d "$OUT/$f" ]; then
      echo "\"$f\" = \"dir:$(find "$OUT/$f" -type f -exec $SHA256 {} + | sort | $SHA256 | cut -d' ' -f1)\""
    else
      echo "\"$f\" = \"$($SHA256 "$OUT/$f" | cut -d' ' -f1)\""
    fi
  done
  [ "$hashed" -gt 0 ] || die "no build outputs found in $OUT for $TARGET"
} > "$manifest_tmp"
mv -f "$manifest_tmp" "$manifest"

# One append-only line per build. This is what makes fresh_ancestor_sha256
# resolvable at all: the manifest is overwritten every build, so the chain it
# refers to has to be recorded somewhere that survives.
python3 - "$lineage" "$manifest" "$built_at" "$TARGET" "$build_mode" \
  "$patch_contents_sha256" "$args_sha256" <<'PY'
import hashlib, json, pathlib, sys

lineage, manifest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
raw = manifest.read_bytes()
_, _, outputs = raw.partition(b"[outputs]\n")
record = {
    "args_sha256": sys.argv[7],
    "build_mode": sys.argv[5],
    "built_at": sys.argv[3],
    "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    "outputs_sha256": hashlib.sha256(outputs).hexdigest(),
    "patch_contents_sha256": sys.argv[6],
    "target": sys.argv[4],
}
lineage.parent.mkdir(parents=True, exist_ok=True)
with lineage.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
print(f"lineage {record['build_mode']} {record['manifest_sha256']} -> {lineage}")
PY

say "wrote $manifest ($build_mode, parent $parent_manifest_sha256)"
grep -A20 '\[outputs\]' "$manifest"
