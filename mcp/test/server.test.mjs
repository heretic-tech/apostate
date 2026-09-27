// Starts the server over stdio, as an MCP client does, and drives the browser
// through its tools. Needs the Apostate browser installed (`npx apostate install`).
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const CLI = new URL("../cli.mjs", import.meta.url).pathname;
const PAGE = "data:text/html,<title>Form</title><input aria-label=Name><button onclick=\"document.title='Hello '+document.querySelector('input').value\">Send</button>";

function text(result) {
  return (result.content ?? []).filter((item) => item.type === "text").map((item) => item.text).join("\n");
}

test("tools drive a Windows persona", { timeout: 120_000 }, async () => {
  const profile = mkdtempSync(join(tmpdir(), "apostate-mcp-test-"));
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [CLI, "--fingerprint", "42", "--platform", "windows", "--user-data-dir", profile,
      "--no-geoip", "--locale", "en-US", "--timezone", "America/New_York", "--output-dir", join(profile, "output")],
    stderr: "ignore",
  });
  const client = new Client({ name: "apostate-mcp-test", version: "0" });
  try {
    await client.connect(transport);
    const { tools } = await client.listTools();
    for (const name of ["browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_evaluate", "browser_tabs", "browser_take_screenshot"]) {
      assert.ok(tools.some((tool) => tool.name === name), `missing tool ${name}`);
    }

    await client.callTool({ name: "browser_navigate", arguments: { url: PAGE } });
    const snapshot = text(await client.callTool({ name: "browser_snapshot", arguments: {} }));
    const input = snapshot.match(/textbox "Name" \[ref=(\w+)\]/);
    const button = snapshot.match(/button "Send" \[ref=(\w+)\]/);
    assert.ok(input && button, snapshot);

    await client.callTool({ name: "browser_type", arguments: { element: "Name", target: input[1], text: "Ada" } });
    await client.callTool({ name: "browser_click", arguments: { element: "Send", target: button[1] } });

    const values = text(await client.callTool({
      name: "browser_evaluate",
      arguments: {
        function: "() => JSON.stringify({ title: document.title, webdriver: navigator.webdriver, platform: navigator.platform, screen: [screen.width, screen.height, screen.availHeight], timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })",
      },
    }));
    const json = JSON.parse(JSON.parse(values.match(/"\{.*\}"/)[0]));
    assert.equal(json.title, "Hello Ada");
    assert.equal(json.webdriver, false);
    assert.equal(json.platform, "Win32");
    assert.equal(json.timezone, "America/New_York");
    assert.ok(json.screen[2] < json.screen[1], `no taskbar gap: ${json.screen}`);

    const shot = await client.callTool({ name: "browser_take_screenshot", arguments: {} });
    assert.ok((shot.content ?? []).some((item) => item.type === "image"), "no screenshot image");
  } finally {
    await client.close();
    rmSync(profile, { recursive: true, force: true });
  }
});
