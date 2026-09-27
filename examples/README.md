# Examples

Scripts for the Python package (`apostate` on PyPI) and the Node package
(`@heretic-tech/apostate` on npm). Each script launches a headless browser
and prints its results. The documentation is at
[docs.apostate.dev](https://docs.apostate.dev).

## Run the Python examples

Python 3.10 or later:

```bash
pip install apostate
python3 examples/python/quickstart.py
```

The first launch downloads the browser.

## Run the Node examples

Node 22 or later. `puppeteer.mjs` needs Node 22.12 or later, the minimum for
`puppeteer-core` 25.

```bash
cd examples/node
npm install
node quickstart.mjs
```

`npm install` installs the Node package, Patchright (which
`launch-process.mjs` imports) and `puppeteer-core` (which `puppeteer.mjs`
uses).

## Scripts

| Python | Node | What it shows | Docs page |
| --- | --- | --- | --- |
| `quickstart.py` | `quickstart.mjs` | A Windows persona from seed 42: User-Agent, platform, cores, memory, screen and WebGL renderer. | [Quickstart](https://docs.apostate.dev/quickstart) |
| `persistent_profile.py` | `persistent-profile.mjs` | Two launches on `./profiles/demo` with no seed. Prints the seed stored in `profiles/demo/apostate/identity` and the same machine from both launches. | [Seeds and identity](https://docs.apostate.dev/concepts/seeds-and-identity) |
| `personas.py` | `personas.mjs` | Seed 42 as Windows, macOS and Linux: User-Agent, platform, WebGL renderer, screen and the number of font families `queryLocalFonts()` lists. | [Personas](https://docs.apostate.dev/concepts/personas) |
| `persona_details.py` | | One persona's WebGL vendor and renderer, WebGPU adapter, voices, fonts, media devices, keyboard layout and `AudioContext.baseLatency`. | [Personas](https://docs.apostate.dev/concepts/personas) |
| `proxy.py` | `proxy.mjs` | A launch through the proxy in `APOSTATE_PROXY`. Prints the exit IP a page sees and the timezone and languages taken from the exit. Exits with a message when the variable is not set. | [Proxies](https://docs.apostate.dev/guides/proxies) |
| `locale_timezone.py` | `locale-timezone.mjs` | `de-DE` and `Europe/Berlin` set explicitly with GeoIP off: `navigator.languages`, `Intl`, date and number formats, and the `Accept-Language` header. | [Locale and timezone](https://docs.apostate.dev/guides/locale-and-timezone) |
| `custom_profile.py` | `custom-profile.mjs` | A launch from a profile you write, with cores, memory, screen, locale and timezone. Values the profile leaves out are the host's. | [Custom profiles](https://docs.apostate.dev/guides/custom-profiles) |
| `async_sessions.py` | `many-sessions.mjs` | Three browsers at once with seeds 1, 2 and 3, each a different machine. | [Many sessions](https://docs.apostate.dev/guides/many-sessions) |
| `screenshot_and_pdf.py` | | Writes `out/example.png` and `out/example.pdf` for a page. `page.pdf()` works only headless. | [Python](https://docs.apostate.dev/guides/python) |
| `what_a_page_sees.py` | | Prints a JSON of what a page, a worker and the request headers read, served from a local HTTP server. | [Check what a page sees](https://docs.apostate.dev/guides/verify) |
| | `puppeteer.mjs` | The Node package driving `puppeteer-core` instead of Patchright. | [Node](https://docs.apostate.dev/guides/node) |
| `raw_cdp.py` | `launch-process.mjs` | The browser started without a driver, with `--remote-debugging-port=0` and a temporary user data directory, then a Patchright connection over CDP. `raw_cdp.py` runs the binary itself. `launch-process.mjs` uses `launchProcess()`. | [Raw binary](https://docs.apostate.dev/guides/raw-binary) |

`persistent_profile.py`, `persistent-profile.mjs` and `screenshot_and_pdf.py`
write next to themselves, into `profiles/` and `out/`, which `.gitignore` excludes.

## Use cases

Each folder in `use-cases/` holds the scripts of one walkthrough. The Python
scripts run from any directory and write their output next to themselves, in
`out/`, `profiles/`, `backups/` or a `.db` file, which `.gitignore` excludes.
With no arguments, each one runs against a local test server, except
`web-archiving` and `account-profiles`, which open `https://example.com`.

| Folder | Scripts | What it shows | Docs page |
| --- | --- | --- | --- |
| `test-your-own-defenses/` | `compare.py`, `signup_site.py` | Submits a local signup form as four persona and seed pairs and in host mode, and prints the headers and `navigator`, `userAgentData` and WebGL values the backend recorded, with the checks each passed. `--url` opens your own page instead. | [Test your own defenses](https://docs.apostate.dev/use-cases/test-your-own-defenses) |
| `localization-qa/` | `check_locales.py` | Opens a page as `en-US`, `de-DE`, `ja-JP` and `ar-EG` with a matching timezone, saves a screenshot of each, and prints `navigator.languages`, `Accept-Language`, `<html lang>` and `Intl` formats. A region's proxy comes from `APOSTATE_PROXY_US`, `_DE`, `_JP` or `_EG`. | [Localization QA](https://docs.apostate.dev/use-cases/localization-qa) |
| `price-monitoring/` | `monitor.py`, `demo_shop.py` | Reads a value from product pages with one persistent profile per site, follows robots.txt, spaces page loads with a delay and jitter, stores values in SQLite and prints what changed since the last run. | [Price monitoring](https://docs.apostate.dev/use-cases/price-monitoring) |
| `ad-verification/` | `verify_ads.py` | Opens an ad link per region and persona, prints every main-frame response from the first redirect to the landing page, and saves a screenshot. Proxies come from `APOSTATE_PROXY_US`, `_DE` and `_FR`. | [Ad verification](https://docs.apostate.dev/use-cases/ad-verification) |
| `account-profiles/` | `accounts.py`, `accounts.json` | A registry of accounts, each with its own user data directory, persona, region and proxy variable. Lists, opens, checks and backs up profiles, and reports whether a machine changed since its last launch. | [Account profiles](https://docs.apostate.dev/use-cases/account-profiles) |
| `end-to-end-tests/` | `conftest.py`, `test_signup.py`, `app.py`, `e2e.yml`, `node/` | pytest fixtures that launch Apostate with a fixed seed, two tests against a local app, a GitHub Actions workflow, and the same tests in Playwright Test (`cd node && npm install && npx patchright test`). | [End-to-end tests](https://docs.apostate.dev/use-cases/end-to-end-tests) |
| `web-archiving/` | `archive.py` | Captures a page into a folder with a full-page screenshot, a PDF, the HTML, a HAR file and a manifest with the SHA-256 of each file. | [Web archiving](https://docs.apostate.dev/use-cases/web-archiving) |

## AI agents

| Path | What it holds | Docs page |
| --- | --- | --- |
| `agents/mcp/` | MCP configuration for Claude Code, Codex, Cursor, Gemini CLI, VS Code and OpenCode, all running `@heretic-tech/apostate-mcp`, and a Playwright MCP config file for the Apostate binary. | [MCP server](https://docs.apostate.dev/agents/mcp) |
| `agents/skills/apostate/SKILL.md` | A skill that shows an agent when to use the browser tools and how to write an Apostate script. Copy it to `.claude/skills/` or `.agents/skills/`. | [Claude Code](https://docs.apostate.dev/agents/claude-code) |
| `agents/claude-api/agent.py` | A browsing agent on the Claude API's tool runner with the MCP server's browser tools. `--check` runs the tools without a model call. | [Claude API](https://docs.apostate.dev/agents/claude-api) |
| `agents/browser-use/agent.py` | browser-use connected over CDP to an Apostate browser the script starts. `--check` connects without a model call. | [browser-use](https://docs.apostate.dev/agents/browser-use) |

## Docker

`docker/` holds a Dockerfile that installs the Python package, the browser
and the Windows fonts, and `check.py`, which launches a Windows persona in the
container and prints what a page reads.
[Docker](https://docs.apostate.dev/guides/docker)
