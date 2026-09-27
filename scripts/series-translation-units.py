#!/usr/bin/env python3
"""List every translation unit the patch series touches, in series order.

The V1 gate compiles files; the patch series is the only authoritative record
of which files the fork changes. A hand-maintained list of gate targets is the
failure this script exists to prevent: it goes stale silently, and a gate that
silently stops covering a file is worse than no gate, because it reports green.

    scripts/series-translation-units.py
    scripts/series-translation-units.py --with-patches
    scripts/series-translation-units.py --json

Reads patches/series for the patch names and order, then every '+++ b/<path>'
in those patches. Prints the ones that are translation units, de-duplicated,
first-mention order. Paths are Chromium-relative, exactly as the patch writes
them, so they can be handed straight to scripts/checkseries.sh or ninja.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# A compiler input, i.e. something that produces an object file.
#
# Headers are excluded, and the reason usually given for that -- "a header has
# no object of its own, and it is covered by compiling the translation units
# that include it" -- is an assumption rather than a check. Nothing verifies
# that any compiled unit includes a given patched header. The ffmpeg codec list
# falsified the same assumption for .c files: it has no object either, and
# until scripts/series_absences.py started resolving such a file to its
# includer and reading ninja's recorded dependencies, patch 0061's
# registration was compiled by nothing the gate checked. At 152.0.7977.83 the
# series patches 58 files outside this tuple -- 53 headers and 5 .asm, four of
# the .asm include-only in exactly that way -- so they are neither verified
# nor visible as absences. docs/contributing/build.mdx records that gap; closing it means
# putting each includer's object into a paid compile set, which is a coverage
# decision rather than a bug fix.
#
# .m is listed although the series contains no Objective-C file today. The
# point of deriving this list is that it keeps up with the series without
# anyone remembering to update it, and an extension missing from this tuple is
# the one way that can still fail.
TU_SUFFIXES = (".c", ".cc", ".cpp", ".mm", ".m")


def series_patch_names(series: pathlib.Path) -> list[str]:
    """Patch file names from patches/series, in order.

    Comment and blank-line handling matches scripts/apply-patches.sh, which is
    what actually applies these patches. Two parsers that disagree about which
    patches are in the series would give a gate that checks a different fork
    from the one being built.
    """
    names = []
    for line in series.read_text(encoding="utf-8").splitlines():
        name = line.partition("#")[0].strip()
        if name:
            names.append(name)
    return names


def patched_paths(patch: pathlib.Path) -> list[str]:
    """Target-side paths of every hunk header pair in one patch.

    Keyed on the '---' / '+++' pair rather than on '+++' alone. A patch's
    commit message can legitimately quote a diff, and a bare '+++ b/...' match
    would then invent a file that the patch does not touch. The pair only
    occurs in a real unified-diff header.
    """
    paths = []
    lines = patch.read_text(encoding="utf-8", errors="replace").splitlines()
    for previous, line in zip(lines, lines[1:]):
        if not previous.startswith("--- ") or not line.startswith("+++ "):
            continue
        # Trailing tab-separated timestamps are legal in unified diffs, and
        # git quotes paths that contain unusual characters.
        target = line[4:].split("\t")[0].strip()
        if target == "/dev/null":  # the hunk deletes the file
            continue
        if len(target) > 1 and target[0] == '"' and target[-1] == '"':
            target = target[1:-1]
        if target.startswith("b/"):
            target = target[2:]
        if target:
            paths.append(target)
    return paths


def translation_units(root: pathlib.Path) -> dict[str, list[str]]:
    """Every translation unit in the series, mapped to the patches touching it.

    Insertion order is series order, so the returned mapping can be iterated
    to get a gate order that matches the order the patches apply in.
    """
    series = root / "patches" / "series"
    if not series.is_file():
        raise SystemExit(f"missing patch series at {series}")

    units: dict[str, list[str]] = {}
    for name in series_patch_names(series):
        patch = root / "patches" / name
        if not patch.is_file():
            raise SystemExit(f"missing patch file named by the series: {patch}")
        for path in patched_paths(patch):
            if path.endswith(TU_SUFFIXES):
                units.setdefault(path, []).append(name)
    return units


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=str(pathlib.Path(__file__).resolve().parent.parent),
        help="repository root holding patches/series (default: this checkout)",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--with-patches",
        action="store_true",
        help="print 'path<TAB>patch,patch' so a result can be attributed",
    )
    output.add_argument(
        "--json",
        action="store_true",
        help="print [{path, patches}] for a caller that wants structure",
    )
    args = parser.parse_args(argv)

    units = translation_units(pathlib.Path(args.root))
    if args.json:
        json.dump(
            [{"path": path, "patches": patches} for path, patches in units.items()],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    elif args.with_patches:
        for path, patches in units.items():
            print(f"{path}\t{','.join(patches)}")
    else:
        for path in units:
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
