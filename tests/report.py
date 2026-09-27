#!/usr/bin/env python3
"""Write docs/testing/results.mdx from the result files in tests/results/.

    python3 tests/report.py            # write the page
    python3 tests/report.py --check    # exit 1 if the page is out of date

Each file in tests/results/ is one run of the suite (`pytest --results=...`).
The page shows the newest run per host, its offline tier by test, and its
live tier by detector with any control browsers beside it. Nothing on the page
is typed by hand: to change a number, run the suite again.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "tests" / "results"
PAGE = ROOT / "docs" / "testing" / "results.mdx"
REPO = "https://github.com/heretic-tech/apostate/blob/main"

OFFLINE_FILES = {
    "test_automation.py": "No automation traces",
    "test_contexts.py": "Every context agrees",
    "test_profile.py": "The page reads the composed machine",
    "test_determinism.py": "Same seed, same machine",
    "test_host_mode.py": "Host mode",
}


def load() -> list[dict]:
    runs = []
    for path in sorted(RESULTS.glob("*.json")):
        run = json.loads(path.read_text())
        run["_file"] = path.name
        runs.append(run)
    return runs


def host_label(run: dict) -> str:
    host = run["host"]
    memory = f", {round(host['memory_bytes'] / 2**30)} GiB" if host.get("memory_bytes") else ""
    cpu = f", {host['cpu']}" if host.get("cpu") else ""
    return f"{host.get('os_version') or host['os']} {host['machine']}{cpu}, {host['cores']} cores{memory}"


def outcome_cell(entries: list[dict]) -> str:
    counts = defaultdict(int)
    for entry in entries:
        counts[entry["outcome"]] += 1
    parts = []
    for outcome, label in (("passed", "passed"), ("failed", "failed"), ("xfailed", "known gap"), ("skipped", "skipped")):
        if counts[outcome]:
            parts.append(f"{counts[outcome]} {label}")
    return ", ".join(parts) or "none"


def offline_table(run: dict) -> list[str]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for entry in run["results"]:
        name = entry["test"].split("::")[0].split("/")[-1]
        if name in OFFLINE_FILES:
            groups[name].append(entry)
    if not groups:
        return []
    lines = ["| Checks | Tests | Result |", "| --- | --- | --- |"]
    for name, title in OFFLINE_FILES.items():
        if name in groups:
            lines.append(f"| [{title}]({REPO}/tests/{name}) | {len(groups[name])} | {outcome_cell(groups[name])} |")
    gaps = sorted({entry["details"]["known_gap"]["id"] for entry in run["results"]
                   if entry.get("details", {}).get("known_gap") and "live/" not in entry["test"]})
    failures = [entry for entry in run["results"] if entry["outcome"] == "failed" and "live/" not in entry["test"]]
    if gaps:
        lines += ["", "Known gaps met on this host: " + ", ".join(f"`{gap}`" for gap in gaps) + ". [Known gaps](/known-gaps) describes each."]
    if failures:
        lines += ["", "Failures:", ""]
        lines += [f"- `{entry['test']}`: {entry.get('message', '').splitlines()[0][:160]}" for entry in failures]
    return lines


def live_value(details: dict) -> str:
    name = details.get("detector")
    if not details.get("ready"):
        return "no result"
    if name == "sannysoft":
        failed = details.get("failed") or []
        return f"{details.get('passed_rows', 0)} passed, {len(failed)} failed" + (f" ({', '.join(failed[:4])})" if failed else "")
    if name == "deviceandbrowserinfo":
        flags = details.get("flags") or []
        return "isBot false" if details.get("isBot") is False else f"isBot true: {', '.join(flags[:4])}"
    if name == "rebrowser":
        red = details.get("red") or []
        return "no red test" if not red else "red: " + ", ".join(red)
    if name == "creepjs":
        return f"{details.get('headless')}% headless, {details.get('stealth')}% stealth, {details.get('like_headless')}% like headless"
    if name == "browserscan":
        return str(details.get("result"))
    if name == "fingerprint-scan":
        return f"bot score {details.get('score')}/100"
    if name == "iphey":
        signals = "; ".join(line.replace("Detected an ", "").replace("Detected ", "") for line in details.get("signals", []))
        return details.get("verdict", "") + (f" ({signals})" if signals else "")
    if name == "recaptcha-v3":
        return f"score {details.get('score')}"
    if name and name.startswith("turnstile"):
        if details.get("passed"):
            return "token" + (" after one click" if details.get("clicked") else "")
        return "no token"
    if name == "fingerprintjs":
        flags = details.get("browser_flags") or []
        text = f"suspect score {details.get('suspect_score')}"
        return text + (f", flags: {', '.join(flags)}" if flags else ", no browser flags")
    return json.dumps({key: value for key, value in details.items() if key not in ("url", "browser", "criterion")})[:120]


def mark(entry: dict | None) -> str:
    if entry is None:
        return ""
    details = entry.get("details", {})
    if details.get("control") or (entry["outcome"] == "passed" and details.get("passed") is None):
        return ""
    return {"passed": "pass", "failed": "fail", "xfailed": "known gap", "skipped": "no result"}.get(entry["outcome"], entry["outcome"])


def live_table(run: dict) -> list[str]:
    rows: dict[str, dict[str, dict]] = defaultdict(dict)
    criteria: dict[str, tuple[str, str]] = {}
    browsers: list[str] = []
    for entry in run["results"]:
        details = entry.get("details") or {}
        if "live/" not in entry["test"] or "detector" not in details:
            continue
        rows[details["detector"]][details["browser"]] = entry
        criteria[details["detector"]] = (details.get("url", ""), details.get("criterion", ""))
        if details["browser"] not in browsers:
            browsers.append(details["browser"])
    if not rows:
        return []
    labels = {"control-playwright": "Stock Chromium (Playwright)", "control-chrome": "Google Chrome (Playwright)"}
    header = ["Detector", "Pass when"] + [labels.get(b, b.replace("apostate-", "Apostate, ") + " persona") for b in browsers]
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    for name, by_browser in rows.items():
        url, criterion = criteria[name]
        cells = [f"[{name}]({url})", criterion]
        for browser in browsers:
            entry = by_browser.get(browser)
            if entry is None:
                cells.append("")
                continue
            verdict = mark(entry)
            value = live_value(entry.get("details", {})).replace("|", "/")
            cells.append(f"{verdict}: {value}" if verdict else value)
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render(runs: list[dict]) -> str:
    out = [
        "---",
        'title: "Latest results"',
        'description: "Dated results of the Apostate test suite: the offline checks and public detector pages, per host."',
        "---",
        "",
        "{/* Generated by tests/report.py from tests/results/*.json. Do not edit by hand. */}",
        "",
        "Each section is the newest run of the [test suite](/testing/overview) on one host. The raw result files are in "
        f"[`tests/results/`]({REPO}/tests/results). Live results depend on the network the run used; the table names it.",
        "",
    ]
    newest: dict[str, dict] = {}
    for run in runs:
        key = f"{run['host']['os']}-{run['host']['machine']}"
        if key not in newest or run["date"] > newest[key]["date"]:
            newest[key] = run
    if not newest:
        out.append("No results recorded yet.")
        return "\n".join(out) + "\n"
    for run in sorted(newest.values(), key=lambda item: item["date"], reverse=True):
        options = run["options"]
        versions = run.get("versions", {})
        out += [
            f"## {host_label(run)}",
            "",
            "| | |",
            "| --- | --- |",
            f"| Date | {run['date'][:10]} |",
            f"| Apostate | {versions.get('package', '?')}, Chromium {versions.get('chromium', '?')} |",
            f"| Personas | {', '.join(options['personas'])}, seeds {', '.join(options['seeds'])} |",
            f"| Launch modes | {', '.join(options['modes'])}, {'headed' if options['headed'] else 'headless'} |",
            f"| Network | {run.get('network', 'not recorded')} |",
            f"| Result file | [`{run['_file']}`]({REPO}/tests/results/{run['_file']}) |",
            "",
        ]
        offline = offline_table(run)
        if offline:
            out += ["### Offline checks", ""] + offline + [""]
        live = live_table(run)
        if live:
            out += ["### Public detectors", ""] + live + [""]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the page is out of date")
    args = parser.parse_args()
    page = render(load())
    if args.check:
        current = PAGE.read_text() if PAGE.exists() else ""
        if current != page:
            print(f"{PAGE.relative_to(ROOT)} is out of date; run python3 tests/report.py", file=sys.stderr)
            return 1
        return 0
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(page)
    print(f"wrote {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
