#!/usr/bin/env python3
"""Build a GPU family (an anchor, `corpus/anchors/*.json`) from captures.

Takes one or more admitted captures of one GPU, refuses them unless they share
one WebGL1 and WebGL2 capability table and one render digest, and writes the
`apostate/corpus/anchor/1` file. Every field comes from the captures,
`build/CHROMIUM_VERSION` and the other anchors in `--anchors-dir`, except
prose no capture holds (`--extra`) and `host_architecture`
(`--host-architecture`). `--relink` rewrites the sections of the other anchors
that name this one; `anchor_sha256` covers neither.

    scripts/build-anchor.py resources/fingerprints/raw/CAPTURE.json [...]
        [--host-architecture arm|x86|auto] [--write | --out PATH] [--relink]

Without `--write` or `--out` it prints the anchor, as canonical JSON (sorted
keys, no spaces) plus a newline.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "apostate/corpus/anchor/1"
DEFAULT_ANCHORS_DIR = ROOT / "corpus" / "anchors"
DEFAULT_VERSION_FILE = ROOT / "build" / "CHROMIUM_VERSION"
ADMISSIONS_DIR = ROOT / "resources" / "fingerprints" / "raw" / "admissions"

# The WebGL probe's fields, by role. A field this script does not classify is a
# hard error: silently leaving a new capability out of the cluster would give
# two different GPUs one digest.
WEBGL_CLUSTER_FIELDS = ("antialiasSamples", "contextAttributes", "extensions", "parameters", "precision")
WEBGL_IDENTITY_FIELDS = ("renderer", "shadingLanguageVersion", "unmaskedRenderer", "unmaskedVendor",
                         "vendor", "version")
WEBGL_RENDER_FIELDS = ("pixels_base64", "pixels_sha256")
# The parameters that read back the identity strings. They are identity, not
# capability, so they leave the cluster and stay in the member's identity.
WEBGL_IDENTITY_PARAMETERS = ("RENDERER", "SHADING_LANGUAGE_VERSION", "VENDOR", "VERSION")
IDENTITY_PARAMETER_FIELDS = {"RENDERER": "renderer", "SHADING_LANGUAGE_VERSION": "shadingLanguageVersion",
                             "VENDOR": "vendor", "VERSION": "version"}
# GPUAdapterInfo: `device` and `description` name the card and are identity;
# the rest is capability.
WEBGPU_INFO_CLUSTER_FIELDS = ("architecture", "subgroupMaxSize", "subgroupMinSize", "vendor")
WEBGPU_INFO_IDENTITY_FIELDS = ("description", "device")
WEBGPU_ADAPTER_FIELDS = ("features", "info", "limits")

DIGESTS = ("webgl1_caps_sha256", "webgl2_caps_sha256", "webgl1_pixels_sha256", "webgl2_pixels_sha256")
CROSS_BACKEND_DIGESTS = ("webgl1_caps_sha256", "webgl1_pixels_sha256", "webgl2_caps_sha256")
SOFTWARE_RENDERERS = re.compile(r"SwiftShader|llvmpipe|softpipe|Microsoft Basic Render", re.IGNORECASE)
PLATFORMS = {"Windows": "Windows", "macOS": "macOS", "Linux": "Linux"}
ARCHITECTURES = ("arm", "x86")

CANVAS_NOTE = ("Canvas 2D is a font and raster measurement, not a GPU measurement, and is excluded "
               "from the anchor. Recorded so the exclusion can be checked.")
WEBGPU_SPLIT_NOTE = ("WebGPU is not uniform across this anchor's members and is therefore not rotatable "
                     "within it: each member's WebGPU cluster belongs to that member's host driver "
                     "stack. The WebGL capability and render digests, which are the grouping key, did "
                     "match.")
SINGLE_MEMBER_REASON = ("This anchor has one member, so there is no measured second card whose identity "
                        "strings it may present. Any other renderer string on this capability cluster "
                        "is an assertion, not a measurement.")
MEASURED_SAFE_REASON = ("Every member of this anchor produced the same WebGL1 capability digest, the same "
                        "WebGL2 capability digest and the same readback render digest, so presenting one "
                        "member's identity strings on another member's capture is backed by measurement "
                        "rather than assumed.")


class AnchorError(Exception):
    """The captures cannot form one anchor, or an input is malformed."""


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def encode_anchor(anchor: Mapping[str, Any]) -> str:
    """The bytes a committed anchor file holds."""
    return canon(anchor) + "\n"


def _load_decomposer():
    spec = importlib.util.spec_from_file_location("decompose_capture", ROOT / "scripts" / "decompose-capture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_release_pin(path: Path = DEFAULT_VERSION_FILE) -> str:
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != 1:
        raise AnchorError(f"{path} must hold exactly one Chromium version")
    return values[0]


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def build_binding(version: str, release_pin: str) -> str:
    if version == release_pin:
        return "release"
    return "same-major" if _major(version) == _major(release_pin) else "off-major"


def build_caveat(versions: Sequence[str], bindings: Sequence[str], release_pin: str) -> str | None:
    if set(bindings) == {"release"}:
        return None
    measured = ", ".join(versions)
    if "off-major" in bindings:
        return (f"Measured on Chromium {measured} against release pin {release_pin}. Capability tables "
                "are build-bound, so equality of this cluster to a pinned-build capture is not "
                "established by this evidence.")
    return (f"Measured on Chromium {measured} against release pin {release_pin}: same major, different "
            "patch. Version-bearing fields will differ from a pinned-build reference and a V3 run must "
            "report that separately.")


def _probe_value(capture: Mapping[str, Any], section: str, probe: str, where: str) -> Any:
    record = (capture.get(section) or {}).get(probe)
    if not isinstance(record, dict) or not record.get("ok"):
        raise AnchorError(f"{where}: {section}.{probe} is missing or did not complete")
    return record.get("value")


def _check_fields(value: Mapping[str, Any], known: Iterable[str], where: str) -> None:
    unknown = sorted(set(value) - set(known))
    if unknown:
        raise AnchorError(f"{where} has fields this script does not classify as capability or "
                          f"identity: {unknown}")


def webgl_cluster(value: Mapping[str, Any], where: str) -> dict[str, Any]:
    """The capability part of a WebGL probe: everything but identity and pixels."""
    _check_fields(value, WEBGL_CLUSTER_FIELDS + WEBGL_IDENTITY_FIELDS + WEBGL_RENDER_FIELDS, where)
    cluster = {field: value[field] for field in WEBGL_CLUSTER_FIELDS if field in value}
    missing = sorted(set(WEBGL_CLUSTER_FIELDS) - set(cluster))
    if missing:
        raise AnchorError(f"{where} is missing {missing}")
    cluster["parameters"] = {name: parameter for name, parameter in value["parameters"].items()
                             if name not in WEBGL_IDENTITY_PARAMETERS}
    return cluster


def webgl_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    identity = {field: value.get(field) for field in WEBGL_IDENTITY_FIELDS}
    identity["parameters"] = {name: value["parameters"].get(name) for name in WEBGL_IDENTITY_PARAMETERS}
    return identity


def identity_parameters_agree(identity: Mapping[str, Any]) -> bool:
    return all(identity["parameters"][name] == identity[field]
               for name, field in IDENTITY_PARAMETER_FIELDS.items())


def webgpu_cluster(value: Mapping[str, Any], where: str) -> dict[str, Any]:
    """The WebGPU probe without the adapter's identity strings."""
    _check_fields(value, ("adapters", "preferredCanvasFormat"), where)
    adapters: dict[str, Any] = {}
    for name, adapter in (value.get("adapters") or {}).items():
        if adapter is None:
            adapters[name] = None
            continue
        _check_fields(adapter, WEBGPU_ADAPTER_FIELDS, f"{where}.adapters.{name}")
        info = adapter.get("info") or {}
        _check_fields(info, WEBGPU_INFO_CLUSTER_FIELDS + WEBGPU_INFO_IDENTITY_FIELDS,
                      f"{where}.adapters.{name}.info")
        entry = {field: info[field] for field in WEBGPU_INFO_CLUSTER_FIELDS if field in info}
        entry["features"] = adapter.get("features")
        entry["limits"] = adapter.get("limits")
        adapters[name] = entry
    return {"adapters": adapters, "preferredCanvasFormat": value.get("preferredCanvasFormat")}


