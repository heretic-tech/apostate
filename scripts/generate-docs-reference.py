#!/usr/bin/env python3
"""Write the two generated reference pages of the documentation site.

    docs/reference/gpu-models.mdx   the models each GPU family can present,
                                    from resources/profiles/dispersion/gpu_identity.json
                                    and the anchors in resources/profiles/catalogue.json
    docs/reference/font-lists.mdx   the font families each persona lists, by OS
                                    release and font pack, from
                                    resources/profiles/dispersion/font_packs.json
                                    and os_release.json

Edit this script or the tables, never the pages. The output is byte-identical
across runs: families, releases and packs are ordered by explicit keys, and
options stay in file order.

Usage:
  scripts/generate-docs-reference.py           write both pages
  scripts/generate-docs-reference.py --check   write nothing; exit 1 if either
                                               committed page differs from what
                                               this script would write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES = REPO_ROOT / "resources/profiles"
DISPERSION = PROFILES / "dispersion"
CATALOGUE = PROFILES / "catalogue.json"
GPU_IDENTITY = DISPERSION / "gpu_identity.json"
FONT_PACKS = DISPERSION / "font_packs.json"
OS_RELEASE = DISPERSION / "os_release.json"
DOCS = REPO_ROOT / "docs/reference"
GPU_PAGE = DOCS / "gpu-models.mdx"
FONT_PAGE = DOCS / "font-lists.mdx"
SCRIPT = "scripts/generate-docs-reference.py"
BLOB = "https://github.com/heretic-tech/apostate/blob/main/"

PLATFORM_ORDER = ("windows", "macos", "linux")
PLATFORM_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}
# An anchor's host_requirements.architecture, as the page names it. "an" reads
# for both.
ARCHITECTURE_NAMES = {"arm": "ARM", "x86": "x86"}
BACKEND_NAMES = {
    "ANGLE/D3D11": "Direct3D 11",
    "ANGLE/Metal": "Metal",
    "ANGLE/Vulkan": "Vulkan",
    "ANGLE/SwiftShader": "SwiftShader",
}


class GeneratorError(Exception):
    pass


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratorError(f"cannot read {path.relative_to(REPO_ROOT)}: {exc}") from exc
    if not isinstance(value, dict):
        raise GeneratorError(f"{path.relative_to(REPO_ROOT)} is not a JSON object")
    return value


def text(value: str) -> str:
    """*value* as MDX prose or a table cell: no JSX, no column breaks."""
    return (value.replace("\\", "\\\\").replace("|", "\\|").replace("{", "&#123;")
            .replace("}", "&#125;").replace("<", "&lt;").replace(">", "&gt;"))


def code(value: str) -> str:
    """*value* as inline code in a table cell."""
    if "`" in value:
        raise GeneratorError(f"a backtick in {value!r} cannot go into inline code")
    return "`" + value.replace("|", "\\|") + "`"


def share(weight: int, total: int) -> str:
    """A percentage with one decimal, or none when it is whole."""
    value = f"{weight * 100 / total:.1f}"
    return (value[:-2] if value.endswith(".0") else value) + "%"


def join_list(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def frontmatter(title: str, description: str) -> list[str]:
    return ["---", f'title: "{title}"', f'description: "{description}"', "---", ""]


def generated_note(*sources: Path) -> str:
    named = " and ".join(f"[`{path.relative_to(REPO_ROOT)}`]({BLOB}{path.relative_to(REPO_ROOT)})"
                         for path in sources)
    return f"This page is generated from {named} by `{SCRIPT}`."


# --------------------------------------------------------------------------
# GPU models
# --------------------------------------------------------------------------

def model_label(option: dict[str, Any]) -> str:
    """The model name the renderer string carries.

    A registered identity names it in its ``member`` block. A measured one is
    the anchor member its ``source`` points at after ``#``.
    """
    member = option.get("member")
    if isinstance(member, dict) and isinstance(member.get("label"), str):
        return member["label"]
    source = option.get("source")
    if isinstance(source, str) and "#" in source:
        return source.split("#", 1)[1]
    raise GeneratorError(f"gpu_identity option {option.get('id')!r} names no model")


def family_title(anchor: dict[str, Any]) -> str:
    platform = PLATFORM_NAMES.get(anchor["platform"], anchor["platform"])
    backend = BACKEND_NAMES.get(anchor["backend"], anchor["backend"])
    if anchor.get("software_anchor"):
        return f"{platform}, {backend} software renderer"
    return f"{platform}, {anchor['vendor']}, {backend}"


def gpu_page() -> str:
    catalogue = load(CATALOGUE)
    table = load(GPU_IDENTITY)
    anchors = {anchor["id"]: anchor for anchor in catalogue["anchors"]}
    option_sets: dict[str, list[dict[str, Any]]] = {}
    for option_set in table["option_sets"]:
        anchor_id = option_set["key"]["anchor"]
        if anchor_id not in anchors:
            raise GeneratorError(f"gpu_identity keys an unknown anchor {anchor_id!r}")
        option_sets[anchor_id] = option_set["options"]
    missing = sorted(set(anchors) - set(option_sets))
    if missing:
        raise GeneratorError(f"no gpu_identity option set for {', '.join(missing)}")

    def order(anchor_id: str) -> tuple[int, int, str]:
        anchor = anchors[anchor_id]
        return (PLATFORM_ORDER.index(anchor["platform"]), bool(anchor.get("software_anchor")),
                anchor_id)

    lines = frontmatter(
        "GPU models",
        "Every GPU model each GPU family can present, with its WebGL vendor and renderer "
        "strings and its share of the family.")
    lines += [
        generated_note(GPU_IDENTITY, CATALOGUE),
        "",
        "A GPU family is a set of WebGL and WebGPU values measured on real hardware. Every model "
        "in a family presents that family's values, with its own vendor and renderer strings, "
        "which a page reads as `UNMASKED_VENDOR_WEBGL` and `UNMASKED_RENDERER_WEBGL`. A seed "
        "draws a family for the persona's platform and then a model from the family. "
        "[GPU](/guides/gpu) explains how to choose one.",
        "",
        "- **Share** is the model's weight as a share of its family: the fraction of seeds that "
        "draw the family and present this model.",
        "- **Measured** models were captured on a machine with that GPU. The other models are "
        "registered on the family, and present the family's measured values with their own "
        "strings.",
        "",
        "To pin a family, pass its id to `--fingerprint-anchor`. To pin a model, pass its "
        "renderer string to `--fingerprint-gpu-renderer`. A renderer string from another family "
        "is refused. [Switches](/reference/switches) has both.",
        "",
    ]
    for anchor_id in sorted(option_sets, key=order):
        anchor = anchors[anchor_id]
        options = option_sets[anchor_id]
        total = sum(int(option["weight"]) for option in options)
        vendors = sorted({option["value"]["gpu"]["unmasked_vendor"] for option in options})
        if anchor.get("software_anchor"):
            drawn = "A seed never draws this family. Only `--fingerprint-anchor` selects it."
        else:
            # A family measured on one CPU family is drawn only on a host of
            # that family, because the persona claims the host's CPU.
            architecture = (anchor.get("host_requirements") or {}).get("architecture")
            host = f" on an {ARCHITECTURE_NAMES[architecture]} host" if architecture else ""
            drawn = f"{PLATFORM_NAMES[anchor['platform']]} personas{host} draw from this family."
        measured = join_list([text(member) for member in anchor["members"]])
        count = f"{len(options)} {'model' if len(options) == 1 else 'models'}"
        lines += [
            f"## {family_title(anchor)}",
            "",
            f"The family id is `{anchor_id}`. {drawn} It has {count}. It was measured on "
            f"{measured}.",
            "",
        ]
        if len(vendors) == 1:
            lines += [f"Every model's vendor string is {code(vendors[0])}.", ""]
            lines += ["| Model | Share | Measured | Renderer string |",
                      "| --- | --- | --- | --- |"]
        else:
            lines += ["| Model | Share | Measured | Vendor string | Renderer string |",
                      "| --- | --- | --- | --- | --- |"]
        for option in options:
            gpu = option["value"]["gpu"]
            cells = [text(model_label(option)), share(int(option["weight"]), total),
                     "no" if "member" in option else "yes"]
            if len(vendors) != 1:
                cells.append(code(gpu["unmasked_vendor"]))
            cells.append(code(gpu["unmasked_renderer"]))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Font lists
# --------------------------------------------------------------------------

def release_name(option: dict[str, Any]) -> str:
    platform = option["value"]["platform"]
    name = platform["name"]
    if name == "macOS":
        return f"macOS {platform['version']}"
    if name == "Windows":
        parts = option["id"].split("-")[1:]
        return "Windows " + " ".join(part.upper() if part.endswith("h2") or part.endswith("h1")
                                     else part for part in parts)
    return name


def font_page() -> str:
    packs = load(FONT_PACKS)
    releases = load(OS_RELEASE)

    # Every release of a platform with its share of that platform's seeds.
    release_info: dict[str, tuple[str, str, str]] = {}
    for option_set in releases["option_sets"]:
        platform = option_set["key"]["platform"]
        total = sum(int(option["weight"]) for option in option_set["options"])
        for option in option_set["options"]:
            release_info[option["id"]] = (platform, release_name(option),
                                          share(int(option["weight"]), total))

    # Releases whose packs are identical share one section.
    groups: dict[tuple[str, str], list[str]] = {}
    group_packs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for option_set in packs["option_sets"]:
        release = option_set["key"]["os_release"]
        platform = option_set["key"]["platform"]
        if release not in release_info:
            raise GeneratorError(f"font_packs keys an unknown OS release {release!r}")
        if release_info[release][0] != platform:
            raise GeneratorError(f"font_packs puts {release!r} under {platform!r}")
        options = [{key: option[key] for key in ("id", "pack_kind", "weight", "value")}
                   for option in option_set["options"]]
        signature = (platform, json.dumps(options, sort_keys=True))
        groups.setdefault(signature, []).append(release)
        group_packs[signature] = options

    def release_order(release: str) -> tuple[float, str]:
        # Most common release first.
        return (-float(release_info[release][2].rstrip("%")), release)

    def group_order(signature: tuple[str, str]) -> tuple[int, float, str]:
        first = sorted(groups[signature], key=release_order)[0]
        return (PLATFORM_ORDER.index(signature[0]),) + release_order(first)

    lines = frontmatter(
        "Font lists",
        "The font families each persona lists, by OS release and font pack, and the share of "
        "seeds that get each optional pack.")
    lines += [
        generated_note(FONT_PACKS, OS_RELEASE),
        "",
        "A persona's fonts come in packs. Every seed gets its release's core pack, and each "
        "optional pack is included for the share of seeds shown. A page sees a family only when "
        "it is in a drawn pack and installed on the host. Families outside the packs are hidden. "
        "[Fonts](/guides/fonts) explains how to install each persona's fonts.",
        "",
    ]
    for platform in PLATFORM_ORDER:
        signatures = sorted((sig for sig in groups if sig[0] == platform), key=group_order)
        if not signatures:
            continue
        lines += [f"## {PLATFORM_NAMES[platform]}", ""]
        for signature in signatures:
            members = sorted(groups[signature], key=release_order)
            names = [release_info[release][1] for release in members]
            if len(signatures) > 1:
                lines += [f"### {', '.join(names)}", ""]
            covered = [f"{code(release)} ({release_info[release][2]} of "
                       f"{PLATFORM_NAMES[platform]} seeds)" for release in members]
            listed = join_list(covered)
            lines += [f"Seeds on the OS {'release' if len(members) == 1 else 'releases'} "
                      f"{listed} list these fonts.", ""]
            for option in group_packs[signature]:
                fonts = option["value"]["fonts"]
                families = list(fonts["enumeration_allowlist"])
                if option["pack_kind"] == "core":
                    heading = f"Core pack {code(option['id'])}, every seed"
                else:
                    heading = (f"Optional pack {code(option['id'])}, "
                               f"{int(option['weight'])}% of seeds")
                lines += [f"**{heading}.** {len(families)} "
                          f"{'family' if len(families) == 1 else 'families'}:", ""]
                lines += [", ".join(text(family) for family in families), ""]
                generic = fonts.get("generic_family_map")
                if isinstance(generic, dict) and generic:
                    lines += ["CSS generic families resolve to:", "",
                              "| Generic family | Font |", "| --- | --- |"]
                    for name in sorted(generic):
                        lines.append(f"| {code(name)} | {text(generic[name])} |")
                    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------

PAGES = ((GPU_PAGE, gpu_page), (FONT_PAGE, font_page))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if a committed page is out of date")
    args = parser.parse_args(argv)
    try:
        rendered = [(path, build()) for path, build in PAGES]
    except (GeneratorError, KeyError, TypeError, ValueError) as exc:
        print(f"generate-docs-reference: {exc}", file=sys.stderr)
        return 2
    stale = []
    for path, content in rendered:
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current == content:
            continue
        if args.check:
            stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    if stale:
        for path in stale:
            print(f"{path.relative_to(REPO_ROOT)} is out of date; run {SCRIPT}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
