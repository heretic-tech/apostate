#!/usr/bin/env python3
"""Compile the dispersion tables and GPU anchors into C++ source.

`docs/FINGERPRINTS.md` §10 rejects reading the tables from disk at runtime: a
data directory beside the executable is one more thing to lose, to mismatch
against the binary, and to diverge per install. So the tables are compiled in,
and this is the GN action that does it.

Two properties are load-bearing and both are tested by running the script twice
and diffing:

  * **Byte-identical output.** Everything that goes into the generated files is
    sorted by an explicit key -- files by name, dict keys by name, the digest
    manifest by path -- so two runs over the same inputs produce the same bytes.
    Nothing iterates a Python set or a filesystem directory in native order.

  * **`options` stay in file order.** The weighted pick in §5 resolves an index
    against cumulative weights *in file order*, so reordering an option list
    silently reassigns every seed downstream of it. Option *content* is
    canonicalised (sorted keys); option *sequence* is never touched.

The SHA-256 over the inputs is emitted as `kCatalogueVersion` and feeds the
composition root in §5, so a binary and a profile cannot silently disagree
about what the catalogue said.

Usage (the GN action passes all of these):

    generate-dispersion-tables.py --data-root DIR --out-cc PATH --out-h PATH
                                  [--depfile PATH]

`--data-root` is the Apostate repository root. The script reads
`resources/profiles/catalogue.json`, `resources/profiles/dispersion/*.json`,
`corpus/anchors/*.json` and `build/CHROMIUM_VERSION` from it.
`--dispersion-dir`, `--anchors-dir` and `--catalogue` override the individual
paths, which is how a single table can be regenerated against a scratch copy.

Inside a Chromium build this is invoked through
`base/apostate/generate_dispersion_tables.py`, a shim added by
`patches/0081-apostate-profile-compositor.patch` that locates this repository
and execs this file. A GN action's script has to live in the source tree, and
two copies of the table parser is the exact failure the compositor exists to
remove.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

# Bumped when the *shape* of the generated C++ changes, not when the data does.
GENERATED_SCHEMA_VERSION = "1"

# The file shape this parser accepts, from local://dispersion-table-shape.md.
DISPERSION_SCHEMA = "apostate/dispersion/1"
ANCHOR_SCHEMA = "apostate/corpus/anchor/1"

# Dotted profile paths whose arrays are unioned rather than replaced when two
# option fragments in the same axis both carry them. This is what makes a font
# pack additive without the compositor knowing what a font is; every other
# array replaces, which is what the cross-axis deep merge already does.
UNION_ARRAY_PATHS = ("fonts.enumeration_allowlist",)

SELECTIONS = {"single": "kSingle", "core-plus-subset": "kCorePlusSubset"}

SERVABILITIES = {
    "none": "kNone",
    "clamp-down": "kClampDown",
    "window-bounds": "kWindowBounds",
    "anchor-member": "kAnchorMember",
    "files-present": "kFilesPresent",
}

EVIDENCE_CLASSES = {
    "physical-ground-truth",
    "compatibility-capture",
    "catalogue-value",
    "public-corpus",
    "native-derived",
    "proxy-derived",
    "host-inherited",
}

PACK_KINDS = {"core": "kCore", "optional": "kOptional"}

# An unrecognised `requires` key is a hard error, not an ignored field:
# silently skipping a precondition would ship an unservable claim.
REQUIRES_KEYS = {
    "min_logical_cores": int,
    "min_total_bytes": int,
    "min_width": int,
    "min_height": int,
    "anchor": str,
    "families": list,
}

ID_RE = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")
GL_EXTENSION_RE = re.compile(r"^[A-Za-z0-9_]+$")
PRECISION_KEY_RE = re.compile(r"^(VERTEX|FRAGMENT)_SHADER\.(LOW|MEDIUM|HIGH)_(FLOAT|INT)$")

# Anchor `platform` and `backend` are corpus spellings. They are mapped, never
# guessed: an unknown value is a hard error, because a wrong backend makes an
# unservable anchor selectable, which is the §4 failure this model exists to
# avoid.
PLATFORM_MAP = {"windows": "windows", "macos": "macos", "linux": "linux"}
BACKEND_MAP = {
    "angle/metal": "metal",
    "angle/d3d11": "d3d11",
    "angle/d3d9": "d3d9",
    "angle/vulkan": "vulkan",
    "angle/opengl": "gl",
    "angle/gl": "gl",
    "angle/swiftshader": "swiftshader",
    "swiftshader": "swiftshader",
}


class GeneratorError(Exception):
    """A defect in the input tables. Never recovered from."""


def die(path: Path, message: str) -> None:
    raise GeneratorError(f"{path}: {message}")


# --------------------------------------------------------------------------
# C++ emission helpers
# --------------------------------------------------------------------------

_LITERAL_CHUNK = 72


def cpp_chunks(text: str) -> list[str]:
    """Escape `text` as a list of adjacent C++ string literals.

    The input is ASCII-only JSON or a captured identity string, so `"` and
    `\\` are the only characters needing an escape and no `\\x` or octal
    escape is ever emitted. That matters at a chunk boundary: a hex escape
    would swallow a following hex digit from the next chunk, and numeric
    escapes are the one thing that makes naive chunking wrong.

    Callers must keep the chunks as a list. Joining them into one string and
    re-splitting on whitespace drops every space inside the content -- an
    unmasked renderer string became "ANGLE(Apple,ANGLEMetalRenderer:..." that
    way, and adjacent-literal concatenation makes the damage compile.
    """
    tokens: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code > 0x7E:
            raise GeneratorError(
                f"non-printable-ASCII byte {code:#04x} in generated string; "
                "inputs must be ASCII-escaped JSON"
            )
        if ch == "\\":
            tokens.append("\\\\")
        elif ch == '"':
            tokens.append('\\"')
        else:
            tokens.append(ch)

    if not tokens:
        return ['""']

    chunks: list[str] = []
    current = ""
    for token in tokens:
        if len(current) + len(token) > _LITERAL_CHUNK and current:
            chunks.append(current)
            current = ""
        current += token
    if current:
        chunks.append(current)
    return [f'"{chunk}"' for chunk in chunks]


def cpp_string(text: str) -> str:
    """One-line form. Adjacent literals concatenate, so the spaces between the
    chunks are insignificant to the compiler."""
    return " ".join(cpp_chunks(text))


def cpp_literal_block(text: str, indent: str) -> str:
    """Emit a possibly long string literal across indented continuation lines."""
    return f"\n{indent}".join(cpp_chunks(text))


def identifier(*parts: str) -> str:
    """A stable, collision-free C++ identifier fragment for `parts`."""
    out = []
    for part in parts:
        chunk = re.sub(r"[^0-9a-zA-Z]+", " ", str(part)).strip()
        out.append("".join(word[:1].upper() + word[1:] for word in chunk.split(" ") if word))
    name = "".join(out)
    if not name or not name[0].isalpha():
        name = "X" + name
    return name


def canonical_json(value: object) -> str:
    """Canonical JSON: sorted keys, compact separators, ASCII-escaped."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# --------------------------------------------------------------------------
# Input loading
# --------------------------------------------------------------------------


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(path, f"not valid JSON: {exc}")
    return None


