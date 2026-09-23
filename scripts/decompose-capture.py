#!/usr/bin/env python3
"""Decompose a T0 capture into orthogonal, independently permutable blocks.

The corpus is not a set of monolithic profiles. A monolith can only ever be
replayed as itself, so a thousand sessions of one capture emit one fingerprint
and collapse into a single attributable cluster. But the alternative usually
reached for — synthesising values from a PRNG — corrupts engine invariants:
altering low-order canvas bits fails bitstream verification, and scaling a
viewport by an arbitrary float desynchronises Chromium's 60-to-1 subpixel
quantisation so getBoundingClientRect and getClientRects disagree.

This takes the third path. A capture is cut into blocks along the seams where
real hardware is genuinely configurable, and profiles are composed by combining
blocks that came from real machines. Every value shipped was measured on
silicon; the only thing invented is the *combination*, and only across seams
where the combination also exists in the world.

Blocks, and why each seam is real:

  platform   OS identity and everything the OS build alone determines — fonts,
             speech voices, codecs, wasm, the API surface, audio render, system
             colours. Not configurable at purchase; changes only with the OS.
  gpu        Renderer and vendor strings *together with* the capability tables
             and the rendered output they produce. Atomic on purpose: the string
             never travels without the table that belongs to it, so we never
             assert two GPUs are interchangeable.
  display    Screen tuple, DPR, colour gamut, HDR, built-in capture devices.
             Real because one GPU ships in several chassis, and any machine can
             drive an external panel.
  hardware   Core count, installed memory, audio buffer. Real because these are
             chosen at purchase for an otherwise identical machine.
  locale     Timezone, languages, keyboard layout. Real because it is the user's
             setting, not the device's.

Usage:
    scripts/decompose-capture.py CAPTURE.json --out DIR
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys

EXPECTED_CAPTURE_VERSION = 2
EXPECTED_BROWSER_MAJOR = 152
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
HEADLESS_MARKER = re.compile(r"headless", re.IGNORECASE)
FOREIGN_UA_MARKER = re.compile(
    r"(?:Edg|Edge|OPR|Opera|Brave|Vivaldi|YaBrowser|FxiOS|Firefox|CriOS|Electron)/",
    re.IGNORECASE,
)
DETERMINISTIC_PROBES = (
    "navigator.scalars", "navigator.userAgentData", "navigator.plugins",
    "screen.geometry", "intl.locale", "canvas.2d", "canvas.toDataURL_variants",
    "webgl1", "webgl2", "webgpu", "audio.offline_render", "clientrects",
    "fonts.detected", "fonts.query_api", "css.system", "css.media",
    "api.surface", "native_code.toString", "codecs.media",
    "webrtc.capabilities", "media.devices", "permissions.states",
    "keyboard.layout", "touch", "wasm", "math.precision", "headers.echo",
    "headers.echo_worker", "screen.details", "fonts.metrics", "eme.keysystems",
    "worker.parity", "prototype.shape", "chrome.object", "error.stack",
    "storage.persist",
)
PROBE_FIELDS = {"ok", "value", "error", "encoding", "duration_ms"}
CONTEXT_FIELDS = {
    "taken_at", "label", "ua", "collector_sha256", "device_pixel_ratio",
    "secure_context", "headed", "automation_suspected", "automation_signals",
    "notes",
}
ADMISSION_DIRNAME = "admissions"


class CaptureAdmissionError(ValueError):
    """A capture failed a corpus-admission requirement."""


def _version_major(value):
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d+)(?:\.|$)", value)
    return int(match.group(1)) if match else None


def _is_grease_brand(name):
    return isinstance(name, str) and name.startswith("Not") and "Brand" in name


def _validate_brand_list(brands, where):
    if not isinstance(brands, list) or not brands:
        raise CaptureAdmissionError("browser identity missing %s brands" % where)
    standard = []
    for entry in brands:
        if not isinstance(entry, dict) or not isinstance(entry.get("brand"), str):
            raise CaptureAdmissionError("invalid browser brand entry in %s" % where)
        name = entry["brand"]
        if HEADLESS_MARKER.search(name):
            raise CaptureAdmissionError("browser identity contains a Headless marker")
        if name in ("Chromium", "Google Chrome"):
            major = _version_major(entry.get("version"))
            if major is None:
                raise CaptureAdmissionError("browser brand %s has no version" % name)
            standard.append((name, major))
        elif not _is_grease_brand(name):
            raise CaptureAdmissionError("browser identity includes foreign brand %s" % name)
    if not standard:
        raise CaptureAdmissionError("browser identity has no Chromium or Google Chrome brand")
    for name, major in standard:
        if major != EXPECTED_BROWSER_MAJOR:
            raise CaptureAdmissionError(
                "browser brand %s is Chromium %s; release requires Chromium %s"
                % (name, major, EXPECTED_BROWSER_MAJOR)
            )


def validate_context(context):
    """Validate the provenance fields that are required for corpus admission."""
    if not isinstance(context, dict):
        raise CaptureAdmissionError("capture context must be an object")
    unknown = set(context) - CONTEXT_FIELDS
    if unknown:
        raise CaptureAdmissionError("unexpected context fields: %s" % ", ".join(sorted(unknown)))
    required = ("taken_at", "ua", "collector_sha256", "secure_context",
                "automation_suspected", "automation_signals")
    missing = [key for key in required if key not in context]
    if missing:
        raise CaptureAdmissionError("context missing required field(s): %s" % ", ".join(missing))
    taken_at = context["taken_at"]
    if not isinstance(taken_at, str) or "T" not in taken_at:
        raise CaptureAdmissionError("context.taken_at must be an RFC3339 date-time")
    try:
        parsed = datetime.datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise CaptureAdmissionError("context.taken_at must include a timezone")
    if not isinstance(context["ua"], str):
        raise CaptureAdmissionError("context.ua must be a string")
    collector = context["collector_sha256"]
    if not isinstance(collector, str) or not HEX64.fullmatch(collector):
        raise CaptureAdmissionError("context.collector_sha256 must be 64 hexadecimal characters")
    if context["secure_context"] is not True:
        raise CaptureAdmissionError("context.secure_context must be true")
    if context["automation_suspected"] is not False:
        raise CaptureAdmissionError("context.automation_suspected must be false")
    if context["automation_signals"] != []:
        raise CaptureAdmissionError("context.automation_signals must be an empty array")
    if context.get("label") is not None and not isinstance(context.get("label"), str):
        raise CaptureAdmissionError("context.label must be a string or null")
    if "device_pixel_ratio" in context and (
            isinstance(context["device_pixel_ratio"], bool)
            or not isinstance(context["device_pixel_ratio"], (int, float))):
        raise CaptureAdmissionError("context.device_pixel_ratio must be numeric")
    if "headed" in context and context["headed"] is not None and type(context["headed"]) is not bool:
        raise CaptureAdmissionError("context.headed must be boolean or null")
    if "notes" in context and not isinstance(context["notes"], str):
        raise CaptureAdmissionError("context.notes must be a string")


def _validate_probe(value, where):
    if not isinstance(value, dict) or set(value) - PROBE_FIELDS or type(value.get("ok")) is not bool:
        raise CaptureAdmissionError("invalid probe record for %s" % where)


def _validate_browser_identity(capture):
    context = capture["context"]
    ua = context["ua"]
    if HEADLESS_MARKER.search(ua):
        raise CaptureAdmissionError("context.ua contains a Headless marker")
    if FOREIGN_UA_MARKER.search(ua):
        raise CaptureAdmissionError("context.ua contains a foreign browser marker")
    ua_match = re.search(r"(?:Chrome|Chromium)/(\d+)(?:\.|\s|$)", ua)
    if not ua_match:
        raise CaptureAdmissionError("context.ua is not a Chromium browser identity")
    if int(ua_match.group(1)) != EXPECTED_BROWSER_MAJOR:
        raise CaptureAdmissionError(
            "context.ua reports Chrome %s; release requires Chromium %s"
            % (ua_match.group(1), EXPECTED_BROWSER_MAJOR)
        )

    scalars = capture["probes"]["navigator.scalars"]["value"]
    if not isinstance(scalars, dict):
        raise CaptureAdmissionError("navigator.scalars value must be an object")
    scalar_ua = scalars.get("userAgent")
    if not isinstance(scalar_ua, str):
        raise CaptureAdmissionError("navigator.scalars.userAgent is required")
    if HEADLESS_MARKER.search(scalar_ua):
        raise CaptureAdmissionError("navigator.scalars.userAgent contains a Headless marker")
    if FOREIGN_UA_MARKER.search(scalar_ua):
        raise CaptureAdmissionError("navigator.scalars.userAgent contains a foreign browser marker")
    if scalar_ua != ua:
        raise CaptureAdmissionError("context.ua and navigator.scalars.userAgent disagree")
    if scalars.get("webdriver") is not False:
        raise CaptureAdmissionError("navigator.scalars.webdriver must be false")

    user_agent_data = capture["probes"]["navigator.userAgentData"]["value"]
    if not isinstance(user_agent_data, dict):
        raise CaptureAdmissionError("navigator.userAgentData value must be an object")
    low = user_agent_data.get("low")
    high = user_agent_data.get("high")
    if not isinstance(low, dict) or not isinstance(high, dict):
        raise CaptureAdmissionError("navigator.userAgentData low/high identity is required")
    _validate_brand_list(low.get("brands"), "low")
    for key in ("brands", "fullVersionList"):
        if key in high:
            _validate_brand_list(high[key], "high.%s" % key)
    high_version = _version_major(high.get("uaFullVersion"))
    if high_version != EXPECTED_BROWSER_MAJOR:
        raise CaptureAdmissionError(
            "navigator.userAgentData.uaFullVersion must be Chromium %s"
            % EXPECTED_BROWSER_MAJOR
        )


def validate_capture(capture):
    """Raise CaptureAdmissionError unless *capture* is eligible for admission."""
    if not isinstance(capture, dict):
        raise CaptureAdmissionError("capture must be an object")
    allowed = {"capture_version", "context", "probes", "repeat"}
    unknown = set(capture) - allowed
    if unknown:
        raise CaptureAdmissionError("unexpected capture fields: %s" % ", ".join(sorted(unknown)))
    if type(capture.get("capture_version")) is not int or capture.get("capture_version") != EXPECTED_CAPTURE_VERSION:
        raise CaptureAdmissionError("capture_version must be 2")
    validate_context(capture.get("context"))
    probes = capture.get("probes")
    repeat = capture.get("repeat")
    if not isinstance(probes, dict):
        raise CaptureAdmissionError("capture.probes must be an object")
    if not isinstance(repeat, dict):
        raise CaptureAdmissionError("capture.repeat must be an object")
    for pid, value in probes.items():
        _validate_probe(value, "probes.%s" % pid)
        if not value["ok"]:
            raise CaptureAdmissionError("probe %s did not complete successfully" % pid)
    for pid, value in repeat.items():
        _validate_probe(value, "repeat.%s" % pid)
    for pid in DETERMINISTIC_PROBES:
        first = probes.get(pid)
        second = repeat.get(pid)
        if first is None or second is None:
            raise CaptureAdmissionError("deterministic probe %s is missing from probes or repeat" % pid)
        if not first["ok"] or not second["ok"]:
            raise CaptureAdmissionError("deterministic probe %s did not complete successfully" % pid)
    _validate_browser_identity(capture)
    return True


def admission_rejection_reason(capture):
    try:
        validate_capture(capture)
    except CaptureAdmissionError as exc:
        return str(exc)
    return None


def admission_record_path(out_dir, raw_sha256):
    return pathlib.Path(out_dir) / ADMISSION_DIRNAME / (raw_sha256 + ".json")


def persist_admission_decision(out_dir, raw_sha256, decision, reason, context=None,
                               capture_path=None, raw=None):
    """Write the admission decision for a capture, or refuse to.

    `accepted` is not a string a caller may assert. Anything downstream reads
    this record as the decision and never re-derives it, so a writer that takes
    the verdict on trust is the whole admission system's single point of
    failure.

    It failed exactly that way. resources/fingerprints/raw/admissions/70fd8f09…
    recorded stock-linux-20260907T150627Z.json as `accepted` with the reason
    "capture passed admission checks", for a capture that fails four of them:
    context.automation_suspected is true, context.automation_signals names the
    Headless token in its UA, capture_version is 1, and screen.details did not
    complete. No flag was involved and no check was missing or misspelled — the
    record was minted by calling this function directly, and this function
    validated nothing. The gate was never run, so it could not refuse.

    So the verdict is now derived here, from the capture, by the same gate.
    `accepted` requires the capture's own bytes, requires them to be the bytes
    the digest addresses, and requires validate_capture to pass on them. A
    caller with no capture to show cannot record an acceptance at all: absence
    fails closed. `rejected` needs no capture, because a refusal that cannot be
    substantiated is still a refusal.
    """
    if not HEX64.fullmatch(raw_sha256):
        raise ValueError("raw capture hash must be 64 hexadecimal characters")
    if decision == "accepted":
        if raw is None:
            raise ValueError(
                "refusing to record %s as accepted with no capture to check: "
                "an accepted decision is derived from the capture, never "
                "asserted by the caller" % raw_sha256[:16])
        if hashlib.sha256(raw).hexdigest() != raw_sha256:
            raise ValueError(
                "refusing to record %s as accepted: the capture supplied "
                "hashes to %s, so the record would not address the bytes it "
                "was decided on"
                % (raw_sha256[:16], hashlib.sha256(raw).hexdigest()[:16]))
        def reject_constant(_value):
            raise ValueError("non-finite JSON number")
        refusal = admission_rejection_reason(
            json.loads(raw, parse_constant=reject_constant))
        if refusal:
            raise ValueError(
                "refusing to record %s as accepted: %s"
                % (raw_sha256[:16], refusal))
    record = {
        "raw_sha256": raw_sha256,
        "decision": decision,
        "reason": reason,
        "context": context if isinstance(context, dict) else None,
        "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if capture_path is not None:
        record["capture_path"] = str(capture_path)
    path = admission_record_path(out_dir, raw_sha256)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = json.loads(path.read_text())
        if (existing.get("raw_sha256"), existing.get("decision"), existing.get("reason")) != (
                raw_sha256, decision, reason):
            raise ValueError("admission decision already exists for raw capture hash")
        return path
    with os.fdopen(fd, "wb") as output:
        output.write(encoded)
    return path

EVIDENCE_CLASSES = ("physical-ground-truth", "compatibility-capture")

# Probe -> block. A probe listed nowhere is deliberately not carried into any
# block; see UNASSIGNED at the bottom for why each one is left out.
BLOCK_PROBES = {
    "platform": [
        "navigator.userAgentData", "navigator.plugins", "api.surface",
        "wasm", "native_code.toString", "math.precision",
        "webrtc.capabilities", "fonts.detected", "fonts.query_api",
        "audio.offline_render", "speech.voices", "touch",
    ],
    # codecs.media sits here rather than in platform, on evidence. Comparing a
    # Windows VM with no GPU driver against a real Windows machine with an
    # RTX 3070 Ti, the only codec field that differed was decodingInfo's
    # powerEfficient on the three video codecs — false on the VM, true on the
    # card. powerEfficient means hardware-accelerated decode, so it is a
    # property of the GPU, not of the OS. Everything else in the probe was
    # identical across the two machines.
    "gpu": ["webgl1", "webgl2", "webgpu", "canvas.2d", "canvas.toDataURL_variants",
            "clientrects", "codecs.media"],
    "display": ["screen.geometry", "screen.details", "css.media", "media.devices"],
    "hardware": ["memory.heap", "audio.properties"],
    "locale": ["intl.locale", "keyboard.layout"],
    "theme": ["css.system", "css.media"],
}

# css.media answers two unrelated questions in one probe: what the panel can do,
# and what the user has chosen. Those are independent axes — a p3 HDR display is
# equally plausible in light or dark mode — so the probe is split by feature
# rather than assigned whole to either block.
DISPLAY_MEDIA_FEATURES = {"color-gamut", "dynamic-range", "any-pointer", "pointer",
                          "any-hover", "hover", "orientation", "update",
                          "overflow-block", "display-mode", "scripting"}
THEME_MEDIA_FEATURES = {"prefers-color-scheme", "prefers-reduced-motion",
                        "prefers-contrast", "forced-colors", "inverted-colors"}

# Fields inside a probe that identify the *part* rather than its behaviour.
# Excluded from a block's identity hash so that two captures of the same silicon
# hash equal even though their strings differ.
IDENTITY_FIELDS = {"unmaskedVendor", "unmaskedRenderer", "vendor", "renderer"}


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def strip_identity(value):
    """A copy of a probe value with the part-identifying strings removed."""
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if k not in IDENTITY_FIELDS}


def probe(cap, pid):
    p = cap.get("probes", {}).get(pid)
    return p["value"] if p and p.get("ok") else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--evidence", choices=EVIDENCE_CLASSES,
                    default="physical-ground-truth",
                    help="evidence class for the emitted blocks; compatibility "
                         "captures require --evidence-source")
    ap.add_argument("--evidence-source",
                    help="named compatibility runtime or other provenance source")
    ap.add_argument("--allow-software-gpu", action="store_true",
                    help="keep a gpu block whose renderer is a software or "
                         "virtual rasteriser (normally skipped)")
    args = ap.parse_args()
    raw_sha256 = None
    try:
        raw = args.capture.read_bytes()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        def reject_constant(_value):
            raise ValueError("non-finite JSON number")
        cap = json.loads(raw, parse_constant=reject_constant)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
        reason = "invalid capture JSON: %s" % exc
        if raw_sha256 is None:
            print("REFUSED: %s" % reason, file=sys.stderr)
            return 2
        try:
            persist_admission_decision(args.out, raw_sha256, "rejected", reason)
        except (OSError, ValueError, json.JSONDecodeError) as persist_error:
            print("REFUSED: %s (admission decision could not be saved: %s)" %
                  (reason, persist_error), file=sys.stderr)
            return 2
        print("REFUSED: %s" % reason, file=sys.stderr)
        return 2

    reason = admission_rejection_reason(cap)
    if reason:
        try:
            persist_admission_decision(args.out, raw_sha256, "rejected", reason,
                                       cap.get("context") if isinstance(cap, dict) else None)
        except (OSError, ValueError, json.JSONDecodeError) as persist_error:
            print("REFUSED: %s (admission decision could not be saved: %s)" %
                  (reason, persist_error), file=sys.stderr)
            return 2
        print("REFUSED: %s" % reason, file=sys.stderr)
        return 2

    ctx = cap["context"]

    # Evidence classification is an explicit command-line decision. Labels and
    # filenames remain descriptive and never decide whether a capture is valid.
    if args.evidence == "compatibility-capture" and not args.evidence_source:
        reason = "--evidence=compatibility-capture requires --evidence-source"
        persist_admission_decision(args.out, raw_sha256, "rejected", reason, ctx)
        print("REFUSED: %s" % reason, file=sys.stderr)
        return 2
    if args.evidence == "physical-ground-truth" and args.evidence_source:
        reason = "--evidence-source requires compatibility-capture classification"
        persist_admission_decision(args.out, raw_sha256, "rejected", reason, ctx)
        print("REFUSED: %s" % reason, file=sys.stderr)
        return 2

    label = ctx.get("label") or args.capture.stem
    written = []

    for block, pids in BLOCK_PROBES.items():
        content = {}
        for pid in pids:
            v = probe(cap, pid)
            if v is not None:
                content[pid] = v
        if block in ("display", "theme") and "css.media" in content:
            keep = DISPLAY_MEDIA_FEATURES if block == "display" else THEME_MEDIA_FEATURES
            content["css.media"] = {k: v for k, v in content["css.media"].items()
                                    if k in keep}

        if block == "hardware":
            # navigator.deviceMemory is a browser bucket, not installed RAM.
            # Keep it out of the hardware content unless an owner sidecar
            # supplies the physical value used by profile composition.
            nav = probe(cap, "navigator.scalars") or {}
            side = args.capture.with_suffix(".memory")
            if nav.get("deviceMemory") and not side.exists():
                heap = content.get("memory.heap")
                if isinstance(heap, dict):
                    content["memory.heap"] = {
                        key: value for key, value in heap.items()
                        if key != "deviceMemory"
                    }

        if not content and block != "hardware":
            continue

        if not content:
            if block != "hardware":
                continue
            nav = probe(cap, "navigator.scalars") or {}
            if nav.get("hardwareConcurrency") is None:
                continue

        # The identity hash ignores the part's *name*. Two captures of the same
        # silicon therefore hash equal even if their renderer strings differ,
        # which is what makes "are these interchangeable?" a measurement rather
        # than an assertion. It stays unused until a second capture exists to
        # compare against.
        ident = {pid: strip_identity(v) for pid, v in content.items()}

        rec = {
            "block": block,
            "label": label,
            "source_capture": str(args.capture.name),
            "source_capture_sha256": raw_sha256,
            "source_context": ctx,
            "source_taken_at": ctx.get("taken_at"),
            "provenance": {
                "source_capture_sha256": raw_sha256,
                "source_context": ctx,
                "evidence": args.evidence,
            },
            "browser_version": (probe(cap, "navigator.userAgentData") or {})
                               .get("high", {}).get("uaFullVersion"),
            "captured_browser": "Google Chrome",
            "captured_platform": ((probe(cap, "navigator.userAgentData") or {})
                                  .get("high") or {}).get("platform"),
            "evidence": args.evidence,
            "content_sha256": sha(content),
            "identity_sha256": sha(ident),
            "content": content,
        }
        if args.evidence_source:
            rec["evidence_source"] = args.evidence_source
            rec["provenance"]["evidence_source"] = args.evidence_source

        if block == "platform":
            # Carried as a side field rather than as a probe, because
            # navigator.scalars spans blocks: its UA and platform strings belong
            # to the OS, its languages to locale, its core and memory counts to
            # hardware. Each block takes only the fields it owns.
            nav = probe(cap, "navigator.scalars") or {}
            rec["navigator_scalars"] = {
                k: nav.get(k) for k in ("userAgent", "platform", "languages")
                if nav.get(k) is not None
            }

        if block == "hardware":
            # Sourced from probes that span blocks, so they are pulled out by
            # field rather than carried whole.
            nav = probe(cap, "navigator.scalars") or {}
            audio = probe(cap, "audio.properties") or {}
            rec["logical_cores"] = nav.get("hardwareConcurrency")
            # baseLatency is buffer size over sample rate, so the frame count is
            # recoverable exactly. Stored as frames because that is what the
            # emitter takes; storing the latency would make the profile carry a
            # derived value and lose the rate it was derived against.
            if audio.get("baseLatency") and audio.get("sampleRate"):
                rec["audio_buffer_frames"] = round(
                    audio["baseLatency"] * audio["sampleRate"])
                rec["audio_sample_rate"] = audio["sampleRate"]
            # deviceMemory is a browser bucket, not installed RAM. It is never
            # promoted to profile memory without an owner-reported sidecar.
            side = args.capture.with_suffix(".memory")
            if nav.get("deviceMemory") and not side.exists():
                rec["evidence_detail"] = "memory-deviceMemory-floor-excluded"
            if side.exists():
                try:
                    gib_real = int(side.read_text().strip())
                except (OSError, ValueError) as exc:
                    raise ValueError(
                        "owner memory sidecar must contain a positive integer GiB"
                    ) from exc
                if gib_real <= 0:
                    raise ValueError(
                        "owner memory sidecar must contain a positive integer GiB"
                    )
                rec["evidence"] = "physical-ground-truth"
                rec["memory_total_bytes"] = gib_real * 1024**3
                rec["memory_is_floor"] = False
                rec["evidence_detail"] = "memory-owner-reported"
                rec["memory_source"] = "owner-reported (%s)" % side.name

        if block == "gpu":
            # Three hashes, because the block spans two different questions and
            # one hash over all of it answers neither.
            #
            # caps_sha256 is the GPU's capability surface: WebGL parameters,
            # extensions and precision formats, with the identifying strings
            # removed. Equal caps means two GPUs report the same abilities.
            #
            # webgl_render_sha256 is what the GPU actually draws.
            #
            # canvas_sha256 is deliberately apart from both, because canvas 2D
            # is not a GPU measurement. Measured on two Apple Silicon Macs, every
            # WebGL parameter, extension, precision format and readback pixel was
            # identical while canvas differed — and the canvas difference was
            # 0.0% across the shapes and 1.8% in the text band, tracking a macOS
            # version gap of 26.6.2 against 27.0.0. Folding canvas into GPU
            # identity made two interchangeable GPUs look distinct.
            caps = {}
            for gl in ("webgl1", "webgl2"):
                v = content.get(gl) or {}
                caps[gl] = {k: v.get(k) for k in
                             ("parameters", "extensions", "precision",
                              "contextAttributes", "antialiasSamples")}
            caps["webgpu"] = strip_identity(content.get("webgpu") or {})
            rec["caps_sha256"] = sha(caps)
            rec["webgl_render_sha256"] = sha({
                "webgl1": (content.get("webgl1") or {}).get("pixels_sha256"),
                "webgl2": (content.get("webgl2") or {}).get("pixels_sha256"),
            })
            rec["canvas_sha256"] = sha(
                (content.get("canvas.2d") or {}).get("pixels_sha256"))

            g = content.get("webgl1") or {}
            # A software or virtual renderer is a real measurement of a real
            # machine, and it is not a machine any consumer profile should
            # claim. "Microsoft Basic Render Driver" is what Windows falls back
            # to with no GPU driver, which is the signature of a VM — it says
            # data centre as loudly as anything a page can read. Blocked here
            # rather than at composition time, so it never enters the corpus.
            r = (g.get("unmaskedRenderer") or "")
            soft = next((s for s in ("Basic Render Driver", "Basic Display",
                                     "SwiftShader", "llvmpipe", "softpipe",
                                     "VMware", "VirtualBox", "Parallels",
                                     "Microsoft Remote Display", "Mesa OffScreen")
                         if s.lower() in r.lower()), None)
            if soft and not args.allow_software_gpu:
                print("SKIPPED gpu block: renderer is %r, a software or virtual "
                      "rasteriser (%s)." % (r, soft), file=sys.stderr)
                print("  No consumer machine reports this, so it must not become "
                      "a GPU block. The", file=sys.stderr)
                print("  capture's other blocks are still usable. Override with "
                      "--allow-software-gpu.", file=sys.stderr)
                continue
            rec["renderer"] = g.get("unmaskedRenderer")
            rec["vendor"] = g.get("unmaskedVendor")
            # Rendered output, kept separate from the capability tables. Two
            # GPUs may agree on every table and still rasterise differently;
            # only equal render hashes justify swapping one for the other.
            rec["render_sha256"] = sha({
                "canvas": (content.get("canvas.2d") or {}).get("pixels_sha256"),
                "webgl1": (content.get("webgl1") or {}).get("pixels_sha256"),
                "webgl2": (content.get("webgl2") or {}).get("pixels_sha256"),
            })

        d = args.out / block
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("%s-%s.json" % (label, rec["content_sha256"][:12]))
        path.write_text(json.dumps(rec, indent=2) + "\n")
        written.append((block, path, rec))
    try:
        persist_admission_decision(args.out, raw_sha256, "accepted",
                                   "capture passed admission checks", ctx, raw=raw)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("REFUSED: admission decision could not be saved: %s" % exc,
              file=sys.stderr)
        return 2

    print("decomposed %s" % args.capture.name)
    for block, path, rec in written:
        extra = ""
        if block == "gpu":
            extra = "  renderer=%s" % rec.get("renderer")
        print("  %-9s %-58s caps=%s%s"
              % (block, str(path), rec["identity_sha256"][:12], extra))
    return 0


# UNASSIGNED, deliberately:
#   battery            power state, not device identity; its own axis
#   headers.echo*      derived from locale + platform, not independent
#   navigator.scalars  spans blocks; its fields are sourced from the block that
#                      owns each one rather than carried whole
#   storage.estimate, timing.resolution, network.connection, permissions.states
#                      measured volatile, or state rather than identity
#   memory.heap        carried in hardware for measured heap observations;
#                      deviceMemory is excluded from composed memory unless an
#                      owner-reported sidecar supplies installed RAM
if __name__ == "__main__":
    sys.exit(main())
