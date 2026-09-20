#!/usr/bin/env bash
# Developer ID sign, notarize and staple the macOS app bundle.
#
# Writes to $WORKSPACE/signed/<target>/, never into out/. The build contract in
# docs/BUILD.md is that build/MANIFEST.lock's [outputs] hashes describe what
# ninja produced, and scripts/verify-reproducible.sh compares two builds
# through those hashes. A signature is not reproducible -- it carries a
# timestamp and a certificate -- so signing in place would make every
# reproducibility comparison fail for a reason that has nothing to do with the
# build. The signed tree is a separate artifact derived from the reproducible
# one, and scripts/package-artifact.sh prefers it when it exists.
#
# No secrets configured: prints one line and exits 0, so nightlies and forks
# keep producing an unsigned bundle. Half-configured is fatal, because a
# release that quietly shipped unsigned is the failure this script exists to
# prevent.
source "$(dirname "$0")/lib.sh"

TARGET="${1:-$(target_default)}"
[ "$TARGET" = macos-arm64 ] || die "sign-macos.sh signs macos-arm64 only, not $TARGET"
[ "$(uname -s)" = Darwin ] || die "signing requires a native macOS host"

OUT="$SRC/out/$TARGET"
# Also spelled in scripts/package-artifact.sh, which stages from here when the
# directory exists.
SIGNED="${APOSTATE_SIGNED_ROOT:-$WORKSPACE/signed}/$TARGET"
PACKAGING="$OUT/Chromium Packaging"

# Apple's notary service usually answers in two to five minutes. 30m is the
# ceiling: long enough to absorb a queue backlog, short enough that a wedged
# submission fails the job instead of holding a paid macOS runner for hours.
# notarytool exits non-zero when the wait elapses.
NOTARY_TIMEOUT="${APOSTATE_NOTARY_TIMEOUT:-30m}"

# The six secrets, as GitHub Actions passes them. APPLE_SIGNING_IDENTITY is the
# full common name, e.g. "Developer ID Application: Example Inc (AB12CD34EF)".
signing_vars='APPLE_SIGNING_IDENTITY
APPLE_DEVELOPER_ID_P12_BASE64
APPLE_DEVELOPER_ID_P12_PASSWORD
APPLE_NOTARY_KEY_P8_BASE64
APPLE_NOTARY_KEY_ID
APPLE_NOTARY_ISSUER_ID'

present=0
missing=()
for name in $signing_vars; do
  if [ -n "${!name:-}" ]; then
    present=$((present + 1))
  else
    missing+=("$name")
  fi
done

if [ "$present" = 0 ]; then
  echo "sign-macos: no signing identity configured; the bundle stays unsigned" >&2
  # A stale signed tree from an earlier configured run must not be packaged as
  # if this run had produced it.
  rm -rf "$SIGNED"
  exit 0
fi
if [ "${#missing[@]}" -gt 0 ]; then
  die "signing is half-configured; set all six or none. Missing: ${missing[*]}"
fi

[ -d "$PACKAGING" ] || die "no $PACKAGING; run scripts/build.sh $TARGET (it builds chrome/installer/mac)"
[ -f "$PACKAGING/sign_chrome.py" ] || die "no sign_chrome.py in $PACKAGING"
[ -d "$OUT/Chromium.app" ] || die "no $OUT/Chromium.app to sign"

# chrome/installer/mac/signing/pipeline.py uses asyncio.TaskGroup, which is
# 3.11+. An older interpreter dies thousands of lines into the signing log with
# an AttributeError that says nothing about the version.
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' ||
  die "signing needs python3 >= 3.11 (asyncio.TaskGroup); found $(python3 -V 2>&1)"

for tool in security codesign spctl ditto xcrun; do
  command -v "$tool" >/dev/null 2>&1 || die "signing needs $tool"
done

umask 077
work="$(mktemp -d "${TMPDIR:-/tmp}/apostate-signing.XXXXXX")"
keychain="$work/apostate-signing.keychain-db"
keychain_created=""

# The user keychain search list has to name our keychain for codesign to find
# the identity, so it is mutated and restored rather than replaced.
saved_keychains=()
while IFS= read -r line; do
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%\"}"
  line="${line#\"}"
  [ -n "$line" ] && saved_keychains+=("$line")
done < <(security list-keychains -d user)

cleanup() {
  if [ -n "$keychain_created" ]; then
    if [ "${#saved_keychains[@]}" -gt 0 ]; then
      security list-keychains -d user -s "${saved_keychains[@]}" >/dev/null 2>&1 || true
    fi
    security delete-keychain "$keychain" >/dev/null 2>&1 || true
  fi
  # Holds the decoded .p12 and the notary .p8. Both are private keys.
  rm -rf "$work"
}
trap cleanup EXIT INT TERM

