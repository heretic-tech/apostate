#!/usr/bin/env bash
# V1 series gate: compile every translation unit the patch series touches on
# this platform, and classify each one honestly.
#
#   scripts/checkseries.sh                      # this host's default target
#   scripts/checkseries.sh linux-x64            # a configured target
#   scripts/checkseries.sh --list               # membership only, no compiling
#   scripts/checkseries.sh --merge a.json b.json
#
# scripts/checkfile.sh gates one file at a time, which is the right tool while
# writing a patch and the wrong one for answering "is the series green on this
# platform". Driven by hand on the one configured target, it reported green for
# a series whose Linux-only and Windows-only files had never been compiled at
# all: at 152.0.7977.83, 20 of the 130 translation units the series touches are
# absent from the macOS build graph, so no per-file run on this host could ever
# have covered them.
#
# Six outcomes per translation unit, and the last four are why this script has
# the shape it has:
#
#   compiles        ninja produced every object the graph derives from the file
#   fails           an object was not produced; the error is reported and the
#                   gate exits non-zero
#   include-only    the file is no translation unit of its own, and an object
#                   this run compiled recorded reading it. Verified, not skipped
#   absent-platform declared in scripts/series-absences.tsv as scoped away
#                   from this target by a GN condition
#   absent-config   declared there as excluded by this build's configuration
#   unexplained     none of the above; the gate exits non-zero
#
# "absent" used to be one word for the last four, and that folded two unlike
# claims into one passing badge. font_cache_linux.cc is Linux-only and skipping
# it on Windows is correct; the ffmpeg config's libavcodec/codec_list.c was
# skipped on every platform because it is never a translation unit at all --
# libavcodec/allcodecs.c includes it textually -- so patch 0061's codec
# registration was compiled by nothing this gate checked while the gate
# reported 129 compiles, 3 absent and a green badge. An absence nothing
# accounts for now fails the run.
source "$(dirname "$0")/lib.sh"

GATE_ARGV=("$@")

TARGET=""
REPORT=""
MODE="compile"
VERIFY_ABSENT=1
JOBS="${APOSTATE_JOBS:-}"
MERGE_REPORTS=()

while (($#)); do
  case "$1" in
    --list) MODE="list" ;;
    # "absent" is the classification that can hide a defect, so confirming it
    # against the graph node itself is on by default. Each confirmation costs
    # one ninja manifest load -- about 10s on this warm macOS checkout, less on
    # a fresh CI one -- so --fast exists for a local edit-compile loop.
    --fast) VERIFY_ABSENT=0 ;;
    --report) REPORT="${2:?--report needs a path}"; shift ;;
    --report=*) REPORT="${1#*=}" ;;
    -j) JOBS="${2:?-j needs a job count}"; shift ;;
    -j*) JOBS="${1#-j}" ;;
    --merge) MODE="merge"; shift; MERGE_REPORTS=("$@"); break ;;
    -h|--help) sed -n '2,38p' "$0"; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$TARGET" ] || die "target given twice: $TARGET and $1"; TARGET="$1" ;;
  esac
  shift
done

TU_TOOL="$REPO_ROOT/scripts/series-translation-units.py"
[ -f "$TU_TOOL" ] || die "missing $TU_TOOL"
ABSENCES="$REPO_ROOT/scripts/series-absences.tsv"
[ -f "$ABSENCES" ] || die "missing $ABSENCES; the gate cannot tell a declared absence from an unchecked one without it"
ABSENCE_TOOL="$REPO_ROOT/scripts/series_absences.py"
[ -f "$ABSENCE_TOOL" ] || die "missing $ABSENCE_TOOL"
REPORT_TOOL="$REPO_ROOT/scripts/series-gate-report.py"
[ -f "$REPORT_TOOL" ] || die "missing $REPORT_TOOL"
CLOSURE_TOOL="$REPO_ROOT/scripts/series-symbol-closure.py"
[ -f "$CLOSURE_TOOL" ] || die "missing $CLOSURE_TOOL"
# Chromium's own llvm-nm, for the same reason the gate uses Chromium's ninja:
# the host's nm may not read this target's objects at all, and on a Windows
# runner there is no system nm. `.exe` is tolerated so one path serves both.
NM_BIN="$SRC/third_party/llvm-build/Release+Asserts/bin/llvm-nm"
[ -x "$NM_BIN" ] || NM_BIN="$NM_BIN.exe"

