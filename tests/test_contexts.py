"""Every context a page can create reads the same machine, and so do the request headers."""

from __future__ import annotations

import json

CONTEXTS = ("sameOriginFrame", "crossOriginFrame", "worker", "sharedWorker", "serviceWorker")
KEYS = ("userAgent", "platform", "language", "languages", "hardwareConcurrency", "deviceMemory", "timeZone",
        "locale", "offsetJanuary", "offsetJuly", "uaData", "uaHigh", "connection")


def _noiseless(value, key):
    """Chrome adds per-origin noise to downlink and rtt; the rest of connection must agree."""
    if key == "connection" and isinstance(value, dict):
        return {name: item for name, item in value.items() if name not in ("downlink", "rtt")}
    return value


def _webgl(values):
    webgl = values.get("webgl") or {}
    return {key: webgl.get(key) for key in ("vendor", "renderer", "extensions", "limits", "precisions")}


def _webgpu(values):
    webgpu = values.get("webgpu") or {}
    return {key: webgpu.get(key) for key in ("vendor", "architecture", "subgroupMinSize", "subgroupMaxSize", "features", "limits")}


def test_contexts_agree(case, record):
    window = case.values["window"]
    differences = []
    compared = 0
    for name in CONTEXTS:
        values = case.values.get(name) or {}
        if "error" in values:
            differences.append(f"{name}: {values['error']}")
            continue
        for key in KEYS:
            compared += 1
            if _noiseless(values.get(key), key) != _noiseless(window.get(key), key):
                differences.append(f"{name}.{key}: {json.dumps(values.get(key))[:200]} != window {json.dumps(window.get(key))[:200]}")
        for label, reader in (("webgl", _webgl), ("webgpu", _webgpu)):
            compared += 1
            if reader(values) != reader(window):
                differences.append(f"{name}.{label} differs from the window's")
    record.update(compared=compared, differences=differences)
    assert differences == []


def test_frames_see_the_same_screen(case, record):
    screen = case.values["page"]["screen"]
    differences = []
    for name in ("sameOriginFrame", "crossOriginFrame"):
        frame = (case.values.get(name) or {}).get("screen") or {}
        for key in ("width", "height", "availWidth", "availHeight", "devicePixelRatio"):
            if frame.get(key) != screen.get(key):
                differences.append(f"{name}.{key}: {frame.get(key)} != {screen.get(key)}")
    record["differences"] = differences
    assert differences == []


def _quoted(value):
    return f'"{value}"'


def test_request_headers_match_javascript(case, record):
    window = case.values["window"]
    document = case.document_headers
    echo = window.get("headers") or {}
    high = window.get("uaHigh") or {}
    checks = {
        "document user-agent": (document.get("user-agent"), window["userAgent"]),
        "fetch user-agent": (echo.get("user-agent"), window["userAgent"]),
        "sec-ch-ua-platform": (document.get("sec-ch-ua-platform"), _quoted(window["uaData"]["platform"])),
        "sec-ch-ua-mobile": (document.get("sec-ch-ua-mobile"), "?1" if window["uaData"]["mobile"] else "?0"),
        "sec-ch-ua-platform-version": (echo.get("sec-ch-ua-platform-version"), _quoted(high.get("platformVersion"))),
        "sec-ch-ua-arch": (echo.get("sec-ch-ua-arch"), _quoted(high.get("architecture"))),
        "sec-ch-ua-bitness": (echo.get("sec-ch-ua-bitness"), _quoted(high.get("bitness"))),
        "sec-ch-ua-model": (echo.get("sec-ch-ua-model"), _quoted(high.get("model"))),
        "sec-ch-ua-wow64": (echo.get("sec-ch-ua-wow64"), "?1" if high.get("wow64") else "?0"),
    }
    brands = ", ".join(f'"{brand["brand"]}";v="{brand["version"]}"' for brand in window["uaData"]["brands"])
    checks["sec-ch-ua"] = (document.get("sec-ch-ua"), brands)
    full = ", ".join(f'"{brand["brand"]}";v="{brand["version"]}"' for brand in high.get("fullVersionList", []))
    checks["sec-ch-ua-full-version-list"] = (echo.get("sec-ch-ua-full-version-list"), full)
    for name in ("worker", "sharedWorker", "serviceWorker"):
        headers = (case.values.get(name) or {}).get("headers") or {}
        checks[f"{name} user-agent"] = (headers.get("user-agent"), window["userAgent"])
    languages = window["languages"]
    accept = document.get("accept-language", "")
    checks["accept-language"] = ([part.split(";")[0] for part in accept.split(",")], languages)
    mismatched = {name: {"header": got, "javascript": want} for name, (got, want) in checks.items() if got != want}
    record.update(checked=len(checks), mismatched=mismatched)
    assert mismatched == {}
