"""Public detector pages: how to load each one, when it has finished, and what it said.

Each detector returns a dict with:

- ``ready``: the page produced its result. A page that did not is
  inconclusive, never a pass.
- ``passed``: the page's own verdict mapped to True or False, or None for a
  page that only reports a score.
- the values the verdict came from.

Parsers read the rendered text of the page, so a redesign shows up as
``ready: False`` rather than as a silent pass.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

TIMEOUT = 45


def _text(page: Any) -> str:
    return page.evaluate("document.body ? document.body.innerText : ''")


def _wait_for(page: Any, predicate: Callable[[str], bool], timeout: float = TIMEOUT) -> str:
    """The page text once *predicate* holds, or the last text read."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        try:
            text = _text(page)
        except Exception:  # noqa: BLE001 - the page may be navigating
            text = ""
        if predicate(text):
            return text
        time.sleep(1)
    return text


def sannysoft(page: Any) -> dict:
    page.goto("https://bot.sannysoft.com", wait_until="domcontentloaded", timeout=60000)
    script = """() => {
      const cells = [...document.querySelectorAll('td.passed, td.failed, td.warn')];
      return {passed_rows: cells.filter(c => c.classList.contains('passed')).length,
              failed: cells.filter(c => c.classList.contains('failed')).map(c => (c.previousElementSibling || c).innerText.trim().split('\\n')[0]),
              warn: cells.filter(c => c.classList.contains('warn')).map(c => (c.previousElementSibling || c).innerText.trim().split('\\n')[0])};
    }"""
    deadline = time.monotonic() + TIMEOUT
    result = page.evaluate(script)
    while time.monotonic() < deadline and result["passed_rows"] + len(result["failed"]) < 20:
        time.sleep(1)
        result = page.evaluate(script)
    ready = result["passed_rows"] + len(result["failed"]) >= 20
    return {"ready": ready, "passed": ready and not result["failed"], **result}


def deviceandbrowserinfo(page: Any) -> dict:
    page.goto("https://deviceandbrowserinfo.com/are_you_a_bot", wait_until="domcontentloaded", timeout=60000)
    _wait_for(page, lambda text: '"isBot"' in text)
    raw = page.evaluate("""() => {
      const el = [...document.querySelectorAll('pre, code')].find(e => e.textContent.includes('"isBot"'));
      return el ? el.textContent : null;
    }""")
    if not raw:
        return {"ready": False, "passed": None}
    data = json.loads(raw)
    flags = sorted(name for name, value in data.get("details", {}).items() if value is True)
    return {"ready": True, "passed": data.get("isBot") is False, "isBot": data.get("isBot"), "flags": flags}


REBROWSER_ROW = re.compile(r"^(🟢|🔴|🟡|⚪️|⚪)\s*(\w+)", re.M)


def rebrowser(page: Any) -> dict:
    page.goto("https://bot-detector.rebrowser.net", wait_until="domcontentloaded", timeout=60000)
    text = _wait_for(page, lambda t: re.search(r"^(🟢|🔴)\s*useragent", t, re.M) is not None)
    rows = REBROWSER_ROW.findall(text)
    if not rows:
        return {"ready": False, "passed": None}
    red = sorted(name for mark, name in rows if mark == "🔴")
    green = sorted(name for mark, name in rows if mark == "🟢")
    return {"ready": any(name == "useragent" for _, name in rows), "passed": not red, "red": red, "green": green}


def creepjs(page: Any) -> dict:
    page.goto("https://abrahamjuliot.github.io/creepjs/", wait_until="domcontentloaded", timeout=60000)
    text = _wait_for(page, lambda t: "% stealth" in t, timeout=60)
    values = {}
    for key, pattern in (("like_headless", r"(\d+)% like headless"), ("headless", r"(\d+)% headless"),
                         ("stealth", r"(\d+)% stealth")):
        match = re.search(pattern, text)
        values[key] = int(match.group(1)) if match else None
    ready = None not in values.values()
    return {"ready": ready, "passed": ready and values["headless"] == 0 and values["stealth"] == 0, **values}


def browserscan(page: Any) -> dict:
    page.goto("https://www.browserscan.net/bot-detection", wait_until="domcontentloaded", timeout=60000)
    text = _wait_for(page, lambda t: re.search(r"Test Results:\s*\n\s*(Normal|Abnormal|Robot)", t) is not None)
    match = re.search(r"Test Results:\s*\n\s*(\w+)", text)
    if not match:
        return {"ready": False, "passed": None}
    return {"ready": True, "passed": match.group(1) == "Normal", "result": match.group(1)}


def fingerprint_scan(page: Any) -> dict:
    page.goto("https://fingerprint-scan.com", wait_until="domcontentloaded", timeout=60000)
    text = _wait_for(page, lambda t: "Fingerprint collected" in t and re.search(r"Bot score \d+/100", t) is not None)
    match = re.search(r"Bot score (\d+)/100", text)
    if not match:
        return {"ready": False, "passed": None}
    categories = dict(re.findall(r"^(Hardware / OS|Browser|Network / proxy): (\w+)", text, re.M))
    return {"ready": True, "passed": None, "score": int(match.group(1)), "categories": categories}


