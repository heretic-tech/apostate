#!/usr/bin/env python3
"""When a patch adds sources to a GN list, prove the resulting symbol closure.

THE FAILURE THIS CATCHES

A patch adds source A, which calls a function defined in source B, and forgets
to add B. Everything still compiles: a call to an undeclared-but-declared
symbol is a link-time question, not a compile-time one. Patch 0109's arm64 leg
is the live example -- it adds nine objects for the HEVC SIMD path, and three
of them live under libavcodec/aarch64/h26x with no "hevc" anywhere in their
names. Omitting those three leaves 355 undefined ff_hevc_put_hevc_* symbols and
every translation unit still compiles clean.

WHY THE COMPILE GATE CANNOT SEE IT

scripts/checkseries.sh compiles the translation units the series touches, and
these nine are listed in GN rather than patched, so they are outside its unit
list by construction. Adding them to it would not help either:
//third_party/ffmpeg:ffmpeg_internal is a static_library whenever
is_component_ffmpeg is false, which build/args/linux-arm64.gn:12 makes it, so
ninja stops at `alink` and an archive does not resolve undefined symbols. The
355-symbol failure appears only when something links a final binary.

THE BOUNDED CHECK

Not "everything a patch's GN edit pulls in" -- that is the whole build. One
question per affected library:

    undefined(objects the patch added)
      - defined(every member of the library)
      - undefined(members the patch did not add)

The third term is what keeps this quiet. `memcpy` is undefined in the library's
pre-existing members too, so it is externally provided and not this patch's
problem. A symbol undefined ONLY in the objects the patch added, that no member
defines, is the patch's own hole. ff_hevc_profiles resolves because
libavcodec/profiles.c is a member from the universal block, so the expected
result here is EMPTY rather than "one known exception" -- an invariant with no
allowlist to rot.

Cost is one ninja target and one nm invocation per library: the archive pulls
exactly that library's objects and nm -A reports every member's symbols at once.

    series-symbol-closure.py plan  --root . --source-index index.tsv
    series-symbol-closure.py check --root . --source-index index.tsv --out OUTDIR

`check` FAILS when the closure is non-empty, and equally when it had nothing to
measure: a missing archive, an archive with no members, or a library none of
whose members are the objects we set out to check. A closure that ran on
nothing must never be reportable as a clean closure.
"""

from __future__ import annotations

import argparse
import collections
import os
import pathlib
import posixpath
import re
import subprocess
import sys

# A GN string literal naming a compiler input, on an added line of a patch.
GN_SOURCE_RE = re.compile(r'^\+\s*"([^"$]+\.(?:c|cc|cpp|cxx|m|mm|S|s|asm))"\s*,?\s*$')
# `+  angle_test("apostate_parallel_shader_tests") {` -- a target the patch
# declares itself. One string argument and a trailing brace, so a call like
# `resolve_policy("locale", request.locale_policies)` cannot match.
GN_TARGET_RE = re.compile(r'^\+\s*[a-z_][a-z0-9_]*\(\s*"([^"]+)"\s*\)\s*\{\s*$')
# `  ffmpeg_c_sources += [` / `  sources = [`, on an added or context line. Only
# a candidate: the compile database decides whether the literal is a source
# this platform builds, which is also what keeps an `outputs = [` list -- patch
# 0081 has one -- from being mistaken for sources.
GN_LIST_RE = re.compile(r'^[+ ]\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\+=|=)\s*\[')
# `obj/third_party/ffmpeg/ffmpeg_internal/allcodecs.o` -> dir, target, member.
OBJECT_RE = re.compile(r"^obj/(?P<dir>.+)/(?P<target>[^/]+)/(?P<member>[^/]+\.(?:o|obj))$")
# `lib.a:member/file.o: 0000 T _symbol` from `nm -A`.
NM_RE = re.compile(r"^(?P<file>.+?):(?P<sym>[^:]*)$")


def series_patches(root):
    series = root / "patches" / "series"
    names = []
    for line in series.read_text(encoding="utf-8").splitlines():
        name = line.partition("#")[0].strip()
        if name:
            names.append(name)
    return names