# Linux compiles inside the pinned container, exactly as build.sh and
# checkfile.sh do. Compiling against the host's libraries would answer a
# question about this machine rather than about the build.
#
# After argument parsing, not before it: --merge reads finished reports on a
# plain hosted runner with no checkout, no Docker and no image, and re-execing
# it into a build container would make the cross-platform summary the most
# expensive step in the workflow.
if [ "$MODE" != "merge" ] && [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/checkseries.sh "${GATE_ARGV[@]}"
fi

# ----------------------------------------------------------------- merge mode
#
# One run sees one platform. "Not in this platform's build graph" is a fact
# about a platform; "in no platform's build graph" is a defect in the series,
# and it is only visible with the per-platform reports side by side.
if [ "$MODE" = "merge" ]; then
  [ "${#MERGE_REPORTS[@]}" -ge 2 ] || die "--merge needs at least two report files"
  python3 - "$REPO_ROOT" "${MERGE_REPORTS[@]}" <<'PY'
import json, pathlib, sys

repo_root = sys.argv[1]
sys.path.insert(0, str(pathlib.Path(repo_root, "scripts")))
import series_absences

# The status vocabulary comes from the module the per-platform runs classify
# with, so the merge cannot fall behind it. It did once: this step filtered on
# the literal string "absent", and splitting that one word into
# absent-platform, absent-config, include-only and unexplained would have left
# the filter matching nothing and the cross-platform coverage question
# silently answering "all clear" for every file.
SCHEMA = "apostate-v1-series-gate/2"

reports = []
for arg in sys.argv[2:]:
    path = pathlib.Path(arg)
    if not path.is_file():
        raise SystemExit(f"error: no report at {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    schema = report.get("schema")
    if schema != SCHEMA:
        raise SystemExit(
            f"error: {path} is schema {schema!r}, not {SCHEMA!r}. Its statuses mean\n"
            "something else, and merging it would compare two different vocabularies."
        )
    reports.append(report)

targets = [report["target"] for report in reports]
if len(set(targets)) != len(targets):
    raise SystemExit(f"error: two reports name the same target: {targets}")

# Families rather than target names: linux-x64 and linux-arm64 answer almost
# the same question, and merging those two alone must not be allowed to
# conclude anything about macOS or Windows.
families = {target.split("-")[0] for target in targets}
uncovered = sorted({"linux", "macos", "windows"} - families)

status = {}
for report in reports:
    for unit in report["units"]:
        status.setdefault(unit["path"], {})[report["target"]] = unit["status"]

# "Compiled somewhere" now includes include-only, because a file compiled as
# part of its includer is compiled: the object that read it is named in the
# report. Everything else -- a declared platform or config absence, or an
# unexplained one -- is not coverage.
dead = [
    path
    for path, per_target in status.items()
    if len(per_target) == len(targets)
    and not (set(per_target.values()) & series_absences.COVERED_STATUSES)
]
failures = sorted(
    (target, path, state)
    for path, per_target in status.items()
    for target, state in per_target.items()
    if state in series_absences.FAILING_STATUSES
)
stale = sorted(
    (report["target"], row["path"], row["why"])
    for report in reports
    for row in report.get("stale_declarations", [])
)

print(f"merged {len(reports)} reports: {', '.join(targets)}")
print(f"translation units: {len(status)}")
for path, per_target in status.items():
    states = "  ".join(f"{t}={per_target.get(t, 'no-report')}" for t in targets)
    print(f"  {path}\n      {states}")

if failures:
    print("\nunresolved on at least one platform:")
    for target, path, state in failures:
        print(f"  {target}  {state:12} {path}")

if stale:
    print("\nstale declared absences:")
    for target, path, why in stale:
        print(f"  {target}  {path}\n      {why}")

exit_code = 1 if failures or stale else 0
if not dead:
    print("\nevery translation unit is compiled on at least one merged platform")
elif uncovered:
    print(f"\nnot compiled on any merged platform ({len(dead)}):")
    for path in dead:
        print(f"  {path}")
    print(
        "not conclusive: no report for " + ", ".join(uncovered) +
        ". Add one and merge again before calling these dead."
    )
else:
    print(f"\nCOMPILED BY NO PLATFORM ({len(dead)}):")
    for path in dead:
        print(f"  {path}")
    print("A patch edits each of these and nothing compiles it.")
    exit_code = 1

raise SystemExit(exit_code)
PY
  exit $?
fi

# ------------------------------------------------------------ gate the series
TARGET="${TARGET:-${APOSTATE_TARGET:-$(target_default)}}"
OUT="$SRC/out/$TARGET"
REPORT="${REPORT:-$OUT/apostate-v1-$TARGET.json}"

[ -f "$OUT/build.ninja" ] || die "no build graph for $TARGET; run scripts/configure.sh $TARGET"
[ -x "$NINJA" ] || die "no ninja at $NINJA; the checkout is incomplete"
# Command substitution strips trailing newlines but not a trailing carriage
# return, and Windows Python writes one, so a bare $(python3 -c 'print(...)')
# yields "10\r" here and ninja is then invoked as `-j 10\r`. Same defect class
# as the object paths, one line earlier in the run, and it would have been the
# next thing a Windows run failed on. end="" removes the newline at the source.
JOBS="${JOBS:-$(python3 -c 'import os;print(max(1,int(os.cpu_count()*0.75)),end="")')}"

# nice is not guaranteed off Linux and macOS, and a missing nice must not be
# the reason the gate cannot run on a Windows runner.
NINJA_CMD=("$NINJA")
command -v nice >/dev/null 2>&1 && NINJA_CMD=(nice -n 10 "$NINJA")

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
UNITS="$WORK/units.tsv"
MAP="$WORK/map.tsv"
BUILD_LOG="$WORK/build.log"
DRY_LOG="$WORK/dry.log"
ABSENT_LOG="$WORK/absent.tsv"
SOURCE_INDEX="$WORK/compdb-sources.tsv"
INCLUDERS="$WORK/includers.tsv"
DEPS_LOG="$WORK/deps.txt"
: > "$ABSENT_LOG"
: > "$INCLUDERS"
: > "$DEPS_LOG"

python3 "$TU_TOOL" --root "$REPO_ROOT" --with-patches > "$UNITS"
total="$(grep -c . "$UNITS" || true)"
[ "${total:-0}" -gt 0 ] || die "patches/series names no translation units; that cannot be right"

say "V1 series gate  target=$TARGET  $total translation units from patches/series"

# Phase 1 -- which of them does this platform compile?
#
# Asked of the build graph through `ninja -t compdb`, which lists every edge
# with its first input and its output. Two alternatives were measured and
# rejected:
#
#   * compile_commands.json, which checkfile.sh uses. It is a side artifact of
#     `gn gen`, 573MB here, and matched there by path suffix, so a short path
#     can bind to an entry for a different file. It is not what ninja builds
#     from.
#   * Asking ninja to build the object and reading the failure. Measured:
#     `ninja obj/...` for a target outside the graph prints
#     "ninja: error: unknown target '...'" and exits 1, and a translation unit
#     that fails to compile also exits 1. Keying the classification on that
#     text makes one ninja message change turn a missing file into a pass.
#
# So the gate never asks ninja to build an object the graph has not already
# named, and a file that produces no object is absent by construction rather
# than by interpretation. One invocation covers every unit in the series.
#
# Passed with -c rather than on stdin, because stdin is the compdb stream.
PHASE1_PY="$(cat <<'PY'
import collections, re, sys

# Windows Python writes \r\n for every \n in text mode, and this stream is
# read back by `while IFS=$'\t' read -r path objs` in bash. The trailing \r
# lands on the last field, so an object path becomes
# "obj/net/net/socks5_client_socket.obj\r" and ninja reports
# "unknown target" for a target it produces -- which the first Windows run of
# this gate hit, after the toolchain and the patch series were both fixed. The
# gate correctly called itself broken rather than blaming the series, but it
# was broken for this. Fixing it at the writer covers every consumer at once:
# the object list, the #prefix line read by awk, and the absent log.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(newline="\n")

units = []
for line in open(sys.argv[1], encoding="utf-8"):
    path = line.partition("\t")[0].strip()
    if path:
        units.append(path)
objects = {path: [] for path in units}
# Every compiled source in the graph, keyed the same way.
index = collections.defaultdict(list)

field = re.compile(rb'^\s*"(file|output)": "(.*?)",?\s*$')
depths = collections.Counter()
records = 0
record = {}
for raw in sys.stdin.buffer:
    match = field.match(raw)
    if not match:
        continue
    record[match.group(1)] = match.group(2).decode("utf-8", "replace")
    if len(record) < 2:
        continue
    source, output = record[b"file"], record[b"output"]
    record = {}
    records += 1
    # ninja writes source paths relative to the build directory. The depth is
    # counted rather than assumed, so a build directory at another depth --
    # or a file inside a nested DEPS repository -- still maps.
    depth = 0
    while source.startswith("../"):
        source = source[3:]
        depth += 1
    if depth:
        depths[depth] += 1
    # An object output is the whole question: not "does ninja know this path"
    # but "does this platform compile it".
    if output.endswith((".o", ".obj")):
        index[source].append(output)
        if source in objects:
            objects[source].append(output)

if records == 0:
    raise SystemExit("error: ninja -t compdb listed no edges; the build graph is unreadable")
mapped = sum(1 for path in units if objects[path])
if mapped == 0:
    raise SystemExit(
        f"error: none of the {len(units)} series translation units mapped to an object.\n"
        "Every platform compiles some of them, so this is a broken gate rather than a\n"
        "clean series. Check the path spelling ninja uses in -t compdb."
    )

print(f"#prefix\t{'../' * (depths.most_common(1)[0][0] if depths else 0)}")
for path in units:
    print(f"{path}\t{' '.join(objects[path])}")

# The whole graph's source-to-object mapping, not just the series'. Phase 1d
# needs the objects of an includer that is not itself a series unit --
# libavcodec/allcodecs.c is nobody's patch target -- and one compdb stream is
# already in hand, so resolving it here spends nothing. A second ninja
# invocation for the same answer costs a manifest load, measured at about ten
# seconds warm.
with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as index_file:
    for path in sorted(index):
        index_file.write(f"{path}\t{' '.join(index[path])}\n")
print(
    f"  {records} graph edges read, {mapped}/{len(units)} units produce objects,"
    f" {len(index)} sources indexed",
    file=sys.stderr,
)
PY
)"
say "phase 1: build-graph membership (ninja -t compdb)"
COMPDB_ERR="$WORK/compdb.err"
set +e
( cd "$OUT" && "$NINJA" -t compdb ) 2>"$COMPDB_ERR" \
  | python3 -c "$PHASE1_PY" "$UNITS" "$SOURCE_INDEX" > "$MAP"
phase1=("${PIPESTATUS[@]}")
set -e
if [ "${phase1[0]}" -ne 0 ]; then
  # ninja's own message, not a summary of it: a malformed build.ninja names
  # the file and line, and "compdb failed" alone would send the reader back
  # here to find that out.
  cat "$COMPDB_ERR" >&2
  die "ninja -t compdb failed in $OUT"
fi
[ "${phase1[1]}" -eq 0 ] || exit "${phase1[1]}"

# Out-dir-relative spelling of a source path, as ninja itself writes it. Taken
# from the compdb output rather than computed from the directory depth, so the
# gate cannot disagree with ninja about the prefix.
#
# Both readers tolerate a trailing \r even though the writer no longer emits
# one. The writer is the fix; this is so a future one cannot reintroduce the
# same failure silently, and the cost is a character class.
PREFIX="$(awk -F'\t' '$1=="#prefix"{sub(/\r$/, "", $2); print $2; exit}' "$MAP")"

objects=()
in_graph=()
absent=()
while IFS=$'\t\r' read -r path objs; do
  case "$path" in '#'*) continue ;; esac
  if [ -n "$objs" ]; then
    in_graph+=("$path")
    for obj in $objs; do objects+=("$obj"); done
  else
    absent+=("$path")
  fi
