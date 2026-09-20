#!/usr/bin/env python3
"""Derive the country -> Accept-Language table the launchers use after GeoIP.

A GeoIP lookup answers with a country and a timezone. It never answers with a
locale, because no provider knows what language a machine's browser is set
to; the launcher infers one, and the inference has to be total -- an exit in
a country the table does not name must still get a language, or the host's
own list leaks through behind that exit. Before this generator the table was
forty-five hand-picked countries and a Malaysian exit produced
"resolved no locale".

Two inputs, both cited in the output so a regeneration is checkable:

1. CLDR ``territoryInfo``: for each territory, the language with official or
   de-facto-official status and the largest population share; when no
   language has official status, the largest share. CLDR's ``likelySubtags``
   decides whether a script subtag is redundant (``zh_Hant`` in TW is
   ``zh-TW``).
2. Chromium's own ``kAcceptLanguageList`` (ui/base/l10n/
   chromium_language_matcher.cc at the pinned checkout): the tags Chrome
   itself will put in Accept-Language. A regional tag is used only when Chrome
   has it (``de-CH``, ``en-IN``); otherwise the bare language, which is what a
   stock Chrome on that OS locale reports. Four languages get a default region
   where the bare tag is not what a real install shows: ``en`` -> ``en-US``,
   ``pt`` -> ``pt-PT`` outside Brazil, ``es`` -> ``es-419`` in the Americas,
   ``zh`` -> ``zh-CN`` outside TW/HK/MO. A CLDR language Chrome does not know
   at all falls to the territory's next official language, then to ``en-US``.

Usage:
  scripts/generate-country-locales.py [--cldr-dir DIR] [--check]

``--cldr-dir`` holds ``territoryInfo.json`` and ``likelySubtags.json`` from
cldr-json at the tag recorded in CLDR_VERSION; without it they are fetched.
Writes config/country-locales.json; --check verifies it is current.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "config/country-locales.json"
MATCHER = REPO_ROOT / ".workspace/src/ui/base/l10n/chromium_language_matcher.cc"

CLDR_VERSION = "48.0.0"
CLDR_BASE = ("https://raw.githubusercontent.com/unicode-org/cldr-json/"
             f"{CLDR_VERSION}/cldr-json/cldr-core/supplemental/")
CLDR_FILES = ("territoryInfo.json", "likelySubtags.json")

OFFICIAL = {"official", "de_facto_official"}

#: Territories CLDR places in the Americas (UN M.49 region 019), where Chrome's
#: Latin American Spanish tag applies. Listed rather than derived from
#: territoryContainment so the output does not depend on a third CLDR file.
AMERICAS = frozenset("""
AG AI AR AW BB BL BM BO BQ BR BS BZ CA CL CO CR CU CW DM DO EC FK GD GF GL GP
GS GT GY HN HT JM KN KY LC MF MQ MS MX NI PA PE PM PR PY SR SV SX TC TT US UY
VC VE VG VI
""".split())

#: Where the population rule names a language a desktop browser there almost
#: never runs in. Every persona this catalogue offers is a desktop machine,
#: and desktop Windows and macOS installs in these countries are English even
#: where the most-spoken official language is not. Authored, and said so in
#: the output; a per-country census of real Accept-Language lists would
#: replace the whole table, this row first.
DESKTOP_ENGLISH = {
    "IN": "en-IN",  # hi 41% official; en-IN is in Chromium's list
    "PK": "en-US",  # ur 95% official; no en-PK tag
    "BD": "en-US",  # bn 98% official
    "LK": "en-US",  # si 82% official
    "NP": "en-US",  # ne official
    "KE": "en-US",  # sw 66% official beside en
}


def load_cldr(cldr_dir: Path | None) -> tuple[dict, dict]:
    data = []
    for name in CLDR_FILES:
        if cldr_dir is not None:
            raw = (cldr_dir / name).read_bytes()
        else:
            with urllib.request.urlopen(CLDR_BASE + name, timeout=60) as response:
                raw = response.read()
        data.append(json.loads(raw)["supplemental"])
    return data[0]["territoryInfo"], data[1]["likelySubtags"]


def chrome_accept_languages() -> list[str]:
    source = MATCHER.read_text(encoding="utf-8")
    match = re.search(r"kAcceptLanguageList = std::to_array<LanguageTag>\(\{(.*?)\}\);",
                      source, re.S)
    if match is None:
        raise SystemExit(f"kAcceptLanguageList not found in {MATCHER}")
    return re.findall(r'GetKnownLanguageTag\("([A-Za-z0-9-]+)"\)', match.group(1))


def ranked_languages(territory: dict) -> list[str]:
    """CLDR language codes for one territory, best candidate first."""
    population = territory.get("languagePopulation", {})

    def rank(item: tuple[str, dict]) -> tuple[bool, float]:
        code, info = item
        return (info.get("_officialStatus") in OFFICIAL,
                float(info.get("_populationPercent", 0)))

    return [code for code, _ in sorted(population.items(), key=rank, reverse=True)]


def chrome_tag(code: str, cc: str, likely: dict, accepted: set[str]) -> str | None:
    """The tag Chrome would present for CLDR language *code* in *cc*, or None."""
    base, _, script = code.partition("_")
    inferred = likely.get(f"{base}-{cc}") or likely.get(base) or ""
    parts = inferred.split("-")
    inferred_script = parts[1] if len(parts) >= 3 else ""
    if script and script != inferred_script:
        base = f"{base}-{script}"
    if base == "und":
        return None
    regional = f"{base}-{cc}"
    if regional in accepted:
        return regional
    if base == "en":
        return "en-US"
    if base == "pt":
        return "pt-BR" if cc == "BR" else "pt-PT"
    if base == "es":
        return "es-419" if cc in AMERICAS else "es"
    if base == "zh":
        return {"TW": "zh-TW", "HK": "zh-HK", "MO": "zh-HK"}.get(cc, "zh-CN")
    if base in accepted:
        return base
    return None


def build(cldr_dir: Path | None) -> dict:
    territories, likely = load_cldr(cldr_dir)
    accepted_list = chrome_accept_languages()
    accepted = set(accepted_list)
    locales: dict[str, str] = {}
    for cc, territory in territories.items():
        if len(cc) != 2 or not cc.isalpha() or cc == "ZZ":
            continue
        tag = None
        for code in ranked_languages(territory):
            tag = chrome_tag(code, cc, likely, accepted)
            if tag is not None:
                break
        locales[cc] = DESKTOP_ENGLISH.get(cc, tag or "en-US")
    return {
        "chromium_accept_language_source": "ui/base/l10n/chromium_language_matcher.cc kAcceptLanguageList",
        "chromium_accept_language_tags": len(accepted_list),
        "cldr_version": CLDR_VERSION,
        "generator": "scripts/generate-country-locales.py",
        "locales": locales,
        "overrides": {cc: "desktop installs there are English" for cc in sorted(DESKTOP_ENGLISH)},
        "rule": ("CLDR territoryInfo: official or de-facto-official language with the "
                 "largest population share, else the largest share. Tag: lang-CC when "
                 "Chromium's kAcceptLanguageList has it, else en->en-US, pt->pt-PT (pt-BR "
                 "in BR), es->es-419 in the Americas else es, zh->zh-CN (zh-TW, zh-HK, "
                 "MO->zh-HK), else the bare language when Chromium lists it, else the "
                 "territory's next official language, else en-US."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cldr-dir", type=Path, help="local copies of the CLDR inputs")
    parser.add_argument("--check", action="store_true", help="verify the committed table")
    args = parser.parse_args(argv)
    payload = json.dumps(build(args.cldr_dir), ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")) + "\n"
    if args.check:
        if OUTPUT.read_text(encoding="utf-8") != payload:
            print(f"{OUTPUT} is stale; rerun {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is current")
        return 0
    OUTPUT.write_text(payload, encoding="utf-8")
    print(f"wrote {OUTPUT}: {len(json.loads(payload)['locales'])} territories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