def gn_source_additions(root):
    """-> {checkout-relative source: {(patch, gn file, list, created target or "")}}.

    A GN source string is relative to the directory holding the build file, not
    to the checkout root: ffmpeg_generated.gni says "libavcodec/hevc/data.c" for
    third_party/ffmpeg/libavcodec/hevc/data.c. Resolving that here is what makes
    the result comparable with the compile database.

    The created target is what keeps this honest about which library the patch
    actually changed. Patches 0057 and 0063 add angle's pre-existing
    test_utils/ANGLETest.cpp into a target they declare themselves, and that
    source is compiled into three OTHER angle test libraries on a platform
    where the new target does not exist. Attributing it to those would run a
    closure over libraries the patch never touched and report their unrelated
    undefined symbols as this series' fault. When the patch declares the
    target, only that target counts; when it appends to a pre-existing list --
    ffmpeg_c_sources, or component("base")'s own sources -- nothing is
    declared and every object built from the source is fair game.

    Both pieces of state reset at each hunk header. A `sources = [` opener in
    one hunk says nothing about an added string three hunks later, and 0081 is
    exactly that shape: its source additions sit inside component("base") in
    one hunk and it declares action("apostate_dispersion_tables") with an
    `outputs` list in another.
    """
    added = collections.defaultdict(set)
    for name in series_patches(root):
        patch = root / "patches" / name
        if not patch.is_file():
            continue
        gn_file = None
        list_name = None
        created = ""
        for line in patch.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("+++ b/"):
                gn_file = line[6:].strip()
                list_name, created = None, ""
                continue
            if not gn_file or not gn_file.endswith((".gn", ".gni")):
                continue
            if line.startswith("@@"):
                list_name, created = None, ""
                continue
            target = GN_TARGET_RE.match(line)
            if target:
                created = target.group(1)
                continue
            match = GN_LIST_RE.match(line)
            if match:
                list_name = match.group(1)
                continue
            source = GN_SOURCE_RE.match(line)
            if source and list_name:
                rel = posixpath.normpath(
                    posixpath.join(posixpath.dirname(gn_file), source.group(1))
                )
                added[rel].add((name, gn_file, list_name, created))
    return added


def parse_source_index(path):
    """source<TAB>object object ... as the gate's compdb pass writes it."""
    index = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            source, _, objects = raw.rstrip("\r\n").partition("\t")
            if source and not source.startswith("#"):
                index[source] = objects.split()
    return index


def affected_libraries(added, index):
    """-> ({library key: {...}}, [(source, why)]) for sources with no library here.

    The compile database is the authority on whether an added literal is a
    source this platform builds and on which library it lands in, so no list
    name or target mapping is hardcoded. The one restriction is the created
    target: if every patch that adds this source declares the target it adds it
    to, then only that target's objects are this series' business.
    """
    libraries = {}
    skipped = []
    for source in sorted(added):
        attributions = added[source]
        declared = {target for *_, target in attributions if target}
        # Permissive unless every attribution named a target the patch created:
        # one append to a pre-existing list means any object counts.
        restrict = declared if declared and all(t for *_, t in attributions) else None
        objects = index.get(source)
        if not objects:
            skipped.append((source, "no object in this platform's build graph"))
            continue
        landed = False
        for obj in objects:
            match = OBJECT_RE.match(obj)
            if not match:
                continue
            directory, target = match.group("dir"), match.group("target")
            if restrict is not None and target not in restrict:
                continue
            archive = (f"obj/{directory}/{target}.lib" if obj.endswith(".obj")
                       else f"obj/{directory}/lib{target}.a")
            entry = libraries.setdefault(
                f"{directory}:{target}",
                {"archive": archive, "prefix": f"obj/{directory}/",
                 "directory": directory, "objects": set(), "sources": set(),
                 "compiled": set()},
            )
            entry["objects"].add(obj)
            entry["sources"].add(source)
            landed = True
        if not landed:
            skipped.append((source, f"built only into target(s) this series did not declare; "
                                    f"it declares {', '.join(sorted(restrict or []))}"))

    # Every source this platform compiles into each affected library. A symbol
    # one of these defines cannot be a missing GN source, so they are excluded
    # when the tree is searched for a definer.
    for source, objects in index.items():
        for obj in objects:
            match = OBJECT_RE.match(obj)
            if not match:
                continue
            key = f"{match.group('dir')}:{match.group('target')}"
            if key in libraries:
                libraries[key]["compiled"].add(source)
    return libraries, skipped


