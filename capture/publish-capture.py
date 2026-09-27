#!/usr/bin/env python3
"""Move a capture that came through a tunnel into the evidence tree.

A capture taken over a quick tunnel (cloudflared, ngrok) carries the headers
the tunnel added, in `headers.echo` and `headers.echo_worker`: the tunnel's
host name, the visitor's IP address in X-Forwarded-For and Cf-Connecting-Ip,
and request ids. None of them describe the machine, and the IP address
identifies the network the capture was taken on, so they are removed before
the capture is committed. The tunnel host name becomes `capture.invalid`.

The scrubbed capture is re-admitted by the receiver's own checks, written to
resources/fingerprints/raw/<name>.json, and given an admission record that
says what was removed. A capture the receiver would reject is refused.

    python3 capture/publish-capture.py QUARANTINED.json --name windows-qualcomm-adreno-x1-85

The name gets the capture's UTC time appended, as the other raw captures have.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "capture" / "server"))
import receive  # noqa: E402

RAW = ROOT / "resources" / "fingerprints" / "raw"
TUNNEL_HOSTS = re.compile(r"[a-z0-9-]+\.(?:trycloudflare\.com|ngrok(?:-free)?\.(?:app|dev|io))")
HEADER_PROBES = ("headers.echo", "headers.echo_worker")


def added_by_tunnel(name: str) -> bool:
    name = name.lower()
    return name.startswith("cf-") or name in {"cdn-loop", "x-forwarded-for", "x-forwarded-proto",
                                              "x-forwarded-host", "x-real-ip", "ngrok-trace-id"}


def scrub(capture: dict) -> list[str]:
    """Remove tunnel headers in place and return the names removed."""
    removed: set[str] = set()
    for section in ("probes", "repeat"):
        for probe in HEADER_PROBES:
            entry = (capture.get(section) or {}).get(probe)
            if not entry or not isinstance(entry.get("value"), dict):
                continue
            value = entry["value"]
            kept = []
            for name, header in value.get("headers", []):
                if added_by_tunnel(name):
                    removed.add(name)
                else:
                    kept.append([name, TUNNEL_HOSTS.sub("capture.invalid", header)])
            value["headers"] = kept
            value["header_order"] = [name for name in value.get("header_order", [])
                                     if not added_by_tunnel(name)]
    return sorted(removed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture", type=pathlib.Path)
    parser.add_argument("--name", required=True,
                        help="file name stem, such as windows-qualcomm-adreno-x1-85")
    parser.add_argument("--dry-run", action="store_true",
                        help="check and report, write nothing")
    args = parser.parse_args()

    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    removed = scrub(capture)
    text = json.dumps(capture, indent=1, ensure_ascii=False)
    if TUNNEL_HOSTS.search(text):
        print("a tunnel host name is still in the capture outside the header probes; not published",
              file=sys.stderr)
        return 1
    reason = receive.admission_rejection_reason(capture)
    if reason is not None:
        print(f"the receiver rejects this capture: {reason}", file=sys.stderr)
        return 1

    taken = datetime.datetime.fromisoformat(capture["context"]["taken_at"].replace("Z", "+00:00"))
    path = RAW / f"{args.name}-{taken.astimezone(datetime.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    if path.exists():
        print(f"{path.relative_to(ROOT)} exists; not overwritten", file=sys.stderr)
        return 1
    raw = text.encode("utf-8")
    note = "capture passed admission checks"
    pin = (ROOT / "build" / "CHROMIUM_VERSION").read_text(encoding="utf-8").strip()
    measured = (((capture["probes"].get("navigator.userAgentData") or {}).get("value") or {})
                .get("high") or {}).get("uaFullVersion")
    if measured and measured != pin:
        note += f"; browser build {measured} differs from release pin {pin} (same-major)"
    if removed:
        note += (". Received through a tunnel: the headers it added (" + ", ".join(removed) +
                 ") were removed and its host name reads capture.invalid before admission. "
                 "The tunnel may also rewrite Accept-Encoding and header casing, so headers.echo "
                 "is not ground truth for this machine.")
    digest = hashlib.sha256(raw).hexdigest()
    if args.dry_run:
        print(f"would write {path.relative_to(ROOT)} and admissions/{digest}.json")
        print(f"reason: {note}")
        return 0
    path.write_bytes(raw)
    receive.persist_admission_decision(str(RAW), digest, "accepted", note, context=capture["context"],
                                       capture_path=str(path.relative_to(ROOT)), raw=raw)
    print(f"wrote {path.relative_to(ROOT)} and admissions/{digest}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
