#!/usr/bin/env python3
"""Focused tests for the shared launch and release contract validator."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CHROMIUM_VERSION = (ROOT / "build" / "CHROMIUM_VERSION").read_text(encoding="utf-8").strip()


def run_validator(kind: str, value: dict[str, object]) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate-release-contract.py"), "--kind", kind, stream.name],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


class ContractValidatorTests(unittest.TestCase):
    def test_schema_documents_validate_without_being_treated_as_data(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate-release-contract.py"), "--check-schemas"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launch_accepts_integer_and_string_seed(self) -> None:
        for fingerprint in (17, "stable-seed"):
            result = run_validator(
                "launch",
                {
                    "fingerprint": fingerprint,
                    "fingerprint_platform": "macos",
                    "profile": None,
                    "locale": None,
                    "timezone": None,
                    "geoip": False,
                    "proxy": None,
                    "headless": True,
                    "user_data_dir": None,
                    "args": [],
                },
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_launch_rejects_unknown_fields(self) -> None:
        value = {
            "fingerprint": "stable-seed",
            "fingerprint_platform": "macos",
            "profile": None,
            "locale": None,
            "timezone": None,
            "geoip": False,
            "proxy": None,
            "headless": True,
            "user_data_dir": None,
            "args": [],
            "unexpected": True,
        }
        self.assertNotEqual(run_validator("launch", value).returncode, 0)

    def test_manifest_requires_exactly_the_release_fields(self) -> None:
        value = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": 1,
            "platform": "linux-x64",
            "artifact": f"apostate-{CHROMIUM_VERSION}-linux-x64.tar.zst",
            "sha256": "a" * 64,
            "source_revision": "b" * 40,
            "patch_series_sha256": "c" * 64,
            "build_manifest_sha256": "d" * 64,
        }
        self.assertEqual(run_validator("manifest", value).returncode, 0)
        # The detached Ed25519 signature was retired for GitHub artifact
        # attestations. A manifest still carrying one is a manifest produced by
        # a tool that predates the cutover, so it must be rejected rather than
        # tolerated and silently ignored.
        with_signature = dict(value, signature="A" * 86 + "==")
        self.assertNotEqual(run_validator("manifest", with_signature).returncode, 0)
        bad_hash = dict(value, sha256="a" * 63)
        self.assertNotEqual(run_validator("manifest", bad_hash).returncode, 0)


    def test_profile_contains_requires_exactly_one_primary_display(self) -> None:
        display = {
            "left": 0, "top": 0, "width": 1920, "height": 1080,
            "avail_width": 1920, "avail_height": 1040, "device_pixel_ratio": 1,
            "color_depth": 24, "is_primary": True, "is_internal": True, "label": "Built-in",
        }
        base = {"screen": {"displays": [display]}}
        self.assertEqual(run_validator("profile", base).returncode, 0)
        no_primary = dict(base)
        no_primary["screen"] = {"displays": [{**display, "is_primary": False}]}
        self.assertNotEqual(run_validator("profile", no_primary).returncode, 0)
        two_primary = dict(base)
        two_primary["screen"] = {"displays": [display, {**display, "left": 1920, "is_primary": True}]}
        self.assertNotEqual(run_validator("profile", two_primary).returncode, 0)

    def test_profile_property_names_and_exclusive_minimum_are_enforced(self) -> None:
        invalid_name = {"gl_precisions": {"not-a-gl-key": {"rangeMin": 1, "rangeMax": 1, "precision": 1}}}
        self.assertNotEqual(run_validator("profile", invalid_name).returncode, 0)
        invalid_size = {"theme": {"system_fonts": {"caption": {"family": "Arial", "size_px": 0}}}}
        self.assertNotEqual(run_validator("profile", invalid_size).returncode, 0)

    def test_battery_step_accepts_every_level_the_schema_permits(self) -> None:
        """`multipleOf: 0.01` under binary floating point.

        All 101 levels the schema permits leave a nonzero `% 0.01` remainder
        except eight, so a modulo implementation rejects 93 legal profiles --
        including `level: 1.0`, the value the emitter reports whenever the
        battery is absent. Every level is asserted rather than a sample,
        because which ones survive `%` is an artefact of the representation
        and not a property anyone can reason about.
        """
        for step in range(101):
            value = {"battery": {"present": True, "charging": False, "level": step / 100,
                                 "discharging_time_seconds": 60 * step}}
            self.assertEqual(run_validator("profile", value).returncode, 0,
                             f"level {step / 100} was rejected")
        for off_step in ({"level": 0.875}, {"charging_time_seconds": 4230},
                         {"discharging_time_seconds": 90}):
            value = {"battery": {"present": True, "charging": True, "level": 0.5, **off_step}}
            self.assertNotEqual(run_validator("profile", value).returncode, 0, off_step)


if __name__ == "__main__":
    unittest.main()
