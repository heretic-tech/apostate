#!/usr/bin/env python3
"""Run the release-candidate checks that are available in a checkout.

This is an evidence collector, not a build driver.  It only invokes existing
local validators/tests, never downloads or publishes anything, and records
missing release prerequisites as ``blocked`` instead of silently treating them
as successful.  The JSON output is stable so it can be compared in CI or
saved as a handoff receipt.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ("linux-x64", "tar.zst"),
    ("linux-arm64", "tar.zst"),
    ("macos-arm64", "zip"),
    ("windows-x64", "zip"),
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CheckSpec:
    id: str
    label: str
    command: tuple[str, ...] | None = None
    reason: str | None = None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _record(
    spec: CheckSpec,
    *,
    status: str,
    root: Path,
    result: subprocess.CompletedProcess[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build one JSON-safe record with a fixed set of keys."""
    record: dict[str, Any] = {
        "id": spec.id,
        "label": spec.label,
        "status": status,
        "command": list(spec.command) if spec.command is not None else None,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "reason": reason,
    }
    if result is not None:
        record["exit_code"] = int(result.returncode)
        record["stdout"] = _text(result.stdout)
        record["stderr"] = _text(result.stderr)
    return record


def classify_result(returncode: int) -> str:
    """Map a subprocess exit status to the report status."""
    return "pass" if returncode == 0 else "fail"


