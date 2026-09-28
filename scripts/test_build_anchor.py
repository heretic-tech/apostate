#!/usr/bin/env python3
"""build-anchor.py reproduces the committed GPU families byte for byte."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-anchor.py"
ANCHORS = ROOT / "corpus" / "anchors"
RAW = ROOT / "resources" / "fingerprints" / "raw"
INTEL = ANCHORS / "windows-d3d11-intel-79dfeb5b4f99.json"
INTEL_CAPTURE = RAW / "windows-chrome-20260910T140813Z.json"
ADRENO_CAPTURE = RAW / "windows-qualcomm-adreno-x2-90-20260927T185251Z.json"

SPEC = importlib.util.spec_from_file_location("build_anchor", SCRIPT)
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)

# The pin the Intel capture was measured against, for tests of other refusals.
INTEL_PIN = json.loads(INTEL.read_text(encoding="utf-8"))["release_pin"]

def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, check=False)


class BuildAnchorTests(unittest.TestCase):
    def test_intel_anchor_is_reproduced_byte_for_byte(self) -> None:
        result = run(str(INTEL_CAPTURE), "--host-architecture", "x86", "--release-pin", INTEL_PIN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, INTEL.read_text(encoding="utf-8"))

    def test_every_committed_anchor_is_reproduced(self) -> None:
        """Only the fields no capture holds are passed in: the software anchor's prose.

        Each anchor is rebuilt against the release pin it records, which is the
        pin it was measured against, not necessarily today's.
        """
        peers = [BUILD._strip(peer) for peer in BUILD.load_anchors(ANCHORS)]
        for path in sorted(ANCHORS.glob("*.json")):
            committed = json.loads(path.read_text(encoding="utf-8"))
            pin = committed["release_pin"]
            with self.subTest(anchor=committed["anchor_id"]):
                extra = {key: committed[key] for key in ("software_anchor_policy",) if key in committed}
                built = BUILD.build_anchor(
                    [ROOT / member["capture"] for member in committed["members"]], peers, pin,
                    host_architecture=committed.get("host_architecture"), extra=extra or None,
                    # The software anchor's capture predates capture version 2.
                    allow_unadmitted="software_anchor_policy" in committed)
                self.assertEqual(BUILD.encode_anchor(built), path.read_text(encoding="utf-8"))

    def test_the_adreno_capture_gives_the_expected_id(self) -> None:
        result = run(str(ADRENO_CAPTURE), "--host-architecture", "auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        anchor = json.loads(result.stdout)
        self.assertEqual(anchor["anchor_id"], "windows-d3d11-qualcomm-6388f9914f3f")
        self.assertEqual(anchor["host_architecture"], "arm")
        self.assertEqual(anchor["anchor_sha256"], BUILD.sha256_of(anchor["capability_cluster"]))

    def test_captures_of_two_vendors_are_refused(self) -> None:
        result = run(str(INTEL_CAPTURE), str(ADRENO_CAPTURE), "--release-pin", INTEL_PIN)
        self.assertEqual(result.returncode, 1)
        self.assertIn("has vendor 'Qualcomm' where", result.stderr)

    def test_captures_with_two_capability_tables_are_refused(self) -> None:
        # The Adreno X2-90's one difference from the UHD 630, on an Intel capture.
        capture = json.loads(INTEL_CAPTURE.read_text(encoding="utf-8"))
        for section in ("probes", "repeat"):
            capture[section]["webgl2"]["value"]["parameters"]["MAX_SAMPLES"] = 8
        with tempfile.TemporaryDirectory() as temporary:
            other = Path(temporary) / "windows-intel-other.json"
            other.write_text(json.dumps(capture), encoding="utf-8")
            result = run(str(INTEL_CAPTURE), str(other), "--release-pin", INTEL_PIN)
        self.assertEqual(result.returncode, 1)
        self.assertIn("differ on webgl2_caps_sha256, so they are not one GPU family", result.stderr)

    def test_a_host_architecture_the_captures_contradict_is_refused(self) -> None:
        result = run(str(ADRENO_CAPTURE), "--host-architecture", "x86")
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not report CPU architecture 'x86'", result.stderr)

    def test_relink_leaves_the_committed_corpus_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "anchors"
            shutil.copytree(ANCHORS, copy)
            result = run("--relink", "--anchors-dir", str(copy))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", "the committed anchors are not linked to each other")
            for path in sorted(ANCHORS.glob("*.json")):
                self.assertEqual((copy / path.name).read_bytes(), path.read_bytes())


if __name__ == "__main__":
    unittest.main()