def drop_unarchived(libraries, ninja, out):
    """-> [(key, archive)] removed because ninja knows no archive for the target.

    The object path names the target but not its kind. A static_library
    stops at alink and leaves an archive nm can read; an executable or a
    source_set produces the same objects and no archive, and its symbols are
    resolved by the link that consumes them, which the full build proves and
    this gate does not. Asking ninja whether the archive is a target it can
    plan is the only way to tell the two apart without GN, and a name ninja
    cannot plan would otherwise stop the whole compile with "unknown target"
    (which is exactly what patch 0063's angle_test executable did to the
    Linux gate).
    """
    dropped = []
    for key in sorted(libraries):
        archive = libraries[key]["archive"]
        known = subprocess.run([ninja, "-C", out, "-t", "query", archive],
                               capture_output=True, text=True).returncode == 0
        if not known:
            dropped.append((key, archive))
            del libraries[key]
    return dropped


def read_symbols(nm, archive, out):
    """-> ({member: defined}, {member: undefined}) for one archive.

    One nm invocation for the whole library. `-A` prefixes every line with the
    archive and member, which is what lets a per-member split come out of a
    single pass.
    """
    defined = collections.defaultdict(set)
    undefined = collections.defaultdict(set)
    for flag, sink in (("--defined-only", defined), ("-u", undefined)):
        result = subprocess.run(
            [nm, "-A", flag, archive], cwd=out, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"error: {nm} -A {flag} {archive} failed in {out}:\n{result.stderr.strip()}"
            )
        for line in result.stdout.splitlines():
            # `<archive>:<member>: <addr> <type> <symbol>` -- the member may
            # itself contain a colon on no platform we build, but the symbol
            # never does, so the split is from the right.
            head, _, rest = line.partition(": ")
            symbol = rest.split()[-1] if rest.split() else ""
            if not symbol:
                continue
            member = head.split(":", 1)[1] if ":" in head else head
            sink[member.strip()].add(symbol)
    return defined, undefined


def classify_candidates(nm, out, candidates, own_archive):
    """-> (defined by another archive, referenced by another archive).

    Two facts about each candidate symbol, both read off the object files
    rather than guessed, because the three ways to get this wrong are all
    expensive:

      * DEFINED ELSEWHERE means another library in this build supplies it.
        Measured on the real macos-arm64 output, base/apostate/*.cc references
        BoringSSL's SHA256_Update and libc++'s operator+ this way.
      * REFERENCED ELSEWHERE means other libraries reference it and nothing in
        the build defines it, so the platform supplies it. Measured on the same
        output: __stderrp is referenced as undefined by 33 archives, __stdoutp
        by 13, fputs by 7. None of them is anybody's missing source.

    What is left -- undefined, defined by no archive, and referenced by no
    archive except the library under test -- is a symbol this series' own
    objects are alone in wanting and nothing can supply. That is the shape of a
    source missing from a GN list. For contrast, ff_hevc_profiles is referenced
    by exactly one archive and IS defined in it, so it never becomes a
    candidate at all.

    An earlier version searched the library's source tree for a text
    definition. It was wrong in both directions -- it read `fputs(...)` in
    base/i18n/build_utf8_validator_tables.cc as a definition when it is a
    call, and it would have missed ffmpeg's aarch64 assembly entirely, where
    definitions come from a `function ff_hevc_put_hevc_qpel_h4_8_neon` macro
    and look nothing like C. Guessing definitions out of source text cannot be
    made reliable; asking the object files cannot be wrong.

    Cost is two nm passes over the build's archives, measured at 13s and 12s
    over 2,209 of them on macos-arm64, and only candidate symbols are held.
    """
    if not candidates:
        return set(), set()
    archives = []
    for dirpath, dirnames, filenames in os.walk(out):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if filename.endswith((".a", ".lib")):
                archives.append(
                    os.path.relpath(os.path.join(dirpath, filename), out).replace(os.sep, "/")
                )
    wanted = set(candidates)
    defined, referenced = set(), set()
    for flag, sink in (("--defined-only", defined), ("-u", referenced)):
        for start in range(0, len(archives), 200):
            batch = archives[start:start + 200]
            result = subprocess.run(
                [nm, "-A", flag] + batch, cwd=out,
                capture_output=True, text=True, errors="replace",
            )
            # A nonzero status is normal: nm complains about an archive with no
            # symbol table and carries on. Output with a nonzero status and no
            # output at all is the real problem.
            if not result.stdout and result.returncode != 0:
                raise SystemExit(
                    f"error: {nm} -A {flag} produced no output in {out}:\n"
                    f"{result.stderr.strip()[:400]}"
                )
            for line in result.stdout.splitlines():
                head, _, rest = line.partition(": ")
                parts = rest.split()
                if not parts or parts[-1] not in wanted:
                    continue
                if head.split(":", 1)[0] != own_archive:
                    sink.add(parts[-1])
    return defined, referenced


