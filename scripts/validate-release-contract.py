#!/usr/bin/env python3
"""Validate Apostate's shared launch and release contract documents.

This checker intentionally uses only the Python standard library so a clean
checkout can validate a launch document or release manifest before packages or
build tooling are installed:

    python3 scripts/validate-release-contract.py --kind launch launch.json
    python3 scripts/validate-release-contract.py --kind manifest manifest.json

``--kind auto`` (the default) selects the manifest when a manifest field or a
filename containing ``manifest`` is present, and otherwise selects launch.
The process exits non-zero when any input is missing, malformed, or fails its
schema. A manifest passing this structural check still needs its artifact's
SHA-256 verified against ``sha256`` before extraction; build provenance is a
separate, keyless check via ``gh attestation verify``.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
import pathlib
import re
import sys
from typing import Any
from urllib.parse import urldefrag, urlparse


ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_PATHS = {
    "launch": ROOT / "config" / "launch.schema.json",
    "manifest": ROOT / "release" / "manifest.schema.json",
    "profile": ROOT / "config" / "profile.schema.json",
}

# JSON Schema annotations do not affect validation. x-precedence is ours: it
# records which input wins when several could supply the same value.
ANNOTATIONS = {
    "$comment",
    "$id",
    "$schema",
    "default",
    "deprecated",
    "description",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
    "x-precedence",
}

TYPE_NAMES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}

MANIFEST_FIELDS = {
    "package_version",
    "chromium_version",
    "catalogue_version",
    "platform",
    "artifact",
    "sha256",
    "source_revision",
    "patch_series_sha256",
    "build_manifest_sha256",
}
LAUNCH_FIELDS = {
    "fingerprint",
    "fingerprint_platform",
    "profile",
    "locale",
    "timezone",
    "geoip",
    "proxy",
    "headless",
    "user_data_dir",
    "args",
}


class ContractError(Exception):
    """Raised for an unreadable schema or an unsupported schema construct."""


def json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool-is-an-int equivalence."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    return left == right


def pointer(root: Any, fragment: str) -> Any:
    """Resolve a local JSON Pointer fragment."""

    if fragment in ("", "/"):
        return root
    if not fragment.startswith("/"):
        raise ContractError(f"unsupported $ref fragment '#{fragment}'")
    node = root
    for raw_part in fragment[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ContractError(f"$ref fragment not found: '#{fragment}'") from exc
    return node


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path} is not valid JSON: {exc}") from exc


def resolve_ref(
    reference: str,
    root: Any,
    base_path: pathlib.Path,
) -> tuple[Any, Any, pathlib.Path]:
    """Resolve local refs and repository-local external schema refs."""

    ref_uri, fragment = urldefrag(reference)
    if not ref_uri:
        return pointer(root, fragment), root, base_path

    parsed = urlparse(ref_uri)
    if parsed.scheme in {"http", "https"}:
        # The public schemas use stable GitHub IDs, but validation must remain
        # offline. Map only this repository's own public schema URL into the
        # checkout and reject all network refs.
        prefix = "https://github.com/apostate/apostate/"
        if not ref_uri.startswith(prefix):
            raise ContractError(f"external network $ref is not supported: {reference}")
        target = ROOT / ref_uri[len(prefix) :]
    elif parsed.scheme:
        raise ContractError(f"unsupported external $ref: {reference}")
    else:
        target = (base_path.parent / ref_uri).resolve()

    try:
        target.relative_to(ROOT)
    except ValueError as exc:
        raise ContractError(f"$ref escapes repository: {reference}") from exc
    external_root = load_json(target)
    return pointer(external_root, fragment), external_root, target


def type_matches(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPE_NAMES[expected])


def multiple_step(value: Any, path: str, node_path: str) -> Fraction:
    """Return a schema's ``multipleOf`` as an exact positive rational."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ContractError(f"{path}: {node_path} multipleOf must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ContractError(f"{path}: {node_path} multipleOf must be positive and finite")
    return Fraction(str(value))


def is_multiple_of(value: int | float, step: Fraction) -> bool:
    """Exact multiple test, because binary floating point cannot do this one.

    ``value % step`` is wrong for a decimal step. Of the 101 battery levels
    ``multipleOf: 0.01`` permits, 93 leave a nonzero remainder under ``%``
    (0.09 % 0.01 is 0.009999999999999995), and dividing instead fails the
    other way round (0.29 / 0.01 is 28.999999999999996). Both would reject a
    level the schema allows. ``str()`` gives the shortest decimal that
    round-trips the float, which is the literal the schema author wrote, so
    the rationals built from it divide exactly.
    """
    if not math.isfinite(value):
        return False
    return (Fraction(str(value)) / step).denominator == 1


