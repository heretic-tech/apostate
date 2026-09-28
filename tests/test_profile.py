"""The page reads exactly the machine the browser composed.

The browser composes a profile before any other process starts and hands it
to every child process. These tests read that profile from a child's command
line and compare it with what the probe page read.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

import gaps
from harness import ROOT, TABLES, WINDOWS_ALIASES, host_facts

PLATFORMS = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}
UA_TOKENS = {"windows": "Windows NT 10.0; Win64; x64", "macos": "Macintosh; Intel Mac OS X", "linux": "X11; Linux x86_64"}
NAVIGATOR_PLATFORMS = {"windows": "Win32", "macos": "MacIntel", "linux": "Linux x86_64"}


def profile_of(case):
    if case.profile is None:
        pytest.fail("no composed profile was found on a child process")
    return case.profile


def compare(record, pairs, gap=None, allowed=()):
    """Every pair must match. Mismatches only in *allowed* are the known *gap*."""
    mismatched = {name: {"page": got, "profile": want} for name, (got, want) in pairs.items() if got != want}
    record.update(checked=len(pairs), mismatched=mismatched)
    if mismatched and gap and set(mismatched) <= set(allowed):
        gaps.expect(record, gap)
    assert mismatched == {}


def test_persona_is_the_one_asked_for(case, record):
    profile = profile_of(case)
    persona = case.launch.persona
    window = case.values["window"]
    anchors = {
        option_set["key"]["anchor"]
        for option_set in json.loads((TABLES / "gpu_identity.json").read_text())["option_sets"]
    }
    families = {"windows": "windows-", "macos": "macos-", "linux": "linux-vulkan-"}[persona]
    renderers = {
        option["value"]["gpu"]["unmasked_renderer"]
        for option_set in json.loads((TABLES / "gpu_identity.json").read_text())["option_sets"]
        if option_set["key"]["anchor"].startswith(families)
        for option in option_set["options"]
    }
    record.update(platform=profile["platform"]["name"], renderer=profile["gpu"]["unmasked_renderer"], anchors=len(anchors))
    assert profile["platform"]["name"] == PLATFORMS[persona]
    assert UA_TOKENS[persona] in window["userAgent"]
    assert window["platform"] == NAVIGATOR_PLATFORMS[persona]
    assert window["uaData"]["platform"] == PLATFORMS[persona]
    assert profile["gpu"]["unmasked_renderer"] in renderers


def test_user_agent_and_client_hints(case, record):
    profile = profile_of(case)
    window = case.values["window"]
    high = window["uaHigh"]
    platform = profile["platform"]
    compare(record, {
        "userAgent": (window["userAgent"], profile["browser"]["user_agent"]),
        "platform": (window["platform"], platform["navigator_platform"]),
        "uaData.platform": (window["uaData"]["platform"], platform["name"]),
        "uaData.mobile": (window["uaData"]["mobile"], platform["mobile"]),
        "platformVersion": (high["platformVersion"], platform["version"]),
        "architecture": (high["architecture"], platform["architecture"]),
        "bitness": (high["bitness"], platform["bitness"]),
        "model": (high["model"], platform["model"]),
        "wow64": (high["wow64"], platform["wow64"]),
        "formFactors": (high["formFactors"], platform["form_factors"]),
    })


def _device_memory(total_bytes):
    """navigator.deviceMemory: the nearest power of two in GiB. Chrome 152 reads 32 on a 36 GiB Mac."""
    gib = total_bytes / 2**30
    return min(32, 2 ** round(math.log2(gib))) if gib >= 0.25 else 0.25


def test_cpu_and_memory(case, record):
    profile = profile_of(case)
    window = case.values["window"]
    host = host_facts()
    pairs = {}
    # When no option of the persona fits the host, the page reads the host's own value.
    if "cpu" in profile:
        pairs["hardwareConcurrency"] = (window["hardwareConcurrency"], profile["cpu"]["logical_cores"])
    else:
        pairs["hardwareConcurrency is the host's"] = (window["hardwareConcurrency"], host["cores"])
    if "memory" in profile:
        pairs["deviceMemory"] = (window["deviceMemory"], _device_memory(profile["memory"]["total_bytes"]))
    else:
        pairs["deviceMemory is the host's"] = (window["deviceMemory"], _device_memory(host["memory_bytes"]))
    compare(record, pairs)
    if "cpu" not in profile or "memory" not in profile:
        gaps.expect(record, "host-cap")


def test_screen(case, record):
    profile = profile_of(case)
    screen = case.values["page"]["screen"]
    want = profile["screen"]
    compare(record, {
        "width": (screen["width"], want["width"]),
        "height": (screen["height"], want["height"]),
        "availWidth": (screen["availWidth"], want["avail_width"]),
        "availHeight": (screen["availHeight"], want["avail_height"]),
        "availLeft": (screen["availLeft"], want["avail_left"]),
        "availTop": (screen["availTop"], want["avail_top"]),
        "devicePixelRatio": (screen["devicePixelRatio"], want["device_pixel_ratio"]),
        "colorDepth": (screen["colorDepth"], want["color_depth"]),
    })


def test_screen_has_a_taskbar_or_menu_bar(case, record):
    screen = case.values["page"]["screen"]
    record.update(screen)
    assert screen["availHeight"] < screen["height"] or screen["availWidth"] < screen["width"]
    assert screen["outerWidth"] <= screen["availWidth"] and screen["outerHeight"] <= screen["availHeight"]
    assert screen["innerWidth"] <= screen["outerWidth"] and screen["innerHeight"] < screen["outerHeight"]


def test_webgl(case, record):
    profile = profile_of(case)
    webgl = case.values["window"]["webgl"]
    pairs = {
        "vendor": (webgl["vendor"], profile["gpu"]["unmasked_vendor"]),
        "renderer": (webgl["renderer"], profile["gpu"]["unmasked_renderer"]),
    }
    for name, value in webgl["limits"].items():
        if name in profile.get("gl_limits", {}):
            pairs[f"limit {name}"] = (value, profile["gl_limits"][name])
    for name, (low, high, precision) in webgl["precisions"].items():
        shader, kind = name.split(".")
        want = profile.get("gl_precisions", {}).get(shader, {}).get(kind)
        if want:
            pairs[f"precision {name}"] = ([low, high, precision], [want["rangeMin"], want["rangeMax"], want["precision"]])
    extra = sorted(set(webgl["extensions"]) - set(profile.get("gl_extensions", [])))
    missing = sorted(set(profile.get("gl_extensions", [])) - set(webgl["extensions"]))
    record.update(extensions_not_in_profile=extra, profile_extensions_missing=missing)
    pairs["extensions outside the profile"] = (extra, [])
    apple = gaps.apple_host() and case.launch.persona != "macos"
    compare(record, pairs, "apple-gpu-limits" if apple else None, ("limit MAX_UNIFORM_BLOCK_SIZE",))


def test_webgpu(case, record):
    profile = profile_of(case)
    webgpu = case.values["window"]["webgpu"]
    want = profile.get("webgpu")
    if not want:
        pytest.skip("the profile leaves WebGPU to the host")
    if want.get("available") is False:
        # The claimed device reported no adapter when it was captured (patch 0155).
        record["webgpu"] = webgpu
        # navigator.gpu exists, as on the real device; requestAdapter() finds nothing.
        assert not webgpu.get("vendor"), f"the claimed device has no WebGPU adapter and the page has one: {webgpu}"
        return
    if not webgpu.get("available") or not webgpu.get("vendor"):
        record["webgpu"] = webgpu
        if sys.platform.startswith("linux") and not case.launch.headed:
            gaps.expect(record, "linux-headless-webgpu")
        pytest.fail(f"the profile claims a WebGPU adapter and the page has none: {webgpu}")
    pairs = {
        "vendor": (webgpu["vendor"], want["info"]["vendor"]),
        "architecture": (webgpu["architecture"], want["info"]["architecture"]),
        "subgroupMinSize": (webgpu["subgroupMinSize"], want["info"].get("subgroup_min_size")),
        "subgroupMaxSize": (webgpu["subgroupMaxSize"], want["info"].get("subgroup_max_size")),
        "features": (webgpu["features"], sorted(want.get("features", []))),
    }
    for name, value in want.get("limits", {}).items():
        pairs[f"limit {name}"] = (webgpu["limits"].get(name), value)
    apple = gaps.apple_host() and case.launch.persona != "macos"
    adapter = tuple(name for name in pairs if name.startswith("limit ") or name == "features")
    compare(record, pairs, "apple-gpu-limits" if apple else None, adapter)


def test_fonts_are_the_personas(case, record):
    profile = profile_of(case)
    allowlist = set(profile["fonts"]["enumeration_allowlist"])
    visible = set(case.values["page"]["fonts"]["visible"])
    allowed = allowlist | (WINDOWS_ALIASES if case.launch.persona == "windows" else set())
    leaked = sorted(visible - allowed)
    record.update(visible=len(visible), allowlisted=len(allowlist), visible_of_allowlist=len(visible & allowlist), leaked=leaked)
    assert leaked == [], f"fonts outside the persona's list are visible: {leaked}"
    assert visible & allowlist, "none of the persona's fonts is visible; install them (docs: guides/fonts)"


def test_generic_families_resolve_to_the_personas_fonts(case, record):
    profile = profile_of(case)
    mapping = profile["fonts"].get("generic_family_map") or {}
    generic = case.values["page"]["fonts"]["generic"]
    named = case.values["page"]["fonts"].get("named", {})
    visible = set(case.values["page"]["fonts"]["visible"])
    pairs = {}
    for family, font in mapping.items():
        if font in visible and family in generic and font in named:
            pairs[family] = (generic[family], named[font])
    record["mapping"] = mapping
    if not pairs:
        pytest.skip("no mapped font is installed on this host")
    compare(record, pairs)


def test_voices(case, record):
    profile = profile_of(case)
    want = [(voice["name"], voice["lang"]) for voice in (profile.get("speech") or {}).get("voices", [])]
    got = [(voice["name"], voice["lang"]) for voice in case.values["page"]["voices"]]
    local = [voice for voice in got if voice in want]
    record.update(profile_voices=len(want), page_voices=len(got))
    missing = [voice for voice in want if voice not in got]
    assert missing == [], f"voices in the profile that the page does not list: {missing}"
    extra_local = [voice for voice in case.values["page"]["voices"] if voice["local"] and (voice["name"], voice["lang"]) not in want]
    assert extra_local == [], f"local voices the profile does not list: {extra_local}"
    assert local or not want


def test_audio_buffer(case, record):
    profile = profile_of(case)
    audio = case.values["page"]["audio"]
    frames = round(audio["baseLatency"] * audio["sampleRate"])
    record.update(audio, frames=frames)
    assert frames == profile["audio"]["hardware_buffer_frames"]


def test_media_devices(case, record):
    profile = profile_of(case)
    media = profile.get("media") or {}
    devices = case.values["page"]["devices"]
    got = {kind: devices.count(kind) for kind in ("audioinput", "audiooutput", "videoinput")}
    # Without a permission grant Chrome lists at most one device of each kind.
    want = {kind: min(1, media.get(f"{kind}_count", 0)) for kind in got}
    extra = tuple(kind for kind in got if got[kind] > want[kind])
    compare(record, {kind: (got[kind], want[kind]) for kind in got}, "host-media-devices", extra)


def test_network_battery_theme_and_keyboard(case, record):
    profile = profile_of(case)
    page = case.values["page"]
    window = case.values["window"]
    network = profile.get("network") or {}
    battery = profile.get("battery") or {}
    pairs = {
        "prefers-color-scheme dark": (page["media"]["dark"], (profile.get("theme") or {}).get("prefers_dark", False)),
        "keyboard KeyQ": ((page["keyboard"] or {}).get("KeyQ"), "q"),
        "chrome.runtime": (page["chrome"]["runtime"] != "undefined", bool((profile.get("extensions") or {}).get("externally_connectable"))),
    }
    if network:
        connection = window.get("connection") or {}
        pairs["connection.effectiveType"] = (connection.get("effectiveType"), network.get("effective_type"))
        pairs["connection.saveData"] = (connection.get("saveData"), network.get("save_data"))
        # Chrome adds up to 10% of noise per origin to downlink and rtt, as stock Chrome does.
        downlink = connection.get("downlink")
        want = network.get("downlink_mbps")
        pairs["connection.downlink within 10%"] = (downlink is not None and abs(downlink - want) <= want * 0.1 + 0.05, True)
    if battery.get("present") is False:
        pairs["battery"] = ((page["battery"]["charging"], page["battery"]["level"], page["battery"]["chargingTime"]), (True, 1, 0))
    compare(record, pairs)


def test_desktop_pointer_and_hover(case, record):
    """Every persona is a desktop, so the page sees a mouse: (pointer: fine) and (hover: hover)."""
    profile_of(case)
    media = case.values["page"]["media"]
    pairs = {"(pointer: fine)": (media["pointerFine"], True), "(hover: hover)": (media["hover"], True)}
    gap = "host-pointer" if case.launch.mode == "bare" else None
    compare(record, pairs, gap, allowed=set(pairs))


def test_the_gpu_matches_the_cpu_family(case, record):
    """A Windows machine with an ARM CPU has a Qualcomm Adreno GPU, and one with an x86 CPU never does."""
    profile_of(case)
    if case.launch.persona != "windows":
        pytest.skip("the pairing is checked for the Windows persona")
    architecture = case.values["window"]["uaHigh"]["architecture"]
    renderer = case.values["window"]["webgl"]["renderer"] or ""
    adreno = "Adreno" in renderer
    pairs = {f"Adreno GPU with {architecture}": (adreno, architecture == "arm")}
    record.update(architecture=architecture, renderer=renderer)
    gap = "arm-windows-gpu" if architecture == "arm" and not adreno else None
    compare(record, pairs, gap, allowed=set(pairs))


def _measured_member(renderer):
    """The corpus member whose captured WebGL renderer is *renderer*, and its WebGPU adapter."""
    for path in sorted((ROOT / "corpus" / "anchors").glob("*.json")):
        anchor = json.loads(path.read_text())
        for member in anchor.get("members", []):
            if ((member.get("identity") or {}).get("webgl1") or {}).get("unmaskedRenderer") != renderer:
                continue
            capture = member["capture"].rsplit("/", 1)[-1]
            for variant in anchor["capability_cluster"]["webgpu"]["variants"]:
                if capture in variant["members"]:
                    adapters = variant["cluster"]["adapters"]
                    return member, adapters.get("high-performance") or adapters.get("low-power")
            return member, None
    return None, None


def test_webgpu_is_the_captured_devices(case, record):
    """A persona that claims a measured GPU gets that GPU's WebGPU adapter, or none when the capture had none."""
    profile_of(case)
    renderer = case.values["window"]["webgl"]["renderer"]
    member, adapter = _measured_member(renderer)
    if member is None:
        pytest.skip("the renderer is a registered identity, not a captured one")
    webgpu = case.values["window"]["webgpu"]
    record.update(renderer=renderer, captured_adapter=bool(adapter), page_adapter=bool(webgpu.get("vendor")))
    if adapter is None:
        assert not webgpu.get("vendor"), f"{member['device']} had no WebGPU adapter and the page has one: {webgpu}"
        return
    if not webgpu.get("vendor"):
        if sys.platform.startswith("linux") and not case.launch.headed:
            gaps.expect(record, "linux-headless-webgpu")
        pytest.fail(f"{member['device']} had a WebGPU adapter and the page has none")
    compare(record, {"vendor": (webgpu["vendor"], adapter["vendor"]),
                     "architecture": (webgpu["architecture"], adapter["architecture"])})