def renderer_facts(renderer: str, vendor: str) -> tuple[str, str, str | None]:
    """Backend, device name and PCI device id from an ANGLE renderer string."""
    if "SwiftShader" in renderer:
        backend = "ANGLE/SwiftShader"
    elif renderer.endswith(", D3D11)"):
        backend = "ANGLE/D3D11"
    elif renderer.endswith(", D3D9)"):
        backend = "ANGLE/D3D9"
    elif "Metal Renderer" in renderer:
        backend = "ANGLE/Metal"
    elif ", Vulkan " in renderer:
        backend = "ANGLE/Vulkan"
    elif "OpenGL" in renderer:
        backend = "ANGLE/OpenGL"
    else:
        raise AnchorError(f"cannot tell the graphics backend from renderer {renderer!r}")

    metal = re.match(r"^ANGLE \([^,]+, ANGLE Metal Renderer: (?P<device>[^,]+),", renderer)
    if metal:
        return backend, metal.group("device"), None
    vulkan = re.match(r"^ANGLE \([^,]+, Vulkan [0-9.]+ \((?P<device>.+) \(0x(?P<pci>[0-9A-Fa-f]+)\)\), ",
                      renderer)
    if vulkan:
        device = vulkan.group("device")
        # ANGLE's Vulkan string puts the vendor name before the driver's device
        # name, which for NVIDIA already starts with it.
        if device.startswith(vendor + " "):
            device = device[len(vendor) + 1:]
        return backend, device, vulkan.group("pci").lower()
    other = re.match(r"^ANGLE \([^,]+, (?P<device>.+?) \(0x(?P<pci>[0-9A-Fa-f]+)\)", renderer)
    if other:
        return backend, other.group("device"), other.group("pci").lower()
    raise AnchorError(f"cannot read a device name from renderer {renderer!r}")