done < "$MAP"

# Phase 1b -- confirm every absent file against the graph node itself.
#
# compdb answers "no edge produces an object from this file", which is the
# question the gate asks. `ninja -t query` answers the narrower "ninja has
# never heard of this path", and the difference matters: a file ninja knows but
# never compiles is a louder finding than a file that is simply not part of
# this platform, and both are reported with the evidence that produced them.
if [ "${#absent[@]}" -gt 0 ] && [ "$VERIFY_ABSENT" -eq 1 ]; then
  say "phase 1b: confirming ${#absent[@]} absent unit(s) with ninja -t query"
  for path in "${absent[@]}"; do
    query_out="$( ( cd "$OUT" && "$NINJA" -t query "$PREFIX$path" ) 2>&1 || true )"
    if printf '%s' "$query_out" | grep -q "unknown target"; then
      reason="not in this platform's build graph (ninja -t query: unknown target)"
    else
      reason="IN THE GRAPH BUT NO OBJECT IS PRODUCED FROM IT (ninja -t query: $(printf '%s' "$query_out" | tr '\n' ' ' | cut -c1-160))"
    fi
    printf '%s\t%s\n' "$path" "$reason" >> "$ABSENT_LOG"
  done
else
  for path in "${absent[@]-}"; do
    [ -n "$path" ] || continue
    printf '%s\t%s\n' "$path" "not in this platform's build graph (no object edge in ninja -t compdb)" >> "$ABSENT_LOG"
  done
