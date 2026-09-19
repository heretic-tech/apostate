#!/usr/bin/env python3
"""Deterministic, dependency-free anchor-and-dispersion profile compositor.

Catalogue version 2 replaced the fourteen-family catalogue with a composition
model: invariants belong to the build, an anchor is a measured GPU capability
cluster taken atomically, and every other varying surface is drawn by seed from
an enumerated option table under ``resources/profiles/dispersion/``.
``docs/FINGERPRINTS.md`` is the model and this module is its reference
implementation.

The browser process owns the production compositor; this module exists so the
tables can be validated, listed and resolved outside a build, and so the C++
implementation has golden vectors to agree with.  It is not a random-number
generator: every choice is an indexed SHA-256 draw over explicit inputs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOGUE = REPO_ROOT / "resources" / "profiles" / "catalogue.json"
DEFAULT_PROFILE_SCHEMA = REPO_ROOT / "config" / "profile.schema.json"
CATALOGUE_VERSION = 2
PROFILE_SCHEMA_VERSION = 3
SEED_DOMAIN = "apostate/fp/v1"
DISPERSION_SCHEMA = "apostate/dispersion/1"
SUPPORTED_EVIDENCE = {
    "physical-ground-truth",
    "compatibility-capture",
    "catalogue-value",
    "native-derived",
    "proxy-derived",
    "host-inherited",
}
PLATFORMS = {"macos", "windows", "linux"}
_PLATFORM_ALIASES = {
    "mac": "macos",
    "macos": "macos",
    "osx": "macos",
    "darwin": "macos",
    "win": "windows",
    "win32": "windows",
    "windows": "windows",
    "linux": "linux",
}
AXES = (
    "os_release",
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
)
SELECTIONS = {"single", "core-plus-subset"}
SERVABILITY = {"none", "clamp-down", "window-bounds", "anchor-member", "files-present"}
_REQUIRES_KEYS = {
    "min_logical_cores",
    "min_total_bytes",
    "min_width",
    "min_height",
    "anchor",
    "families",
}
_POLICY_KINDS = ("locale", "theme")
_PROFILE_TOP_LEVEL = {
    "id",
    "source_capture",
    "cpu",
    "memory",
    "platform",
    "browser",
    "speech",
    "screen",
    "window",
    "gl_limits",
    "gl_extensions",
    "gl_precisions",
    "webgpu",
    "gpu",
    "locale",
    "theme",
    "input",
    "audio",
    "media",
    "keyboard",
    "fonts",
    # navigator.connection and navigator.getBattery, served by patches 0088 and
    # 0089. config/profile.schema.json defines both.
    "network",
    "battery",
}
_UNION_PATHS = frozenset({("fonts", "enumeration_allowlist")})
_BUILD_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)$")
_CHROME_UA_RE = re.compile(r"(?:Chrome|Chromium)/(\d+)(?:\.|\s|$)")
# The reduced UA's browser-version token. Chrome's UA reduction renders the
# version as MAJOR.0.0.0 and freezes everything else per platform, so the major
# is the only part of a captured UA string that may legitimately differ from the
# build serving it. `\b` before the product name is load-bearing: it refuses to
# match inside "HeadlessChrome/...", which is a product token patch 0002 removes
# at the emitter and which no table may carry.
_UA_PRODUCT_RE = re.compile(r"\b(Chrome|Chromium)/(\d+)\.0\.0\.0\b")
# The OS token Chrome's reduced UA carries for each platform persona. Frozen per
# platform by the reduction, which is why one string covers every release: every
# Windows capture in resources/fingerprints/raw carries "Windows NT 10.0"
# whatever the real build, every macOS one "Intel Mac OS X 10_15_7" whatever the
# real version. Keys are exactly PLATFORMS, and _normalise_platform admits
# nothing else.
_UA_OS_TOKENS = {
    "windows": "Windows NT 10.0",
    "macos": "Macintosh; Intel Mac OS X 10_15_7",
    "linux": "X11; Linux x86_64",
}
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_TIMEZONE_RE = re.compile(r"^(?:UTC|[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*)$")
_ID_RE = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")
_ANCHOR_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_U64 = 1 << 64


class ResolverError(ValueError):
    """Raised when catalogue or launch inputs cannot produce a coherent profile."""


class ProfileValidationError(ResolverError):
    """Raised when a runtime profile does not satisfy the repository schema."""


# --------------------------------------------------------------------------
# JSON Schema subset
# --------------------------------------------------------------------------
def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _schema_path(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path != "$" else f"$.{key}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _schema_errors(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Small JSON-Schema 2020-12 subset sufficient for profile.schema.json.

    Keeping this validator in the resolver avoids making a launch-time
    dependency on jsonschema.  It implements every assertion keyword used by
    the checked-in profile schema, including contains/minContains and dynamic
    additionalProperties.
    """
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, item) for item in choices):
            errors.append(f"{path}: expected {expected}")
            return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}")

    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}: missing required property {required!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors.extend(_schema_errors(item, properties[key], _schema_path(path, key)))
            elif additional is False:
                errors.append(f"{_schema_path(path, key)}: additional property is not allowed")
            elif isinstance(additional, dict):
                errors.extend(_schema_errors(item, additional, _schema_path(path, key)))
        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for key in value:
                errors.extend(_schema_errors(key, property_names, f"{path} property name {key!r}"))

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, _schema_path(path, index)))
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for index, item in enumerate(value):
                encoded = _canonical_json(item)
                if encoded in seen:
                    errors.append(f"{_schema_path(path, index)}: duplicate array item")
                seen.add(encoded)
        contains = schema.get("contains")
        if isinstance(contains, dict):
            matches = sum(not _schema_errors(item, contains, _schema_path(path, i)) for i, item in enumerate(value))
            minimum = schema.get("minContains", 1)
            maximum = schema.get("maxContains")
            if matches < minimum:
                errors.append(f"{path}: contains matched {matches}, minimum is {minimum}")
            if maximum is not None and matches > maximum:
                errors.append(f"{path}: contains matched {matches}, maximum is {maximum}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                errors.append(f"{path}: invalid schema pattern: {exc}")
            else:
                if matched is None:
                    errors.append(f"{path}: does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be greater than {schema['exclusiveMinimum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
    return errors


def _load_profile_schema(path: Path = DEFAULT_PROFILE_SCHEMA) -> Mapping[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResolverError(f"cannot read profile schema {path}: {exc}") from exc
    if not isinstance(schema, dict):
        raise ResolverError(f"profile schema {path} is not an object")
    return schema


def validate_profile(profile: Mapping[str, Any], schema_path: Path = DEFAULT_PROFILE_SCHEMA) -> bool:
    """Validate a native runtime profile against the checked-in profile schema."""
    if not isinstance(profile, dict):
        raise ProfileValidationError("profile must be a JSON object")
    errors = _schema_errors(profile, _load_profile_schema(schema_path))
    if errors:
        raise ProfileValidationError("; ".join(errors[:12]))
    return True


def _read_object(path: Path, what: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResolverError(f"cannot read {what} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResolverError(f"{what} {path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ResolverError(f"{what} {path} must contain a JSON object")
    return value


# --------------------------------------------------------------------------
# Seed derivation (docs/FINGERPRINTS.md section 5)
# --------------------------------------------------------------------------
def seed_root(seed: str, platform: str, browser_build: str,
              catalogue_version: int = CATALOGUE_VERSION,
              profile_schema_version: int = PROFILE_SCHEMA_VERSION) -> bytes:
    """The root of every draw substream. Changing any component is a new identity."""
    material = b"\x00".join(part.encode("utf-8") for part in (
        SEED_DOMAIN,
        str(profile_schema_version),
        str(catalogue_version),
        browser_build,
        platform,
        seed,
    ))
    return hashlib.sha256(material).digest()


def draw(root: bytes, label: str, index: int) -> int:
    """High 64 bits of the label's substream at ``index``, as an unsigned int."""
    if index < 0 or index >= 1 << 32:
        raise ResolverError(f"draw index {index} out of range")
    material = root + b"\x00" + label.encode("utf-8") + b"\x00" + index.to_bytes(4, "big")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def weighted_pick(root: bytes, label: str, options: Sequence[Mapping[str, Any]]) -> int:
    """Cumulative-weight index. No modulo, no rejection, no floating point."""
    # coh: coh.per-seed-entropy-across-axes - every draw is a pure function of the
    # seed root, so one seed is constant across launches and two seeds are
    # independent. There is no clock, no PID and no process state in this path.
    if not options:
        raise ResolverError(f"axis {label} has no servable options")
    total = sum(int(option["weight"]) for option in options)
    if total <= 0:
        raise ResolverError(f"axis {label} has no positive total weight")
    target = (draw(root, label, 0) * total) >> 64
    cumulative = 0
    for index, option in enumerate(options):
        cumulative += int(option["weight"])
        if target < cumulative:
            return index
    return len(options) - 1


def subset_included(root: bytes, label: str, optional_index: int, weight: int) -> bool:
    """Independent inclusion for optional pack ``optional_index``."""
    return ((draw(root, label, optional_index + 1) * 100) >> 64) < int(weight)


# --------------------------------------------------------------------------
# The one build-stamping rule, shared with the table generator
# --------------------------------------------------------------------------
def user_agent_for_build(user_agent: str, browser_build: str) -> str:
    """A captured UA string re-stamped with the major of ``browser_build``.

    ``resources/profiles/dispersion/os_release.json`` stores each platform's UA
    exactly as a physical device sent it, because the OS token, the AppleWebKit
    version and the trailing Safari token vary together and only a capture
    records them together -- the argument ``config/profile.schema.json`` makes
    for the field and patch 0009 makes for replacing the string whole.

    The browser version inside it is not the profile's to choose.
    ``resources/profiles/catalogue.json`` names ``chromium_version`` a build
    invariant a profile never varies, and the schema for ``browser.user_agent``
    says outright that its version must match the binary because Apostate does
    not spoof its own version. So the major is substituted here from the build
    that will actually serve the string. That is what stops a Chromium bump from
    shipping the major of whatever device the capture came off: the two real
    Windows captures in ``resources/fingerprints/raw`` differ in nothing but
    this token, 152 on one box and 153 on the other.

    ``scripts/generate-dispersion-tables.py`` calls this when it compiles the
    tables into the binary, so the C++ compositor merges a string that already
    carries the pinned build's major and needs no substitution of its own.
    """
    build = _BUILD_RE.fullmatch(browser_build)
    if build is None:
        raise ResolverError(
            f"browser_build {browser_build!r} is not a Chromium four-component version, "
            "so there is no major to stamp into a user agent"
        )
    found = _UA_PRODUCT_RE.findall(user_agent)
    if len(found) != 1:
        raise ResolverError(
            f"user agent {user_agent!r} carries {len(found)} reduced Chrome version "
            "token(s), expected exactly one of the form Chrome/MAJOR.0.0.0: a string "
            "without one is not a reduced Chrome UA, and one with two would leave a "
            "stale major behind wherever this rule did not reach"
        )
    return _UA_PRODUCT_RE.sub(r"\g<1>/" + build.group(1) + ".0.0.0", user_agent)

# --------------------------------------------------------------------------
# Catalogue and dispersion tables
# --------------------------------------------------------------------------
def _normalise_platform(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResolverError("fingerprint_platform must be a non-empty string")
    platform = _PLATFORM_ALIASES.get(value.strip().lower())
    if platform is None:
        raise ResolverError(f"unsupported fingerprint platform {value!r}")
    return platform


def _validate_policy_entry(kind: str, entry: Any, index: int) -> None:
    path = f"policies.{kind}[{index}]"
    if not isinstance(entry, dict):
        raise ResolverError(f"{path} must be an object")
    if not isinstance(entry.get("id"), str) or not _ID_RE.fullmatch(entry["id"]):
        raise ResolverError(f"{path}.id is invalid")
    platform = entry.get("platform")
    if platform != "all":
        _normalise_platform(platform)
    if entry.get("evidence_class") not in SUPPORTED_EVIDENCE:
        raise ResolverError(f"{path}.evidence_class is invalid")
    value = entry.get("value")
    if not isinstance(value, dict):
        raise ResolverError(f"{path}.value must be an object")
    try:
        validate_profile(value)
    except ProfileValidationError as exc:
        raise ResolverError(f"{path}.value is not schema-valid: {exc}") from exc


def _validate_catalogue_index(catalogue: Mapping[str, Any]) -> None:
    if catalogue.get("catalogue_id") != "apostate":
        raise ResolverError("catalogue_id must be 'apostate'")
    if catalogue.get("catalogue_version") != CATALOGUE_VERSION or catalogue.get("version") != CATALOGUE_VERSION:
        raise ResolverError("unsupported catalogue version")
    if catalogue.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
        raise ResolverError("unsupported profile schema version")
    if catalogue.get("model") != "anchors+dispersion":
        raise ResolverError("catalogue model must be 'anchors+dispersion'")
    for retired in ("families", "family_count", "distributions", "compatibility_acceptance"):
        if retired in catalogue:
            raise ResolverError(f"catalogue still carries retired key {retired!r}")
    build = catalogue.get("browser_build")
    if not isinstance(build, str) or _BUILD_RE.fullmatch(build) is None:
        raise ResolverError("catalogue browser_build is missing or invalid")
    if catalogue.get("platforms") != sorted(PLATFORMS):
        raise ResolverError("catalogue platforms must list every supported persona")
    language_sets = catalogue.get("language_sets")
    if not isinstance(language_sets, list) or "" not in language_sets:
        raise ResolverError("catalogue language_sets must include the empty projection key")

    anchors = catalogue.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise ResolverError("catalogue anchors must be a non-empty array")
    seen: set[str] = set()
    for index, anchor in enumerate(anchors):
        path = f"anchors[{index}]"
        if not isinstance(anchor, dict):
            raise ResolverError(f"{path} must be an object")
        anchor_id = anchor.get("id")
        if not isinstance(anchor_id, str) or _ANCHOR_ID_RE.fullmatch(anchor_id) is None:
            raise ResolverError(f"{path}.id is invalid")
        if anchor_id in seen:
            raise ResolverError(f"duplicate anchor id {anchor_id!r}")
        seen.add(anchor_id)
        _normalise_platform(anchor.get("platform"))
        if not isinstance(anchor.get("backend"), str) or not anchor["backend"]:
            raise ResolverError(f"{path}.backend is missing")
        if anchor.get("evidence_class") not in SUPPORTED_EVIDENCE:
            raise ResolverError(f"{path}.evidence_class is invalid")
        if anchor.get("rotation_status") not in {"measured-safe", "single-member"}:
            raise ResolverError(f"{path}.rotation_status is invalid")
        members = anchor.get("members")
        if not isinstance(members, list) or len(members) != anchor.get("member_count"):
            raise ResolverError(f"{path}.members does not match member_count")
        if anchor["rotation_status"] == "single-member" and len(members) != 1:
            raise ResolverError(f"{path} claims single-member with {len(members)} members")
        requirements = anchor.get("host_requirements")
        if not isinstance(requirements, dict) or requirements.get("platform") != anchor["platform"] \
                or requirements.get("backend") != anchor["backend"]:
            raise ResolverError(f"{path}.host_requirements disagrees with the anchor")

    axes = catalogue.get("axes")
    if not isinstance(axes, list) or [entry.get("axis") for entry in axes] != list(AXES):
        raise ResolverError("catalogue axes must list every dispersion axis in order")
    policies = catalogue.get("policies")
    if not isinstance(policies, dict) or set(policies) != set(_POLICY_KINDS):
        raise ResolverError("catalogue policies must be exactly locale and theme")
    for kind in _POLICY_KINDS:
        entries = policies[kind]
        if not isinstance(entries, list) or not entries:
            raise ResolverError(f"catalogue policies.{kind} must be non-empty")
        ids: set[str] = set()
        for index, entry in enumerate(entries):
            _validate_policy_entry(kind, entry, index)
            if entry["id"] in ids:
                raise ResolverError(f"duplicate {kind} policy id {entry['id']!r}")
            ids.add(entry["id"])


def _validate_requires(axis: str, option_id: str, requires: Any) -> None:
    if requires is None:
        return
    if not isinstance(requires, dict) or not requires:
        raise ResolverError(f"{axis}/{option_id}.requires must be a non-empty object")
    unknown = set(requires) - _REQUIRES_KEYS
    if unknown:
        raise ResolverError(
            f"{axis}/{option_id}.requires has unrecognised keys {sorted(unknown)}: "
            "silently skipping a precondition would ship an unservable claim"
        )
    for key, value in requires.items():
        if key == "families":
            if not isinstance(value, list) or not value or not all(
                    isinstance(item, str) and item for item in value):
                raise ResolverError(f"{axis}/{option_id}.requires.families must be non-empty strings")
        elif key == "anchor":
            if not isinstance(value, str) or _ANCHOR_ID_RE.fullmatch(value) is None:
                raise ResolverError(f"{axis}/{option_id}.requires.anchor is invalid")
        elif not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ResolverError(f"{axis}/{option_id}.requires.{key} must be a positive integer")


def _validate_axis(axis: str, doc: Mapping[str, Any]) -> None:
    if doc.get("schema") != DISPERSION_SCHEMA:
        raise ResolverError(f"{axis}: schema must be {DISPERSION_SCHEMA!r}")
    if doc.get("axis") != axis or doc.get("label") != axis:
        raise ResolverError(f"{axis}: axis and label must both equal the file stem")
    conditioned_on = doc.get("conditioned_on")
    if not isinstance(conditioned_on, list) or not all(isinstance(p, str) for p in conditioned_on):
        raise ResolverError(f"{axis}: conditioned_on must be a list of parent names")
    selection = doc.get("selection")
    if selection not in SELECTIONS:
        raise ResolverError(f"{axis}: selection must be one of {sorted(SELECTIONS)}")
    if doc.get("servability") not in SERVABILITY:
        raise ResolverError(f"{axis}: servability must be one of {sorted(SERVABILITY)}")
    if not isinstance(doc.get("description"), str) or not doc["description"].strip():
        raise ResolverError(f"{axis}: description is missing")
    option_sets = doc.get("option_sets")
    if not isinstance(option_sets, list) or not option_sets:
        raise ResolverError(f"{axis}: option_sets must be a non-empty array")
    seen_keys: set[str] = set()
    for set_index, option_set in enumerate(option_sets):
        if not isinstance(option_set, dict):
            raise ResolverError(f"{axis}.option_sets[{set_index}] must be an object")
        key = option_set.get("key")
        if not isinstance(key, dict) or sorted(key) != sorted(conditioned_on):
            raise ResolverError(
                f"{axis}.option_sets[{set_index}].key must hold exactly {conditioned_on}"
            )
        encoded = _canonical_json(key)
        if encoded in seen_keys:
            raise ResolverError(f"{axis}: duplicate option_sets key {encoded}")
        seen_keys.add(encoded)
        options = option_set.get("options")
        if not isinstance(options, list) or not options:
            raise ResolverError(f"{axis}.option_sets[{set_index}].options must be non-empty")
        ids: set[str] = set()
        cores = 0
        for option_index, option in enumerate(options):
            label = f"{axis}.option_sets[{set_index}].options[{option_index}]"
            if not isinstance(option, dict):
                raise ResolverError(f"{label} must be an object")
            option_id = option.get("id")
            if not isinstance(option_id, str) or _ID_RE.fullmatch(option_id) is None:
                raise ResolverError(f"{label}.id is invalid")
            if option_id in ids:
                raise ResolverError(f"{axis}: duplicate option id {option_id!r} in {encoded}")
            ids.add(option_id)
            weight = option.get("weight")
            if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
                raise ResolverError(f"{label}.weight must be a positive integer")
            if option.get("evidence") not in SUPPORTED_EVIDENCE:
                raise ResolverError(f"{label}.evidence is invalid")
            pack_kind = option.get("pack_kind")
            if selection == "core-plus-subset":
                if pack_kind not in {"core", "optional"}:
                    raise ResolverError(f"{label}.pack_kind must be core or optional")
                if pack_kind == "optional" and not 1 <= weight <= 100:
                    raise ResolverError(f"{label}.weight must be a percentage for an optional pack")
                cores += pack_kind == "core"
            elif pack_kind is not None:
                raise ResolverError(f"{label}.pack_kind is only valid for core-plus-subset")
            _validate_requires(axis, option_id, option.get("requires"))
            value = option.get("value")
            if not isinstance(value, dict):
                raise ResolverError(f"{label}.value must be an object")
            forbidden = set(value) - _PROFILE_TOP_LEVEL
            if forbidden:
                raise ResolverError(f"{label}.value has non-profile sections {sorted(forbidden)}")
            try:
                validate_profile(value)
            except ProfileValidationError as exc:
                raise ResolverError(f"{label}.value is not schema-valid: {exc}") from exc
        if selection == "core-plus-subset" and cores == 0:
            raise ResolverError(f"{axis}: option set {encoded} has no core pack")
        if axis == "gpu_identity":
            for option in options:
                if (option.get("requires") or {}).get("anchor") != key.get("anchor"):
                    raise ResolverError(
                        f"{axis}: option {option['id']!r} is not required to be a member of "
                        f"anchor {key.get('anchor')!r}"
                    )


def load_dispersion(base_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load and validate every dispersion table, including each option's value."""
    base = Path(base_dir) if base_dir is not None else DEFAULT_CATALOGUE.parent
    root = (base / "dispersion").resolve()
    tables: dict[str, dict[str, Any]] = {}
    for axis in AXES:
        path = root / f"{axis}.json"
        if not path.is_file():
            raise ResolverError(f"dispersion table is missing: {path}")
        doc = _read_object(path, f"dispersion table {axis}")
        _validate_axis(axis, doc)
        tables[axis] = doc
    extra = {p.stem for p in root.glob("*.json")} - set(AXES)
    if extra:
        raise ResolverError(f"unknown dispersion tables present: {sorted(extra)}")
    return tables


def load_anchors(catalogue: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Load every anchor the catalogue indexes and check its digests still agree."""
    anchors: dict[str, dict[str, Any]] = {}
    for entry in catalogue["anchors"]:
        path = (REPO_ROOT / entry["file"]).resolve()
        if not path.is_file():
            raise ResolverError(f"catalogue anchor file is missing: {path}")
        anchor = _read_object(path, f"anchor {entry['id']}")
        if anchor.get("anchor_id") != entry["id"]:
            raise ResolverError(f"anchor file {path} id does not match the catalogue entry")
        for field, expected in entry["digests"].items():
            if anchor.get("digests", {}).get(field) != expected:
                raise ResolverError(f"anchor {entry['id']} digest {field} disagrees with the catalogue")
        members = [member["device"] for member in anchor.get("members", [])]
        if members != entry["members"]:
            raise ResolverError(f"anchor {entry['id']} member list disagrees with the catalogue")
        anchors[entry["id"]] = {"index": dict(entry), "record": anchor}
    return anchors


_CATALOGUE_CACHE: dict[tuple[str, int], tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]] = {}


def _catalogue_stamp(catalogue_path: Path) -> int:
    """Newest mtime across the catalogue, its dispersion tables and the anchors."""
    paths = [catalogue_path, *(catalogue_path.parent / "dispersion").glob("*.json"),
             *(REPO_ROOT / "corpus" / "anchors").glob("*.json")]
    return max((path.stat().st_mtime_ns for path in paths if path.exists()), default=0)


def _load_catalogue(path: Path | str | None = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    catalogue_path = (Path(path) if path is not None else DEFAULT_CATALOGUE).resolve()
    cache_key = (str(catalogue_path), _catalogue_stamp(catalogue_path))
    cached = _CATALOGUE_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    catalogue = _read_object(catalogue_path, "catalogue")
    _validate_catalogue_index(catalogue)
    tables = load_dispersion(catalogue_path.parent)
    for entry in catalogue["axes"]:
        doc = tables[entry["axis"]]
        if entry["selection"] != doc["selection"] or entry["servability"] != doc["servability"] \
                or entry["conditioned_on"] != doc["conditioned_on"]:
            raise ResolverError(f"catalogue axis entry for {entry['axis']} disagrees with its table")
        if entry["option_sets"] != len(doc["option_sets"]) or \
                entry["options"] != sum(len(s["options"]) for s in doc["option_sets"]):
            raise ResolverError(f"catalogue axis counts for {entry['axis']} disagree with its table")
    anchors = load_anchors(catalogue)
    _CATALOGUE_CACHE[cache_key] = (catalogue, tables, anchors)
    return copy.deepcopy((catalogue, tables, anchors))


def load_catalogue(path: Path | str | None = None) -> dict[str, Any]:
    """Load and integrity-check the catalogue index, every axis and every anchor."""
    catalogue, _, _ = _load_catalogue(path)
    return catalogue


def validate_catalogue(path: Path | str | None = None) -> bool:
    """Return True only if the catalogue, all dispersion tables and anchors validate."""
    _load_catalogue(path)
    return True


# --------------------------------------------------------------------------
# Host capability
# --------------------------------------------------------------------------
_HOST_BACKENDS = {"macos": "ANGLE/Metal", "windows": "ANGLE/D3D11", "linux": "ANGLE/Vulkan"}

# The POSIX environment variables that name a language list, strongest first.
# `C` and `POSIX` are not languages, so they name nothing.
_HOST_LANGUAGE_VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")
_HOST_LANGUAGE_NONE = frozenset({"c", "posix", ""})


def host_locale(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The host's own timezone name and language list, probed as one fact.

    Read together and reported together, because the locale surface falls back
    to the host as a unit: a host zone beside a language list from anywhere
    else is the same incoherence a seeded draw produced, in a smaller form.

    Diagnostic only. Neither value is ever written into a composed profile --
    the profile carries no `locale` section at all when this layer owns the
    surface, which is what leaves the host's real zone in ICU (patch 0011) and
    the host's real list in the Accept-Language pref (patch 0013). Reporting
    them here is so an operator comparing a served timezone against an exit IP
    can see what the fallback is without launching a browser.

    The timezone comes from `/etc/localtime`, which is the same file ICU reads
    on Linux and which macOS keeps symlinked into its own zoneinfo tree. The
    language list comes from the POSIX environment, which is what Chromium
    reads on Linux; macOS and Windows answer from `AppleLanguages` and
    `GetUserPreferredUILanguages` instead, so the probe is labelled rather
    than asserted and can be `None` on a host whose real list is not empty.
    """
    overrides = dict(overrides or {})
    timezone = overrides.get("host_timezone")
    if timezone is None:
        timezone = _probe_host_timezone()
    languages = overrides.get("host_languages")
    if languages is None:
        languages = _probe_host_languages()
    return {"timezone": timezone, "languages": languages}


def _probe_host_timezone() -> str | None:
    """The host's IANA zone name, or None when nothing names one."""
    env = os.environ.get("TZ", "").strip().lstrip(":")
    if env and _TIMEZONE_RE.fullmatch(env):
        return env
    try:
        link = os.readlink("/etc/localtime")
    except OSError:
        link = ""
    # ".../zoneinfo/Asia/Bangkok" -> "Asia/Bangkok". The marker is the zoneinfo
    # directory rather than a fixed prefix: Linux uses /usr/share/zoneinfo and
    # macOS /var/db/timezone/zoneinfo.
    marker = "zoneinfo/"
    index = link.find(marker)
    if index != -1:
        name = link[index + len(marker):]
        if name and _TIMEZONE_RE.fullmatch(name):
            return name
    try:
        name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name if name and _TIMEZONE_RE.fullmatch(name) else None


def _probe_host_languages() -> str | None:
    """The POSIX environment's language list as the pref stores it, or None."""
    for name in _HOST_LANGUAGE_VARS:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        tags: list[str] = []
        for item in raw.replace(":", ",").split(","):
            tag = item.strip().split(".", 1)[0].split("@", 1)[0].replace("_", "-")
            if tag.lower() in _HOST_LANGUAGE_NONE or _LOCALE_RE.fullmatch(tag) is None:
                continue
            if tag not in tags:
                tags.append(tag)
        if tags:
            return ",".join(tags)
    return None


def host_facts(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What the host can serve. Absent facts disable the corresponding clamp."""
    overrides = dict(overrides or {})
    platform = overrides.get("host_platform")
    if platform is None:
        platform = _PLATFORM_ALIASES.get(sys.platform, None)
        if platform is None and sys.platform.startswith("linux"):
            platform = "linux"
    facts: dict[str, Any] = {
        "platform": _normalise_platform(platform) if platform else None,
        "logical_cores": overrides.get("host_logical_cores") or os.cpu_count(),
        "total_bytes": overrides.get("host_total_bytes"),
        "window_width": overrides.get("window_width"),
        "window_height": overrides.get("window_height"),
        "font_families": overrides.get("host_font_families"),
        # The fallback the locale surface inherits when no launch layer names
        # one. Read as a pair so a host zone can never end up beside a
        # language list from somewhere else.
        **host_locale(overrides),
    }
    facts["backend"] = overrides.get("host_backend") or (
        _HOST_BACKENDS.get(facts["platform"]) if facts["platform"] else None)
    if facts["total_bytes"] is None:
        try:
            facts["total_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (OSError, ValueError, AttributeError):
            facts["total_bytes"] = None
    return facts


def _servable(axis: str, servability: str, options: Sequence[Mapping[str, Any]],
              host: Mapping[str, Any], dropped: list[str]) -> list[Mapping[str, Any]]:
    if servability == "none":
        return list(options)
    kept: list[Mapping[str, Any]] = []
    for option in options:
        requires = option.get("requires") or {}
        keep = True
        if servability == "clamp-down":
            # coh: coh.claim-within-host-capacity - an option is offered only when
            # its requirement is at or below the host's real capacity, so a
            # composed profile can never claim more cores or more memory than the
            # machine can serve. Capacity only ever goes down.
            if "min_logical_cores" in requires and host.get("logical_cores") is not None:
                keep = keep and requires["min_logical_cores"] <= host["logical_cores"]
            if "min_total_bytes" in requires and host.get("total_bytes") is not None:
                keep = keep and requires["min_total_bytes"] <= host["total_bytes"]
        elif servability == "window-bounds":
            if host.get("window_width") is not None:
                keep = keep and requires.get("min_width", 0) >= host["window_width"]
            if host.get("window_height") is not None:
                keep = keep and requires.get("min_height", 0) >= host["window_height"]
        elif servability == "anchor-member":
            keep = requires.get("anchor") == host.get("anchor")
        elif servability == "files-present":
            available = host.get("font_families")
            if available is not None and option.get("pack_kind") == "optional":
                keep = set(requires.get("families", [])) <= set(available)
        if keep:
            kept.append(option)
        else:
            dropped.append(f"{axis}/{option['id']}")
    if not kept and servability in ("clamp-down", "window-bounds"):
        # Every option exceeds the host. The lowest bucket is still a claim above
        # host capability, so the axis contributes nothing and the surface stays
        # host-inherited, which is servable by definition. Reported, not hidden.
        return []
    if not kept:
        raise ResolverError(f"axis {axis} has no servable option for this host")
    return kept


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------
def _merge(base: Mapping[str, Any], overlay: Mapping[str, Any],
           path: tuple[str, ...] = ()) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        here = path + (key,)
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = _merge(current, value, here)
        elif here in _UNION_PATHS and isinstance(value, list) and isinstance(current, list):
            merged = list(current)
            merged.extend(item for item in value if item not in current)
            result[key] = merged
        else:
            result[key] = copy.deepcopy(value)
    return result


def _option_set(axis: str, table: Mapping[str, Any], parents: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    # A child axis only ever draws from the option set keyed on its resolved
    # parent, which is what turns one chip into one machine instead of three
    # independent draws. The edges that rest on that conditioning:
    #
    # coh: coh.hardware-product-configuration    - cpu and memory on gpu_identity
    # coh: coh.gpu-panel-media-product-pairing   - panel and media_topology on machine_class
    # coh: coh.dpr-times-screen-is-real-panel    - panel carries width, height and dpr as one value
    # coh: coh.device-counts-vs-claimed-machine  - media_topology on machine_class
    # coh: coh.battery-shape-vs-claimed-chassis  - battery on machine_class
    wanted = {name: parents[name] for name in table["conditioned_on"]}
    matches = [entry for entry in table["option_sets"] if entry["key"] == wanted]
    if len(matches) != 1:
        raise ResolverError(
            f"axis {axis} has {len(matches)} option sets for {_canonical_json(wanted)}; "
            "exactly one is required and a miss is a table defect, not a runtime condition"
        )
    return list(matches[0]["options"])


def _language_key(accept_languages: Any, table: Mapping[str, Any]) -> str:
    """Total projection of an accept-languages list onto the voices table's keys.

    This is computed before the draw, so it is a key projection and never a
    fallback after a failed match.
    """
    available = {entry["key"]["languages"] for entry in table["option_sets"]}
    if isinstance(accept_languages, str) and accept_languages in available:
        return accept_languages
    return ""


def _choose_anchor(catalogue: Mapping[str, Any], host: Mapping[str, Any],
                   root: bytes, requested: Any) -> tuple[Mapping[str, Any], list[str]]:
    anchors = catalogue["anchors"]
    warnings: list[str] = []
    if requested is not None:
        matches = [a for a in anchors if a["id"] == requested]
        if len(matches) != 1:
            raise ResolverError(f"unknown anchor {requested!r}")
        return matches[0], warnings
    # An anchor is offered only when the host's graphics stack can actually serve
    # its backend, so the renderer string, the limit tables and the measured
    # throughput all describe a stack the binary is really running.
    #
    # coh: coh.angle-backend-vs-gpu-claim      - the limit and precision tables follow the backend
    # coh: coh.gpu-claim-vs-measured-throughput - a software host is offered only the SwiftShader anchor
    # coh: coh.headless-display-vs-gpu-claim   - the renderer string comes from a servable stack
    servable = [a for a in anchors
                if host.get("backend") is None or a["backend"] == host["backend"]]
    if host.get("platform") is not None:
        servable = [a for a in servable if a["platform"] == host["platform"]] or servable
    if not servable:
        raise ResolverError(
            "no anchor matches this host's graphics backend; a coherent cluster cannot "
            "be composed, and presenting another backend's cluster is the retired "
            "catalogue's failure"
        )
    servable = sorted(servable, key=lambda a: a["id"])
    chosen = servable[weighted_pick(root, "anchor", [dict(a, weight=1) for a in servable])]
    return chosen, warnings


# The three layers that may own the locale surface, strongest first. There is
# no fourth, and in particular the seed is not one of them.
_LOCALE_SOURCES = ("command-line", "geoip", "host")


def _accept_languages_for(locale: str) -> str:
    """A bare tag becomes a list; a list is already one and is kept as given.

    `en-GB` becomes `en-GB,en` because that is the shape the pref stores and
    the shape the voices table is keyed on, and `scripts/geoip.py` derives the
    same list from a country's locale the same way. A value that already
    carries a comma is the caller's list and is passed through untouched.
    """
    if "," in locale:
        return locale
    fallback = locale.split("-", 1)[0]
    return locale if fallback.lower() == locale.lower() else f"{locale},{fallback}"


def _geoip_fields(value: Any) -> tuple[str | None, str | None]:
    """The locale and timezone one GeoIP result contributed, or None each.

    The shape is `scripts/geoip.py`'s `GeoIPResult.to_dict()` and the mapping
    `python/apostate/resolver.py` already accepts, so one lookup feeds both
    implementations. A field the lookup did not resolve is absent rather than
    guessed: `_COUNTRY_LOCALES` deliberately names no locale for a country it
    has no policy for, and the standing decision on a failed or partial lookup
    is to proceed with no override, warn, and invent nothing.
    """
    if not isinstance(value, Mapping):
        return None, None
    locale = value.get("locale") or value.get("accept_languages")
    if not isinstance(locale, str) or not locale.strip():
        languages = value.get("languages")
        if isinstance(languages, (list, tuple)) and languages:
            locale = ",".join(str(item) for item in languages)
        else:
            locale = None
    timezone = value.get("timezone")
    if not isinstance(timezone, str) or not timezone.strip():
        timezone = None
    return (locale.strip() if isinstance(locale, str) else None,
            timezone.strip() if isinstance(timezone, str) else None)


def _resolve_locale_surface(profile: dict[str, Any], config: Mapping[str, Any],
                            host: Mapping[str, Any],
                            policy: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Settle locale.accept_languages and locale.timezone, and name each source.

    FINGERPRINTS.md §6 conditions this surface on "launch precedence, GeoIP"
    and gives it no option table. It is not a dispersion axis and a seed must
    never reach it: a drawn timezone does not merely fail to match the egress,
    it guarantees a mismatch, because the draw cannot see where the connection
    comes out. An IP that geolocates to one country beside an
    `Intl.DateTimeFormat().resolvedOptions().timeZone` naming another is a
    first-line correlation check at every fraud vendor, and the host's own zone
    is strictly better than a drawn one: on a direct egress it matches, and
    behind a proxy it is at worst wrong in the same way a real traveller is.

    Three sources, strongest first:

    1. the command line. `--fingerprint-locale` / `--fingerprint-timezone` name
       a field each, and `--locale-policy=<id>` names a catalogue policy, which
       supplies both as one internally consistent pair -- `en-gb` is `en-GB,en`
       with `Europe/London`, never one of those beside the other's country.
       This is also where the Python and Node launchers put the answer from
       their prelaunch GeoIP lookup, so from the browser's side layers 1 and 2
       arrive as one;
    2. GeoIP of the effective egress -- the proxy exit when one is configured,
       the direct IP otherwise -- passed in as a result mapping rather than
       looked up here, so composition stays a pure function of its inputs;
    3. the host, expressed as *absence*. No `locale` section is written, which
       leaves the host's real zone in ICU and the host's real list in the
       Accept-Language pref. Absence is why the two fields cannot drift apart:
       a field nobody named is not written, so the host serves it rather than a
       second source that happens to agree today.

    The seed is not on that list at any position. It is the whole point.

    Called before the voices axis resolves rather than merged over the finished
    profile, because the voices table is keyed on the resolved accept-languages
    list: applying it later would leave a launch claiming en-GB while offering
    the voice set measured for another list. That is patch 0085's reason, and
    the reference path has to share it for the two implementations to agree.

    Sole writer of the profile's `locale` section, so there is one place a
    value can enter it and one place to check where it came from.
    """
    geo_locale, geo_timezone = _geoip_fields(config.get("geoip"))
    named = (policy or {}).get("value") or {}
    named = named.get("locale") or {}
    policy_id = (policy or {}).get("id")

    locale = config.get("fingerprint_locale")
    if locale is not None:
        if not isinstance(locale, str) or _LOCALE_RE.fullmatch(locale.split(",", 1)[0]) is None:
            raise ResolverError(f"invalid locale {locale!r}")
        languages = {"value": _accept_languages_for(locale.strip()), "source": "command-line"}
    elif isinstance(named.get("accept_languages"), str):
        languages = {"value": named["accept_languages"], "source": "command-line",
                     "policy": policy_id}
    elif geo_locale is not None:
        if _LOCALE_RE.fullmatch(geo_locale.split(",", 1)[0]) is None:
            raise ResolverError(f"GeoIP returned an invalid locale {geo_locale!r}")
        languages = {"value": _accept_languages_for(geo_locale), "source": "geoip"}
    else:
        languages = {"value": None, "source": "host", "host_value": host.get("languages")}

    timezone = config.get("fingerprint_timezone")
    if timezone is not None:
        if not isinstance(timezone, str) or _TIMEZONE_RE.fullmatch(timezone) is None:
            raise ResolverError(f"invalid timezone {timezone!r}")
        zone = {"value": timezone.strip(), "source": "command-line"}
    elif isinstance(named.get("timezone"), str):
        zone = {"value": named["timezone"], "source": "command-line", "policy": policy_id}
    elif geo_timezone is not None:
        if _TIMEZONE_RE.fullmatch(geo_timezone) is None:
            raise ResolverError(f"GeoIP returned an invalid timezone {geo_timezone!r}")
        zone = {"value": geo_timezone, "source": "geoip"}
    else:
        zone = {"value": None, "source": "host", "host_value": host.get("timezone")}

    section = {name: field["value"]
               for name, field in (("accept_languages", languages), ("timezone", zone))
               if field["value"] is not None}
    if section:
        profile["locale"] = section
    else:
        profile.pop("locale", None)
    return {"accept_languages": languages, "timezone": zone}


def _locale_provenance_check(profile: Mapping[str, Any],
                             sources: Mapping[str, Mapping[str, Any]]) -> None:
    """A locale field the profile carries must name the layer that supplied it.

    # coh: coh.timezone-network-position - the edge asks that the served
    # timezone be plausible for the exit IP's geography. Plausibility itself
    # needs the egress, which composition does not have; what composition owns
    # is the necessary condition, and this is it: the value comes from the
    # command line (where an operator's switch and the launchers' GeoIP answer
    # both arrive) or from a GeoIP result, or it is not in the profile at all
    # and the host serves it. A value with no such source is what the seeded
    # pool draw produced -- a timezone uncorrelated with the egress by
    # construction -- and it is a defect rather than a fallback, so it raises
    # here instead of shipping.
    """
    carried = profile.get("locale") or {}
    if set(carried) - {"accept_languages", "timezone"}:
        raise ResolverError(
            f"profile locale section carries unknown fields {sorted(set(carried) - {'accept_languages', 'timezone'})}"
        )
    for field in ("accept_languages", "timezone"):
        source = sources[field]["source"]
        if source not in _LOCALE_SOURCES:
            raise ResolverError(
                f"locale.{field} names source {source!r}, which is not one of {list(_LOCALE_SOURCES)}"
            )
        named = source != "host"
        if named != (field in carried):
            raise ResolverError(
                f"locale.{field} is {'present in' if field in carried else 'absent from'} the "
                f"composed profile while its source is {source!r}: a locale field is carried "
                "exactly when the command line or a GeoIP result supplied it, and is absent "
                "exactly when the host serves it. Any other combination means a value reached "
                "this surface from a layer that cannot know where the connection comes out, "
                "which is the seeded pool draw this precedence exists to remove"
            )


def _integral(value: Any) -> int | None:
    """A losslessly integral endpoint, or None. 2047.9375 is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


#: A measured GL parameter that arrives as a two-element range, and the pair of
#: scalar profile keys it decomposes into. Both the profile loader and ANGLE's
#: cap lookup read scalars, so a range has to be stored as two of them, and the
#: names on the right are the ones the C++ already asks for by string.
GL_LIMIT_RANGE_KEYS: dict[str, tuple[str, str]] = {
    "ALIASED_LINE_WIDTH_RANGE": (
        "ALIASED_LINE_WIDTH_RANGE_MIN",
        "ALIASED_LINE_WIDTH_RANGE_MAX",
    ),
    "ALIASED_POINT_SIZE_RANGE": (
        "ALIASED_POINT_SIZE_RANGE_MIN",
        "ALIASED_POINT_SIZE_RANGE_MAX",
    ),
    "MAX_VIEWPORT_DIMS": ("MAX_VIEWPORT_DIMS_WIDTH", "MAX_VIEWPORT_DIMS_HEIGHT"),
}

#: Servable scalar GL limits whose names are not MAX_-prefixed. D3D11 mandates
#: UNIFORM_BUFFER_OFFSET_ALIGNMENT 256 where a software rasteriser reports 16,
#: and MIN_PROGRAM_TEXEL_OFFSET is measured negative, so both are lookup-table
#: contradictions against a discrete-GPU renderer string and both have to
#: travel with the anchor.
GL_LIMIT_SCALAR_KEYS: frozenset[str] = frozenset(
    {"MIN_PROGRAM_TEXEL_OFFSET", "UNIFORM_BUFFER_OFFSET_ALIGNMENT"}
)

#: GL parameters that are float-valued in GL itself, so a measured fraction is
#: a real measurement rather than noise. Everything else is a count and must be
#: integral.
#:
#: The Linux/Vulkan anchor measures ALIASED_POINT_SIZE_RANGE max 2047.9375 --
#: 2047 + 15/16, exactly what a four-bit subpixel point size produces. Rounding
#: it to 2047 would serve a number no device reported, and rejecting it served
#: the host's [1, 256] under an NVIDIA renderer string. The other two are
#: float-valued for the same reason and are listed by GL type rather than
#: because the corpus happens to show a fraction in them today.
GL_LIMIT_FRACTIONAL_KEYS: frozenset[str] = frozenset(
    {"ALIASED_LINE_WIDTH_RANGE", "ALIASED_POINT_SIZE_RANGE",
     "MAX_TEXTURE_LOD_BIAS"}
)


def _limit_number(name: str, value: Any) -> int | float | None:
    """A measured limit as the number the profile should carry, or None.

    Integral values stay ``int`` whatever the parameter, so nothing that
    already resolved changes shape or bytes. Only a genuinely fractional
    measurement of a genuinely float-valued parameter stays ``float``.
    """
    exact = _integral(value)
    if exact is not None:
        return exact
    if name in GL_LIMIT_FRACTIONAL_KEYS and isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def gl_limit_entries(name: str, value: Any) -> list[tuple[str, int | float]]:
    """The profile limit entries one measured GL parameter produces.

    One function because three consumers have to agree on it: this resolver,
    ``scripts/generate-dispersion-tables.py`` compiling the tables into the
    binary, and through those the C++ that serves them. A capability that
    reaches the binary but not the resolver, or the reverse, is the same class
    of defect as serving none at all.

    The filter this replaces accepted a scalar only when it was MAX_-prefixed
    and at least 1, with one hardcoded case for ALIASED_POINT_SIZE_RANGE. That
    silently discarded five measured parameters from every anchor:
    MAX_VIEWPORT_DIMS because a two-element range is not an integer,
    UNIFORM_BUFFER_OFFSET_ALIGNMENT and MIN_PROGRAM_TEXEL_OFFSET because they
    carry no MAX_ prefix, ALIASED_LINE_WIDTH_RANGE for both reasons, and
    MAX_SERVER_WAIT_TIMEOUT because its measured value is 0. Each is a
    zero-GL-work lookup a page reads in one call, and a dropped one is served
    from the host instead -- which is how an ANGLE Direct3D11 renderer string
    came to sit beside a 16384x16384 viewport.

    A range yields both endpoints or neither: half an interval is a fabricated
    limit. A scalar yields any losslessly integral value, including 0 and
    negative ones, because those are measurements too.

    A fraction is kept only where GL itself is float-valued, per
    GL_LIMIT_FRACTIONAL_KEYS. That is the seventh place this codebase assumed
    a capability is a positive INTEGRAL scalar, and the integral half outlived
    the other six because the values they held happened to be whole. The
    Linux/Vulkan anchor measures ALIASED_POINT_SIZE_RANGE max 2047.9375 and
    this used to drop it, which served the host's 256 under an NVIDIA
    renderer string. Anywhere else a fraction is still refused rather than
    rounded, because a count with a fraction in it is not a measurement this
    can represent.

    A ``_WEBGL``-suffixed name is a WebGL-specification constant rather than a
    GL limit -- MAX_CLIENT_WAIT_TIMEOUT_WEBGL is answered by Blink, not by the
    GL binding layer, and all four anchors measured it 0, which is what every
    implementation reports. It is excluded because there is no contradiction
    available in it and admitting it would declare a profile key that nothing
    serves.
    """
    if not gl_limit_is_servable(name):
        return []
    endpoints = GL_LIMIT_RANGE_KEYS.get(name)
    if endpoints is not None:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return []
        low = _limit_number(name, value[0])
        high = _limit_number(name, value[1])
        if low is None or high is None or low < 1 or high < 1:
            return []
        return [(endpoints[0], low), (endpoints[1], high)]
    number = _limit_number(name, value)
    if number is None:
        return []
    return [(name, number)]


def gl_limit_is_servable(name: str) -> bool:
    """Whether a measured GL parameter name is one a profile can carry.

    Separate from ``gl_limit_entries`` because ``capture/derive/to_profile.py``
    has to answer it before it has a value to convert: it validates a capture's
    raw measurement and raises on a malformed one, and a name it does not
    recognise has to be skipped rather than rejected.

    Validation is deliberately NOT shared, and the next reader should resist
    unifying it. A capture is untrusted input, so a recognised name with a
    malformed value is an error that pipeline must raise. An anchor is
    already-validated corpus data, so the same input is skipped and the
    surface stays inherited. Those are different policies on purpose, and
    sharing the validation would force one of them onto the other.

    What was drifting between the two pipelines was never the validation. It
    was which names are servable and what keys each becomes -- the capture
    path and the anchor path each kept their own answer, and each silently
    dropped five measured parameters the compiled tables were built to serve.
    That answer is what lives here, and it is the only part that has to be
    one implementation.
    """
    if name.endswith("_WEBGL"):
        # A WebGL-specification constant, answered by Blink rather than the GL
        # binding layer. See gl_limit_entries.
        return False
    return (name in GL_LIMIT_RANGE_KEYS or name.startswith("MAX_")
            or name in GL_LIMIT_SCALAR_KEYS)


def _anchor_capability_layer(record: Mapping[str, Any], renderer: str | None) -> dict[str, Any]:
    """The anchor's capability cluster as a profile fragment.

    An anchor is atomic: selecting it takes the whole cluster, not just the
    identity strings. Presenting a member's renderer string on top of the
    host's own capability tables is precisely the retired catalogue's failure
    (docs/FINGERPRINTS.md sections 1 and 4), so the cluster is merged here.

    WebGL1 and WebGL2 agree on every shared MAX_* limit and every shader
    precision entry in all four anchors -- that agreement is what makes them one
    anchor -- so the limit map is their union and the extension list is the
    union of what either context exposes. WebGPU is different: it is not uniform
    inside the Linux/Vulkan anchor, where two members report an adapter and two
    returned none, so it is taken from the resolved member's variant and never
    from the anchor as a whole.
    """
    cluster = record.get("capability_cluster")
    if not isinstance(cluster, Mapping):
        raise ResolverError(f"anchor {record.get('anchor_id')!r} has no capability cluster")
    extensions: set[str] = set()
    limits: dict[str, int] = {}
    precisions: dict[str, Any] = {}
    for context in ("webgl1", "webgl2"):
        section = cluster.get(context)
        if not isinstance(section, Mapping):
            continue
        extensions.update(str(name) for name in section.get("extensions", []))
        for name, value in (section.get("parameters") or {}).items():
            for key, exact in gl_limit_entries(name, value):
                if limits.get(key, exact) != exact:
                    raise ResolverError(
                        f"anchor {record['anchor_id']} disagrees with itself on {key}: "
                        "a single limit map cannot represent two contexts"
                    )
                limits[key] = exact
        for key, entry in (section.get("precision") or {}).items():
            if not isinstance(entry, Mapping):
                continue
            derived = {field: entry[field] for field in ("rangeMin", "rangeMax", "precision")
                       if field in entry}
            if len(derived) != 3:
                continue
            if precisions.get(key, derived) != derived:
                raise ResolverError(
                    f"anchor {record['anchor_id']} disagrees with itself on precision {key}"
                )
            precisions[key] = derived

    # An anchor that produced no limits is the defect this guard exists for.
    # The whole failure mode was that a profile could carry a discrete-GPU
    # renderer string with no capability table behind it, and every consumer
    # fell through to the host's own numbers -- which looks exactly like
    # success until a detector reads one lookup value. Refusing here is what
    # makes that loud instead of silent.
    if not limits:
        raise ResolverError(
            f"anchor {record['anchor_id']} produced no GL capability limits, so a profile "
            "built on it would present its renderer string over the host's own capability "
            "table -- the contradiction the anchor exists to prevent"
        )

    layer: dict[str, Any] = {}
    if extensions:
        layer["gl_extensions"] = sorted(extensions)
    layer["gl_limits"] = dict(sorted(limits.items()))
    if precisions:
        layer["gl_precisions"] = dict(sorted(precisions.items()))

    webgpu = _member_webgpu(record, cluster, renderer)
    if webgpu is not None:
        layer["webgpu"] = webgpu
    return layer


def _member_webgpu(record: Mapping[str, Any], cluster: Mapping[str, Any],
                   renderer: str | None) -> dict[str, Any] | None:
    """The resolved member's WebGPU adapter, or None when it reported none."""
    # coh: coh.gpu-webgl-webgpu - the WebGPU section is taken from the same anchor
    # member whose unmaskedRenderer the profile presents, and is omitted rather
    # than borrowed from a sibling when that member reported no adapter. A mixture
    # of two members is a device that does not exist.
    if renderer is None:
        return None
    capture = None
    for member in record.get("members", []):
        if ((member.get("identity") or {}).get("webgl1") or {}).get("unmaskedRenderer") == renderer:
            capture = str(member.get("capture", "")).rsplit("/", 1)[-1]
            break
    if not capture:
        raise ResolverError(
            f"renderer {renderer!r} is not a measured member of anchor {record['anchor_id']}"
        )
    variants = (cluster.get("webgpu") or {}).get("variants") or []
    matches = [variant for variant in variants if capture in (variant.get("members") or [])]
    if len(matches) != 1:
        raise ResolverError(
            f"anchor {record['anchor_id']} has {len(matches)} WebGPU variants for {capture}"
        )
    adapters = (matches[0].get("cluster") or {}).get("adapters") or {}
    adapter = adapters.get("high-performance") or adapters.get("low-power")
    if not isinstance(adapter, Mapping):
        # This member returned no adapter at all. Claiming another member's
        # adapter would be an unmeasured assertion, so WebGPU stays inherited.
        return None
    section: dict[str, Any] = {}
    features = adapter.get("features")
    if isinstance(features, list) and features:
        section["features"] = sorted(str(name) for name in features)
    info = {field: adapter[field] for field in ("vendor", "architecture")
            if isinstance(adapter.get(field), str) and adapter[field]}
    if info:
        section["info"] = info
    limits = {name: value for name, value in (adapter.get("limits") or {}).items()
              if isinstance(value, int) and not isinstance(value, bool) and value >= 0}
    if limits:
        section["limits"] = dict(sorted(limits.items()))
    return section or None


def _derive_work_area(profile: dict[str, Any]) -> None:
    """Turn panel geometry plus furniture insets into the avail_* rectangle."""
    screen = profile.get("screen")
    if not isinstance(screen, dict):
        return
    width, height = screen.get("width"), screen.get("height")
    left = screen.pop("avail_inset_left", None)
    top = screen.pop("avail_inset_top", None)
    right = screen.pop("avail_inset_right", None)
    bottom = screen.pop("avail_inset_bottom", None)
    if width is None or height is None or None in (left, top, right, bottom):
        if not screen:
            # Furniture contributed insets but no panel was servable, so nothing
            # measurable is left. An empty section is not a claim: drop it and
            # leave the geometry host-inherited.
            profile.pop("screen")
        return
    # coh: coh.screen-avail-inset - a work area no display could have is rejected
    # here. Only the geometric half: nothing checks the insets against the OS the
    # profile claims, which is the half the edge still carries.
    if left + right >= width or top + bottom >= height:
        raise ResolverError("furniture insets exceed the panel: that work area cannot exist")
    # coh: coh.screen-avail-inset-vs-claimed-os - both arithmetic identities hold
    # by construction below: avail_width is width minus its two insets and
    # avail_height is height minus its two, and the insets are popped, so no
    # profile can carry a contradictory pair in either axis. The edge's third
    # clause -- that a visible dock must make the work area strictly smaller in
    # the axis it occupies -- is not enforced here, because this function sees
    # the insets and not the furniture option that supplied them, so it cannot
    # tell an auto-hiding taskbar, for which an all-zero inset is correct, from
    # a table entry that forgot one.
    screen["avail_left"] = left
    screen["avail_top"] = top
    screen["avail_width"] = width - left - right
    screen["avail_height"] = height - top - bottom


def _coherence_check(profile: Mapping[str, Any], platform: str, browser_build: str) -> None:
    # coh: coh.display-hdr-cluster - color_depth is Chromium's HDR flag rather
    # than a bit depth, so it cannot disagree with hdr or with the gamut.
    screen = profile.get("screen") or {}
    if screen.get("hdr") is True and screen.get("color_gamut") == "srgb":
        raise ResolverError("HDR display cannot use sRGB gamut")
    if screen.get("hdr") is False and screen.get("color_depth") == 30:
        raise ResolverError("color_depth 30 is Chromium's HDR flag and contradicts hdr false")
    input_section = profile.get("input") or {}
    if input_section.get("pointer_type") == "none" and input_section.get("hover") is True:
        raise ResolverError("pointer_type=none with hover=true is incoherent")
    # coh: coh.device-id-vs-device-list - the per-kind counts must derive from the
    # one device list, which is the clause of that edge composition can decide.
    # coh: coh.device-counts-vs-claimed-machine - same condition, counts half.
    media = profile.get("media") or {}
    devices = media.get("devices")
    if isinstance(devices, list):
        for kind in ("audioinput", "audiooutput", "videoinput"):
            declared = media.get(f"{kind}_count")
            actual = sum(1 for device in devices if device.get("kind") == kind)
            if declared is not None and declared != actual:
                raise ResolverError(f"media.{kind}_count {declared} disagrees with {actual} devices")
    # coh: coh.audio-hardware-vs-claimed-os - the buffer size half, which is the
    # only half composition owns. An absent audio section is a defect and not a
    # silence, for exactly the reason an absent UA is: patch 0019 built the
    # consumer, config/profile.schema.json declared the field, no option ever
    # supplied it, and the renderer went on serving the host's own buffer under
    # every persona -- 2048 frames of Linux ALSA behind a macOS UA, readable from
    # any page in one property access with no permission. The axis has an option
    # set per platform and `servability: none`, so nothing can drop it; if this
    # fires, a table is missing rather than a host is limited.
    audio = profile.get("audio") or {}
    if audio.get("hardware_buffer_frames") is None:
        raise ResolverError(
            f"composed profile claims platform {platform} but carries no "
            "audio.hardware_buffer_frames: with the field absent AudioContext.baseLatency is "
            "the host's own audio buffer over the host's own sample rate, which contradicts "
            "the claimed platform on every host whose backend differs from it"
        )
    wow64 = (profile.get("platform") or {}).get("wow64")
    if wow64 is True and platform != "windows":
        raise ResolverError("wow64 is a Windows-only fact")
    # coh: coh.ua-platform-agreement - navigator.platform, the two Client Hints
    # platform reads and the UA string's OS token are all drawn from one
    # os_release option, so they cannot disagree by construction. What
    # composition could still get wrong is shipping no UA at all, and for the
    # whole of the first V3 run it did: the field was declared in the schema and
    # consumed by patch 0009, no option supplied it, the override never fired,
    # and Chromium's host-derived default served "X11; Linux x86_64" underneath a
    # Win32 navigator.platform. An absent UA is a defect, not a silence, so the
    # two conditions below are unconditional.
    browser = profile.get("browser") or {}
    user_agent = browser.get("user_agent")
    if not isinstance(user_agent, str) or not user_agent:
        raise ResolverError(
            f"composed profile claims platform {platform} but carries no "
            "browser.user_agent: with the field absent the UA emitter falls back to the "
            "build's host-derived string, which contradicts navigator.platform and both "
            "Client Hints platform reads"
        )
    claimed_token = _UA_OS_TOKENS[platform]
    wrong = sorted(token for name, token in _UA_OS_TOKENS.items()
                   if name != platform and token in user_agent)
    if claimed_token not in user_agent or wrong:
        raise ResolverError(
            f"profile user-agent {user_agent!r} does not describe the claimed platform "
            f"{platform}: expected the OS token {claimed_token!r}"
            + (f" and not {wrong}" if wrong else "")
        )
    # coh: coh.ua-version-agreement - the major a UA claims belongs to the build,
    # not to the device the string was captured off. `user_agent_for_build`
    # stamps it during composition; this is that substitution's post-condition,
    # and it is what fails if a table ever carries a UA the rule could not place.
    matched = _CHROME_UA_RE.search(user_agent)
    build_match = _BUILD_RE.fullmatch(browser_build)
    if matched is None or build_match is None or matched.group(1) != build_match.group(1):
        raise ResolverError(
            f"profile user-agent {user_agent!r} does not claim the major of browser build "
            f"{browser_build}"
        )


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _normalise_config(config: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        raise ResolverError("resolver config must be a JSON object")
    merged = dict(config)
    merged.update({key: value for key, value in overrides.items() if value is not None})
    aliases = {
        "fingerprint": ("seed",),
        "fingerprint_platform": ("platform",),
        "profile_file": ("profile_path", "profileFile"),
        "browser_build": ("chromium_build", "browserBuild"),
        "fingerprint_locale": ("locale",),
        "fingerprint_timezone": ("timezone",),
    }
    for canonical, names in aliases.items():
        if canonical not in merged:
            for alias in names:
                if alias in merged and merged[alias] is not None:
                    merged[canonical] = merged[alias]
                    break
    return merged


def _coerce_seed(value: Any) -> str:
    if value is None:
        return "0"
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ResolverError("fingerprint/seed must be an integer or non-empty string")
    text = str(value)
    if not text:
        raise ResolverError("fingerprint/seed must not be empty")
    return text


def _normalise_build(value: Any, catalogue: Mapping[str, Any]) -> str:
    if value is None:
        value = catalogue.get("browser_build")
    if not isinstance(value, str) or _BUILD_RE.fullmatch(value) is None:
        raise ResolverError("browser_build must be a Chromium four-component version")
    baseline = catalogue.get("browser_build")
    baseline_match = _BUILD_RE.fullmatch(baseline) if isinstance(baseline, str) else None
    if baseline_match and _BUILD_RE.fullmatch(value).group(1) != baseline_match.group(1):
        raise ResolverError(
            f"browser build {value} is incompatible with catalogue baseline {baseline}"
        )
    return value


def _load_explicit_file(value: Any) -> dict[str, Any]:
    if not isinstance(value, (str, os.PathLike)):
        raise ResolverError("profile_file must be a path")
    profile = _read_object(Path(value), "profile file")
    nested = profile.get("profile")
    if isinstance(nested, dict):
        profile = nested
    try:
        validate_profile(profile)
    except ProfileValidationError as exc:
        raise ResolverError(f"explicit profile file is not schema-valid: {exc}") from exc
    return profile


def _policy_candidates(catalogue: Mapping[str, Any], kind: str,
                       platform: str) -> list[Mapping[str, Any]]:
    """The catalogue policies this persona may use, id-sorted.

    Id-sorted because selection is over the id-sorted candidate list on both
    implementations, so a reordered catalogue cannot move a choice.
    """
    candidates = sorted(
        (entry for entry in catalogue["policies"][kind] if entry["platform"] in ("all", platform)),
        key=lambda entry: entry["id"],
    )
    if not candidates:
        raise ResolverError(f"no {kind} policy supports platform {platform}")
    return candidates


def _policy_named(candidates: Sequence[Mapping[str, Any]], kind: str,
                  requested: str) -> Mapping[str, Any]:
    """The one policy a launch named by id."""
    matches = [entry for entry in candidates if entry["id"] == requested]
    if len(matches) != 1:
        raise ResolverError(f"unknown {kind} policy {requested!r}")
    return matches[0]


def _policy_drawn(candidates: Sequence[Mapping[str, Any]], kind: str,
                  root: bytes) -> Mapping[str, Any]:
    """The policy the seed draws.

    Only `theme` reaches this. Every candidate weighs the same: the catalogue
    states no prevalence for them and inventing one would be synthesis dressed
    as data. `locale` deliberately has no drawn form -- see
    `_resolve_locale_surface` for why a seed must not reach that surface.
    """
    return candidates[weighted_pick(root, f"policy.{kind}",
                                    [dict(entry, weight=1) for entry in candidates])]


def _resolve_internal(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    cfg = _normalise_config(config or {}, overrides)
    catalogue, tables, anchor_records = _load_catalogue(cfg.get("catalogue_path"))

    explicit_file = cfg.get("profile_file")
    if explicit_file is not None:
        profile = _load_explicit_file(explicit_file)
        platform = cfg.get("fingerprint_platform")
        platform = _normalise_platform(platform) if platform is not None else "host"
        return {
            "profile": profile,
            "diagnostics": {
                "profile_id": "explicit-profile-file",
                "catalogue_version": catalogue["catalogue_version"],
                "profile_schema_version": PROFILE_SCHEMA_VERSION,
                "browser_build": _normalise_build(cfg.get("browser_build"), catalogue),
                "platform": platform,
                "source": "explicit-profile-file",
                "warnings": [
                    "an explicit profile file bypasses composition: its coherence and "
                    "servability are the author's responsibility, not the catalogue's"
                ],
            },
            "native_loader_support": {
                "profile_schema": "config/profile.schema.json",
                "profile_schema_valid": True,
                "unsupported_fields": [],
            },
        }

    platform_value = cfg.get("fingerprint_platform")
    host = host_facts(cfg)
    platform = _normalise_platform(platform_value) if platform_value is not None else (
        host["platform"] or "linux")
    seed = _coerce_seed(_first(cfg, "fingerprint", default=None))
    browser_build = _normalise_build(cfg.get("browser_build"), catalogue)
    requested_version = cfg.get("catalogue_version")
    if requested_version is not None and requested_version != catalogue["catalogue_version"]:
        raise ResolverError("requested catalogue_version does not match the loaded catalogue")

    root = seed_root(seed, platform, browser_build, catalogue["catalogue_version"],
                     PROFILE_SCHEMA_VERSION)
    warnings: list[str] = []
    dropped: list[str] = []
    if platform_value is None:
        warnings.append(f"fingerprint_platform defaulted to the host persona {platform}")

    anchor, anchor_warnings = _choose_anchor(catalogue, host, root, cfg.get("anchor"))
    warnings.extend(anchor_warnings)
    if anchor["platform"] != platform:
        warnings.append(
            f"anchor {anchor['id']} is a {anchor['platform']} / {anchor['backend']} cluster "
            f"while the persona is {platform}: --fingerprint-platform does not move the GPU "
            "cluster, and this mismatch is a reported limitation rather than a hidden one"
        )
    if anchor.get("build_caveat"):
        warnings.append(f"anchor {anchor['id']}: {anchor['build_caveat']}")
    if anchor["rotation_status"] == "single-member":
        warnings.append(
            f"anchor {anchor['id']} has one measured member, so every identity offered on its "
            "capability cluster beyond that one is a registered catalogue value rather than a "
            "measurement; each such option states in its note what is measured and what is derived"
        )

    parents: dict[str, Any] = {"platform": platform, "anchor": anchor["id"]}
    host_for_axes = dict(host, anchor=anchor["id"])
    profile: dict[str, Any] = {}
    chosen: dict[str, Any] = {}
    locale_policy = cfg.get("locale_policy")
    locale_entry = _policy_named(
        _policy_candidates(catalogue, "locale", platform), "locale", locale_policy
    ) if isinstance(locale_policy, str) else None
    locale_sources: dict[str, dict[str, Any]] | None = None

    for axis in AXES:
        table = tables[axis]
        if axis == "voices":
            # The locale surface is settled first, because this key is a
            # projection of the resolved accept-languages list. It is settled
            # from launch precedence, GeoIP and the host -- never from the
            # seed -- so unlike every axis below it, nothing about its value
            # depends on where in this loop it happens.
            locale_sources = _resolve_locale_surface(profile, cfg, host, locale_entry)
            parents["languages"] = _language_key(
                (profile.get("locale") or {}).get("accept_languages"), table)
        options = _option_set(axis, table, parents)
        options = _servable(axis, table["servability"], options, host_for_axes, dropped)
        if not options:
            chosen[axis] = {"options": [], "evidence": ["host-inherited"], "offered": 0}
            warnings.append(
                f"axis {axis} has no option this host can serve, so the surface stays "
                "host-inherited: the lowest bucket would still be a claim above host "
                "capability, and capacity is only ever reduced"
            )
            continue
        if table["selection"] == "single":
            index = weighted_pick(root, axis, options)
            picked = [options[index]]
        else:
            picked = [option for option in options if option.get("pack_kind") == "core"]
            optional = [option for option in options if option.get("pack_kind") == "optional"]
            picked.extend(option for position, option in enumerate(optional)
                          if subset_included(root, axis, position, option["weight"]))
        for option in picked:
            profile = _merge(profile, option["value"])
        chosen[axis] = {
            "options": [option["id"] for option in picked],
            "evidence": sorted({option["evidence"] for option in picked}),
            "offered": len(options),
        }
        # Every single-selection axis becomes a parent, exactly as `Compose`
        # writes `resolved[axis.axis]` for each of them. A child axis keyed on
        # one of these (machine_class on gpu_identity, panel on machine_class)
        # would otherwise miss.
        if table["selection"] == "single":
            parents[axis] = picked[0]["id"]

        if axis == "gpu_identity":
            # An anchor is atomic. Merge its capability cluster now that the
            # member is known, so the identity strings are backed by the tables
            # that silicon actually produced instead of the host's own.
            identity_renderer = (picked[0]["value"].get("gpu") or {}).get("unmasked_renderer")
            block = picked[0].get("member")
            if isinstance(block, Mapping):
                # A registered identity is not a measured member. Its `member`
                # block names the measured member whose adapter it carries,
                # which is what scripts/generate-dispersion-tables.py folds into
                # the compiled member table. With no donor named, WebGPU stays
                # host-inherited rather than borrowing a sibling's.
                donor = block.get("webgpu_measured_on")
                identity_renderer = None
                for member in anchor_records[anchor["id"]]["record"].get("members", []):
                    if donor and member.get("device") == donor:
                        identity_renderer = (
                            (member.get("identity") or {}).get("webgl1") or {}
                        ).get("unmaskedRenderer")
                        break
            layer = _anchor_capability_layer(
                anchor_records[anchor["id"]]["record"], identity_renderer,
            )
            # Dawn resolves GPUAdapterInfo.architecture from the PCI device id,
            # so it is the one borrowed adapter field that follows the claimed
            # board rather than the donor. Everything else -- vendor, features,
            # the measured limits -- stays the donor's, because that is what was
            # measured. Mirrors register_identities in
            # scripts/generate-dispersion-tables.py; the compiled table and this
            # resolver must not disagree about what a persona reports.
            if isinstance(block, Mapping) and block.get("webgpu_architecture") \
                    and isinstance(layer.get("webgpu"), dict) \
                    and isinstance(layer["webgpu"].get("info"), dict) \
                    and "architecture" in layer["webgpu"]["info"]:
                layer["webgpu"]["info"]["architecture"] = block["webgpu_architecture"]
            profile = _merge(profile, layer)
            chosen["anchor"] = {
                "options": [anchor["id"]],
                "evidence": [anchor["evidence_class"]],
                "offered": len(catalogue["anchors"]),
                "sections": sorted(layer),
            }
            if "webgpu" not in layer:
                warnings.append(
                    f"the resolved member of anchor {anchor['id']} reported no WebGPU adapter, "
                    "so WebGPU stays host-inherited: presenting another member's adapter would "
                    "be an unmeasured assertion"
                )

    # The voices axis is the only thing that needed the locale settled mid-loop.
    # A catalogue without that axis would leave it unsettled, so settle it here
    # too rather than leaving the surface undecided, which is what the compiled
    # compositor does with its own `locale_resolved` flag.
    if locale_sources is None:
        locale_sources = _resolve_locale_surface(profile, cfg, host, locale_entry)
    chosen["locale"] = {
        "options": [locale_entry["id"]] if locale_entry is not None else [],
        "evidence": [locale_entry["evidence_class"]] if locale_entry is not None
        else ["launch-precedence" if any(field["source"] != "host"
                                        for field in locale_sources.values())
              else "host-inherited"],
        # The four options are the set `--locale-policy` selects from, not a
        # pool anything draws from. Reported so the count is not read as a
        # dispersion width it never was.
        "offered": len(catalogue["policies"]["locale"]),
    }

    theme_candidates = _policy_candidates(catalogue, "theme", platform)
    theme_requested = cfg.get("theme_policy")
    theme_entry = (_policy_named(theme_candidates, "theme", theme_requested)
                   if isinstance(theme_requested, str)
                   else _policy_drawn(theme_candidates, "theme", root))
    profile = _merge(profile, theme_entry["value"])
    chosen["theme"] = {"options": [theme_entry["id"]],
                       "evidence": [theme_entry["evidence_class"]],
                       "offered": len(catalogue["policies"]["theme"])}

    # The loader reads `id` into source_id_ for diagnostics (patch 0004), so it
    # is part of the runtime payload and the reference implementation must emit
    # it. Derived from the composition root rather than the profile digest: a
    # field inside the profile cannot depend on a hash of the profile.
    profile["id"] = f"fp-{root.hex()[:24]}"

    # The tables carry the UA as captured. The compiled tables the browser reads
    # are stamped once by scripts/generate-dispersion-tables.py; this is the same
    # rule on the reference path, so both implementations serve one string.
    composed_browser = profile.get("browser")
    if isinstance(composed_browser, dict) and isinstance(composed_browser.get("user_agent"), str):
        composed_browser["user_agent"] = user_agent_for_build(
            composed_browser["user_agent"], browser_build)

    _derive_work_area(profile)
    # Last, so that nothing merged after the locale surface was settled can
    # leave a value in it whose source nobody can name. That is the state the
    # seeded draw produced, and it is the one this precedence exists to make
    # unreachable rather than merely unlikely.
    _locale_provenance_check(profile, locale_sources)
    validate_profile(profile)
    _coherence_check(profile, platform, browser_build)

    profile_digest = hashlib.sha256(_canonical_json(profile).encode("utf-8")).hexdigest()
    identity_material = {
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "catalogue_version": catalogue["catalogue_version"],
        "browser_build": browser_build,
        "platform": platform,
        "seed": seed,
        "anchor": anchor["id"],
        "axes": {axis: value["options"] for axis, value in sorted(chosen.items())},
        "profile_sha256": profile_digest,
    }
    identity = hashlib.sha256(_canonical_json(identity_material).encode("utf-8")).hexdigest()

    if dropped:
        warnings.append(
            "options dropped as unservable by this host, which changes the realised "
            f"distribution and is reported rather than hidden: {sorted(dropped)}"
        )
    warnings.append(
        "catalogue-value options are authored from platform release history and are "
        "labelled in each option's note; they are not physical-device claims"
    )

    return {
        "profile": profile,
        "diagnostics": {
            "profile_id": f"apostate-{identity[:24]}",
            "identity": f"sha256:{identity}",
            "profile_sha256": profile_digest,
            "catalogue_version": catalogue["catalogue_version"],
            "profile_schema_version": PROFILE_SCHEMA_VERSION,
            "browser_build": browser_build,
            "platform": platform,
            "seed": seed,
            "anchor": {
                "id": anchor["id"],
                "platform": anchor["platform"],
                "backend": anchor["backend"],
                "vendor": anchor["vendor"],
                "rotation_status": anchor["rotation_status"],
                "evidence_class": anchor["evidence_class"],
            },
            "axes": chosen,
            "host": {key: host[key] for key in ("platform", "backend", "logical_cores",
                                                "total_bytes", "timezone", "languages")},
            # Per field, the value the profile carries and the layer that
            # decided it: "command-line", "geoip" or "host". A null value with
            # source "host" is the surface staying host-inherited, which is
            # what an absent `locale` section means. An operator debugging a
            # timezone that does not match their exit IP needs to know whether
            # GeoIP answered or was never consulted, and this is where that is
            # written down.
            "locale": locale_sources,
            "locale_source": locale_sources["accept_languages"]["source"],
            "timezone_source": locale_sources["timezone"]["source"],
            "timezone": (profile.get("locale") or {}).get("timezone"),
            "warnings": warnings,
        },
        "native_loader_support": {
            "profile_schema": "config/profile.schema.json",
            "profile_schema_valid": True,
            "unsupported_fields": [],
            "transport": catalogue["native_loader"]["transport"],
        },
    }


def resolve_with_diagnostics(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Compose a profile and return it with the resolution envelope."""
    return _resolve_internal(config, **overrides)


def resolve_profile(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Compose and return only the native profile payload."""
    return _resolve_internal(config, **overrides)["profile"]


def resolve(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Short alias returning only the schema-valid native profile payload."""
    return resolve_profile(config, **overrides)


def list_catalogue(path: Path | str | None = None) -> dict[str, Any]:
    catalogue, tables, _ = _load_catalogue(path)
    return {
        "catalogue_version": catalogue["catalogue_version"],
        "profile_schema_version": catalogue["profile_schema_version"],
        "browser_build": catalogue["browser_build"],
        "model": catalogue["model"],
        "anchors": [
            {
                "id": anchor["id"],
                "platform": anchor["platform"],
                "backend": anchor["backend"],
                "members": anchor["members"],
                "rotation_status": anchor["rotation_status"],
            }
            for anchor in catalogue["anchors"]
        ],
        "axes": [
            {
                "axis": axis,
                "selection": tables[axis]["selection"],
                "servability": tables[axis]["servability"],
                "conditioned_on": tables[axis]["conditioned_on"],
                "option_sets": len(tables[axis]["option_sets"]),
                "options": sum(len(s["options"]) for s in tables[axis]["option_sets"]),
                "evidence": sorted({option["evidence"]
                                    for s in tables[axis]["option_sets"]
                                    for option in s["options"]}),
            }
            for axis in AXES
        ],
        "policies": {kind: [entry["id"] for entry in catalogue["policies"][kind]]
                     for kind in _POLICY_KINDS},
    }


def _json_output(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--resolve", action="store_true", help="compose a profile")
    operations.add_argument("--list", action="store_true", help="list anchors and axes")
    operations.add_argument("--catalogue", action="store_true",
                            help="print the validated catalogue index")
    parser.add_argument("--catalogue-path", type=Path)
    parser.add_argument("--fingerprint")
    parser.add_argument("--fingerprint-platform")
    parser.add_argument("--anchor")
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--fingerprint-locale")
    parser.add_argument("--fingerprint-timezone")
    parser.add_argument("--locale-policy")
    parser.add_argument("--theme-policy")
    parser.add_argument("--browser-build")
    parser.add_argument("--host-platform")
    parser.add_argument("--host-backend")
    parser.add_argument("--host-logical-cores", type=int)
    parser.add_argument("--host-total-bytes", type=int)
    parser.add_argument("--host-timezone",
                        help="the host zone the locale surface inherits; probed when omitted")
    parser.add_argument("--host-languages",
                        help="the host language list the locale surface inherits")
    parser.add_argument("--geoip", type=Path,
                        help="a JSON GeoIP result for the effective egress, as "
                             "scripts/geoip.py emits; supplies locale and timezone "
                             "below the command line and above the host")
    parser.add_argument("--runtime-only", action="store_true",
                        help="emit only the native profile payload")
    args = parser.parse_args(argv)
    try:
        if args.catalogue:
            value: Any = load_catalogue(args.catalogue_path)
        elif args.list:
            value = list_catalogue(args.catalogue_path)
        else:
            geoip = _read_object(args.geoip, "geoip result") if args.geoip else None
            envelope = resolve_with_diagnostics({
                "fingerprint": args.fingerprint,
                "fingerprint_platform": args.fingerprint_platform,
                "anchor": args.anchor,
                "profile_file": args.profile_file,
                "fingerprint_locale": args.fingerprint_locale,
                "fingerprint_timezone": args.fingerprint_timezone,
                "locale_policy": args.locale_policy,
                "theme_policy": args.theme_policy,
                "browser_build": args.browser_build,
                "host_platform": args.host_platform,
                "host_backend": args.host_backend,
                "host_logical_cores": args.host_logical_cores,
                "host_total_bytes": args.host_total_bytes,
                "host_timezone": args.host_timezone,
                "host_languages": args.host_languages,
                "geoip": geoip,
                "catalogue_path": args.catalogue_path,
            })
            value = envelope["profile"] if args.runtime_only else envelope
        sys.stdout.write(_json_output(value))
        return 0
    except (ResolverError, OSError, TypeError, ValueError) as exc:
        sys.stderr.write(_json_output({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
