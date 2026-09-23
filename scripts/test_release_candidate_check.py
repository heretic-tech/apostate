#!/usr/bin/env python3
"""Focused tests for scripts/release-candidate-check.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("release-candidate-check.py")
SPEC = importlib.util.spec_from_file_location("release_candidate_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["release_candidate_check"] = CHECK
SPEC.loader.exec_module(CHECK)


class FakeRunner:
    def __init__(self, returncodes: dict[str, int] | None = None):
        self.returncodes = returncodes or {}
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        key = command[0]
        return subprocess.CompletedProcess(
            command,
            self.returncodes.get(key, 0),
            stdout=f"stdout:{key}\n",
            stderr=f"stderr:{key}\n" if self.returncodes.get(key, 0) else "",
        )


class ReleaseCandidateCheckTests(unittest.TestCase):
    def test_classify_result_has_only_pass_or_fail(self):
        self.assertEqual(CHECK.classify_result(0), "pass")
        self.assertEqual(CHECK.classify_result(1), "fail")
        self.assertEqual(CHECK.classify_result(127), "fail")

    def test_blocked_spec_is_not_invoked(self):
        runner = FakeRunner()
        spec = CHECK.CheckSpec("missing", "missing prerequisite", reason="required input absent")
        record = CHECK.run_spec(spec, Path.cwd(), runner)
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["reason"], "required input absent")
        self.assertEqual(runner.calls, [])
        self.assertIsNone(record["exit_code"])

    def test_command_output_and_exit_code_are_captured(self):
        runner = FakeRunner({"tool": 3})
        spec = CHECK.CheckSpec("tool", "tool check", command=("tool", "arg"))
        record = CHECK.run_spec(spec, Path.cwd(), runner)
        self.assertEqual(record["status"], "fail")
        self.assertEqual(record["exit_code"], 3)
        self.assertEqual(record["stdout"], "stdout:tool\n")
        self.assertEqual(record["stderr"], "stderr:tool\n")
    def test_report_is_deterministic_and_absent_gates_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            # These files make the scripts discoverable; the fake runner
            # means no source or build is launched by this test.
            for name in ("validate-release-baseline.py", "manifest.py"):
                (root / "scripts" / name).write_text("", encoding="utf-8")
            runner = FakeRunner()
            first = CHECK.collect_report(root, runner)
            second = CHECK.collect_report(root, runner)
        self.assertEqual(first, second)
        self.assertEqual(first["overall"], "blocked")
        self.assertGreater(first["summary"]["blocked"], 0)
        self.assertEqual(
            [record["id"] for record in first["checks"]],
            sorted(record["id"] for record in first["checks"]),
        )
        self.assertIn("v2-native", {record["id"] for record in first["checks"]})
        self.assertIn("v3-conformance", {record["id"] for record in first["checks"]})
        self.assertIn("v4-four-target", {record["id"] for record in first["checks"]})
    def test_release_manifests_use_signed_contract_validator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release").mkdir()
            (root / "scripts").mkdir()
            (root / "scripts" / "validate-release-contract.py").write_text("", encoding="utf-8")
            (root / "release" / "artifact.json").write_text("{}", encoding="utf-8")
            specs = CHECK._manifest_specs(root)
        self.assertEqual(len(specs), 1)
        self.assertTrue(any(item.endswith("validate-release-contract.py") for item in specs[0].command))
        self.assertIn("--kind", specs[0].command)
        self.assertIn("manifest", specs[0].command)
        self.assertFalse(any(item.endswith("manifest.py") for item in specs[0].command))

    def test_runner_environment_disables_bytecode_and_network_package_mode(self):
        seen = {}

        def runner(command, **kwargs):
            seen.update(kwargs["env"])
            return subprocess.CompletedProcess(command, 0, "", "")

        CHECK.run_spec(CHECK.CheckSpec("x", "x", command=("x",)), Path.cwd(), runner)
        self.assertEqual(seen["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(seen["npm_config_offline"], "true")


if __name__ == "__main__":
    unittest.main()