def closure(libraries, nm, out, omit=()):
    """-> (findings, report rows). The bounded question, per library."""
    findings, rows = [], []
    for key in sorted(libraries):
        entry = libraries[key]
        archive = entry["archive"]
        if not (pathlib.Path(out) / archive).is_file():
            findings.append(
                f"CLOSURE RAN ON NOTHING   {key}\n"
                f"        {archive} does not exist under {out}.\n"
                f"        The patch adds {len(entry['sources'])} source(s) to this library's "
                f"GN list and nothing built it, so there is no closure to report. A check "
                f"that cannot see its subject must not pass."
            )
            continue
        defined, undefined = read_symbols(nm, archive, out)
        members = set(defined) | set(undefined)
        if not members:
            findings.append(
                f"CLOSURE RAN ON NOTHING   {key}\n"
                f"        {archive} exists and nm reports no members in it."
            )
            continue

        # Member names are archive-relative; the objects are out-dir relative.
        wanted = {obj[len(entry["prefix"]):] for obj in entry["objects"]}
        omitted = {m for m in members if any(m.endswith(o) for o in omit)}
        # An omitted member stands for a source that was never added to the GN
        # list, so neither its definitions nor its own references exist: it
        # leaves `present` as well as `kept`. Keeping its undefined symbols
        # would report the hole it was going to fill as its own fault.
        present = (wanted & members) - omitted
        if not present:
            findings.append(
                f"CLOSURE RAN ON NOTHING   {key}\n"
                f"        none of the {len(wanted)} object(s) the patch added is a member of "
                f"{archive}.\n"
                f"        Members seen: {len(members)}. The closure would be over the wrong "
                f"library, which is not a clean closure."
            )
            continue

        kept = members - omitted
        all_defined = set().union(*(defined[m] for m in kept)) if kept else set()
        added_undefined = set().union(*(undefined[m] for m in present)) if present else set()
        others = kept - present
        other_undefined = set().union(*(undefined[m] for m in others)) if others else set()
        unresolved = added_undefined - all_defined - other_undefined

        # An unresolved symbol is not yet a finding. Most are provided by
        # another library and are undefined here for good reason. A symbol no
        # archive in the build defines is the one that means something.
        supplied, referenced = classify_candidates(nm, out, unresolved, archive)
        external = supplied | referenced
        holes = sorted(unresolved - external)

        rows.append({
            "library": key, "archive": archive, "members": len(members),
            "added_objects": len(present), "omitted": sorted(omitted),
            "unresolved": sorted(unresolved), "holes": holes,
            "external": sorted(external), "supplied": sorted(supplied),
            "referenced": sorted(referenced), "sources": sorted(entry["sources"]),
        })
        if holes:
            shown = holes[:12]
            findings.append(
                f"SYMBOL CLOSURE INCOMPLETE {key}\n"
                f"        {len(holes)} symbol(s) undefined in the {len(present)} object(s) "
                f"this series adds to {archive}, and defined by NO archive in this build:\n"
                + "".join(f"          {s}\n" for s in shown)
                + (f"          ... and {len(holes) - len(shown)} more\n"
                   if len(holes) > len(shown) else "")
                + f"        Nothing can link these. A source that defines them is missing "
                  f"from a GN list, and the compile gate cannot see it: a static_library "
                  f"stops at alink."
            )
    return findings, rows


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "check"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True, help="repository root holding patches/")
        p.add_argument("--source-index", required=True, help="source<TAB>objects from ninja -t compdb")
        p.add_argument("--out", required=(name == "check"), help="build directory")
        p.add_argument("--ninja", help="ninja binary; with --out, drops targets that "
                                       "link directly and leave no archive")
        if name == "check":
            p.add_argument("--nm", default="llvm-nm", help="nm binary")
            p.add_argument("--verbose", action="store_true",
                           help="also print every added source with no library here")
            p.add_argument("--omit", action="append", default=[],
                           help="treat this member as absent; for proving the check fails")
            p.add_argument("--library", action="append", default=[],
                           help="restrict to these 'dir:target' libraries")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root)
    added = gn_source_additions(root)
    index = parse_source_index(args.source_index)
    libraries, skipped = affected_libraries(added, index)
    unarchived = []
    if args.ninja and args.out:
        unarchived = drop_unarchived(libraries, args.ninja, args.out)

    if args.command == "plan":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(newline="\n")
        for key in sorted(libraries):
            print(libraries[key]["archive"])
        print(f"  {len(added)} source(s) added to a GN list by the series; "
              f"{len(libraries)} library/libraries affected on this platform, "
              f"{len(skipped)} source(s) not in this platform's build graph",
              file=sys.stderr)
        for key, archive in unarchived:
            print(f"  no archive for {key}: the target links directly (executable or "
                  f"source_set), so its symbol closure is the link's and {archive} "
                  f"is not a ninja target", file=sys.stderr)
        return 0

    if args.library:
        libraries = {k: v for k, v in libraries.items() if k in set(args.library)}
    print(f"==> symbol closure: {len(added)} GN-added source(s), "
          f"{len(libraries)} affected library/libraries, "
          f"{len(skipped)} not in this platform's graph, "
          f"{len(unarchived)} target(s) with no archive")
    for key, archive in unarchived:
        print(f"  {key}: links directly, no archive; closure is the link's")
    findings, rows = closure(libraries, args.nm, args.out, tuple(args.omit))
    for row in rows:
        print(f"  {row['library']}")
        print(f"    archive {row['archive']}  members {row['members']}  "
              f"added objects {row['added_objects']}")
        if row["omitted"]:
            print(f"    omitted (simulating a source missing from the GN list): "
                  f"{', '.join(row['omitted'])}")
        print(f"    undefined in the added objects and defined by no member: "
              f"{len(row['unresolved'])}")
        print(f"      of those, defined by NO archive in this build (holes): "
              f"{len(row['holes'])}")
        print(f"      of those, defined by another archive: {len(row['supplied'])}")
        print(f"      of those, referenced by other archives and defined by none, "
              f"so platform-supplied: {len(row['referenced'])}")
        if row["external"]:
            shown = row["external"][:6]
            print(f"        {', '.join(shown)}"
                  f"{f' ... and {len(row['external']) - len(shown)} more' if len(row['external']) > len(shown) else ''}")
    for finding in findings:
        print(f"  FAIL {finding}")
    for source, why in skipped:
        if args.verbose:
            print(f"  note {source}: {why}")
    if not libraries:
        print("  no library on this platform receives a source the series adds to a GN list")
    elif not findings:
        print("  ok   every symbol the added objects need is defined in their own library, "
              "or by another library in the build")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
