"""Live tier: public detector pages, loaded the way a user's script loads them.

Skipped unless ``--live`` is given. Needs the internet. Results depend on the
network: run behind the proxy you use in production (APOSTATE_PROXY), and
compare with a control browser on the same network (``--controls``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gaps  # noqa: E402
from detectors import DETECTORS  # noqa: E402

pytestmark = pytest.mark.live


def _installed_playwright_chromium(headless: bool) -> str | None:
    """The newest Chromium in Playwright's browser cache, for when the driver expects another build."""
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), Path.home() / "Library/Caches/ms-playwright",
             Path.home() / ".cache/ms-playwright", Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"]
    prefix = "chromium_headless_shell-" if headless else "chromium-"
    names = ("chrome-headless-shell", "chrome-headless-shell.exe", "Chromium", "chrome", "chrome.exe")
    found = []
    for root in filter(None, roots):
        root = Path(root)
        if not root.is_dir():
            continue
        for build in root.glob(prefix + "*"):
            for path in build.rglob("*"):
                if path.name in names and path.is_file() and os.access(path, os.X_OK):
                    found.append((int(build.name.rsplit("-", 1)[1]) if build.name.rsplit("-", 1)[1].isdigit() else 0, str(path)))
    return max(found)[1] if found else None


def _browsers(config: pytest.Config) -> list[str]:
    personas = [item.strip() for item in config.getoption("--personas").split(",") if item.strip()]
    controls = [item.strip() for item in config.getoption("--controls").split(",") if item.strip()]
    return [f"apostate-{persona}" for persona in personas] + [f"control-{name}" for name in controls]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "live_case" in metafunc.fixturenames:
        cases = [(detector, browser) for browser in _browsers(metafunc.config) for detector in DETECTORS]
        metafunc.parametrize("live_case", cases, ids=[f"{d.name}-{b}" for d, b in cases])


@pytest.fixture(scope="module")
def live_browsers(pytestconfig: pytest.Config, binary: str):
    opened: dict[str, object] = {}
    extra: list = []
    headed = pytestconfig.getoption("--headed")
    seed = pytestconfig.getoption("--seeds").split(",")[0].strip()

    def close_all() -> None:
        for browser in opened.values():
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
        opened.clear()
        while extra:
            extra.pop().stop()

    def get(kind: str):
        if kind in opened:
            return opened[kind]
        # Playwright's and Patchright's sync drivers cannot run in one thread
        # together, and tests run one browser at a time anyway.
        close_all()
        if kind.startswith("apostate-"):
            import apostate
            browser = apostate.launch(fingerprint=seed, fingerprint_platform=kind.split("-", 1)[1],
                                      headless=not headed, binary_path=binary,
                                      proxy=os.environ.get("APOSTATE_PROXY"))
        else:
            from playwright.sync_api import sync_playwright
            driver = sync_playwright().start()
            extra.append(driver)
            name = kind.split("-", 1)[1]
            options = {"headless": not headed}
            if name == "chrome":
                options["channel"] = "chrome"
            elif name != "playwright":
                pytest.skip(f"unknown control {name!r}: use playwright or chrome")
            if os.environ.get("APOSTATE_PROXY"):
                options["proxy"] = {"server": os.environ["APOSTATE_PROXY"]}
            try:
                browser = driver.chromium.launch(**options)
            except Exception as exc:  # noqa: BLE001 - a missing control browser is a skip
                fallback = _installed_playwright_chromium(not headed) if name == "playwright" else None
                if fallback is None:
                    pytest.skip(f"control {name} did not start: {str(exc).splitlines()[0]}")
                browser = driver.chromium.launch(executable_path=fallback, **options)
        opened[kind] = browser
        return browser

    yield get
    close_all()


def test_detector(live_case, live_browsers, record, pytestconfig):
    detector, kind = live_case
    browser = live_browsers(kind)
    page = browser.new_page()
    try:
        result = detector.measure(page)
    finally:
        page.close()
    record.update(detector=detector.name, url=detector.url, browser=kind, criterion=detector.what, **result)
    if kind.startswith("control-"):
        record["control"] = True
        return
    if not result["ready"]:
        pytest.skip(f"inconclusive: {detector.name} produced no result")
    if result["passed"] is None:
        return
    if not result["passed"] and detector.name == "iphey":
        signals = " ".join(result.get("signals", []))
        known = {"(roadmap)": "font-filter-iphey", "(butterfly)": "arm-windows-gpu"}
        found = [gap for marker, gap in known.items() if marker in signals]
        rest = [line for line in result.get("signals", []) if not any(marker in line for marker in known)]
        if found and not rest:
            gaps.expect(record, found[0] if len(found) == 1 else "font-filter-iphey")
    assert result["passed"], f"{detector.name}: {detector.what} failed: {result}"