fi

while IFS=$'\t' read -r path probe; do
  printf '  no object %s\n            %s\n' "$path" "$probe"
done < "$ABSENT_LOG"

# Phase 1d -- account for the files that produce no object.
#
# Some of them are not translation units at all. A file with no object of its
# own can still be compiled, textually, as part of another one, and patch 0061
# edits two such files: allcodecs.c and parsers.c include the generated codec
# and parser lists. The gate called them absent on every platform, so 0061's
# registration was compiled by nothing the gate checked while the gate
# reported 129 compiles and a green badge.
#
# The includer is discovered by searching the file's own module for an
# #include naming it, so the next such file is found by the same pass and no
# filename is special-cased here or in the module. Its objects join the
# compile set -- the real consumer gets compiled, which is the point -- and
# phase 4 then asks ninja whether those objects actually read the file.
includer_objects=()
if [ "${#absent[@]}" -gt 0 ]; then
  say "phase 1d: searching for includers of ${#absent[@]} unit(s) with no object"
  python3 "$ABSENCE_TOOL" discover \
    --root "$SRC" --source-index "$SOURCE_INDEX" -- "${absent[@]}" > "$INCLUDERS"
  while IFS=$'\t\r' read -r unit includer line operand _objs; do
    [ -n "$unit" ] || continue
    printf '  includes  %s\n            %s:%s  include "%s"\n' "$unit" "$includer" "$line" "$operand"
  done < "$INCLUDERS"
  # sort -u because two units can share an includer, and because an includer
  # can already be a series unit whose object is in the compile set.
  while IFS= read -r obj; do
    [ -n "$obj" ] || continue
    includer_objects+=("$obj")
    objects+=("$obj")
  done < <(awk -F'\t' 'NF>=5{n=split($5,a," "); for(i=1;i<=n;i++) print a[i]}' "$INCLUDERS" | sort -u)
  [ "${#includer_objects[@]}" -eq 0 ] ||
    say "phase 1d: ${#includer_objects[@]} includer object(s) join the compile set"
