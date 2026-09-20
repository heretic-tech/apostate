#!/usr/bin/env python3
"""Make the pip and npm packages current with this checkout.

Two independent jobs, because they have different inputs:

**assets** -- copy the authoritative profile schema and profile catalogue into
both packages. The packages ship them as package data so an installed package
works with no checkout, which means every edit to ``config/profile.schema.json``
or ``resources/profiles/catalogue.json`` leaves two stale copies behind. Run
this after any such edit.

**manifest** -- write the release digests into both packages. The packages do
not bundle the browser; they download it on first use and verify it against a
manifest. That manifest is the trust anchor, so it must travel inside the
package: a digest fetched from the same place as the bytes it describes proves
nothing. This is the step that puts it there.

The manifest job consumes exactly what ``scripts/verify-release-inputs.sh``
consumes -- a directory of ``<archive>`` and ``<archive>.manifest.json`` pairs
-- and reuses ``.github/release/artifact-policy.json`` for the field set, the
platform set and the artifact filenames, so there is one contract rather than
two.

Usage:
  scripts/sync-packages.py                          copy the data assets
  scripts/sync-packages.py --release <inputs-dir>   also write the release digests
  scripts/sync-packages.py --check                  report staleness, write nothing

  --release   directory holding <archive> and <archive>.manifest.json pairs
  --tag       release tag the artifacts are published under; defaults to
              v<package_version>, the form docs/RELEASE.md step 2 requires
  --check     do not write; exit non-zero if either package is out of date.
              This is what CI runs to catch a release whose packages still
              carry the previous build's digests, or a schema edit that never
              reached the packages.

Manifest output is byte-identical across runs: sorted keys, compact separators,
UTF-8, one trailing newline. Asset output is a byte-for-byte copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / ".github/release/artifact-policy.json"
PACKAGE_ROOTS = (REPO_ROOT / "python/apostate/assets", REPO_ROOT / "npm/assets")
MANIFEST_NAME = "release-manifest.json"
#: Authoritative source for each file both packages ship as package data.
DATA_ASSETS = {
    "profile.schema.json": REPO_ROOT / "config/profile.schema.json",
    "catalogue.json": REPO_ROOT / "resources/profiles/catalogue.json",
}
REPOSITORY = "heretic-tech/apostate"
#: Fields copied from a per-artifact manifest into the package manifest. The
#: rest of the per-artifact contract (patch_series_sha256, build_manifest_sha256)
#: is build provenance the packages have no use for.
ARTIFACT_FIELDS = ("platform", "artifact", "sha256")

def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"


def _load(path: Path, description: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read {description}: {path}: {exc}")
    if not isinstance(parsed, dict):
        raise SystemExit(f"{description} must be a JSON object: {path}")
    return parsed


def build(inputs_dir: Path, tag: str | None) -> dict[str, Any]:
    policy = _load(POLICY_PATH, "release policy")
    contract = policy["manifest_contract"]
    required = set(contract["required_fields"])
    platforms = set(contract["platforms"])
    filenames = set(policy["artifacts"]["filenames"])

    manifests = sorted(inputs_dir.glob("*.manifest.json"))
    if not manifests:
        raise SystemExit(f"no *.manifest.json files in {inputs_dir}")

    artifacts: dict[str, dict[str, Any]] = {}
    revisions: set[str] = set()
    for manifest_path in manifests:
        data = _load(manifest_path, "artifact manifest")
        if set(data) != required:
            raise SystemExit(
                f"manifest fields do not match the release contract: {manifest_path}"
            )
        archive = manifest_path.parent / str(data["artifact"])
        if data["artifact"] not in filenames:
            raise SystemExit(f"unexpected artifact name: {data['artifact']}")
        if data["platform"] not in platforms:
            raise SystemExit(f"platform not named by the release policy: {data['platform']}")
        if data["platform"] in artifacts:
            raise SystemExit(f"duplicate platform: {data['platform']}")
        if data["package_version"] != contract["package_version"]:
            raise SystemExit(f"package_version mismatch: {manifest_path}")
        if data["chromium_version"] != contract["chromium_version"]:
            raise SystemExit(f"chromium_version mismatch: {manifest_path}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(data["sha256"])):
            raise SystemExit(f"sha256 is not a lowercase digest: {manifest_path}")
        if not archive.is_file():
            raise SystemExit(f"artifact missing beside its manifest: {archive}")
        # Re-hash rather than trust the manifest that travelled with the bytes.
        # This script is what makes the digest authoritative for every future
        # install, so it is the wrong place to take one on faith.
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != data["sha256"]:
            raise SystemExit(
                f"artifact SHA-256 does not match its manifest: {archive}\n"
                f"  manifest: {data['sha256']}\n  actual:   {actual}"
            )
        artifacts[str(data["platform"])] = {key: data[key] for key in ARTIFACT_FIELDS}
        revisions.add(str(data["source_revision"]))

    if len(revisions) != 1:
        raise SystemExit(f"artifacts name {len(revisions)} different source revisions: {sorted(revisions)}")

    catalogue_path = REPO_ROOT / "resources/profiles/catalogue.json"
    catalogue_version = _load(catalogue_path, "profile catalogue").get("catalogue_version")
    if not isinstance(catalogue_version, int) or isinstance(catalogue_version, bool):
        raise SystemExit(f"{catalogue_path} has no usable catalogue_version")

    return {
        "artifacts": artifacts,
        "catalogue_version": catalogue_version,
        "chromium_version": contract["chromium_version"],
        "package_version": contract["package_version"],
        "repository": REPOSITORY,
        "source_revision": revisions.pop(),
        "status": "published",
        "tag": tag or f"v{contract['package_version']}",
    }


def _sync(target: Path, payload: bytes, check: bool, stale: list[Path]) -> None:
    current = target.read_bytes() if target.is_file() else None
    if current == payload:
        return
    if check:
        stale.append(target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    print(f"wrote {target.relative_to(REPO_ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", type=Path, metavar="INPUTS_DIR",
                        help="directory of artifact/manifest pairs; also writes release digests")
    parser.add_argument("--tag", help="release tag; defaults to v<package_version>")
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args(argv)

    if args.tag is not None and not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag):
        raise SystemExit(f"tag must have the form vMAJOR.MINOR.PATCH: {args.tag}")
    if args.tag is not None and args.release is None:
        raise SystemExit("--tag only applies with --release")

    stale: list[Path] = []
    for name, source in DATA_ASSETS.items():
        if not source.is_file():
            raise SystemExit(f"authoritative asset is missing: {source}")
        payload = source.read_bytes()
        for assets in PACKAGE_ROOTS:
            _sync(assets / name, payload, args.check, stale)

    if args.release is not None:
        if not args.release.is_dir():
            raise SystemExit(f"no such directory: {args.release}")
        manifest = build(args.release, args.tag)
        payload = _canonical(manifest).encode("utf-8")
        for assets in PACKAGE_ROOTS:
            _sync(assets / MANIFEST_NAME, payload, args.check, stale)
        if not args.check:
            print(f"platforms: {', '.join(sorted(manifest['artifacts']))}")
            print(f"tag: {manifest['tag']}  revision: {manifest['source_revision']}")

    if stale:
        for path in stale:
            print(f"out of date: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        print("run scripts/sync-packages.py to update", file=sys.stderr)
        return 1
    if args.check:
        print("packages are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
