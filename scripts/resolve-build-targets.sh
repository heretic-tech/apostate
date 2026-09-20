#!/usr/bin/env bash
# Resolve which targets a build workflow may schedule, and on which runner.
#
# This is the runner-availability gate. A job pinned to a label no runner
# carries does not fail: it queues until GitHub gives up, holding the
# concurrency group while the workflow looks "running". The set of targets is
# therefore configuration (the repository variable APOSTATE_BUILD_TARGETS)
# rather than something baked into the workflow. The default is every target
# that has both build args and a Blacksmith runner label below.
#
# Reads:
#   REQUESTED_TARGETS   comma-separated override (workflow_dispatch input)
#   CONFIGURED_TARGETS  comma-separated repository variable
# Writes GitHub Actions step outputs on stdout:
#   targets=<comma-separated>
#   matrix=<compact JSON for strategy.matrix>
source "$(dirname "$0")/lib.sh"

python3 - "$REPO_ROOT" <<'PY'
import json, os, pathlib, sys

root = pathlib.Path(sys.argv[1])
raw = (os.environ.get("REQUESTED_TARGETS") or "").strip()
if not raw:
    raw = (os.environ.get("CONFIGURED_TARGETS") or "").strip()
# This map is the runner-availability gate: a target without a label here
# cannot be scheduled. Both Linux targets use the pinned linux/amd64 build
# container, which requires an x64 host, so linux-arm64 cross-compiles on the
# same runner as linux-x64. windows-x64 builds natively: the windows-2025
# image carries VS 2022 Build Tools, which vs_toolchain.py autodetects, but it
# lacks the SDK's Debugging Tools feature and the Visual Studio ATL component,
# both of which scripts/provision-windows-toolchain.sh installs before the sync.
runner_labels = {
    "linux-x64": ["warp-ubuntu-latest-x64-32x"],
    "linux-arm64": ["warp-ubuntu-latest-x64-32x"],
    "macos-arm64": ["warp-macos-26-arm64-12x"],
    "windows-x64": ["warp-windows-2025-x64-32x"],
}

if not raw:
    raw = ",".join(sorted(runner_labels))

targets: list[str] = []
for piece in raw.split(","):
    target = piece.strip()
    if not target or target in targets:
        continue
    if not (root / "build/args" / f"{target}.gn").is_file():
        raise SystemExit(f"unknown build target {target!r}: no build/args/{target}.gn")
    if target not in runner_labels:
        raise SystemExit(f"no runner is registered for {target}")
    targets.append(target)
if not targets:
    raise SystemExit("no build targets resolved")

include = [
    {
        "runner_labels": json.dumps(
            runner_labels[target],
            separators=(",", ":"),
        ),
        "target": target,
    }
    for target in targets
]
print("targets=" + ",".join(targets))
print("matrix=" + json.dumps({"include": include}, sort_keys=True, separators=(",", ":")))
PY
