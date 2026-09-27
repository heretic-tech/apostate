#!/usr/bin/env bash
# Verify a directory of release artifacts against the release contract.
#
# This is the publish gate. It lives in a script rather than inline in
# .github/workflows/release.yml so it can be run against nightly artifacts
# before a release is ever tagged: every check here -- field set, artifact
# binding, platform binding, catalogue agreement, source revision, SHA-256,
# duplicate and missing platforms -- is exercisable offline. Inline, it was
# first exercised during a paid release, after every build had been paid for.
#
# Usage:
#   scripts/verify-release-inputs.sh <inputs-dir> <platform,...> [revision]
#
#   inputs-dir  directory holding <archive> and <archive>.manifest.json pairs
#   platforms   comma-separated set the release must carry, exactly
#   revision    40-hex commit every manifest must name. Omitted or "any"
#               accepts whatever the manifests carry, which is what makes a
#               nightly's artifacts checkable; a release always passes the
#               tagged revision.

# mapfile and declare -A below need Bash 4; macOS's /bin/bash is 3.2, which
# also cannot parse the heredoc inside $(...) further down. Bash parses one
# top-level command at a time, so this runs before it reaches either.
# ${BASH_VERSINFO:-0} is element 0, and reads as 0 under a shell that is not bash.
if [ "${BASH_VERSINFO:-0}" -lt 4 ]; then
  echo "scripts/verify-release-inputs.sh needs Bash 4 or later, and this is ${BASH_VERSION:-not bash};" \
    "on macOS run it with Homebrew's bash (brew install bash)" >&2
  exit 1
fi

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
shopt -s nullglob

usage() {
  sed -n '12,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

inputs_dir="${1:-}"
built_targets="${2:-}"
expected_revision="${3:-any}"
[ -n "$inputs_dir" ] && [ -n "$built_targets" ] || usage
[ -d "$inputs_dir" ] || { echo "no such directory: $inputs_dir" >&2; exit 1; }

policy="$REPO_ROOT/.github/release/artifact-policy.json"
[ -f "$policy" ] || { echo "missing release policy: $policy" >&2; exit 1; }

# Publication requires exactly the platforms the release actually built -- the
# set scripts/resolve-build-targets.sh resolved from APOSTATE_BUILD_TARGETS --
# not every platform the policy knows how to name. Demanding the full policy
# set means a repository without a runner for every platform can never publish
# anything. Each platform must still be one the policy recognises, and every
# artifact's SHA-256 is verified below, so a subset release is smaller but not
# weaker.
mapfile -t known < <(python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["manifest_contract"]["platforms"]))' "$policy")
IFS=, read -r -a platforms <<< "$built_targets"
[ "${#platforms[@]}" -gt 0 ] || { echo "no platforms requested" >&2; exit 1; }
for platform in "${platforms[@]}"; do
  printf '%s\n' "${known[@]}" | grep -qxF "$platform" || {
    echo "platform not named by the release policy: $platform" >&2
    exit 1
  }
done

manifests=("$inputs_dir"/*.manifest.json)
[ "${#manifests[@]}" -eq "${#platforms[@]}" ] || {
  echo "expected ${#platforms[@]} manifests (${platforms[*]}), got ${#manifests[@]}" >&2
  exit 1
}

declare -A seen=()
for manifest in "${manifests[@]}"; do
  artifact="${manifest%.manifest.json}"
  [ -f "$artifact" ] || { echo "artifact missing for $manifest" >&2; exit 1; }
  platform="$(python3 - "$manifest" "$artifact" "$expected_revision" "$policy" "$REPO_ROOT" <<'PY'
import hashlib, json, pathlib, re, sys

manifest, artifact = map(pathlib.Path, sys.argv[1:3])
expected_revision, policy_path, root = sys.argv[3], pathlib.Path(sys.argv[4]), pathlib.Path(sys.argv[5])
policy = json.loads(policy_path.read_text(encoding="utf-8"))
contract = policy["manifest_contract"]
required = set(contract["required_fields"])

# Filename to platform comes from the policy's own artifact list, so adding a
# platform needs no edit here.
by_artifact = {}
for name in policy["artifacts"]["filenames"]:
    matches = [p for p in contract["platforms"]
               if name.startswith(f"apostate-{contract['chromium_version']}-{p}.")]
    if len(matches) != 1:
        raise SystemExit(f"release policy artifact {name} does not name exactly one platform: {matches}")
    by_artifact[name] = matches[0]

data = json.loads(manifest.read_text(encoding="utf-8"))
if set(data) != required:
    raise SystemExit(f"manifest fields do not match release contract: {manifest}")
if artifact.name not in by_artifact:
    raise SystemExit(f"unexpected artifact name: {artifact}")
platform = by_artifact[artifact.name]

# catalogue_version is derived, not pinned. The profile catalogue is versioned
# independently of this release contract, so a constant here could never be
# corrected. The rule worth enforcing is that the manifest states the catalogue
# the checked-out revision actually shipped, so read it.
catalogue_path = root / "resources/profiles/catalogue.json"
catalogue_version = json.loads(catalogue_path.read_text(encoding="utf-8")).get("catalogue_version")
if isinstance(catalogue_version, bool) or not isinstance(catalogue_version, int) or catalogue_version < 1:
    raise SystemExit(f"{catalogue_path} has no usable catalogue_version: {catalogue_version!r}")

if (data["package_version"] != contract["package_version"]
        or data["chromium_version"] != contract["chromium_version"]):
    raise SystemExit(f"manifest version mismatch: {manifest}")
if data["catalogue_version"] != catalogue_version:
    raise SystemExit(
        f"manifest catalogue_version {data['catalogue_version']!r} is not the catalogue "
        f"shipped by this revision ({catalogue_version!r}): {manifest}")
if data["artifact"] != artifact.name:
    raise SystemExit(f"artifact binding mismatch: {manifest}")
if data["platform"] != platform:
    raise SystemExit(f"manifest platform mismatch: {manifest}")
if not re.fullmatch(r"[0-9a-f]{40}", data["source_revision"]):
    raise SystemExit(f"source_revision is not a commit sha: {manifest}")
if expected_revision != "any" and data["source_revision"] != expected_revision:
    raise SystemExit(f"source revision mismatch: {manifest}")
if data["sha256"] != hashlib.sha256(artifact.read_bytes()).hexdigest():
    raise SystemExit(f"artifact SHA-256 mismatch: {artifact}")
print(platform)
PY
  )"
  python3 "$REPO_ROOT/scripts/validate-release-contract.py" --kind manifest "$manifest"
  [ -z "${seen[$platform]:-}" ] || { echo "duplicate platform: $platform" >&2; exit 1; }
  seen[$platform]=1
  printf 'verified %s  %s\n' "$platform" "$(basename "$artifact")"
done

for platform in "${platforms[@]}"; do
  [ -n "${seen[$platform]:-}" ] || { echo "missing platform: $platform" >&2; exit 1; }
done
printf 'release inputs verified: %s\n' "${platforms[*]}"
