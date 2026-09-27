#!/usr/bin/env node
// An MCP server that gives an AI agent an Apostate browser.
//
// The tools are Playwright MCP's (navigate, snapshot, click, type, tabs and the
// rest). The browser is launched by @heretic-tech/apostate, so the agent gets
// what a script using the package gets: the Patchright driver, the persona,
// locale and timezone from GeoIP, the window's own viewport, and Xvfb for a
// headed launch on a Linux host with no display.
import { mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const VERSION = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "package.json"), "utf8")).version;

const USAGE = `apostate-mcp ${VERSION}

Usage: apostate-mcp [options]

Browser
  --fingerprint <seed>        seed for the machine; default: the one stored in the profile
  --platform <name>           windows, macos or linux; default: macos on a Mac, otherwise windows
  --profile <name>            persistent profile name (default: default)
  --user-data-dir <dir>       persistent profile directory; overrides --profile
  --isolated                  temporary profile, deleted when the server stops
  --proxy <url>               http://, https:// or socks5:// proxy, credential in the URL
                              (APOSTATE_PROXY does the same)
  --locale <tag>              locale such as de-DE; default: from GeoIP
  --timezone <iana>           timezone such as Europe/Berlin; default: from GeoIP
  --no-geoip                  do not look up the exit's locale and timezone
  --headed                    open a window (on Linux with no display, Xvfb is started)
  --arg <switch>              extra browser switch, repeatable
  --executable-path <path>    browser to run (APOSTATE_BINARY does the same)

Tools
  --caps <list>               extra Playwright MCP tool groups: vision, pdf, network,
                              storage, devtools, testing (comma-separated)
  --output-dir <dir>          where screenshots and PDFs are written

  --version, --help
`;

function fail(message) {
  process.stderr.write(`apostate-mcp: ${message}\n`);
  process.exit(2);
}

function dataDir() {
  if (process.platform === "win32") return join(process.env.LOCALAPPDATA || homedir(), "apostate-mcp");
  if (process.platform === "darwin") return join(homedir(), "Library", "Application Support", "apostate-mcp");
  return join(process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "apostate-mcp");
}

let parsed;
try {
  parsed = parseArgs({
    options: {
      fingerprint: { type: "string" },
      platform: { type: "string" },
      profile: { type: "string", default: "default" },
      "user-data-dir": { type: "string" },
      isolated: { type: "boolean", default: false },
      proxy: { type: "string" },
      locale: { type: "string" },
      timezone: { type: "string" },
      "no-geoip": { type: "boolean", default: false },
      headed: { type: "boolean", default: false },
      arg: { type: "string", multiple: true, default: [] },
      "executable-path": { type: "string" },
      caps: { type: "string" },
      "output-dir": { type: "string" },
      version: { type: "boolean", default: false },
      help: { type: "boolean", default: false },
    },
    strict: true,
  }).values;
} catch (error) {
  fail(`${error.message}\n\n${USAGE}`);
}

if (parsed.help) {
  process.stdout.write(USAGE);
  process.exit(0);
}
if (parsed.version) {
  process.stdout.write(`${VERSION}\n`);
  process.exit(0);
}
if (parsed.isolated && parsed["user-data-dir"]) fail("--isolated and --user-data-dir cannot be combined");
if (!/^[A-Za-z0-9._-]+$/.test(parsed.profile)) fail("--profile takes letters, digits, '.', '_' and '-'");

const options = {
  headless: !parsed.headed,
  geoip: !parsed["no-geoip"],
  args: parsed.arg,
};
if (parsed.fingerprint !== undefined) options.fingerprint = parsed.fingerprint;
if (parsed.platform !== undefined) options.fingerprintPlatform = parsed.platform;
const proxy = parsed.proxy ?? process.env.APOSTATE_PROXY;
if (proxy) options.proxy = proxy;
if (parsed.locale !== undefined) options.locale = parsed.locale;
if (parsed.timezone !== undefined) options.timezone = parsed.timezone;
if (parsed["executable-path"] !== undefined) options.executablePath = parsed["executable-path"];

const config = { browser: { isolated: false } };
if (parsed.caps) config.capabilities = ["core", ...parsed.caps.split(",").map((cap) => cap.trim()).filter(Boolean)];
if (parsed["output-dir"]) config.outputDir = resolve(parsed["output-dir"]);

// stdout carries the protocol, so nothing else may write to it.
console.log = console.info = (...items) => process.stderr.write(items.join(" ") + "\n");

const apostate = await import("@heretic-tech/apostate");
const { createConnection } = await import("@playwright/mcp");
const { StdioServerTransport } = await import("@modelcontextprotocol/sdk/server/stdio.js");

let context;
async function browserContext() {
  if (context) return context;
  if (parsed.isolated) {
    context = await apostate.launchContext(options);
  } else {
    const directory = resolve(parsed["user-data-dir"] ?? join(dataDir(), "profiles", parsed.profile));
    mkdirSync(directory, { recursive: true });
    context = await apostate.launchPersistentContext(directory, options);
  }
  return context;
}

let closing = false;
async function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  try {
    await context?.close();
  } catch {
    // The browser may already be gone.
  }
  process.exit(code);
}

process.on("SIGINT", () => void shutdown(0));
process.on("SIGTERM", () => void shutdown(0));
process.stdin.on("close", () => void shutdown(0));

// The browser starts on the first tool call, so a client that only lists the
// tools never launches one.
const server = await createConnection(config, browserContext);
await server.connect(new StdioServerTransport());
