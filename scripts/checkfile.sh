#!/usr/bin/env bash
# V1 gate: compile one translation unit against the configured build.
#
# This is what keeps builds out of the debugging loop. A patch is validated in
# seconds here, so entering a full build is an expectation of success rather
# than an experiment.
#
#   scripts/checkfile.sh third_party/blink/renderer/core/frame/navigator.cc
#
# Drives ninja at the object target rather than replaying the raw compile
# command. Chromium translation units include generated headers, and a replayed
# command fails on the first one that has not been produced yet — reporting a
# missing module map instead of the thing you changed. Asking ninja for the
# object builds whatever that file needs and nothing else.

# mapfile below needs Bash 4. Under macOS's /bin/bash 3.2 it is missing, the
# object list comes back empty, and the script blamed compile_commands.json.
# ${BASH_VERSINFO:-0} is element 0, and reads as 0 under a shell that is not bash.
if [ "${BASH_VERSINFO:-0}" -lt 4 ]; then
  echo "scripts/checkfile.sh needs Bash 4 or later, and this is ${BASH_VERSION:-not bash};" \
    "on macOS run it with Homebrew's bash (brew install bash)" >&2
  exit 1
fi

source "$(dirname "$0")/lib.sh"

if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/checkfile.sh "$@"
fi

FILE="${1:?usage: checkfile.sh <chromium-relative-path> [target]}"
TARGET="${2:-$(target_default)}"
OUT="$SRC/out/$TARGET"
CC_JSON="$OUT/compile_commands.json"

[ -f "$CC_JSON" ] || die "no compile_commands.json for $TARGET; run scripts/configure.sh $TARGET"
[ -x "$NINJA" ] || die "no ninja at $NINJA; the checkout is incomplete"

# A file may compile into several toolchains or variants; check every object it
# produces, since a patch can break one and not another.
mapfile -t objects < <(python3 - "$CC_JSON" "$FILE" <<'PY'
import json, os, re, sys
db, want = sys.argv[1], os.path.normpath(sys.argv[2])
seen = []
for e in json.load(open(db)):
    if not os.path.normpath(e["file"]).endswith(want):
        continue
    m = re.search(r'-o\s+(\S+)', e.get("command", ""))
    if m and m.group(1) not in seen:
        seen.append(m.group(1))
for o in seen:
    print(o)
PY
)

[ "${#objects[@]}" -gt 0 ] || die "$FILE is not in compile_commands.json (not built for $TARGET, or the path is wrong)"

say "compiling $FILE  (${#objects[@]} object(s))"
for obj in "${objects[@]}"; do
  printf '  %s\n' "$obj"
done

( cd "$OUT" && nice -n 10 "$NINJA" "${objects[@]}" ) && say "OK  $FILE compiles"
