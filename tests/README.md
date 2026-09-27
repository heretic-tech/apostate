# Test suite

Checks what a page reads from an Apostate browser, on a real browser, and
writes every result to a file. The docs explain each check:
https://docs.apostate.dev/testing/overview

```bash
pip install ../python pytest
apostate install
python3 -m pytest                               # offline tier, ~300 tests
python3 -m pytest live --live                   # public detector pages
python3 -m pytest --live --controls=playwright,chrome --results=results/DATE-HOST.json
python3 report.py                               # rewrite docs/testing/results.mdx
```

| File | What it holds |
| --- | --- |
| `harness.py` | Local probe server, bare and package launchers, the composed-profile reader |
| `pages/` | The probe page, its frame and its three workers |
| `test_automation.py`, `test_contexts.py`, `test_profile.py`, `test_determinism.py`, `test_host_mode.py` | Offline tier |
| `live/` | Live tier: detector parsers and their tests |
| `gaps.py` | Known gaps a test may report instead of failing, each with its docs link |
| `report.py` | Builds the results page from `results/*.json` |
| `results/` | Published result files, one per run and host |

Options: `--personas`, `--seeds`, `--modes`, `--headed`, `--binary`,
`--results`, `--network`, `--live`, `--controls`. Use the `--option=value`
form. See https://docs.apostate.dev/testing/run.