fi

# Phase 1e -- the libraries a GN source addition lands in.
#
# Compiling proves a translation unit is well formed; it does not prove the
# library it joined can still resolve its own symbols. Patch 0109 adds nine
# objects for ffmpeg's aarch64 HEVC path, three of them under
# libavcodec/aarch64/h26x with no "hevc" in their names, and dropping those
# three leaves 355 undefined ff_hevc_put_hevc_* symbols while every unit still
# compiles clean. Those files are listed in GN rather than patched, so the unit
# list cannot see them, and //third_party/ffmpeg:ffmpeg_internal is a
# static_library in this configuration, so ninja stops at alink and an archive
# resolves nothing.
#
# So the archive of each affected library joins the build and phase 5 asks nm
# whether the added objects' references are satisfiable. One extra ninja target
# per library, and the library's own objects are mostly prerequisites of the
# series objects already being built.
closure_archives=()
if [ "$MODE" != "list" ]; then
  while IFS= read -r archive; do
    [ -n "$archive" ] || continue
    closure_archives+=("$archive")
    objects+=("$archive")
  done < <(python3 "$CLOSURE_TOOL" plan --root "$REPO_ROOT" --source-index "$SOURCE_INDEX" \
             --out "$OUT" --ninja "$NINJA")
  [ "${#closure_archives[@]}" -eq 0 ] ||
    say "phase 1e: ${#closure_archives[@]} library archive(s) join the build for symbol closure"