def validate(
    value: Any,
    schema: dict[str, Any],
    root: Any,
    path: str,
    base_path: pathlib.Path,
    errors: list[str],
) -> None:
    """Validate the subset of Draft 2020-12 used by the public contracts."""

    if not isinstance(schema, dict):
        raise ContractError(f"{path}: schema node is not an object")

    if "$ref" in schema:
        target, target_root, target_base = resolve_ref(schema["$ref"], root, base_path)
        validate(value, target, target_root, path, target_base, errors)
        # A $ref replaces the current schema in Draft 2020-12. Any sibling
        # annotations in these contracts are intentionally ignored.
        return

    supported = {
        "$defs", "allOf", "anyOf", "const", "else", "enum", "if", "items",
        "maxItems", "maxLength", "maximum", "maxContains", "minItems",
        "minLength", "minimum", "minContains", "not", "oneOf", "pattern",
        "properties", "propertyNames", "required", "then", "type",
        "additionalProperties", "contains", "exclusiveMinimum", "exclusiveMaximum",
        "uniqueItems", "multipleOf",
    }
    unknown = set(schema) - supported - ANNOTATIONS
    if unknown:
        raise ContractError(f"{path}: unsupported schema keywords {sorted(unknown)}")

    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not all(isinstance(name, str) and name in TYPE_NAMES for name in names):
            raise ContractError(f"{path}: invalid type declaration {expected!r}")
        if not any(type_matches(value, name) for name in names):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return

    if "const" in schema and not json_equal(value, schema["const"]):
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")

    if "enum" in schema and not any(json_equal(value, item) for item in schema["enum"]):
        errors.append(f"{path}: {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matches = re.search(schema["pattern"], value)
            except (re.error, TypeError) as exc:
                raise ContractError(f"{path}: invalid schema pattern: {exc}") from exc
            if matches is None:
                errors.append(f"{path}: {value!r} does not match the required pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            errors.append(f"{path}: non-finite number is not valid JSON data")
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: not above exclusiveMinimum {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: not below exclusiveMaximum {schema['exclusiveMaximum']}")
        if "multipleOf" in schema:
            step = multiple_step(schema["multipleOf"], path, "schema")
            if not is_multiple_of(value, step):
                errors.append(f"{path}: {value!r} is not a multiple of {schema['multipleOf']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} item(s)")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items must be unique")
        if "items" in schema:
            item_schema = schema["items"]
            for index, item in enumerate(value):
                validate(item, item_schema, root, f"{path}[{index}]", base_path, errors)
        contains = schema.get("contains")
        if isinstance(contains, dict):
            matches = 0
            for item in value:
                branch_errors: list[str] = []
                validate(item, contains, root, f"{path}[]", base_path, branch_errors)
                if not branch_errors:
                    matches += 1
            minimum = schema.get("minContains", 1)
            maximum = schema.get("maxContains")
            if matches < minimum:
                errors.append(f"{path}: contains too few matching items")
            if maximum is not None and matches > maximum:
                errors.append(f"{path}: contains too many matching items")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property '{key}'")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ContractError(f"{path}: properties must be an object")
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], root, f"{path}.{key}", base_path, errors)
            elif additional is False:
                errors.append(f"{path}: unexpected property '{key}'")
            elif isinstance(additional, dict):
                validate(item, additional, root, f"{path}.{key}", base_path, errors)
        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for key in value:
                validate(key, property_names, root, f"{path} property name", base_path, errors)

    for branch in schema.get("allOf", []):
        validate(value, branch, root, path, base_path, errors)

    if "anyOf" in schema:
        matches = 0
        for branch in schema["anyOf"]:
            branch_errors: list[str] = []
            validate(value, branch, root, path, base_path, branch_errors)
            if not branch_errors:
                matches += 1
        if matches == 0:
            errors.append(f"{path}: does not satisfy anyOf")

    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            branch_errors = []
            validate(value, branch, root, path, base_path, branch_errors)
            if not branch_errors:
                matches += 1
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches; exactly one is required")

    if "not" in schema:
        branch_errors = []
        validate(value, schema["not"], root, path, base_path, branch_errors)
        if not branch_errors:
            errors.append(f"{path}: matches a forbidden schema")

    if "if" in schema:
        condition_errors: list[str] = []
        validate(value, schema["if"], root, path, base_path, condition_errors)
        selected = schema.get("then") if not condition_errors else schema.get("else")
        if selected is not None:
            validate(value, selected, root, path, base_path, errors)


def select_kind(requested: str, path: pathlib.Path, value: Any) -> str:
    if requested != "auto":
        return requested
    lowered = path.name.lower()
    if "manifest" in lowered:
        return "manifest"
    if isinstance(value, dict):
        keys = set(value)
        if keys & MANIFEST_FIELDS and not keys & LAUNCH_FIELDS:
            return "manifest"
    return "launch"


def parse_document(path_text: str) -> tuple[Any, pathlib.Path | None]:
    if path_text == "-":
        try:
            return json.load(sys.stdin), None
        except json.JSONDecodeError as exc:
            raise ContractError(f"stdin is not valid JSON: {exc}") from exc
    path = pathlib.Path(path_text)
    if not path.is_file():
        raise ContractError("no such file")
    return load_json(path), path


def validate_schema_document(path: pathlib.Path) -> None:
    """Check that repository-owned schema documents are structurally usable."""
    schema = load_json(path)
    if not isinstance(schema, dict):
        raise ContractError(f"{path}: schema root is not an object")

    def visit(node: Any, node_path: str, base_path: pathlib.Path) -> None:
        if not isinstance(node, dict):
            raise ContractError(f"{path}: {node_path} is not an object")
        if "$ref" in node:
            reference = node["$ref"]
            if not isinstance(reference, str):
                raise ContractError(f"{path}: {node_path} has a non-string $ref")
            target, _, target_path = resolve_ref(reference, schema, base_path)
            visit(target, f"{node_path}.$ref", target_path)
        expected = node.get("type")
        allowed_types = set(TYPE_NAMES)
        if isinstance(expected, str) and expected not in allowed_types:
            raise ContractError(f"{path}: {node_path} has unsupported type {expected!r}")
        if isinstance(expected, list) and any(item not in allowed_types for item in expected):
            raise ContractError(f"{path}: {node_path} has unsupported union type")
        required = node.get("required")
        if required is not None and (not isinstance(required, list) or any(not isinstance(item, str) for item in required)):
            raise ContractError(f"{path}: {node_path}.required must be a string array")
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                raise ContractError(f"{path}: {node_path}.properties must be an object")
            for name, child in properties.items():
                if not isinstance(name, str):
                    raise ContractError(f"{path}: {node_path}.properties has a non-string name")
                visit(child, f"{node_path}.properties[{name!r}]", base_path)
        for keyword in ("items", "additionalProperties", "contains", "propertyNames", "if", "then", "else", "not"):
            child = node.get(keyword)
            if isinstance(child, dict):
                visit(child, f"{node_path}.{keyword}", base_path)
            elif keyword == "additionalProperties" and child is not None and not isinstance(child, bool):
                raise ContractError(f"{path}: {node_path}.additionalProperties must be boolean or schema")
        for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
            children = node.get(keyword)
            if children is not None:
                if not isinstance(children, list):
                    raise ContractError(f"{path}: {node_path}.{keyword} must be an array")
                for index, child in enumerate(children):
                    visit(child, f"{node_path}.{keyword}[{index}]", base_path)
        pattern = node.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ContractError(f"{path}: {node_path}.pattern must be a string")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ContractError(f"{path}: {node_path}.pattern is invalid") from exc
        if "multipleOf" in node:
            # Checked here as well as against an instance, because
            # --check-schemas never walks into a document: a nonsense step
            # would otherwise sit in the schema until the first profile
            # carrying that field arrived.
            multiple_step(node["multipleOf"], str(path), node_path)

    visit(schema, "$", path)


def check_schemas() -> None:
    paths = [*SCHEMA_PATHS.values(), ROOT / "config" / "profile.schema.json"]
    for path in paths:
        validate_schema_document(path)
        print(f"ok    schema   {path.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-schemas",
        action="store_true",
        help="validate repository-owned schema structure without treating schemas as data documents",
    )
    parser.add_argument(
        "--kind",
        "--type",
        "--schema",
        choices=("auto", "launch", "manifest", "profile"),
        default="auto",
        help="contract schema to use (default: infer from filename/content)",
    )
    parser.add_argument("documents", nargs="*", metavar="JSON")
    args = parser.parse_args(argv)
    if args.check_schemas:
        try:
            check_schemas()
        except (ContractError, KeyError, TypeError, ValueError) as exc:
            print(f"FAIL  schema: {exc}")
            return 1
        return 0
    if not args.documents:
        parser.error("at least one JSON document is required unless --check-schemas is used")

    failures = 0
    for document_text in args.documents:
        display_path = document_text
        try:
            value, path = parse_document(document_text)
            kind = select_kind(args.kind, path or pathlib.Path("stdin.json"), value)
            schema_path = SCHEMA_PATHS[kind]
            schema = load_json(schema_path)
            schema_errors: list[str] = []
            validate(value, schema, schema, "$", schema_path, schema_errors)
            if schema_errors:
                failures += 1
                print(f"FAIL  {kind:8} {display_path}")
                for error in schema_errors:
                    print(f"        {error}")
            else:
                print(f"ok    {kind:8} {display_path}")
        except (ContractError, KeyError, TypeError, ValueError) as exc:
            failures += 1
            print(f"FAIL  {display_path}: {exc}")

    valid = len(args.documents) - failures
    print(f"{valid}/{len(args.documents)} contract document(s) valid")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
