"""The same seed gives the same machine; reads repeat; a persistent profile keeps its machine."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import gaps
import harness

#: Values that legitimately change between two launches of the same machine.
VOLATILE = {"token", "headers", "error"}


def _strip(value):
    if isinstance(value, dict):
        # Chrome adds per-origin noise to downlink and rtt, with a new salt each launch.
        return {key: _strip(item) for key, item in value.items()
                if key not in VOLATILE and not (key in ("downlink", "rtt") and "effectiveType" in value)}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _diff(left, right, path=""):
    if isinstance(left, dict) and isinstance(right, dict):
        out = []
        for key in sorted(set(left) | set(right)):
            out += _diff(left.get(key), right.get(key), f"{path}.{key}" if path else key)
        return out
    if left != right:
        return [f"{path}: {json.dumps(left)[:120]} != {json.dumps(right)[:120]}"]
    return []


def test_reads_repeat_within_a_session(case, record):
    page = case.values["page"]
    record.update(canvas=page["canvas"], audio=page["audioHash"])
    assert page["canvas"][0] == page["canvas"][1]
    assert page["audioHash"][0] == page["audioHash"][1]


def test_same_seed_same_machine(persona_seed, server, binary, probes, record):
    persona, seed = persona_seed
    first = probes(persona, seed, "bare")
    second = harness.run_probe(server, binary, harness.Launch(persona, seed, "bare"))
    profile_differences = _diff(first.profile, second.profile)
    page_differences = _diff(_strip(first.values), _strip(second.values))
    record.update(profile_differences=profile_differences, page_differences=page_differences[:50])
    assert profile_differences == []
    assert page_differences == []


def test_the_package_serves_the_same_machine(persona_seed, probes, record):
    """The Python package adds nothing a page can see: same seed, same profile, same page values."""
    persona, seed = persona_seed
    bare = probes(persona, seed, "bare")
    package = probes(persona, seed, "package")
    profile_differences = _diff(bare.profile, package.profile)
    page_differences = _diff(_strip(bare.values), _strip(package.values))
    record.update(profile_differences=profile_differences, page_differences=page_differences[:50])
    assert profile_differences == []
    inner = bare.values["page"]["screen"]["innerHeight"], package.values["page"]["screen"]["innerHeight"]
    fields = {item.split(":")[0] for item in page_differences}
    pointer = {"page.media.pointerFine", "page.media.hover"}
    if fields & pointer and fields <= pointer | {"page.screen.innerHeight"}:
        gaps.expect(record, "host-pointer")
    if fields == {"page.screen.innerHeight"} and inner[1] - inner[0] == 56:
        gaps.expect(record, "api-keys-infobar")
    assert page_differences == []


def test_different_seeds_give_different_machines(persona, probes, pytestconfig, record):
    seeds = [item for item in pytestconfig.getoption("--seeds").split(",") if item.strip()]
    if len(seeds) < 2:
        import pytest
        pytest.skip("needs two seeds")
    first = probes(persona, seeds[0], "bare").profile
    second = probes(persona, seeds[1], "bare").profile
    record.update(seeds=seeds[:2], first_id=first["id"], second_id=second["id"])
    assert first["id"] != second["id"]
    assert _diff(first, second), "two seeds composed the same profile"


def test_persistent_profile_keeps_its_machine(persona, server, binary, record):
    directory = tempfile.mkdtemp(prefix="apostate-persistent-")
    try:
        runs = [harness.run_probe(server, binary, harness.Launch(persona, None, "bare", user_data_dir=directory))
                for _ in range(3)]
        identity = (Path(directory) / "apostate" / "identity").read_text().strip()
        differences = [_diff(runs[0].profile, run.profile) for run in runs[1:]]
        record.update(identity=identity[:12] + "...", differences=differences)
        assert all(not item for item in differences)
        (Path(directory) / "apostate" / "identity").unlink()
        fresh = harness.run_probe(server, binary, harness.Launch(persona, None, "bare", user_data_dir=directory))
        assert fresh.profile["id"] != runs[0].profile["id"], "deleting the identity file kept the same machine"
    finally:
        shutil.rmtree(directory, ignore_errors=True)