fi

ninja_exit=0
if [ "$MODE" = "list" ]; then
  say "list mode: ${#in_graph[@]} unit(s) in the graph, ${#absent[@]} absent; nothing compiled"
  : > "$BUILD_LOG"
  : > "$DRY_LOG"
else
  [ "${#objects[@]}" -gt 0 ] || die "no objects to compile; the mapping is broken, not the fork"

  # Phase 1c -- plan before spending. Two things for one manifest load:
  #
  #   * Every object name is proved to be a real target now rather than after
  #     forty minutes of compiling. A name that does not exist makes ninja
  #     print "unknown target" and stop, which is a broken gate, not a clean
  #     series, and it must not be reachable from the compile phase.
  #   * The edge count is the gate's real cost, and it is not the 130 files.
  #     Measured on macos-arm64 at 152.0.7977.83: the static prerequisite
  #     closure of these objects is 53,670 generated files, or 112,086 once
  #     obj/chrome/app/test_support/chrome_main_delegate.o is included --
  #     that one object's GN hard deps include phony/chrome/chrome_framework,
  #     the macOS framework bundle, so compiling it first links the browser.
  #     Printing the number keeps that visible instead of surprising someone
  #     with a build-sized bill.
  if ! ( set +x; cd "$OUT" && "$NINJA" -n -k 0 "${objects[@]}" ) > "$DRY_LOG" 2>&1; then
    cat "$DRY_LOG" >&2
    die "ninja cannot plan the objects the graph named; the gate is broken, not the series"
  fi
  if grep -q '^ninja: no work to do\.$' "$DRY_LOG"; then
    say "phase 1c: every object is already up to date"
  else
    say "phase 1c: ninja plans $(grep -c . "$DRY_LOG") edge(s) for ${#objects[@]} object(s)"
  fi

  say "phase 2: compiling ${#in_graph[@]} unit(s), ${#objects[@]} object(s), -j$JOBS"
  # One invocation, not one per file. Every translation unit here shares the
  # generated-header prerequisites, which dominate a fresh build directory; a
  # per-file loop rebuilds nothing twice but pays ninja's manifest load each
  # time (about 10s warm here, so 130 files is 20 idle minutes). -k 0 keeps
  # going after a failure so one broken patch does not hide the rest.
  set +e
  ( cd "$OUT" && "${NINJA_CMD[@]}" -k 0 -j "$JOBS" "${objects[@]}" ) 2>&1 | tee "$BUILD_LOG"
  ninja_exit="${PIPESTATUS[0]}"
  # Phase 3 -- state, not text. After a keep-going build every requested
  # object must be up to date; whatever ninja would still rebuild was not
  # produced, whether or not its own edge printed "FAILED:". This is what
  # catches a translation unit blocked by a failed prerequisite action, where
  # the failure names a code generator and never mentions the object.
  #
  # set +x inside the subshell because the redirect captures its stderr, and
  # under `bash -x` the trace of this very command -- which lists every object
  # on one line -- would land in the file being scanned for object names.
  # Debugging the gate would then make it report every unit as failing.
  ( set +x; cd "$OUT" && "$NINJA" -n -k 0 "${objects[@]}" ) > "$DRY_LOG" 2>&1
  set -e
