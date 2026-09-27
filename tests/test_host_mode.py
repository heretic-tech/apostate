"""--fingerprint=host composes nothing and shows the real machine."""

from __future__ import annotations

import os
import sys

HOST_PLATFORMS = {"darwin": "MacIntel", "win32": "Win32"}


def test_host_mode_composes_nothing(probes, record):
    probe = probes(None, "host", "bare")
    window = probe.values["window"]
    record.update(platform=window["platform"], cores=window["hardwareConcurrency"])
    assert probe.profile is None, "a child process carries a composed profile in host mode"
    assert window["hardwareConcurrency"] == os.cpu_count()
    expected = HOST_PLATFORMS.get(sys.platform, "Linux x86_64")
    assert window["platform"] == expected