def iphey(page: Any) -> dict:
    page.goto("https://iphey.com", wait_until="domcontentloaded", timeout=60000)
    text = _wait_for(page, lambda t: "Trustworthy" in t[:400] or "Unreliable" in t[:400], timeout=40)
    head = text[:400]
    verdict = "Trustworthy" if "Trustworthy" in head else "Unreliable" if "Unreliable" in head else None
    signals: list[str] = []
    if verdict == "Unreliable":
        try:
            page.get_by_text("Click here to see").first.click(timeout=5000)
            time.sleep(3)
            signals = [line.strip() for line in _text(page).splitlines() if line.strip().startswith("Detected")]
        except Exception:  # noqa: BLE001 - the details are optional
            pass
    return {"ready": verdict is not None, "passed": verdict == "Trustworthy", "verdict": verdict, "signals": signals}


def recaptcha_v3(page: Any) -> dict:
    page.goto("https://recaptcha-demo.appspot.com/recaptcha-v3-request-scores.php",
              wait_until="domcontentloaded", timeout=60000)
    text = _wait_for(page, lambda t: re.search(r'"score":\s*[\d.]+', t) is not None)
    match = re.search(r'"score":\s*([\d.]+)', text)
    if not match:
        return {"ready": False, "passed": None}
    score = float(match.group(1))
    # 0.5 is the threshold Google's reCAPTCHA v3 documentation suggests to start with.
    return {"ready": True, "passed": score >= 0.5, "score": score}


def _turnstile(url: str) -> Callable[[Any], dict]:
    def measure(page: Any) -> dict:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        started = time.monotonic()
        clicked = False
        while time.monotonic() - started < 30:
            token = page.evaluate("""() => {
              const input = document.querySelector('input[name="cf-turnstile-response"]');
              return input ? input.value : null;
            }""")
            if token:
                return {"ready": True, "passed": True, "clicked": clicked,
                        "seconds": round(time.monotonic() - started, 1)}
            if not clicked and time.monotonic() - started > 4:
                frame = page.locator('iframe[src*="challenges.cloudflare.com"]')
                if frame.count():
                    box = frame.first.bounding_box()
                    if box and box["width"] > 50:
                        # Where a person clicks: the checkbox at the widget's left.
                        page.mouse.click(box["x"] + 30, box["y"] + box["height"] / 2)
                        clicked = True
            time.sleep(0.5)
        # The page loaded its Turnstile container but no token came, with or
        # without a widget to click: that is a failure, not an inconclusive run.
        loaded = page.evaluate("() => !!document.querySelector('.cf-turnstile, [data-sitekey], iframe[src*=\"challenges.cloudflare.com\"]')")
        return {"ready": loaded, "passed": False if loaded else None, "clicked": clicked}
    return measure


FPJS_KEYS = ("suspect_score", "bot", "tampering", "tampering_ml_score", "anti_detect_browser", "anomaly_score",
             "virtual_machine", "developer_tools", "incognito", "rare_device", "vpn", "proxy", "timezone_mismatch")


def fingerprintjs(page: Any) -> dict:
    page.goto("https://demo.fingerprint.com/playground", wait_until="domcontentloaded", timeout=90000)
    text = _wait_for(page, lambda t: re.search(r"^anti_detect_browser: ", t, re.M) is not None, timeout=90)
    values: dict[str, Any] = {}
    for key in FPJS_KEYS:
        match = re.search(rf"^{key}: (.+)$", text, re.M)
        if match:
            raw = match.group(1).strip()
            try:
                values[key] = json.loads(raw)
            except ValueError:
                values[key] = raw
    if "suspect_score" not in values:
        return {"ready": False, "passed": None}
    browser_flags = [key for key in ("tampering", "anti_detect_browser", "virtual_machine", "developer_tools", "incognito")
                     if values.get(key) is True]
    if values.get("bot") not in (None, "not_detected"):
        browser_flags.append("bot")
    return {"ready": True, "passed": not browser_flags, "browser_flags": browser_flags, **values}


@dataclass(frozen=True)
class Detector:
    name: str
    url: str
    measure: Callable[[Any], dict]
    what: str


DETECTORS = (
    Detector("sannysoft", "https://bot.sannysoft.com", sannysoft, "no failed row in the intoli and fpscanner tables"),
    Detector("deviceandbrowserinfo", "https://deviceandbrowserinfo.com/are_you_a_bot", deviceandbrowserinfo,
             "isBot is false"),
    Detector("rebrowser", "https://bot-detector.rebrowser.net", rebrowser, "no red test"),
    Detector("creepjs", "https://abrahamjuliot.github.io/creepjs/", creepjs, "0% headless and 0% stealth"),
    Detector("browserscan", "https://www.browserscan.net/bot-detection", browserscan, "test result Normal"),
    Detector("fingerprint-scan", "https://fingerprint-scan.com", fingerprint_scan, "bot score, reported only"),
    Detector("iphey", "https://iphey.com", iphey, "verdict Trustworthy"),
    Detector("recaptcha-v3", "https://recaptcha-demo.appspot.com/recaptcha-v3-request-scores.php", recaptcha_v3,
             "score of 0.5 or more"),
    Detector("turnstile-seleniumbase", "https://seleniumbase.io/apps/turnstile",
             _turnstile("https://seleniumbase.io/apps/turnstile"), "Turnstile token within 30 s"),
    Detector("turnstile-managed", "https://peet.ws/turnstile-test/managed.html",
             _turnstile("https://peet.ws/turnstile-test/managed.html"), "Turnstile token within 30 s"),
    Detector("turnstile-non-interactive", "https://peet.ws/turnstile-test/non-interactive.html",
             _turnstile("https://peet.ws/turnstile-test/non-interactive.html"), "Turnstile token within 30 s"),
    Detector("fingerprintjs", "https://demo.fingerprint.com/playground", fingerprintjs,
             "no bot, tampering, anti-detect, virtual machine, developer tools or incognito flag"),
)
