"""Options, shared launches and the results file for the Apostate test suite."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402

PERSONAS = ("windows", "macos", "linux")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("apostate")
    group.addoption("--personas", default=",".join(PERSONAS), help="comma-separated personas (default: all three)")
    group.addoption("--seeds", default="42,1337", help="comma-separated seeds (default: 42,1337)")
    group.addoption("--modes", default="bare,package", help="bare (no automation), package (Python package), or both")
    group.addoption("--headed", action="store_true", help="open windows; on Linux with no display, Xvfb is started")
    group.addoption("--binary", default=None, help="browser executable (default: APOSTATE_BINARY or the installed one)")
    group.addoption("--results", default=None, help="write a JSON results file here")
    group.addoption("--live", action="store_true", help="also run the live tier against public detector sites")
    group.addoption("--controls", default="", help="live tier only: comma-separated control browsers (playwright, chrome)")
    group.addoption("--network", default=None,
                    help="free text for the results file, e.g. 'home broadband, no proxy' (default: direct or APOSTATE_PROXY)")


def _list(config: pytest.Config, name: str) -> list[str]:
    return [item.strip() for item in config.getoption(name).split(",") if item.strip()]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: needs the internet and a public detector site")
    config._apostate_records = []  # type: ignore[attr-defined]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live tier: pass --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    config = metafunc.config
    personas = _list(config, "--personas")
    seeds = _list(config, "--seeds")
    modes = _list(config, "--modes")
    if "case" in metafunc.fixturenames:
        cases = [(persona, seed, mode) for persona in personas for seed in seeds for mode in modes]
        metafunc.parametrize("case", cases, ids=[f"{p}-{s}-{m}" for p, s, m in cases], indirect=True)
    elif "persona_seed" in metafunc.fixturenames:
        pairs = [(persona, seed) for persona in personas for seed in seeds]
        metafunc.parametrize("persona_seed", pairs, ids=[f"{p}-{s}" for p, s in pairs])
    elif "persona" in metafunc.fixturenames:
        metafunc.parametrize("persona", personas)


@pytest.fixture(scope="session")
def server():
    probe_server = harness.ProbeServer()
    yield probe_server
    probe_server.close()


@pytest.fixture(scope="session")
def binary(pytestconfig: pytest.Config) -> str:
    return harness.binary_path(pytestconfig.getoption("--binary"))


@pytest.fixture(scope="session")
def probes(server: harness.ProbeServer, binary: str, pytestconfig: pytest.Config):
    """One launch per persona, seed and mode, shared by every test that reads it."""
    cache: dict[str, harness.Probe] = {}
    headed = pytestconfig.getoption("--headed")

    def get(persona: str | None, seed: str | None, mode: str, **extra: Any) -> harness.Probe:
        launch = harness.Launch(persona, seed, mode, headed=headed, **extra)
        key = launch.label() + json.dumps(extra, sort_keys=True)
        if key not in cache:
            cache[key] = harness.run_probe(server, binary, launch)
        return cache[key]

    return get


@pytest.fixture
def case(request: pytest.FixtureRequest, probes) -> harness.Probe:
    persona, seed, mode = request.param
    return probes(persona, seed, mode)


@pytest.fixture
def record(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Values a test measured, written to the results file beside its outcome."""
    data: dict[str, Any] = {}
    request.node._apostate_record = data
    return data


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        outcome = "xfailed" if hasattr(report, "wasxfail") else report.outcome
        entry = {
            "test": item.nodeid,
            "outcome": outcome,
            "seconds": round(report.duration, 2),
            "details": getattr(item, "_apostate_record", {}),
        }
        if report.outcome == "failed":
            entry["message"] = str(report.longrepr.reprcrash.message if hasattr(report.longrepr, "reprcrash") else report.longrepr)[:2000]
        elif outcome == "xfailed":
            entry["message"] = str(report.wasxfail)
        elif report.outcome == "skipped":
            entry["message"] = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) else ""
        item.config._apostate_records.append(entry)  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    path = session.config.getoption("--results")
    if not path:
        return
    try:
        import apostate
        versions = {"package": apostate.PACKAGE_VERSION, "chromium": apostate.CHROMIUM_VERSION}
    except ImportError:
        versions = {}
    config = session.config
    records = config._apostate_records  # type: ignore[attr-defined]
    document = {
        "suite": "apostate",
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "versions": versions,
        "binary": config.getoption("--binary") or "installed",
        "host": harness.host_facts(),
        "network": config.getoption("--network") or ("proxy from APOSTATE_PROXY" if os.environ.get("APOSTATE_PROXY") else "direct, no proxy"),
        "options": {
            "personas": _list(config, "--personas"),
            "seeds": _list(config, "--seeds"),
            "modes": _list(config, "--modes"),
            "headed": config.getoption("--headed"),
            "live": config.getoption("--live"),
        },
        "summary": {
            outcome: sum(1 for entry in records if entry["outcome"] == outcome)
            for outcome in ("passed", "failed", "xfailed", "skipped")
        },
        "results": records,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")
