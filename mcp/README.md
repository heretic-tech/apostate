# @heretic-tech/apostate-mcp

An MCP server that gives an AI agent an Apostate browser. The tools are
[Playwright MCP](https://github.com/microsoft/playwright-mcp)'s: navigate,
snapshot, click, type, fill forms, tabs, screenshots and the rest. The browser
is launched by [`@heretic-tech/apostate`](https://www.npmjs.com/package/@heretic-tech/apostate),
so the agent gets a persona, the Patchright driver, the locale and timezone of
the connection's exit, and Xvfb for a headed launch on a Linux server with no
display.

## Add it to an agent

Claude Code:

```bash
claude mcp add apostate -- npx -y @heretic-tech/apostate-mcp --platform windows
```

Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.apostate]
command = "npx"
args = ["-y", "@heretic-tech/apostate-mcp", "--platform", "windows"]
startup_timeout_sec = 120
default_tools_approval_mode = "approve"
```

Any client that takes an `mcpServers` JSON block (Cursor, Gemini CLI,
Windsurf and others):

```json
{
  "mcpServers": {
    "apostate": {
      "command": "npx",
      "args": ["-y", "@heretic-tech/apostate-mcp", "--platform", "windows"]
    }
  }
}
```

The browser starts on the first tool call. The first start downloads it
(about 150 MB) unless `npx @heretic-tech/apostate install` ran before.

## Options

| Option | Default | Effect |
| --- | --- | --- |
| `--fingerprint <seed>` | the seed stored in the profile | Seed for the machine |
| `--platform <name>` | `macos` on a Mac, otherwise `windows` | Persona: `windows`, `macos` or `linux` |
| `--profile <name>` | `default` | Persistent profile, kept in the data directory below |
| `--user-data-dir <dir>` | none | Persistent profile directory; overrides `--profile` |
| `--isolated` | off | Temporary profile, deleted when the server stops |
| `--proxy <url>` | none | `http://`, `https://` or `socks5://`, credential in the URL. `APOSTATE_PROXY` does the same |
| `--locale <tag>` | from GeoIP | Locale, such as `de-DE` |
| `--timezone <iana>` | from GeoIP | Timezone, such as `Europe/Berlin` |
| `--no-geoip` | off | Skip the exit lookup; the persona uses `en-US` and the host's timezone |
| `--headed` | off | Open a window. On Linux with no display, Xvfb is started |
| `--arg <switch>` | none | Extra browser switch, repeatable |
| `--executable-path <path>` | the installed browser | Browser to run. `APOSTATE_BINARY` does the same |
| `--caps <list>` | none | Extra Playwright MCP tool groups: `vision`, `pdf`, `network`, `storage`, `devtools`, `testing` |
| `--output-dir <dir>` | `.playwright-mcp` in the working directory | Where snapshots, screenshots and PDFs are written |

Persistent profiles live in `~/Library/Application Support/apostate-mcp/profiles/`
on macOS, `~/.local/share/apostate-mcp/profiles/` on Linux and
`%LOCALAPPDATA%\apostate-mcp\profiles\` on Windows. A profile keeps its
machine, cookies and logins between sessions. One server uses one profile at
a time, so give each concurrent agent its own `--profile`.

## Test

```bash
npm install
npm test
```

The test starts the server over stdio and drives a Windows persona through
the tools. It needs the browser installed (`npx @heretic-tech/apostate install`).

Documentation: https://docs.apostate.dev/agents/mcp

GPL-3.0-or-later.
