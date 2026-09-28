"""Known gaps: a failure that matches one is reported as an expected failure.

Each gap names what a page sees, why, and the docs section that tracks it.
A test calls ``expect()`` only after checking that its mismatch is exactly
the documented one, so any other difference still fails.
"""

from __future__ import annotations

import sys

import pytest

DOCS = "https://docs.apostate.dev/known-gaps"

GAPS = {
    "host-media-devices": (
        "A camera or microphone on the host stays listed when the persona claims none: the "
        "persona's device counts are a floor, not an exact count.",
        f"{DOCS}#cameras-and-microphones",
    ),
    "apple-gpu-limits": (
        "On an Apple silicon host, a Windows or Linux persona reports the Apple GPU's value for "
        "WebGL MAX_UNIFORM_BLOCK_SIZE and for some WebGPU adapter limits and features.",
        f"{DOCS}#webgl-and-webgpu-limits-on-apple-silicon",
    ),
    "font-filter-iphey": (
        "iphey.com flags every composed persona, the macOS persona on a Mac included, as "
        "'inconsistent browser fingerprint (roadmap)', and the Windows persona also as "
        "'(butterfly)'. Both come from the font filter: the same profile without its fonts "
        "section passes, whatever architecture and GPU it claims.",
        f"{DOCS}#iphey-and-the-font-filter",
    ),
    "fpjs-linux-persona": (
        "FingerprintJS Pro flags a Linux persona with tampering and anti_detect_browser, and "
        "scores it anomaly_score 1, where Windows and macOS personas in the same run have no "
        "browser flags. The cause is not isolated.",
        f"{DOCS}#linux-personas-score-as-an-anomaly",
    ),
    "arm-windows-gpu": (
        "Up to 0.4.3, a Windows persona on an ARM host reports the arm architecture next to a "
        "desktop Intel or NVIDIA GPU, a pair no real Windows machine has. Real Windows on ARM "
        "has a Qualcomm Adreno GPU.",
        f"{DOCS}#arm-hosts",
    ),
    "linux-headless-webgpu": (
        "On Linux, navigator.gpu.requestAdapter() returns null in headless mode, although the "
        "persona claims a WebGPU adapter. Headed launches as a regular user serve it.",
        f"{DOCS}#webgpu-in-headless-mode-on-linux",
    ),
    "host-cap": (
        "The host has fewer cores or less memory than every option the persona's machine offers, "
        "so the page reads the host's own hardwareConcurrency or deviceMemory next to the "
        "persona's GPU.",
        f"{DOCS}#cores-and-memory-are-capped-at-the-hosts",
    ),
    "host-pointer": (
        "A launch without Playwright or Patchright reports the host's pointer and hover media "
        "features. A server with no mouse reports (pointer: none) and (hover: none) under a "
        "desktop persona.",
        f"{DOCS}#pointer-and-hover-come-from-the-driver-or-the-host",
    ),
    "api-keys-infobar": (
        "The first tab of a launch without a driver shows Chromium's 'Google API keys are missing' "
        "bar, or its bar for --no-sandbox where that flag is passed. Either takes 56 px from "
        "innerHeight. Google Chrome shows neither.",
        f"{DOCS}#a-bar-on-the-first-tab",
    ),
}


def expect(record: dict, gap: str) -> None:
    reason, link = GAPS[gap]
    record["known_gap"] = {"id": gap, "reason": reason, "link": link}
    pytest.xfail(f"known gap {gap}: {reason} {link}")


def apple_host() -> bool:
    import platform
    return sys.platform == "darwin" and platform.machine() == "arm64"