def _invoke(command: Sequence[str], root: Path, runner: Runner) -> subprocess.CompletedProcess[str]:
    # The environment prevents Python commands from creating __pycache__ files
    # and keeps package tooling in offline mode.  It does not alter the caller's
    # environment or permit a network operation.
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["npm_config_offline"] = "true"
    return runner(
        list(command),
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_spec(spec: CheckSpec, root: Path, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Run one spec, or return its explicit blocked record."""
    if spec.command is None:
        return _record(spec, status="blocked", root=root, reason=spec.reason)
    try:
        result = _invoke(spec.command, root, runner)
    except FileNotFoundError as error:
        return _record(spec, status="blocked", root=root, reason=f"command dependency is unavailable: {error}")
    except OSError as error:
        return _record(spec, status="fail", root=root, reason=f"could not start command: {error}")
    return _record(spec, status=classify_result(result.returncode), root=root, result=result)


def _python(root: Path, script: Path, *args: str) -> tuple[str, ...]:
    return (sys.executable, _relative(script, root), *args)


def _test_files(root: Path, directory: Path, pattern: str = "test*.py") -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob(pattern) if path.is_file())


def _first_existing(root: Path, candidates: Iterable[str]) -> Path | None:
    for name in candidates:
        path = root / name
        if path.is_file():
            return path
    return None


def _manifest_specs(root: Path) -> list[CheckSpec]:
    script = root / "scripts" / "validate-release-contract.py"
    manifests = sorted(
        path for path in (root / "release").glob("*.json")
        if path.is_file() and path.name != "manifest.schema.json"
    )
    if not manifests:
        return [CheckSpec("release-manifests", "release manifest validation", reason="no release manifest artifacts are present")]
    if not script.is_file():
        return [CheckSpec("release-manifests", "release manifest validation", reason=f"validator absent: {_relative(script, root)}")]
    return [
        CheckSpec(
            f"release-manifest-{path.stem}",
            f"release manifest validation ({_relative(path, root)})",
            command=_python(root, script, "--kind", "manifest", _relative(path, root)),
        )
        for path in manifests
    ]


def _profile_test_specs(root: Path) -> list[CheckSpec]:
    candidates: set[Path] = set()
    for directory in (root / "scripts", root / "python" / "tests", root / "node" / "tests"):
        if directory.is_dir():
            candidates.update(path for path in directory.glob("test*profile*.py") if path.is_file())
            candidates.update(path for path in directory.glob("test*resolver*.py") if path.is_file())
            candidates.update(path for path in directory.glob("test*catalogue*.py") if path.is_file())
    if not candidates:
        return [CheckSpec("profile-resolver-tests", "profile resolver tests", reason="no profile resolver test command is present")]
    return [
        CheckSpec(f"profile-resolver-{path.stem}", f"profile resolver test ({_relative(path, root)})", command=_python(root, path))
        for path in sorted(candidates)
    ]


def _geoip_specs(root: Path) -> list[CheckSpec]:
    candidates: set[Path] = set()
    for directory in (root / "scripts", root / "capture", root / "python" / "tests", root / "node" / "tests"):
        if directory.is_dir():
            candidates.update(path for path in directory.rglob("test*geoip*.py") if path.is_file())
            candidates.update(path for path in directory.rglob("*geoip*test*.py") if path.is_file())
    specs: list[CheckSpec] = []
    if candidates:
        specs.extend(
            CheckSpec(f"geoip-{path.stem}", f"GeoIP test ({_relative(path, root)})", command=_python(root, path))
            for path in sorted(candidates)
        )
    else:
        specs.append(CheckSpec("geoip-tests", "GeoIP tests", reason="no local GeoIP test command is present"))
    endpoint = os.environ.get("APOSTATE_GEOIP_ENDPOINT", "").strip()
    endpoint_reason = (
        "network access is disabled; external detector endpoint was not probed"
        if endpoint
        else "external detector endpoint is not configured"
    )
    specs.append(CheckSpec("geoip-external-detector", "external GeoIP detector", reason=endpoint_reason))
    return specs


def _python_package_specs(root: Path) -> list[CheckSpec]:
    tests = _test_files(root, root / "python" / "tests")
    if not tests:
        return [CheckSpec("python-package-tests", "Python package tests", reason="no Python package test files are present")]
    return [
        CheckSpec(
            "python-package-tests",
            "Python source-level package tests",
            command=(sys.executable, "-m", "unittest", "discover", "-s", "python/tests", "-p", "test*.py"),
        )
    ]


def _node_package_specs(root: Path) -> list[CheckSpec]:
    package = _first_existing(root, ("npm/package.json", "node/package.json", "packages/node/package.json", "js/package.json"))
    if package is None:
        return [CheckSpec("node-package-tests", "Node package tests", reason="Node package manifest is absent")]
    package_root = package.parent
    return [
        CheckSpec(
            "node-package-tests",
            "Node source-level package tests",
            command=("npm", "--prefix", _relative(package_root, root), "test"),
        )
    ]

def _target_manifest(root: Path, target: str, artifact_name: str) -> Path | None:
    release = root / "release"
    direct = (
        release / f"{artifact_name}.manifest.json",
        release / f"manifest-{target}.json",
        release / f"{target}.manifest.json",
    )
    for candidate in direct:
        if candidate.is_file():
            return candidate
    # The contract does not require one particular manifest filename.  Match
    # the immutable artifact field so a different filename cannot satisfy the
    # wrong target by accident.
    for candidate in sorted(release.glob("*.json")):
        if candidate.name == "manifest.schema.json" or not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("platform") == target and payload.get("artifact") == artifact_name:
            return candidate
    return None


def _artifact_specs(root: Path) -> list[CheckSpec]:
    version_path = root / "build" / "CHROMIUM_VERSION"
    version = "unknown"
    if version_path.is_file():
        values = [line.strip() for line in version_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if len(values) == 1 and re.fullmatch(r"[0-9]+(?:\.[0-9]+){3,4}", values[0]):
            version = values[0]
    specs: list[CheckSpec] = []
    release = root / "release"
    for target, extension in TARGETS:
        artifact_name = f"apostate-{version}-{target}.{extension}"
        artifact = release / artifact_name
        manifest = _target_manifest(root, target, artifact_name)
        if not artifact.is_file():
            reason = f"target output is absent: {_relative(artifact, root)}"
        elif manifest is None:
            reason = f"release manifest for {target} is absent or does not name {artifact_name}"
        else:
            reason = "artifact and manifest present; this checker does not verify archive SHA-256 or build-provenance attestations"
        specs.append(CheckSpec(f"artifact-{target}", f"four-target artifact ({target})", reason=reason))
    return specs


def _contract_specs(root: Path) -> list[CheckSpec]:
    script = root / "scripts" / "validate-release-contract.py"
    if not script.is_file():
        return [CheckSpec("release-contract", "canonical schema and manifest contract", reason=f"validator absent: {_relative(script, root)}")]
    documents = [root / "config" / "launch.schema.json", root / "release" / "manifest.schema.json"]
    missing = [path for path in documents if not path.is_file()]
    if missing:
        return [CheckSpec("release-contract", "canonical schema and manifest contract", reason="contract document absent: " + ", ".join(_relative(path, root) for path in missing))]
    return [CheckSpec("release-contract", "canonical schema and manifest contract", command=_python(root, script) + ("--check-schemas",))]

def _package_artifact_specs(root: Path) -> list[CheckSpec]:
    specs: list[CheckSpec] = []
    python_candidates = [
        path for directory in (root / "dist", root / "python" / "dist")
        if directory.is_dir()
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".whl", ".gz", ".zip"}
    ]
    if python_candidates:
        reason = "Python package artifacts present; this checker does not exercise installation or verify archive SHA-256 or build-provenance attestations"
    else:
        reason = "Python package release artifacts are absent (expected wheel or source archive under dist/ or python/dist/)"
    specs.append(CheckSpec("python-package-artifacts", "Python package release artifacts", reason=reason))

    node_dirs = (root / "npm" / "dist", root / "node" / "dist", root / "packages" / "node" / "dist", root / "js" / "dist")
    node_candidates = [
        path for directory in node_dirs
        if directory.is_dir()
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".tgz", ".zip"}
    ]
    if node_candidates:
        reason = "Node package artifacts present; this checker does not exercise installation or verify archive SHA-256 or build-provenance attestations"
    else:
        reason = "Node package release artifacts are absent (expected tarball under npm/dist/, node/dist/, or packages/node/dist/)"
    specs.append(CheckSpec("node-package-artifacts", "Node package release artifacts", reason=reason))
    return specs


def _fixed_specs(root: Path) -> list[CheckSpec]:
    specs: list[CheckSpec] = []
    baseline = root / "scripts" / "validate-release-baseline.py"
    if baseline.is_file():
        specs.append(CheckSpec("release-baseline", "release baseline validation", command=_python(root, baseline, "--root", ".")) )
    else:
        specs.append(CheckSpec("release-baseline", "release baseline validation", reason=f"validator absent: {_relative(baseline, root)}"))
    return specs


def _milestone_specs(root: Path) -> list[CheckSpec]:
    source = root / ".workspace" / "src"
    source_reason = f"Chromium source checkout is absent: {_relative(source, root)}"
    return [
        CheckSpec("v2-native", "V2 native process evidence", reason=source_reason if not source.is_dir() else "native release binary and process evidence are not recorded"),
        CheckSpec("v3-conformance", "V3 corpus conformance", reason=source_reason if not source.is_dir() else "release binary and matching conformance run are not recorded"),
        CheckSpec("v4-four-target", "V4 four-target evidence", reason="four target artifacts and independent runner evidence are not recorded"),
    ]


def collect_report(root: Path = ROOT, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Run all local checks and return a deterministic report mapping."""
    root = root.resolve()
    specs: list[CheckSpec] = []
    specs.extend(_package_artifact_specs(root))
    specs.extend(_fixed_specs(root))
    specs.extend(_manifest_specs(root))
    specs.extend(_profile_test_specs(root))
    specs.extend(_geoip_specs(root))
    specs.extend(_python_package_specs(root))
    specs.extend(_node_package_specs(root))
    specs.extend(_artifact_specs(root))
    specs.extend(_contract_specs(root))
    specs.extend(_milestone_specs(root))
    records = [run_spec(spec, root, runner) for spec in sorted(specs, key=lambda item: item.id)]
    summary = {status: sum(record["status"] == status for record in records) for status in ("pass", "fail", "blocked")}
    overall = "fail" if summary["fail"] else ("blocked" if summary["blocked"] else "pass")
    return {
        "schema_version": 1,
        "overall": overall,
        "summary": summary,
        "checks": records,
    }

def run_checks(root: Path = ROOT, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Compatibility-friendly entry point for callers embedding the checker."""
    return collect_report(root, runner)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: parent of scripts/)")
    parser.add_argument("--json", action="store_true", help="emit the deterministic JSON report")
    return parser


def render_human(report: dict[str, Any]) -> str:
    lines = [f"release-candidate: {report['overall']}"]
    for record in report["checks"]:
        line = f"{record['status'].upper():7} {record['id']}"
        if record["reason"]:
            line += f" - {record['reason']}"
        elif record["exit_code"] is not None:
            line += f" (exit {record['exit_code']})"
        lines.append(line)
    summary = report["summary"]
    lines.append(f"summary: {summary['pass']} pass, {summary['fail']} fail, {summary['blocked']} blocked")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = collect_report(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print(render_human(report))
    return {"pass": 0, "blocked": 2, "fail": 1}[report["overall"]]


if __name__ == "__main__":
    raise SystemExit(main())