def _browser_version(user_agent_data: Mapping[str, Any], where: str) -> str:
    high = user_agent_data.get("high") or {}
    for entry in high.get("fullVersionList") or []:
        if entry.get("brand") in ("Chromium", "Google Chrome"):
            return entry["version"]
    version = high.get("uaFullVersion")
    if not isinstance(version, str):
        raise AnchorError(f"{where}: navigator.userAgentData has no full browser version")
    return version


def _is_admitted(raw: bytes, capture: Any, release_pin: str) -> str | None:
    """None when the capture is admitted, else the reason it is not.

    A capture with no admission record is checked against the major of
    `release_pin`, not of build/CHROMIUM_VERSION, so an anchor built against an
    earlier pin can still be reproduced after a Chromium update.
    """
    digest = hashlib.sha256(raw).hexdigest()
    record = ADMISSIONS_DIR / f"{digest}.json"
    if record.is_file():
        decision = json.loads(record.read_text(encoding="utf-8"))
        if decision.get("raw_sha256") == digest and decision.get("decision") == "accepted":
            return None
        return f"its admission record says {decision.get('decision')!r}: {decision.get('reason')}"
    decomposer = _load_decomposer()
    decomposer.EXPECTED_BROWSER_MAJOR = int(_major(release_pin))
    return decomposer.admission_rejection_reason(capture)


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_member(path: Path, release_pin: str, allow_unadmitted: bool = False) -> dict[str, Any]:
    """Everything the anchor needs from one capture."""
    raw = path.read_bytes()
    capture = json.loads(raw)
    where = path.name
    refusal = _is_admitted(raw, capture, release_pin)
    if refusal is not None and not allow_unadmitted:
        raise AnchorError(f"{where} is not an admitted capture: {refusal}")

    probes: dict[str, Any] = {}
    for section in ("probes", "repeat"):
        values = {}
        for context in ("webgl1", "webgl2"):
            value = _probe_value(capture, section, context, where)
            values[f"{context}_caps"] = sha256_of(webgl_cluster(value, f"{where} {section}.{context}"))
            values[f"{context}_pixels"] = value["pixels_sha256"]
            values[f"{context}_value"] = value
        webgpu = _probe_value(capture, section, "webgpu", where)
        values["webgpu_cluster"] = webgpu_cluster(webgpu, f"{where} {section}.webgpu")
        values["webgpu_sha256"] = sha256_of(values["webgpu_cluster"])
        probes[section] = values
    first, second = probes["probes"], probes["repeat"]

    webgl1, webgl2 = first["webgl1_value"], first["webgl2_value"]
    vendor_match = re.fullmatch(r"Google Inc\. \((?P<vendor>.+)\)", webgl1["unmaskedVendor"] or "")
    if vendor_match is None:
        raise AnchorError(f"{where}: cannot read a GPU vendor from {webgl1['unmaskedVendor']!r}")
    vendor = vendor_match.group("vendor")
    backend, device, pci_id = renderer_facts(webgl1["unmaskedRenderer"], vendor)

    user_agent_data = _probe_value(capture, "probes", "navigator.userAgentData", where)
    platform = PLATFORMS.get((user_agent_data.get("high") or {}).get("platform")
                             or (user_agent_data.get("low") or {}).get("platform"))
    if platform is None:
        raise AnchorError(f"{where}: navigator.userAgentData names no known platform")
    version = _browser_version(user_agent_data, where)
    canvas = _probe_value(capture, "probes", "canvas.2d", where)

    identity = {"webgl1": webgl_identity(webgl1), "webgl2": webgl_identity(webgl2)}
    member = {
        "browser_version": version,
        "build_binding": build_binding(version, release_pin),
        "canvas_pixels_sha256": canvas["pixels_sha256"],
        "capture": _display_path(path),
        "capture_sha256": hashlib.sha256(raw).hexdigest(),
        "device": device,
        "identity": identity,
        "identity_parameters_agree": {context: identity_parameters_agree(identity[context])
                                      for context in ("webgl1", "webgl2")},
        "label": (capture.get("context") or {}).get("label"),
        "pci_id": pci_id,
        "repeat_stable": {field: first[field] == second[field]
                          for field in ("webgl1_caps", "webgl1_pixels", "webgl2_caps", "webgl2_pixels")},
        "taken_at": (capture.get("context") or {}).get("taken_at"),
        "webgpu_cluster_sha256": first["webgpu_sha256"],
    }
    member["repeat_stable"]["webgpu_cluster"] = first["webgpu_sha256"] == second["webgpu_sha256"]
    return {
        "member": member,
        "basename": path.name,
        "platform": platform,
        "backend": backend,
        "vendor": vendor,
        "architecture": (user_agent_data.get("high") or {}).get("architecture"),
        "software": bool(SOFTWARE_RENDERERS.search(webgl1["unmaskedRenderer"])),
        "webgl1_cluster": webgl_cluster(webgl1, f"{where} probes.webgl1"),
        "webgl2_cluster": webgl_cluster(webgl2, f"{where} probes.webgl2"),
        "digests": {
            "webgl1_caps_sha256": first["webgl1_caps"],
            "webgl2_caps_sha256": first["webgl2_caps"],
            "webgl1_pixels_sha256": first["webgl1_pixels"],
            "webgl2_pixels_sha256": first["webgl2_pixels"],
        },
        "webgpu_cluster": first["webgpu_cluster"],
    }


