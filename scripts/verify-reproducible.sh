#!/usr/bin/env bash
# Build twice from clean and compare. This is the check that the build contract
# in docs/contributing/build.mdx is actually being honoured rather than merely intended.
source "$(dirname "$0")/lib.sh"

if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/verify-reproducible.sh "$@"
fi

TARGET="${1:-$(target_default)}"

# --against-manifest compares one fresh build against the committed
# build/MANIFEST.lock instead of building twice. Same guarantee for half the
# compute whenever a recorded baseline already exists, which after the first
# build it always does.
AGAINST_MANIFEST=0
[ "${2:-}" = "--against-manifest" ] && AGAINST_MANIFEST=1

manifest_outputs() {
  grep -A20 '\[outputs\]' "$1" | grep -v '^\[outputs\]' | grep -v '^$'
}

# Only the manifest's output hashes may reach stdout. Silencing build.sh alone
# was not enough: apply-patches and configure also print, and their output ended
# up in the comparison, which reported a mismatch that was never about the
# binaries.
run_once() {
  rm -rf "$SRC/out/$TARGET" >&2
  "$REPO_ROOT/scripts/apply-patches.sh" >&2
  "$REPO_ROOT/scripts/configure.sh" "$TARGET" >&2
  "$REPO_ROOT/scripts/build.sh" "$TARGET" >&2
  manifest_outputs "$REPO_ROOT/build/MANIFEST.lock"
}

if [ "$AGAINST_MANIFEST" = "1" ]; then
  [ -f "$REPO_ROOT/build/MANIFEST.lock" ] || die "no build/MANIFEST.lock to compare against"
  first="$(manifest_outputs "$REPO_ROOT/build/MANIFEST.lock")"
  say "recorded baseline:"
  printf '%s\n' "$first"
  say "rebuilding from clean to compare"
  second="$(run_once)"
else
  say "build 1 of 2"
  first="$(run_once)"
  say "build 2 of 2"
  second="$(run_once)"
fi

if [ "$first" = "$second" ]; then
  say "reproducible: both builds produced identical outputs"
  printf '%s\n' "$first"
else
  printf '\033[31mNOT REPRODUCIBLE\033[0m\n' >&2
  diff <(printf '%s\n' "$first") <(printf '%s\n' "$second") >&2 || true
  exit 1
fi