fi

# Phase 4 -- did the includer actually read the include-only file?
#
# The includer being compiled is not the question. allcodecs.c is compiled on
# every platform, and on each one it reads a different
# chromium/config/.../libavcodec/codec_list.c, because
# third_party/ffmpeg/BUILD.gn puts the per-target config directory on the
# include path. So the proof that a patched fragment reached a compiler is the
# compiler's own dependency record, which ninja keeps and hands back verbatim.
#
# One invocation for every includer object. `ninja -t deps` aborts on the first
# name it does not recognise and prints nothing else, so a bad name would lose
# the evidence for every other unit at once: that case is a broken gate and
# says so, rather than being read as "nothing verified this".
if [ "${#includer_objects[@]}" -gt 0 ]; then
  say "phase 4: recorded dependencies of ${#includer_objects[@]} includer object(s)"
  ( cd "$OUT" && "$NINJA" -t deps "${includer_objects[@]}" ) > "$DEPS_LOG" 2>&1 || true
  if grep -q '^ninja: error:' "$DEPS_LOG"; then
    cat "$DEPS_LOG" >&2
    die "ninja -t deps refused an object name that came out of ninja -t compdb; the gate is broken, not the series"
  fi
fi

# Not exec: the EXIT trap that removes $WORK has to run.
report_status=0
python3 "$REPORT_TOOL" \
  --units "$UNITS" \
  --map "$MAP" \
  --probes "$ABSENT_LOG" \
  --build-log "$BUILD_LOG" \
  --dry-log "$DRY_LOG" \
  --includers "$INCLUDERS" \
  --deps "$DEPS_LOG" \
  --absences "$ABSENCES" \
  --prefix "$PREFIX" \
  --report "$REPORT" \
  --target "$TARGET" \
  --mode "$MODE" \
  --ninja-exit "$ninja_exit" \
  --jobs "$JOBS" \
  --root "$REPO_ROOT" || report_status=$?

# Phase 5 -- symbol closure over the libraries phase 1e named.
#
# After the report, so a compile failure is read first: an unresolved symbol in
# a library whose sources did not compile is a consequence, not a finding.
closure_status=0
if [ "${#closure_archives[@]}" -gt 0 ]; then
  echo
  python3 "$CLOSURE_TOOL" check \
    --root "$REPO_ROOT" \
    --source-index "$SOURCE_INDEX" \
    --out "$OUT" \
    --ninja "$NINJA" \
    --nm "$NM_BIN" || closure_status=$?
fi

[ "$report_status" -eq 0 ] && [ "$closure_status" -eq 0 ]