def _comparison(name: str, values_by_member: Mapping[str, str], note: str | None) -> dict[str, Any]:
    distinct = sorted(set(values_by_member.values()))
    entry: dict[str, Any] = {"digest": name, "distinct_values": len(distinct), "matched": len(distinct) == 1}
    if len(distinct) == 1:
        entry["value"] = distinct[0]
    else:
        entry["values_by_member"] = dict(sorted(values_by_member.items()))
    if note is not None:
        entry["note"] = note
    return entry


def _confounded_by_build(this: Sequence[str], other: Sequence[str]) -> str | None:
    if list(this) == list(other):
        return None
    ours, theirs = ", ".join(this), ", ".join(other)
    if {_major(v) for v in this} != {_major(v) for v in other}:
        return (f"The two anchors were measured on different Chromium majors ({ours} against {theirs}), "
                "so the measured inequality is real but its cause is not isolated to the graphics backend.")
    return (f"The two anchors were measured on different Chromium patch builds ({ours} against {theirs}), "
            "so the measured inequality is real but a build difference is not excluded as part of its cause.")


def cross_backend(anchor: Mapping[str, Any], peers: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """How this anchor's WebGL digests compare with every other anchor's."""
    entries = []
    for peer in sorted(peers, key=lambda p: p["anchor_id"]):
        if peer["anchor_id"] == anchor["anchor_id"]:
            continue
        if all(peer["digests"][name] == anchor["digests"][name] for name in DIGESTS):
            raise AnchorError(f"{anchor['anchor_id']} and {peer['anchor_id']} have the same WebGL digests, "
                              "so they are one anchor")
        entry: dict[str, Any] = {
            "anchor_id": peer["anchor_id"],
            "backend": peer["backend"],
            "evidence": {name: {"matched": peer["digests"][name] == anchor["digests"][name],
                                "other": peer["digests"][name], "this": anchor["digests"][name]}
                         for name in CROSS_BACKEND_DIGESTS},
            "platform": peer["platform"],
            "status": "asserted_not_measured",
        }
        confounded = _confounded_by_build(anchor["browser_versions"], peer["browser_versions"])
        if confounded is not None:
            entry["confounded_by_build"] = confounded
        if peer["backend"] == anchor["backend"]:
            entry["reason"] = (f"This anchor and {peer['anchor_id']} share a graphics backend but not a "
                               "capability cluster, so their identity strings are not interchangeable.")
        else:
            entry["reason"] = (f"Presenting this anchor's identity strings on the {peer['backend']} "
                               "capability cluster is not supported by measurement: the two clusters' "
                               "WebGL digests differ, so the swap would advertise a capability surface "
                               "that was never measured behind that name.")
        entries.append(entry)
    return entries


def render_digest_discrimination(anchor: Mapping[str, Any], peers: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    pixels = anchor["digests"]["webgl1_pixels_sha256"]
    shared = sorted(peer["anchor_id"] for peer in peers
                    if peer["anchor_id"] != anchor["anchor_id"]
                    and peer["digests"]["webgl1_pixels_sha256"] == pixels)
    if shared:
        note = ("The collector's WebGL scene is an analytic mediump gradient, so implementations that agree "
                "on it to 8 bits produce one digest. This render digest is shared with "
                f"{', '.join(shared)}, whose capability tables differ, so the render digest alone does not "
                "separate these anchors: the WebGL capability digests do.")
    else:
        note = "No other anchor produced this render digest."
    return {"also_produced_by": shared, "discriminating": not shared, "note": note,
            "webgl1_pixels_sha256": pixels}


def relink(anchor: dict[str, Any], peers: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Recompute the two sections that name other anchors."""
    peers = list(peers)
    anchor["identity_rotation"]["cross_backend"] = cross_backend(anchor, peers)
    anchor["render_digest_discrimination"] = render_digest_discrimination(anchor, peers)
    return anchor


def build_anchor(capture_paths: Sequence[Path], peers: Sequence[Mapping[str, Any]], release_pin: str,
                 evidence_class: str | None = None, evidence_tier: str = "T0",
                 host_architecture: str | None = None, extra: Mapping[str, Any] | None = None,
                 allow_unadmitted: bool = False) -> dict[str, Any]:
    """The anchor the captures form, compared against `peers`."""
    if not capture_paths:
        raise AnchorError("no captures given")
    read = sorted((read_member(Path(p), release_pin, allow_unadmitted) for p in capture_paths),
                  key=lambda r: r["member"]["capture"])
    if len({r["basename"] for r in read}) != len(read):
        raise AnchorError("two captures share a file name")
    first = read[0]
    for other in read[1:]:
        for field in ("platform", "backend", "vendor"):
            if other[field] != first[field]:
                raise AnchorError(f"{other['basename']} has {field} {other[field]!r} where "
                                  f"{first['basename']} has {first[field]!r}")
        for name in DIGESTS:
            if other["digests"][name] != first["digests"][name]:
                raise AnchorError(f"{other['basename']} and {first['basename']} differ on {name}, so they are "
                                  "not one GPU family")

    digests = dict(first["digests"])
    key_sha256 = sha256_of({name: digests[name] for name in DIGESTS})
    backend_token = first["backend"].split("/")[-1].lower()
    anchor_id = f"{first['platform'].lower()}-{backend_token}-{first['vendor'].lower()}-{key_sha256[:12]}"

    variants: dict[str, dict[str, Any]] = {}
    for r in read:
        sha = r["member"]["webgpu_cluster_sha256"]
        variant = variants.setdefault(sha, {"cluster": r["webgpu_cluster"], "members": [], "sha256": sha})
        variant["members"].append(r["basename"])
    webgpu = {"uniform": len(variants) == 1,
              "variants": [dict(variants[sha], members=sorted(variants[sha]["members"]))
                           for sha in sorted(variants)]}
    capability_cluster = {
        "render": {"webgl1_pixels_sha256": digests["webgl1_pixels_sha256"],
                   "webgl2_pixels_sha256": digests["webgl2_pixels_sha256"]},
        "webgl1": first["webgl1_cluster"],
        "webgl2": first["webgl2_cluster"],
        "webgpu": webgpu,
    }
    digests["webgpu_cluster_sha256"] = sorted(variants)

    members = [r["member"] for r in read]
    by_member = {name: {r["basename"]: r["digests"][name] for r in read} for name in DIGESTS}
    comparisons = [_comparison(name, by_member[name], None) for name in DIGESTS]
    webgpu_by_member = {r["basename"]: r["member"]["webgpu_cluster_sha256"] for r in read}
    webgpu_comparison = _comparison("webgpu_cluster_sha256", webgpu_by_member, None)
    if not webgpu_comparison["matched"]:
        webgpu_comparison["note"] = WEBGPU_SPLIT_NOTE
    comparisons.append(webgpu_comparison)
    comparisons.append(_comparison("canvas_pixels_sha256",
                                   {r["basename"]: r["member"]["canvas_pixels_sha256"] for r in read},
                                   CANVAS_NOTE))

    versions = sorted({m["browser_version"] for m in members})
    bindings = sorted({m["build_binding"] for m in members})
    software = any(r["software"] for r in read)
    anchor: dict[str, Any] = {
        "anchor_id": anchor_id,
        "anchor_key_sha256": key_sha256,
        "anchor_sha256": sha256_of(capability_cluster),
        "backend": first["backend"],
        "browser_versions": versions,
        "build_bindings": bindings,
        "capability_cluster": capability_cluster,
        "digests": digests,
        "equivalence": {
            "all_digests_matched": all(c["matched"] for c in comparisons),
            "comparisons": comparisons,
            "grouping_digests": list(DIGESTS),
            "matched": True,
            "member_count": len(members),
        },
        "evidence_class": evidence_class or ("compatibility-capture" if software else "physical-ground-truth"),
        "evidence_tier": evidence_tier,
        "identity_rotation": {
            "cross_backend": [],
            "within_anchor": {
                "member_count": len(members),
                "reason": SINGLE_MEMBER_REASON if len(members) == 1 else MEASURED_SAFE_REASON,
                "rotatable_strings": [{"capture": r["basename"], "device": r["member"]["device"],
                                       "webgl1": r["member"]["identity"]["webgl1"],
                                       "webgl2": r["member"]["identity"]["webgl2"]} for r in read],
                "status": "single-member" if len(members) == 1 else "measured-safe",
            },
        },
        "members": members,
        "platform": first["platform"],
        "release_pin": release_pin,
        "render_digest_discrimination": {},
        "schema": SCHEMA,
        "vendor": first["vendor"],
    }
    caveat = build_caveat(versions, bindings, release_pin)
    if caveat is not None:
        anchor["build_caveat"] = caveat

    if host_architecture == "auto":
        measured = {r["architecture"] for r in read}
        if len(measured) != 1 or not measured <= set(ARCHITECTURES):
            raise AnchorError(f"the captures report CPU architectures {sorted(map(str, measured))}; "
                              "pass --host-architecture explicitly")
        host_architecture = measured.pop()
    if host_architecture is not None:
        if host_architecture not in ARCHITECTURES:
            raise AnchorError(f"host_architecture must be one of {list(ARCHITECTURES)}")
        disagreeing = sorted(r["basename"] for r in read if r["architecture"] != host_architecture)
        if disagreeing:
            raise AnchorError(f"{', '.join(disagreeing)} did not report CPU architecture {host_architecture!r}")
        anchor["host_architecture"] = host_architecture

    for field, value in (extra or {}).items():
        if field in anchor:
            raise AnchorError(f"--extra may add fields, not replace the derived {field!r}")
        anchor[field] = value

    return relink(anchor, peers)


def load_anchors(directory: Path) -> list[dict[str, Any]]:
    anchors = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("schema") == SCHEMA:
            record["__path"] = path
            anchors.append(record)
    return anchors


def _strip(anchor: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in anchor.items() if key != "__path"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", nargs="*", type=Path, help="admitted captures of one GPU family")
    parser.add_argument("--anchors-dir", type=Path, default=DEFAULT_ANCHORS_DIR,
                        help="the other anchors, compared against in the cross-backend and render sections")
    parser.add_argument("--release-pin", help="the Chromium release pin (default: build/CHROMIUM_VERSION)")
    parser.add_argument("--evidence-class", choices=("physical-ground-truth", "compatibility-capture"),
                        help="default: compatibility-capture for a software renderer, else physical-ground-truth")
    parser.add_argument("--evidence-tier", default="T0")
    parser.add_argument("--host-architecture", choices=(*ARCHITECTURES, "auto"),
                        help="the CPU family of the measured machines; auto reads it from the captures")
    parser.add_argument("--extra", type=Path,
                        help="a JSON object of top-level fields no capture holds, such as software_anchor_policy")
    parser.add_argument("--allow-unadmitted", action="store_true",
                        help="build from a capture that has no accepted admission and fails the admission checks")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--out", type=Path, help="write the anchor here")
    output.add_argument("--write", action="store_true", help="write the anchor to --anchors-dir/<anchor_id>.json")
    parser.add_argument("--relink", action="store_true",
                        help="rewrite the cross-backend and render sections of every anchor in --anchors-dir")
    args = parser.parse_args(argv)

    try:
        release_pin = args.release_pin or read_release_pin()
        peers = load_anchors(args.anchors_dir)
        written: dict[str, Any] | None = None
        if args.captures:
            extra = json.loads(args.extra.read_text(encoding="utf-8")) if args.extra else None
            if extra is not None and not isinstance(extra, dict):
                raise AnchorError("--extra must hold a JSON object")
            written = build_anchor(args.captures, [p for p in peers], release_pin,
                                   evidence_class=args.evidence_class, evidence_tier=args.evidence_tier,
                                   host_architecture=args.host_architecture, extra=extra,
                                   allow_unadmitted=args.allow_unadmitted)
            peers = [p for p in peers if p["anchor_id"] != written["anchor_id"]]
            encoded = encode_anchor(written)
            if args.write or args.out:
                target = args.out or args.anchors_dir / f"{written['anchor_id']}.json"
                target.write_text(encoded, encoding="utf-8")
                print(f"wrote {target}", file=sys.stderr)
            else:
                sys.stdout.write(encoded)
        elif not args.relink:
            parser.error("give at least one capture, or --relink")

        if args.relink:
            everyone = [_strip(p) for p in peers] + ([written] if written is not None else [])
            for peer in peers:
                refreshed = relink(_strip(peer), everyone)
                encoded = encode_anchor(refreshed)
                if peer["__path"].read_text(encoding="utf-8") != encoded:
                    peer["__path"].write_text(encoded, encoding="utf-8")
                    print(f"relinked {peer['__path']}", file=sys.stderr)
    except AnchorError as exc:
        print(f"build-anchor: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
