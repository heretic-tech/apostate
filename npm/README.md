# @heretic-tech/apostate

Node package for [Apostate](https://docs.apostate.dev), a Chromium build that
presents a Windows, macOS or Linux machine you choose. The package downloads
and verifies the browser, looks up the locale and timezone of your connection
or proxy, and launches it through Patchright, Playwright or Puppeteer.

Free and open source under GPL-3.0. Needs Node 22 or later.

## Install

```bash
npm install @heretic-tech/apostate
npx apostate install                   # download the browser now instead of on first launch
npx apostate fonts install windows     # Linux and macOS hosts, for a Windows persona
```

Patchright comes with the package and is the default driver. `playwright`,
`playwright-core`, `puppeteer` and `puppeteer-core` also work; pass `driver`
to pick one. No driver needs to download a browser of its own.

## Launch

```javascript
import { launch } from "@heretic-tech/apostate";

const browser = await launch({ fingerprint: 42, fingerprintPlatform: "windows" });
const page = await browser.newPage();
await page.goto("https://example.com");
console.log(await page.evaluate(() => navigator.platform)); // Win32
await browser.close();
```

- `fingerprint` is the seed: the same seed gives the same machine on every
  launch and host. Leave it out for a new machine each launch.
- `fingerprintPlatform` is `windows`, `macos` or `linux`.
- `proxy` takes `http://`, `https://` or `socks5://` with the credential in
  the URL. GeoIP looks up the exit's locale and timezone; pass `locale` and
  `timezone` to set them yourself.
- `headless` is on by default. Headed on a Linux host with no display starts
  Xvfb for you.
- Use `browser.newPage()`. `newContext()` opens an incognito context, which
  sites can tell apart.

Keep a machine, its cookies and its logins between runs:

```javascript
import { launchPersistentContext } from "@heretic-tech/apostate";

const context = await launchPersistentContext("./profiles/alice", { fingerprintPlatform: "windows" });
```

`launchProcess()` starts the browser without a driver. `browser.apostateDiagnostics`
holds what the launch resolved, GeoIP warnings included.

## Command line

```bash
npx apostate install | path | info | clear
npx apostate run --fingerprint=42 --fingerprint-explain
npx apostate fonts install windows [--from DIR]
npx apostate fonts export-macos DIR
npx apostate fonts install macos --from DIR
```

## AI agents

[`@heretic-tech/apostate-mcp`](https://docs.apostate.dev/agents/mcp) is an MCP
server built on this package. It gives Claude Code, Codex and other MCP clients
browser tools on an Apostate browser.

## Documentation

- [Node guide](https://docs.apostate.dev/guides/node) and
  [API reference](https://docs.apostate.dev/reference/node-api)
- [Personas](https://docs.apostate.dev/concepts/personas),
  [proxies](https://docs.apostate.dev/guides/proxies),
  [fonts](https://docs.apostate.dev/guides/fonts),
  [Linux servers](https://docs.apostate.dev/guides/linux-servers)
- [Installation and verifying downloads](https://docs.apostate.dev/installation)
- [Known gaps](https://docs.apostate.dev/known-gaps)

Source: https://github.com/heretic-tech/apostate