def require_str(path: Path, obj: dict, key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        die(path, f"missing or non-string {key!r}")
    return value


def parse_requires(path: Path, option_id: str, raw: object) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        die(path, f"option {option_id!r}: `requires` must be an object")
    parsed: dict[str, object] = {}
    for key in sorted(raw):
        if key not in REQUIRES_KEYS:
            die(
                path,
                f"option {option_id!r}: unrecognised `requires` key {key!r}. "
                "Silently skipping a precondition would ship an unservable "
                "claim, so this is a hard error",
            )
        expected = REQUIRES_KEYS[key]
        value = raw[key]
        if expected is int:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                die(path, f"option {option_id!r}: `requires.{key}` must be a non-negative integer")
        elif expected is str:
            if not isinstance(value, str) or not value:
                die(path, f"option {option_id!r}: `requires.{key}` must be a non-empty string")
        else:
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                die(path, f"option {option_id!r}: `requires.{key}` must be a list of strings")
        parsed[key] = value
    return parsed


def parse_axis(path: Path) -> dict:
    raw = load_json(path)
    if not isinstance(raw, dict):
        die(path, "top level must be an object")

    schema = require_str(path, raw, "schema")
    if schema != DISPERSION_SCHEMA:
        die(path, f"schema is {schema!r}, expected {DISPERSION_SCHEMA!r}")

    axis = require_str(path, raw, "axis")
    if axis != path.stem:
        die(path, f"axis {axis!r} does not match file stem {path.stem!r}")
    label = require_str(path, raw, "label")

    selection = require_str(path, raw, "selection")
    if selection not in SELECTIONS:
        die(path, f"selection {selection!r} not one of {sorted(SELECTIONS)}")
    servability = require_str(path, raw, "servability")
    if servability not in SERVABILITIES:
        die(path, f"servability {servability!r} not one of {sorted(SERVABILITIES)}")

    conditioned_on = raw.get("conditioned_on", [])
    if not isinstance(conditioned_on, list) or not all(
        isinstance(name, str) and name for name in conditioned_on
    ):
        die(path, "`conditioned_on` must be a list of non-empty strings")

    option_sets_raw = raw.get("option_sets")
    if not isinstance(option_sets_raw, list) or not option_sets_raw:
        die(path, "`option_sets` must be a non-empty array")

    option_sets = []
    seen_keys: set[str] = set()
    for set_index, entry in enumerate(option_sets_raw):
        if not isinstance(entry, dict):
            die(path, f"option_sets[{set_index}] must be an object")
        key = entry.get("key", {})
        if not isinstance(key, dict):
            die(path, f"option_sets[{set_index}].key must be an object")
        if sorted(key) != sorted(conditioned_on):
            die(
                path,
                f"option_sets[{set_index}].key has names {sorted(key)} but the "
                f"axis is conditioned on {sorted(conditioned_on)}; matching is "
                "exact and total",
            )
        for name, value in key.items():
            # An empty value is legal and load-bearing: the voices table ships
            # a set keyed on "" that every unmeasured locale projects
            # onto, which is what keeps matching exact and total.
            if not isinstance(value, str):
                die(path, f"option_sets[{set_index}].key.{name} must be a string")
        # Two option sets with the same key would make the match ambiguous,
        # which the shape doc calls a table defect rather than a runtime
        # condition. Catch it at build time instead of at launch.
        fingerprint = canonical_json(key)
        if fingerprint in seen_keys:
            die(path, f"duplicate option_sets key {fingerprint}")
        seen_keys.add(fingerprint)

        options_raw = entry.get("options")
        if not isinstance(options_raw, list) or not options_raw:
            die(path, f"option_sets[{set_index}].options must be a non-empty array")

        options = []
        seen_ids: set[str] = set()
        for option_index, option in enumerate(options_raw):
            if not isinstance(option, dict):
                die(path, f"option_sets[{set_index}].options[{option_index}] must be an object")
            option_id = require_str(path, option, "id")
            if not ID_RE.match(option_id):
                die(path, f"option id {option_id!r} does not match {ID_RE.pattern}")
            if option_id in seen_ids:
                die(path, f"duplicate option id {option_id!r} in option_sets[{set_index}]")
            seen_ids.add(option_id)

            weight = option.get("weight")
            if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
                die(path, f"option {option_id!r}: weight must be a positive integer")

            evidence = require_str(path, option, "evidence")
            if evidence not in EVIDENCE_CLASSES:
                die(path, f"option {option_id!r}: evidence {evidence!r} is not a contract class")

            pack_kind = option.get("pack_kind")
            if selection == "core-plus-subset":
                if pack_kind not in PACK_KINDS:
                    die(
                        path,
                        f"option {option_id!r}: core-plus-subset requires "
                        f"pack_kind in {sorted(PACK_KINDS)}",
                    )
            elif pack_kind is not None:
                die(path, f"option {option_id!r}: pack_kind is only valid for core-plus-subset")

            if selection == "core-plus-subset" and pack_kind == "optional" and not 1 <= weight <= 100:
                die(
                    path,
                    f"option {option_id!r}: an optional pack's weight is a "
                    "percentage and must be in 1..100",
                )

            value = option.get("value")
            if not isinstance(value, dict):
                die(path, f"option {option_id!r}: `value` must be a profile-fragment object")

            # A GPU identity that is not a measured anchor member registers
            # itself here. `register_identities` validates the block and folds
            # it into the anchor's member table; every other axis must not
            # carry one.
            member = option.get("member")
            if member is not None and not isinstance(member, dict):
                die(path, f"option {option_id!r}: `member` must be an object")

            options.append(
                {
                    "id": option_id,
                    "weight": weight,
                    "evidence": evidence,
                    "source": option.get("source", ""),
                    "pack_kind": pack_kind,
                    "requires": parse_requires(path, option_id, option.get("requires")),
                    "value": value,
                    "member": member,
                }
            )

        if selection == "core-plus-subset" and not any(
            option["pack_kind"] == "core" for option in options
        ):
            die(path, f"option_sets[{set_index}]: core-plus-subset needs at least one core pack")

        if selection == "single":
            total = sum(option["weight"] for option in options)
            # ScaleToRange takes a 32-bit range, so the compositor CHECKs this
            # bound at runtime. Enforcing it here makes an over-wide table a
            # build failure instead of a startup crash.
            if total > 0xFFFFFFFF:
                die(
                    path,
                    f"option_sets[{set_index}]: weights sum to {total}, which "
                    "does not fit in uint32 and would overflow the weighted pick",
                )

        option_sets.append({"key": key, "options": options})

    return {
        "axis": axis,
        "label": label,
        "conditioned_on": conditioned_on,
        "selection": selection,
        "servability": servability,
        "option_sets": option_sets,
    }


# --------------------------------------------------------------------------
# Stamping the pinned build into the captured user agents
# --------------------------------------------------------------------------


def load_build_stamp(data_root: Path) -> tuple[object, Path]:
    """`user_agent_for_build` from the reference resolver, as a module attribute.

    The rule that turns a captured UA string into the one this build may serve
    has to be identical in the compiled tables and in the reference resolver,
    or the binary and the golden vectors disagree about a page-visible string.
    So there is one implementation and this loads it rather than keeping a
    second copy.
    """
    script = data_root / "scripts/profile_resolver.py"
    if not script.is_file():
        raise GeneratorError(f"{script}: missing; it owns the user-agent build stamp")
    spec = importlib.util.spec_from_file_location("apostate_profile_resolver", script)
    if spec is None or spec.loader is None:
        raise GeneratorError(f"{script}: cannot be loaded as a module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for export in ("user_agent_for_build", "gl_limit_entries"):
        if not hasattr(module, export):
            raise GeneratorError(
                f"{script} does not export {export}; the reference resolver changed "
                "shape and the compiled tables would disagree with it about a "
                "page-visible value"
            )
    return module, script


def stamp_user_agents(axes: list[dict], stamp, pinned_version: str) -> int:
    """Rewrite every `browser.user_agent` to the pinned build's major.

    The tables record what a physical device sent, major included. The version a
    UA claims is a build invariant -- `resources/profiles/catalogue.json` lists
    `chromium_version` among the fields a profile never varies -- so it is the
    build compiling these tables that decides it, not the capture. Doing the
    substitution here means the compositor merges a finished string and needs no
    version handling of its own.
    """
    stamped = 0
    for axis in axes:
        for option_set in axis["option_sets"]:
            for option in option_set["options"]:
                browser = option["value"].get("browser")
                if not isinstance(browser, dict):
                    continue
                user_agent = browser.get("user_agent")
                if not isinstance(user_agent, str):
                    continue
                try:
                    browser["user_agent"] = stamp(user_agent, pinned_version)
                except Exception as exc:
                    raise GeneratorError(
                        f"{axis['axis']}/{option['id']}: {exc}"
                    ) from exc
                stamped += 1
    return stamped


# --------------------------------------------------------------------------
# Anchors
# --------------------------------------------------------------------------


def integral(value: object) -> int | None:
    """`value` as an exact integer, or None when it is not losslessly one.

    A nonintegral endpoint stays unrepresented and inherited rather than
    truncated: the Linux/Vulkan anchor's ALIASED_POINT_SIZE_RANGE max is
    2047.9375, and reporting 2047 would be a limit no measurement produced.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def anchor_gl_layer(path: Path, anchor_id: str, cluster: dict, limit_entries) -> dict:
    """The anchor-wide GL capability cluster as a profile fragment.

    An anchor is atomic: selecting it takes the whole cluster, not just the
    identity strings. Presenting a member's renderer string on top of the
    host's own capability tables is precisely the retired catalogue's failure
    (FINGERPRINTS.md §1 and §4), so the cluster travels with the anchor.

    WebGL1 and WebGL2 agree on every shared MAX_* limit and every shader
    precision entry in all four anchors -- that agreement is what makes them
    one anchor -- so the limit map is their union and the extension list is the
    union of what either context exposes. A disagreement is a hard error rather
    than a silently resolved minimum: one limit map cannot represent two
    contexts, and picking one would ship a number neither context reported.
    """
    extensions: set[str] = set()
    limits: dict[str, int] = {}
    precisions: dict[str, dict] = {}

    for context in ("webgl1", "webgl2"):
        section = cluster.get(context)
        if not isinstance(section, dict):
            continue

        for name in section.get("extensions") or []:
            if isinstance(name, str) and GL_EXTENSION_RE.match(name):
                extensions.add(name)

        for name in sorted(section.get("parameters") or {}):
            value = (section.get("parameters") or {})[name]
            # `limit_entries` is scripts/profile_resolver.py's gl_limit_entries,
            # loaded as a module attribute rather than reimplemented, for the
            # same reason the user-agent stamp is: the compiled tables and the
            # reference resolver have to agree on every served number, and two
            # copies of this rule is how they stop agreeing.
            for key, exact in limit_entries(name, value):
                if limits.get(key, exact) != exact:
                    die(
                        path,
                        f"anchor {anchor_id} disagrees with itself on {key}: a "
                        "single limit map cannot represent two contexts",
                    )
                limits[key] = exact

        for key in sorted(section.get("precision") or {}):
            entry = (section.get("precision") or {})[key]
            if not isinstance(entry, dict) or not PRECISION_KEY_RE.match(key):
                continue
            derived = {}
            for field in ("precision", "rangeMax", "rangeMin"):
                exact = integral(entry.get(field))
                if exact is None:
                    derived = {}
                    break
                derived[field] = exact
            if not derived:
                continue
            if precisions.get(key, derived) != derived:
                die(path, f"anchor {anchor_id} disagrees with itself on precision {key}")
            precisions[key] = derived

    # contextAttributes, antialiasSamples and the render digests deliberately
    # stay out: they are V3 conformance targets with no profile field, not
    # loader inputs.
    #
    # Limits are required, not optional. An anchor whose cluster yields none
    # would compile a renderer string into the binary with no capability table
    # behind it, and every serving hook would fall through to the host's own
    # numbers -- indistinguishable from success until a page reads one lookup
    # value and finds a software rasteriser's limits under a discrete-GPU name.
    if not limits:
        die(
            path,
            f"anchor {anchor_id}: capability cluster produced no GL limits, so its "
            "renderer string would be served over the host's own capability table",
        )

    layer: dict[str, object] = {"gl_limits": dict(sorted(limits.items()))}
    if extensions:
        layer["gl_extensions"] = sorted(extensions)
    if precisions:
        layer["gl_precisions"] = dict(sorted(precisions.items()))
    return layer


def member_webgpu(path: Path, anchor_id: str, cluster: dict, capture: str) -> dict:
    """The WebGPU adapter one member measured, or {} when it measured none.

    WebGPU is not uniform inside an anchor. In linux-vulkan-nvidia-adf287b8f0ee
    `capability_cluster.webgpu.uniform` is false: the two Ada cards report
    nvidia/lovelace and the RTX 3090 and RTX PRO 4000 Blackwell returned no
    adapter at all. So it is taken from the resolved member's variant and never
    from the anchor as a whole -- presenting lovelace on a 3090 is an unmeasured
    assertion, and an absent section leaves the host adapter untouched, which is
    the only honest answer for a member that measured none.
    """
    variants = (cluster.get("webgpu") or {}).get("variants") or []
    matches = [
        variant
        for variant in variants
        if isinstance(variant, dict) and capture in (variant.get("members") or [])
    ]
    if len(matches) != 1:
        die(
            path,
            f"anchor {anchor_id} has {len(matches)} WebGPU variants naming "
            f"{capture}; exactly one is required",
        )

    adapters = (matches[0].get("cluster") or {}).get("adapters") or {}
    adapter = adapters.get("high-performance") or adapters.get("low-power")
    if not isinstance(adapter, dict) or not adapter:
        return {}

    section: dict[str, object] = {}
    features = adapter.get("features")
    if isinstance(features, list) and features:
        section["features"] = sorted({str(name) for name in features})
    info = {
        field: adapter[field]
        for field in ("architecture", "vendor")
        if isinstance(adapter.get(field), str) and adapter[field]
    }
    # The subgroup sizes the adapter reported, under the profile's names.
    for measured, field in (("subgroupMinSize", "subgroup_min_size"),
                            ("subgroupMaxSize", "subgroup_max_size")):
        size = integral(adapter.get(measured))
        if size is not None and size > 0:
            info[field] = size
    if info:
        section["info"] = dict(sorted(info.items()))
    limits = {
        name: adapter["limits"][name]
        for name in sorted(adapter.get("limits") or {})
        if integral((adapter.get("limits") or {})[name]) is not None
        and integral((adapter.get("limits") or {})[name]) >= 0
    }
    if limits:
        section["limits"] = dict(sorted(limits.items()))
    return {"webgpu": section} if section else {}


def parse_anchor(path: Path, limit_entries) -> dict | None:
    raw = load_json(path)
    if not isinstance(raw, dict):
        die(path, "top level must be an object")
    if raw.get("schema") != ANCHOR_SCHEMA:
        # Survey and scratch files live in the same directory. Skipping them by
        # schema rather than by filename keeps the two concerns separate.
        return None

    anchor_id = require_str(path, raw, "anchor_id")
    platform_raw = require_str(path, raw, "platform")
    platform = PLATFORM_MAP.get(platform_raw.strip().lower())
    if platform is None:
        die(path, f"platform {platform_raw!r} is not one of {sorted(PLATFORM_MAP)}")

    backend_raw = require_str(path, raw, "backend")
    backend = BACKEND_MAP.get(backend_raw.strip().lower())
    if backend is None:
        die(
            path,
            f"backend {backend_raw!r} is not a known graphics backend "
            f"({sorted(BACKEND_MAP)}); a wrong backend makes an unservable "
            "anchor selectable",
        )

    evidence = raw.get("evidence_class", "")
    if evidence and evidence not in EVIDENCE_CLASSES:
        die(path, f"evidence_class {evidence!r} is not a contract class")

    members_raw = raw.get("members")
    if not isinstance(members_raw, list) or not members_raw:
        die(path, "missing `members`")

    cluster = raw.get("capability_cluster")
    if not isinstance(cluster, dict):
        die(path, "missing `capability_cluster`")

    members = []
    for index, member in enumerate(members_raw):
        if not isinstance(member, dict):
            die(path, f"members[{index}] must be an object")
        identity = member.get("identity", {})
        webgl1 = identity.get("webgl1", {}) if isinstance(identity, dict) else {}
        vendor = webgl1.get("unmaskedVendor") if isinstance(webgl1, dict) else None
        renderer = webgl1.get("unmaskedRenderer") if isinstance(webgl1, dict) else None
        if not isinstance(vendor, str) or not isinstance(renderer, str) or not vendor or not renderer:
            die(path, f"members[{index}] has no measured unmasked vendor/renderer pair")
        capture = member.get("capture")
        if not isinstance(capture, str) or not capture:
            die(path, f"members[{index}] has no capture path to match a WebGPU variant on")
        members.append(
            {
                "label": member.get("label", "") if isinstance(member.get("label"), str) else "",
                # The corpus card name. Not emitted to C++; it is what a
                # registered identity names in `member.webgpu_measured_on`,
                # because a capture label like "REAL-nvidia-4070ti-linux" is
                # not something a catalogue author should have to know.
                "device": member.get("device", "") if isinstance(member.get("device"), str) else "",
                "measured": True,
                "vendor": vendor,
                "renderer": renderer,
                # Per member, not per anchor: WebGPU is not uniform inside an
                # anchor and two of the Linux/Vulkan members measured no adapter.
                "webgpu": member_webgpu(
                    path, anchor_id, cluster, capture.rsplit("/", 1)[-1]
                ),
            }
        )
    members.sort(key=lambda m: (m["vendor"], m["renderer"], m["label"]))

    digests = raw.get("digests", {})
    if not isinstance(digests, dict):
        digests = {}

    # Anchor prevalence is not something this project measured, so the weight is
    # uniform unless an anchor states one. Inventing a market-share prior would
    # be synthesis dressed as data.
    weight = raw.get("dispersion_weight", 1)
    if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
        die(path, "`dispersion_weight` must be a positive integer when present")

    return {
        "id": anchor_id,
        "platform": platform,
        "backend": backend,
        "vendor": raw.get("vendor", "") if isinstance(raw.get("vendor"), str) else "",
        "evidence": evidence,
        "webgl1_caps": digests.get("webgl1_caps_sha256", "") or "",
        "webgl2_caps": digests.get("webgl2_caps_sha256", "") or "",
        "pixels": digests.get("webgl1_pixels_sha256", "") or "",
        "weight": weight,
        "members": members,
        "value": anchor_gl_layer(path, anchor_id, cluster, limit_entries),
    }

def register_identities(axes: list[dict], anchors: list[dict]) -> int:
    """Fold catalogue-registered GPU identities into their anchor's members.

    `Compose` checks after the draw that the identity it drew really is a member
    of the resolved anchor, and `LOG(FATAL)`s when it is not. The member is also
    where WebGPU comes from. So the offered identity list and the compiled member
    table cannot be two separate things: `corpus/anchors/*.json` is what was
    measured, and this is what the catalogue offers on top of it.

    An option whose `(unmasked_vendor, unmasked_renderer)` pair is a measured
    member needs nothing. Any other option must register itself with a `member`
    block carrying the label the runtime reports and, when it carries a sibling's
    measured WebGPU adapter, `webgpu_measured_on` naming that member's device.
    Anything else is a build failure here instead of a crash at launch.

    Two fields of the borrowed adapter may follow the identity instead of the
    donor, and both come from Dawn keying on the PCI device id.
    `webgpu_architecture`: Dawn resolves `GPUAdapterInfo.architecture` from the
    id (`third_party/dawn/src/dawn/gpu_info.json`), so an identity that rotates
    across silicon generations on one backend anchor -- every D3D11 NVIDIA
    board sits on the same feature-level caps, but a 4090 reports `lovelace`
    where the measured 3070 Ti reports `ampere` -- must state its own.
    `webgpu_subgroup_min_size`: on D3D12 Dawn lowers
    `GPUAdapterInfo.subgroupMinSize` to 8 for Intel Gen12LP ids (toggle
    d3d12_relax_min_subgroup_size_to_8), so a UHD 770 or Iris Xe that borrows
    the measured UHD 630 adapter states 8. Each replaces only its own field;
    vendor, features, the other subgroup size and the measured limits still
    come from the donor, because those are what the donor measured.

    Returns the number of identities registered.
    """
    by_id = {anchor["id"]: anchor for anchor in anchors}
    registered = 0

    for axis in axes:
        if axis["axis"] != "gpu_identity":
            for option_set in axis["option_sets"]:
                for option in option_set["options"]:
                    if option["member"] is not None:
                        raise GeneratorError(
                            f"axis {axis['axis']!r} option {option['id']!r} carries a "
                            "`member` block; only gpu_identity registers anchor members"
                        )
            continue

        for option_set in axis["option_sets"]:
            for option in option_set["options"]:
                requires = option["requires"] or {}
                anchor_id = requires.get("anchor")
                if not anchor_id:
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r} has no `requires.anchor`; "
                        "the runtime servability filter keeps an identity only when its "
                        "requirement names the resolved anchor, so an option without one "
                        "is never servable"
                    )
                anchor = by_id.get(anchor_id)
                if anchor is None:
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r} requires anchor "
                        f"{anchor_id!r}, which is not in the corpus"
                    )

                gpu = option["value"].get("gpu")
                if not isinstance(gpu, dict):
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r} has no `gpu` fragment"
                    )
                vendor = gpu.get("unmasked_vendor")
                renderer = gpu.get("unmasked_renderer")
                if not isinstance(vendor, str) or not isinstance(renderer, str):
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r} must carry both "
                        "`gpu.unmasked_vendor` and `gpu.unmasked_renderer`"
                    )

                measured = next(
                    (
                        member
                        for member in anchor["members"]
                        if member["measured"]
                        and member["vendor"] == vendor
                        and member["renderer"] == renderer
                    ),
                    None,
                )
                block = option["member"]

                if measured is not None:
                    if block is not None:
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r} is a measured member of "
                            f"{anchor_id!r} and must not also register one"
                        )
                    # The anchor's own class, not a fixed one: the software
                    # anchor is a real measurement of a machine that claims no
                    # hardware, so it is compatibility-capture, and an identity
                    # drawn on it must not claim more than the cluster it sits on.
                    if option["evidence"] != anchor["evidence"]:
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r} names a measured member "
                            f"of {anchor_id!r} but claims evidence {option['evidence']!r} "
                            f"where the anchor is {anchor['evidence']!r}"
                        )
                    continue

                if block is None:
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r} offers a renderer string that "
                        f"anchor {anchor_id!r} did not measure and does not register it. The "
                        "runtime would LOG(FATAL) on this draw. Add a `member` block, or "
                        "remove the option"
                    )
                if option["evidence"] == "physical-ground-truth":
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r} registers an unmeasured "
                        "identity and may not claim physical-ground-truth"
                    )
                label = block.get("label")
                if not isinstance(label, str) or not label:
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r}: `member.label` must be a "
                        "non-empty string; it is what --fingerprint-explain reports as the "
                        "WebGPU source"
                    )
                unknown = sorted(
                    set(block)
                    - {
                        "label",
                        "webgpu_measured_on",
                        "webgpu_architecture",
                        "webgpu_subgroup_min_size",
                    }
                )
                if unknown:
                    raise GeneratorError(
                        f"gpu_identity option {option['id']!r}: unrecognised `member` keys "
                        f"{unknown}"
                    )

                webgpu: dict = {}
                source = block.get("webgpu_measured_on")
                if source is not None:
                    if not isinstance(source, str) or not source:
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r}: "
                            "`member.webgpu_measured_on` must be a non-empty string"
                        )
                    donor = next(
                        (
                            member
                            for member in anchor["members"]
                            if member["measured"] and member["device"] == source
                        ),
                        None,
                    )
                    if donor is None:
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r}: "
                            f"`member.webgpu_measured_on` names {source!r}, which is not a "
                            f"measured member of {anchor_id!r}"
                        )
                    if not donor["webgpu"]:
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r}: "
                            f"`member.webgpu_measured_on` names {source!r}, which measured no "
                            "WebGPU adapter. Omit the field: WebGPU then stays host-inherited "
                            "instead of claiming a cluster nothing measured"
                        )
                    webgpu = donor["webgpu"]

                # Fields of the borrowed adapter that follow the identity rather
                # than the donor, by the name the profile's `webgpu.info` uses.
                overrides: dict[str, object] = {}
                architecture = block.get("webgpu_architecture")
                if architecture is not None:
                    if not isinstance(architecture, str) or not architecture:
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r}: "
                            "`member.webgpu_architecture` must be a non-empty string"
                        )
                    overrides["architecture"] = architecture
                subgroup_min = block.get("webgpu_subgroup_min_size")
                if subgroup_min is not None:
                    size = integral(subgroup_min)
                    if size is None or size <= 0 or size & (size - 1):
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r}: "
                            "`member.webgpu_subgroup_min_size` must be a positive power of two"
                        )
                    overrides["subgroup_min_size"] = size
                if overrides:
                    # `webgpu` is the profile fragment `{"webgpu": {...}}` that
                    # member_webgpu built, so the adapter sits one level in.
                    section = webgpu.get("webgpu") or {}
                    info = dict(section.get("info") or {})
                    missing = sorted(set(overrides) - set(info))
                    if missing:
                        raise GeneratorError(
                            f"gpu_identity option {option['id']!r}: a `member.webgpu_*` "
                            "field overrides one field of a borrowed adapter, and this "
                            f"option borrows none that reported {', '.join(missing)}. Name "
                            "a donor in `member.webgpu_measured_on` that did, or drop the "
                            "field"
                        )
                    info.update(overrides)
                    # Fresh dicts all the way down: `webgpu` is the donor
                    # member's own object and every other identity registered on
                    # that donor shares it.
                    webgpu = {
                        "webgpu": dict(section, info=dict(sorted(info.items())))
                    }

                anchor["members"].append(
                    {
                        "label": label,
                        "device": label,
                        "measured": False,
                        "vendor": vendor,
                        "renderer": renderer,
                        "webgpu": webgpu,
                    }
                )
                registered += 1

    for anchor in anchors:
        anchor["members"].sort(key=lambda m: (m["vendor"], m["renderer"], m["label"]))

    return registered


def check_conditioning(axes: list[dict]) -> None:
    """Every conditioned key must name an option its parent axis can resolve.

    `FindOptionSet` matching is exact and total, and a miss is `LOG(FATAL)`. So a
    key value that no parent option produces is dead data, and a parent option
    with no matching set is a crash waiting for the seed that draws it. Both are
    build failures.

    Only parents that are themselves dispersion axes can be checked here:
    `platform`, `anchor` and `languages` are resolved by the compositor from the
    host, the corpus and the locale policy.
    """
    option_ids = {
        axis["axis"]: {
            option["id"]
            for option_set in axis["option_sets"]
            for option in option_set["options"]
        }
        for axis in axes
    }

    for axis in axes:
        for parent in axis["conditioned_on"]:
            if parent not in option_ids:
                continue
            used = {
                option_set["key"][parent] for option_set in axis["option_sets"]
            }
            unknown = sorted(used - option_ids[parent])
            if unknown:
                raise GeneratorError(
                    f"axis {axis['axis']!r} is conditioned on {parent!r} and has option "
                    f"sets keyed on {unknown}, which {parent!r} cannot resolve"
                )
            missing = sorted(option_ids[parent] - used)
            if missing:
                raise GeneratorError(
                    f"axis {axis['axis']!r} is conditioned on {parent!r} but has no option "
                    f"set for {missing}. Matching is exact and total, so the compositor "
                    "would abort on any seed that draws one of those"
                )


# --------------------------------------------------------------------------
# Code generation
# --------------------------------------------------------------------------

HEADER_PREAMBLE = """// Copyright 2026 The Apostate Authors
// Use of this source code is governed by a GPL-3.0 license that can be
// found in the LICENSE file.
//
// GENERATED FILE -- DO NOT EDIT.
// Produced by scripts/generate-dispersion-tables.py from
// resources/profiles/dispersion/*.json and corpus/anchors/*.json.
"""


def emit_header(meta: dict) -> str:
    out = [HEADER_PREAMBLE]
    out.append("")
    out.append("#ifndef BASE_APOSTATE_DISPERSION_TABLES_H_")
    out.append("#define BASE_APOSTATE_DISPERSION_TABLES_H_")
    out.append("")
    out.append("#include <string_view>")
    out.append("")
    out.append('#include "base/apostate/dispersion_table.h"')
    out.append('#include "base/base_export.h"')
    out.append('#include "base/containers/span.h"')
    out.append("")
    out.append("namespace base::apostate {")
    out.append("")
    out.append("// The three declared identity components of the composition root in")
    out.append("// FINGERPRINTS.md §5, taken from resources/profiles/catalogue.json so the")
    out.append("// binary and the packages that validate its output cannot disagree about")
    out.append("// what the tuple was.")
    out.append(
        f"inline constexpr std::string_view kProfileSchemaVersion = {cpp_string(meta['profile_schema_version'])};"
    )
    out.append(
        f"inline constexpr std::string_view kCatalogueVersion = {cpp_string(meta['catalogue_version'])};"
    )
    out.append("// base/ cannot include base/version_info: that target's public_deps")
    out.append("// contain //base. The pinned build is read from the catalogue and")
    out.append("// cross-checked against build/CHROMIUM_VERSION at generation time.")
    out.append(
        f"inline constexpr std::string_view kChromiumVersion = {cpp_string(meta['chromium_version'])};"
    )
    out.append("")
    out.append("// SHA-256 over every generator input, as lowercase hex.")
    out.append("//")
    out.append("// This is *not* a root component: §5 fixes the tuple at six fields and")
    out.append("// names the declared `catalogue_version` as the catalogue one, which is")
    out.append("// also what the Python and Node packages read when they validate a")
    out.append("// composed profile. §10's requirement that a binary and a profile cannot")
    out.append("// silently disagree about the catalogue is met by reporting this digest")
    out.append("// from --fingerprint-explain, where it is comparable against the tables")
    out.append("// a profile was composed from.")
    out.append(
        f"inline constexpr std::string_view kCatalogueDigest = {cpp_string(meta['catalogue_digest'])};"
    )
    out.append("")
    out.append("// The application locales the voices table has measured option sets")
    out.append("// for. A named locale that is not one of these projects onto the")
    out.append("// empty-string set before the draw, which keeps option-set matching exact")
    out.append("// and total instead of recovering from a failed match.")
    out.append("BASE_EXPORT span<const std::string_view> LanguageSets();")
    out.append("")
    out.append("// Dotted profile paths whose arrays are unioned rather than replaced when")
    out.append("// two option fragments both carry them. Everything else replaces, so a")
    out.append("// font pack is additive without the compositor knowing what a font is.")
    out.append("BASE_EXPORT span<const std::string_view> UnionArrayPaths();")
    out.append("")
    out.append("// The catalogue's theme policies, id-sorted. Not a dispersion axis --")
    out.append("// theme is not one of the nine -- but drawn by seed from the same root,")
    out.append("// so it is compiled in exactly like the axes.")
    out.append("//")
    out.append("// The catalogue's locale policies are deliberately absent. That surface")
    out.append("// is launch precedence plus GeoIP, then the host, and the compositor")
    out.append("// selects no policy by id, so there is nothing here for it to read.")
    out.append("BASE_EXPORT span<const PolicyOption> ThemePolicies();")
    out.append("")
    out.append("// Every dispersion axis, in the FINGERPRINTS.md §5 resolution order.")
    out.append("BASE_EXPORT span<const DispersionAxis> DispersionAxes();")
    out.append("")
    out.append("// Every GPU anchor admitted from corpus/anchors/, sorted by id.")
    out.append("BASE_EXPORT span<const Anchor> Anchors();")
    out.append("")
    out.append("}  // namespace base::apostate")
    out.append("")
    out.append("#endif  // BASE_APOSTATE_DISPERSION_TABLES_H_")
    out.append("")
    return "\n".join(out)


def emit_string_array(out: list[str], name: str, values: list[str]) -> str | None:
    if not values:
        return None
    out.append(f"constexpr std::string_view {name}[] = {{")
    for value in values:
        out.append(f"    {cpp_literal_block(value, '    ')},")
    out.append("};")
    out.append("")
    return name


def emit_axis(out: list[str], axis: dict) -> str:
    axis_tag = identifier(axis["axis"])
    conditioned_name = emit_string_array(
        out, f"k{axis_tag}ConditionedOn", list(axis["conditioned_on"])
    )

    set_names = []
    for set_index, option_set in enumerate(axis["option_sets"]):
        set_tag = f"k{axis_tag}Set{set_index}"

        key_name = None
        if option_set["key"]:
            key_name = f"{set_tag}Key"
            out.append(f"constexpr ConditionKey {key_name}[] = {{")
            # Emitted in `conditioned_on` order: that is the key's canonical
            # order for diagnostics, and it makes the generated file's byte
            # stability independent of JSON object order.
            for name in axis["conditioned_on"]:
                out.append(f"    {{{cpp_string(name)}, {cpp_string(option_set['key'][name])}}},")
            out.append("};")
            out.append("")

        requirement_names: list[str | None] = []
        for option in option_set["options"]:
            requires = option["requires"]
            if requires is None:
                requirement_names.append(None)
                continue
            req_tag = f"{set_tag}{identifier(option['id'])}Requires"
            families_name = emit_string_array(
                out, f"{req_tag}Families", list(requires.get("families", []))
            )
            out.append(f"constexpr Requirement {req_tag} = {{")
            out.append(f"    /*min_logical_cores=*/{requires.get('min_logical_cores', 0)},")
            out.append(f"    /*min_total_bytes=*/{requires.get('min_total_bytes', 0)}u,")
            out.append(f"    /*min_width=*/{requires.get('min_width', 0)},")
            out.append(f"    /*min_height=*/{requires.get('min_height', 0)},")
            out.append(f"    /*anchor=*/{cpp_string(requires.get('anchor', ''))},")
            if families_name:
                out.append(f"    /*families=*/span<const std::string_view>({families_name}),")
            else:
                out.append("    /*families=*/{},")
            out.append("};")
            out.append("")
            requirement_names.append(req_tag)

        options_name = f"{set_tag}Options"
        out.append(f"constexpr DispersionOption {options_name}[] = {{")
        for option, requirement_name in zip(option_set["options"], requirement_names):
            out.append("    {")
            out.append(f"        /*id=*/{cpp_string(option['id'])},")
            out.append(f"        /*weight=*/{option['weight']}u,")
            out.append(f"        /*evidence=*/{cpp_string(option['evidence'])},")
            out.append(f"        /*source=*/{cpp_literal_block(option['source'], '            ')},")
            pack = PACK_KINDS.get(option["pack_kind"], "kNotAPack")
            out.append(f"        /*pack_kind=*/PackKind::{pack},")
            out.append(
                f"        /*requirement=*/{('&' + requirement_name) if requirement_name else 'nullptr'},"
            )
            out.append("        /*value_json=*/")
            out.append(f"            {cpp_literal_block(canonical_json(option['value']), '            ')},")
            out.append("    },")
        out.append("};")
        out.append("")

        set_names.append((set_tag, key_name, options_name))

    sets_name = f"k{axis_tag}Sets"
    out.append(f"constexpr DispersionOptionSet {sets_name}[] = {{")
    for _set_tag, key_name, options_name in set_names:
        key_expr = f"span<const ConditionKey>({key_name})" if key_name else "{}"
        out.append(f"    {{{key_expr}, span<const DispersionOption>({options_name})}},")
    out.append("};")
    out.append("")

    entry = [
        f"        /*axis=*/{cpp_string(axis['axis'])},",
        f"        /*label=*/{cpp_string(axis['label'])},",
        (
            f"        /*conditioned_on=*/span<const std::string_view>({conditioned_name}),"
            if conditioned_name
            else "        /*conditioned_on=*/{},"
        ),
        f"        /*selection=*/Selection::{SELECTIONS[axis['selection']]},",
        f"        /*servability=*/Servability::{SERVABILITIES[axis['servability']]},",
        f"        /*option_sets=*/span<const DispersionOptionSet>({sets_name}),",
    ]
    return "\n".join(["    {", *entry, "    },"])


def emit_anchor(out: list[str], anchor: dict) -> str:
    tag = f"kAnchor{identifier(anchor['id'])}"
    members_name = f"{tag}Members"
    out.append(f"constexpr AnchorMember {members_name}[] = {{")
    for member in anchor["members"]:
        out.append("    {")
        out.append(f"        /*label=*/{cpp_string(member['label'])},")
        out.append(f"        /*unmasked_vendor=*/{cpp_literal_block(member['vendor'], '            ')},")
        out.append(
            f"        /*unmasked_renderer=*/{cpp_literal_block(member['renderer'], '            ')},"
        )
        # Empty when this member measured no adapter. WebGPU then stays
        # host-inherited rather than borrowing a sibling's.
        out.append("        /*webgpu_json=*/")
        out.append(
            f"            {cpp_literal_block(canonical_json(member['webgpu']) if member['webgpu'] else '', '            ')},"
        )
        out.append("    },")
    out.append("};")
    out.append("")

    value_name = f"{tag}Value"
    out.append(f"constexpr std::string_view {value_name} =")
    out.append(f"    {cpp_literal_block(canonical_json(anchor['value']), '    ')};")
    out.append("")

    entry = [
        f"        /*id=*/{cpp_string(anchor['id'])},",
        f"        /*platform=*/{cpp_string(anchor['platform'])},",
        f"        /*backend=*/{cpp_string(anchor['backend'])},",
        f"        /*vendor=*/{cpp_string(anchor['vendor'])},",
        f"        /*evidence=*/{cpp_string(anchor['evidence'])},",
        f"        /*webgl1_caps_sha256=*/{cpp_string(anchor['webgl1_caps'])},",
        f"        /*webgl2_caps_sha256=*/{cpp_string(anchor['webgl2_caps'])},",
        f"        /*pixels_sha256=*/{cpp_string(anchor['pixels'])},",
        f"        /*weight=*/{anchor['weight']}u,",
        f"        /*members=*/span<const AnchorMember>({members_name}),",
        f"        /*value_json=*/{value_name},",
    ]
    return "\n".join(["    {", *entry, "    },"])


def emit_source(axes: list[dict], anchors: list[dict], meta: dict) -> str:
    out = [HEADER_PREAMBLE, ""]
    out.append('#include "base/apostate/dispersion_tables.h"')
    out.append("")
    out.append("#include <string_view>")
    out.append("")
    out.append('#include "base/apostate/dispersion_table.h"')
    out.append('#include "base/containers/span.h"')
    out.append("")
    out.append("namespace base::apostate {")
    out.append("namespace {")
    out.append("")

    emit_string_array(out, "kLanguageSets", list(meta["language_sets"]))
    emit_string_array(out, "kUnionArrayPaths", list(meta["union_array_paths"]))

    # `theme` only. The catalogue's locale policies are not compiled in: the
    # compositor resolves that surface from launch precedence and the host and
    # selects no policy by id, so a compiled table would be data the build
    # carries and nothing reads. Their caller is scripts/profile_resolver.py's
    # --locale-policy, which reads the catalogue directly.
    for kind in ("theme",):
        name = f"k{kind.capitalize()}Policies"
        out.append(f"constexpr PolicyOption {name}[] = {{")
        for policy in meta[f"{kind}_policies"]:
            out.append("    {")
            out.append(f"        /*id=*/{cpp_string(policy['id'])},")
            out.append(f"        /*platform=*/{cpp_string(policy['platform'])},")
            out.append(f"        /*evidence=*/{cpp_string(policy['evidence'])},")
            out.append("        /*value_json=*/")
            out.append(
                f"            {cpp_literal_block(canonical_json(policy['value']), '            ')},"
            )
            out.append("    },")
        out.append("};")
        out.append("")

    axis_entries = [emit_axis(out, axis) for axis in axes]
    anchor_entries = [emit_anchor(out, anchor) for anchor in anchors]

    out.append("constexpr DispersionAxis kAxes[] = {")
    out.extend(axis_entries)
    out.append("};")
    out.append("")

    if anchors:
        out.append("constexpr Anchor kAnchors[] = {")
        out.extend(anchor_entries)
        out.append("};")
        out.append("")

    out.append("}  // namespace")
    out.append("")
    out.append("span<const std::string_view> LanguageSets() {")
    if meta["language_sets"]:
        out.append("  return span<const std::string_view>(kLanguageSets);")
    else:
        out.append("  return {};")
    out.append("}")
    out.append("")
    out.append("span<const std::string_view> UnionArrayPaths() {")
    if meta["union_array_paths"]:
        out.append("  return span<const std::string_view>(kUnionArrayPaths);")
    else:
        out.append("  return {};")
    out.append("}")
    out.append("")
    out.append("span<const PolicyOption> ThemePolicies() {")
    out.append("  return span<const PolicyOption>(kThemePolicies);")
    out.append("}")
    out.append("")
    out.append("span<const DispersionAxis> DispersionAxes() {")
    out.append("  return span<const DispersionAxis>(kAxes);")
    out.append("}")
    out.append("")
    out.append("span<const Anchor> Anchors() {")
    if anchors:
        out.append("  return span<const Anchor>(kAnchors);")
    else:
        out.append("  return {};")
    out.append("}")
    out.append("")
    out.append("}  // namespace base::apostate")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def catalogue_digest(inputs: list[tuple[str, bytes]]) -> str:
    """SHA-256 over a canonical manifest of every input.

    Domain-separated and length-prefixed by construction (NUL-delimited fields
    over paths that cannot contain NUL), and sorted by path, so the digest is a
    function of the input set and nothing else -- not of filesystem order, not
    of the order arguments were passed.
    """
    digest = hashlib.sha256()
    digest.update(b"apostate/catalogue/")
    digest.update(GENERATED_SCHEMA_VERSION.encode("utf-8"))
    digest.update(b"\x00")
    for relative_path, payload in sorted(inputs):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def collect(directory: Path, suffix: str = ".json") -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == suffix)


def parse_policies(path: Path, raw: dict, kind: str) -> list[dict]:
    """The catalogue's locale and theme policies, validated and id-sorted.

    Neither is a dispersion axis. `theme` is drawn by seed from the same root
    as the axes, so it is compiled in exactly like them. `locale` is launch
    precedence plus GeoIP, then the host, and nothing in the binary selects one
    by id, so it is validated here and compiled in nowhere -- its caller is
    scripts/profile_resolver.py's --locale-policy, which reads the catalogue.

    Sorted by id because selection is over the id-sorted candidate list.
    """
    policies = raw.get("policies")
    if not isinstance(policies, dict):
        die(path, "missing `policies`")
    entries = policies.get(kind)
    if not isinstance(entries, list) or not entries:
        die(path, f"`policies.{kind}` must be a non-empty array")

    parsed = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            die(path, f"policies.{kind}[{index}] must be an object")
        policy_id = require_str(path, entry, "id")
        if not ID_RE.match(policy_id):
            die(path, f"policies.{kind}: id {policy_id!r} does not match {ID_RE.pattern}")
        if policy_id in seen:
            die(path, f"policies.{kind}: duplicate id {policy_id!r}")
        seen.add(policy_id)
        platform = require_str(path, entry, "platform")
        if platform != "all" and platform not in PLATFORM_MAP:
            die(path, f"policies.{kind}: platform {platform!r} is not 'all' or a known platform")
        evidence = require_str(path, entry, "evidence_class")
        if evidence not in EVIDENCE_CLASSES:
            die(path, f"policies.{kind}: evidence_class {evidence!r} is not a contract class")
        value = entry.get("value")
        if not isinstance(value, dict):
            die(path, f"policies.{kind}[{index}]: `value` must be a profile-fragment object")
        parsed.append(
            {
                "id": policy_id,
                "platform": platform,
                "evidence": evidence,
                "value": value,
            }
        )
    parsed.sort(key=lambda policy: policy["id"])
    return parsed


def parse_catalogue(path: Path, pinned_version: str) -> dict:
    """The declared identity components and the catalogue-wide policy inputs.

    These are read from resources/profiles/catalogue.json rather than restated
    here because the Python and Node packages read the same file when they
    validate a composed profile. Restating them would recreate exactly the
    divergence this work exists to remove.
    """
    raw = load_json(path)
    if not isinstance(raw, dict):
        die(path, "top level must be an object")

    for key in ("profile_schema_version", "catalogue_version"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            die(path, f"{key} must be a non-negative integer")

    browser_build = require_str(path, raw, "browser_build")
    if browser_build != pinned_version:
        # A binary built from one Chromium and a catalogue describing another
        # produce a profile whose version-bearing fields contradict the
        # binary's own. That is a build defect, and it is cheap to catch here.
        die(
            path,
            f"browser_build {browser_build!r} does not match the pinned "
            f"Chromium version {pinned_version!r}",
        )

    language_sets = raw.get("language_sets")
    if not isinstance(language_sets, list) or not all(
        isinstance(item, str) for item in language_sets
    ):
        die(path, "`language_sets` must be a list of strings")
    if "" not in language_sets:
        die(
            path,
            "`language_sets` must contain the empty-string key: it is what "
            "every unmeasured locale projects onto, and without "
            "it option-set matching stops being total",
        )

    # The catalogue's locale policies are still parsed and validated below --
    # scripts/profile_resolver.py's --locale-policy selects one by id, so a
    # malformed one is still a catalogue defect worth failing the build over --
    # but no default language list is derived from them any more. There is no
    # such thing as the launch's default list: a launch that names none is
    # served the host's, and the voices table is keyed on the empty projection
    # rather than on whichever policy happened to be first.

    return {
        "profile_schema_version": str(raw["profile_schema_version"]),
        "catalogue_version": str(raw["catalogue_version"]),
        "chromium_version": browser_build,
        "language_sets": sorted(set(language_sets)),
        "union_array_paths": list(UNION_ARRAY_PATHS),
        "locale_policies": parse_policies(path, raw, "locale"),
        "theme_policies": parse_policies(path, raw, "theme"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--dispersion-dir", type=Path)
    parser.add_argument("--anchors-dir", type=Path)
    parser.add_argument("--chromium-version-file", type=Path)
    parser.add_argument("--out-cc", required=True, type=Path)
    parser.add_argument("--out-h", required=True, type=Path)
    parser.add_argument("--depfile", type=Path)
    parser.add_argument("--catalogue", type=Path)
    args = parser.parse_args(argv)

    data_root: Path = args.data_root
    dispersion_dir: Path = args.dispersion_dir or data_root / "resources/profiles/dispersion"
    anchors_dir: Path = args.anchors_dir or data_root / "corpus/anchors"
    version_file: Path = args.chromium_version_file or data_root / "build/CHROMIUM_VERSION"
    catalogue_file: Path = args.catalogue or data_root / "resources/profiles/catalogue.json"

    try:
        if not version_file.is_file():
            raise GeneratorError(f"{version_file}: missing pinned Chromium version")
        pinned_version = version_file.read_text(encoding="utf-8").strip()
        if not pinned_version:
            raise GeneratorError(f"{version_file}: pinned Chromium version is empty")

        meta = parse_catalogue(catalogue_file, pinned_version)

        inputs: list[tuple[str, bytes]] = [
            ("build/CHROMIUM_VERSION", version_file.read_bytes()),
            ("catalogue.json", catalogue_file.read_bytes()),
        ]
        read_paths = [version_file, catalogue_file]

        axes = []
        for path in collect(dispersion_dir):
            axes.append(parse_axis(path))
            inputs.append((f"dispersion/{path.name}", path.read_bytes()))
            read_paths.append(path)
        if not axes:
            raise GeneratorError(
                f"{dispersion_dir}: no dispersion tables found. The compositor "
                "cannot draw from an empty catalogue"
            )

        # The digest above is taken from the file bytes as authored, so it stays
        # a digest of the inputs; build/CHROMIUM_VERSION is already one of them,
        # which is what makes a version bump a new catalogue version. What the
        # compiled table carries is the stamped string.
        resolver, resolver_path = load_build_stamp(data_root)
        read_paths.append(resolver_path)
        stamp_user_agents(axes, resolver.user_agent_for_build, pinned_version)

        anchors = []
        for path in collect(anchors_dir):
            read_paths.append(path)
            inputs.append((f"anchors/{path.name}", path.read_bytes()))
            anchor = parse_anchor(path, resolver.gl_limit_entries)
            if anchor is not None:
                anchors.append(anchor)
        anchors.sort(key=lambda a: a["id"])
        if len({a["id"] for a in anchors}) != len(anchors):
            raise GeneratorError(f"{anchors_dir}: duplicate anchor_id")

        # Axis order in the generated table is the §5 resolution order, and an
        # axis the order does not name is a table the compositor cannot place.
        #
        # `machine_class` sits directly after `gpu_identity` because it turns one
        # chip into one machine, and `cpu`, `memory`, `panel`, `media_topology`
        # and `battery` all hang off that decision. `audio` follows
        # `media_topology` because it describes the same claimed machine's audio
        # output, but it is conditioned on the platform rather than the machine
        # class: the buffer size comes from the AudioManager the claimed OS runs,
        # and the corpus shows no variation within a platform. `network` and
        # `extensions` are unconditioned and only have to land before the
        # profile is serialised; `extensions` is last of the axes because
        # nothing is conditioned on it either, and it describes what the
        # machine's owner installed rather than what the machine is.
        order = {
            name: index
            for index, name in enumerate(
                [
                    "os_release",
                    "anchor",
                    "gpu_identity",
                    "machine_class",
                    "cpu",
                    "memory",
                    "panel",
                    "furniture",
                    "font_packs",
                    "media_topology",
                    "audio",
                    "network",
                    "battery",
                    "voices",
                    "extensions",
                    "locale",
                ]
            )
        }
        unknown = sorted(axis["axis"] for axis in axes if axis["axis"] not in order)
        if unknown:
            raise GeneratorError(
                f"{dispersion_dir}: axes {unknown} are not in the FINGERPRINTS.md "
                "§5 resolution order; the compositor has nowhere to resolve them"
            )
        labels = [axis["label"] for axis in axes]
        if len(set(labels)) != len(labels):
            raise GeneratorError(
                f"{dispersion_dir}: a draw label is reused across axes, which "
                "would couple two axes to the same substream"
            )
        axes.sort(key=lambda axis: order[axis["axis"]])

        registered = register_identities(axes, anchors)
        check_conditioning(axes)

        meta["catalogue_digest"] = catalogue_digest(inputs)
        header = emit_header(meta)
        source = emit_source(axes, anchors, meta)
    except GeneratorError as exc:
        print(f"generate-dispersion-tables: {exc}", file=sys.stderr)
        return 1

    args.out_h.parent.mkdir(parents=True, exist_ok=True)
    args.out_cc.parent.mkdir(parents=True, exist_ok=True)
    args.out_h.write_text(header, encoding="utf-8")
    args.out_cc.write_text(source, encoding="utf-8")

    if args.depfile:
        # The set of table files is discovered at build time, so ninja learns
        # the real input list from a depfile rather than from GN's static
        # `inputs`. Adding a table therefore re-runs the action without a
        # `gn gen`.
        args.depfile.parent.mkdir(parents=True, exist_ok=True)
        deps = " ".join(str(path).replace(" ", "\\ ") for path in sorted(set(read_paths)))
        args.depfile.write_text(f"{args.out_cc}: {deps}\n", encoding="utf-8")

    measured = sum(
        1 for anchor in anchors for member in anchor["members"] if member["measured"]
    )
    print(
        f"generate-dispersion-tables: {len(axes)} axes, {len(anchors)} anchors, "
        f"{measured} measured members, {registered} registered identities, "
        f"catalogue v{meta['catalogue_version']} schema v{meta['profile_schema_version']} "
        f"digest {meta['catalogue_digest'][:12]}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
