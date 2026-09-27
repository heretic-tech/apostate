import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { launchProcess } from "@heretic-tech/apostate";
import { chromium } from "patchright";

const userDataDir = await mkdtemp(join(tmpdir(), "apostate-"));
// launchProcess starts the browser with no driver attached.
const browserProcess = await launchProcess({
  fingerprint: 42,
  fingerprintPlatform: "windows",
  userDataDir,
  // Port 0 picks a free port on 127.0.0.1 and writes it to DevToolsActivePort.
  args: ["--remote-debugging-port=0"],
});

try {
  let port;
  const deadline = Date.now() + 30000;
  while (!port) {
    if (Date.now() > deadline) throw new Error("the browser did not open its DevTools port");
    port = await readFile(join(userDataDir, "DevToolsActivePort"), "utf8")
      .then((text) => text.split("\n")[0].trim())
      .catch(() => undefined);
    if (!port) await sleep(100);
  }

  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  const page = await browser.contexts()[0].newPage();
  await page.goto("https://example.com");
  console.log("platform ", await page.evaluate(() => navigator.platform));
  console.log("userAgent", await page.evaluate(() => navigator.userAgent));
  await browser.close();
} finally {
  await browserProcess.close();
  await rm(userDataDir, { recursive: true, force: true });
}
