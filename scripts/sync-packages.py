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

Two version lines meet here and they are not the same line. The policy's
``package_version`` is the identity of the *binary release*: the artifacts
were built for it and their manifests carry it. The launcher's version is
whatever ``python/apostate/config.py`` and ``npm/package.json`` say, and it
moves on its own -- 0.1.1 is a launcher fix that installs the binaries
published as v0.1.0. So the written manifest carries the launcher's version
(the package it ships in) and the ``tag`` (the release it installs from),
and the launcher's version is required to be at or above the policy's
rather than equal to it. Equality was the rule that made a launcher fix
impossible to ship without rebuilding Chromium.

Usage:
  scripts/sync-packages.py                          copy the data assets
  scripts/sync-packages.py --release <inputs-dir>   also write the release digests
  scripts/sync-packages.py --check                  report staleness, write nothing

  --release   directory holding <archive> and <archive>.manifest.json pairs
  --tag       release tag the artifacts are published under; defaults to
              v<policy package_version>, which is the release the artifacts
              in --release came from, in the form docs/RELEASE.md step 2 requires
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
    # The country -> locale table both launchers infer a locale from. It
    # covers every country, so a Malaysian exit resolves ms rather than no
    # locale at all.
    "country-locales.json": REPO_ROOT / "config/country-locales.json",
    # The font packs. `apostate fonts install windows` installs the files of
    # the Windows core pack's families and names the ones still missing.
    "font_packs.json": REPO_ROOT / "resources/profiles/dispersion/font_packs.json",
}
REPOSITORY = "heretic-tech/apostate"
#: Fields copied from a per-artifact manifest into the package manifest. The
#: rest of the per-artifact contract (patch_series_sha256, build_manifest_sha256)
#: is build provenance the packages have no use for.
ARTIFACT_FIELDS = ("platform", "artifact", "sha256")
#: Where the launcher's own version is written. All three must agree; the
#: check lives here because this is the script a release runs, and a bump
#: that reached two of the three is a package that installs nothing.
VERSION_SOURCES = {
    "python/apostate/config.py": re.compile(r'^PACKAGE_VERSION\s*=\s*"([^"]+)"', re.M),
    "python/pyproject.toml": re.compile(r'^version\s*=\s*"([^"]+)"', re.M),
    "npm/package.json": re.compile(r'"version"\s*:\s*"([^"]+)"'),
}
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def package_version() -> str:
    """The launcher's version, once every file that states it agrees."""
    found: dict[str, str] = {}
    for relative, pattern in VERSION_SOURCES.items():
        path = REPO_ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"cannot read {relative}: {exc}")
        match = pattern.search(text)
        if match is None:
            raise SystemExit(f"no package version found in {relative}")
        found[relative] = match.group(1)
    versions = set(found.values())
    if len(versions) != 1:
        listed = ", ".join(f"{name}={value}" for name, value in sorted(found.items()))
        raise SystemExit(f"package versions disagree: {listed}")
    version = versions.pop()
    if not _VERSION_RE.fullmatch(version):
        raise SystemExit(f"package version must be MAJOR.MINOR.PATCH: {version}")
    return version


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
    launcher = package_version()
    if _version_tuple(launcher) < _version_tuple(str(contract["package_version"])):
        raise SystemExit(
            f"package version {launcher} is older than the release policy's "
            f"{contract['package_version']}: a launcher cannot ship binaries "
            "published after it"
        )

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
        # The artifact manifests belong to the binary release, so they carry
        # the policy's version, not the launcher's.
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
        "package_version": launcher,
        "repository": REPOSITORY,
        "source_revision": revisions.pop(),
        "status": "published",
        # The release the archives live in, which is what turns a digest into
        # a download URL. It is the policy's version and not the launcher's:
        # 0.1.1 installs from v0.1.0.
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


# Every place the package version is written. A release whose sites disagree
# ships a package that downloads another release's browser.
VERSION_SITES = (
    ("python/pyproject.toml", r'^version = "([^"]+)"'),
    ("python/apostate/config.py", r'^PACKAGE_VERSION = "([^"]+)"'),
    ("npm/package.json", r'^  "version": "([^"]+)"'),
    ("npm/src/index.ts", r'^export const PACKAGE_VERSION = "([^"]+)"'),
    (".github/release/artifact-policy.json", r'"package_version": "([^"]+)"'),
)


def version_disagreements() -> list[str]:
    found = {}
    for rel, pattern in VERSION_SITES:
        match = re.search(pattern, (REPO_ROOT / rel).read_text(encoding="utf-8"), re.M)
        found[rel] = match.group(1) if match else "missing"
    if len(set(found.values())) == 1:
        return []
    return [f"{rel}: {version}" for rel, version in found.items()]

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

    mismatched = version_disagreements()
    if mismatched:
        print("package version differs between sites:\n  " + "\n  ".join(mismatched), file=sys.stderr)
        return 1
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