# Decoded through python3 rather than base64(1): the BSD and GNU tools disagree
# on the long option and on how they treat wrapped input, and the value is read
# from the environment so no secret reaches a command line.
decode_base64_secret() {
  APOSTATE_SECRET_NAME="$1" python3 - "$2" <<'PY'
import base64, os, pathlib, sys

name = os.environ["APOSTATE_SECRET_NAME"]
raw = os.environ.get(name, "")
try:
    payload = base64.b64decode("".join(raw.split()), validate=True)
except Exception:
    raise SystemExit(f"{name} is not valid base64")
if not payload:
    raise SystemExit(f"{name} decoded to nothing")
target = pathlib.Path(sys.argv[1])
target.touch(mode=0o600)
target.chmod(0o600)
target.write_bytes(payload)
PY
}

p12="$work/developer-id.p12"
notary_key="$work/notary-key.p8"
decode_base64_secret APPLE_DEVELOPER_ID_P12_BASE64 "$p12" || die "cannot decode APPLE_DEVELOPER_ID_P12_BASE64"
decode_base64_secret APPLE_NOTARY_KEY_P8_BASE64 "$notary_key" || die "cannot decode APPLE_NOTARY_KEY_P8_BASE64"

say "creating a temporary signing keychain"
keychain_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
security create-keychain -p "$keychain_password" "$keychain" >/dev/null
keychain_created=1
# No auto-lock and no idle timeout: notarization can hold this job for tens of
# minutes and a locked keychain fails the staple with an unrelated error.
security set-keychain-settings "$keychain" >/dev/null
security unlock-keychain -p "$keychain_password" "$keychain" >/dev/null
security import "$p12" -k "$keychain" -P "$APPLE_DEVELOPER_ID_P12_PASSWORD" \
  -T /usr/bin/codesign -T /usr/bin/security -f pkcs12 >/dev/null ||
  die "cannot import the Developer ID certificate; check APPLE_DEVELOPER_ID_P12_PASSWORD"
# Without this, codesign prompts for the keychain password and hangs a headless
# runner until the job times out.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$keychain_password" "$keychain" >/dev/null
# Prepended, so the run's identity is found first and the host's own
# keychains keep working. Split because bash 3.2, which is what /usr/bin/env
# bash resolves to on a macOS runner, errors on an empty array expansion
# under set -u.
if [ "${#saved_keychains[@]}" -gt 0 ]; then
  security list-keychains -d user -s "$keychain" "${saved_keychains[@]}" >/dev/null
else
  security list-keychains -d user -s "$keychain" >/dev/null
fi

# The identity is resolved to its certificate hash here and codesign is given
# the hash, not the name. Two reasons. The name has to match exactly, and the
# obvious way to obtain it -- copying the line `security find-identity`
# prints -- brings its quotation marks along; grep would find that quoted
# string in the same command's output and pass it on, and codesign then
# fails with "no identity found" an hour of build later. Matching whole
# names and signing by hash also makes the keychain search order irrelevant
# to which certificate signs. The names are not printed: the identity name is
# a repository secret and GitHub only masks what it knows verbatim.
identity_hash="$(security find-identity -v -p codesigning "$keychain" |
  python3 -c '
import os, re, sys
want = os.environ["APPLE_SIGNING_IDENTITY"]
found = {name: sha1 for sha1, name in re.findall(
    r"^\s*\d+\) ([0-9A-F]{40}) \"(.*)\"$", sys.stdin.read(), re.M)}
if want in found:
    print(found[want])
elif want.strip().strip("\"") in found:
    sys.exit("APPLE_SIGNING_IDENTITY carries quotation marks or whitespace "
             "around the name; set the bare name")
else:
    sys.exit("APPLE_SIGNING_IDENTITY names none of the %d valid code-signing "
             "identit%s in the imported certificate"
             % (len(found), "y" if len(found) == 1 else "ies"))
')" || die "cannot resolve APPLE_SIGNING_IDENTITY"

rm -rf "$SIGNED"
mkdir -p "$SIGNED"

# Chromium's driver is run through a wrapper for one reason: its config sets
# run_spctl_assess, and signing/parts.py asserts that assessment immediately
# after signing and BEFORE any notarization. A Developer ID signature that has
# not been notarized yet is always rejected there with "source=Unnotarized
# Developer ID", so the unmodified driver cannot complete a Developer ID run
# (chromium-dev, "macOS/signing - Trying to make sense of calling `spctl
# --assess` before notarization"). The assessment is not dropped, it is moved:
# this script runs `spctl -a -t exec -vv` after stapling, which is the only
# point at which the answer means anything. --development would also disable it
# but it strips the designated requirements and injects get-task-allow, which
# Apple's notary service rejects outright.
#
# --notarize is deliberately not passed either. With it, pipeline.py sends the
# signed bundle to a temporary work directory that WorkDirectory deletes on
# exit, and for Chromium branding -- whose single Distribution packages as
# neither dmg, pkg nor zip -- nothing is ever copied to --output. Notarization
# and stapling are therefore driven below, against the bundle the driver did
# leave behind.
say "signing Chromium.app with the Developer ID identity"
python3 - "$PACKAGING" \
  --identity "$identity_hash" \
  --input "$OUT" \
  --output "$SIGNED" \
  --disable-packaging <<'PY'
import sys

sys.path.insert(0, sys.argv[1])

from signing import config_factory

_get_class = config_factory.get_class


def get_class():
    base = _get_class()

    class ApostateCodeSignConfig(base):

        @property
        def run_spctl_assess(self):
            return False

    return ApostateCodeSignConfig


config_factory.get_class = get_class

from signing import driver

driver.main(sys.argv[2:])
PY

# pipeline.py names the output subdirectory after the distribution; the single
# Chromium distribution has no channel, so it is "stable". Resolved rather than
# spelled, then hoisted so packaging has one path to look for.
signed_app="$(find "$SIGNED" -maxdepth 2 -type d -name 'Chromium.app' | sort | sed -n '1p')"
[ -n "$signed_app" ] || die "the signing driver produced no Chromium.app under $SIGNED"
if [ "$signed_app" != "$SIGNED/Chromium.app" ]; then
  mv "$signed_app" "$SIGNED/Chromium.app"
  rmdir "$(dirname "$signed_app")" 2>/dev/null || true
fi
app="$SIGNED/Chromium.app"

say "submitting to the Apple notary service (timeout $NOTARY_TIMEOUT)"
# ditto, not zip: the framework bundle is held together by symlinks and the
# notary service rejects a submission whose contents do not match the bundle.
ditto -c -k --keepParent --sequesterRsrc "$app" "$work/notarize.zip"

notary_status=0
xcrun notarytool submit "$work/notarize.zip" \
  --key "$notary_key" \
  --key-id "$APPLE_NOTARY_KEY_ID" \
  --issuer "$APPLE_NOTARY_ISSUER_ID" \
  --wait --timeout "$NOTARY_TIMEOUT" \
  --output-format plist > "$work/notary.plist" || notary_status=$?

submission_id="$(python3 - "$work/notary.plist" <<'PY'
import pathlib, plistlib, sys

raw = pathlib.Path(sys.argv[1]).read_bytes()
try:
    plist = plistlib.loads(raw)
except Exception:
    plist = {}
print(plist.get("id", ""))
PY
)"
notary_result="$(python3 - "$work/notary.plist" <<'PY'
import pathlib, plistlib, sys

raw = pathlib.Path(sys.argv[1]).read_bytes()
try:
    plist = plistlib.loads(raw)
except Exception:
    plist = {}
print(plist.get("status", "Unknown"))
PY
)"

if [ "$notary_status" != 0 ] || [ "$notary_result" != Accepted ]; then
  warn "notarization returned $notary_result (exit $notary_status)"
  if [ -n "$submission_id" ]; then
    # The status alone never says which binary failed; the log does.
    xcrun notarytool log "$submission_id" \
      --key "$notary_key" --key-id "$APPLE_NOTARY_KEY_ID" --issuer "$APPLE_NOTARY_ISSUER_ID" >&2 || true
  fi
  die "the notary service did not accept the bundle"
fi
say "notarized: $submission_id"

# Every nested .app and .xpc gets its own ticket, deepest first, which is what
# chrome/installer/mac/signing/notarize.py staple_bundled_parts does. A helper
# left unstapled fails to launch on a machine that is offline the first time
# the bundle runs.
say "stapling notarization tickets"
staple_targets="$( { printf '%s\n' "$app"; find "$app" \( -name '*.app' -o -name '*.xpc' \) -type d; } | sort -r )"
while IFS= read -r part; do
  [ -n "$part" ] || continue
  xcrun stapler staple "$part" >/dev/null
done <<EOF
$staple_targets
EOF

say "verifying the signed bundle"
codesign --verify --deep --strict --verbose=2 "$app"
spctl -a -t exec -vv "$app"
xcrun stapler validate "$app"

say "signed and notarized $app"
