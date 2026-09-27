import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { launchPersistentContext } from "@heretic-tech/apostate";

const profile = join(dirname(fileURLToPath(import.meta.url)), "profiles", "demo");

async function readMachine() {
  // No seed: the first launch draws one and writes it to profiles/demo/apostate/identity.
  const context = await launchPersistentContext(profile, { fingerprintPlatform: "windows" });
  const page = await context.newPage();
  await page.goto("https://example.com");
  const machine = await page.evaluate(() => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return [
      navigator.platform,
      `${navigator.hardwareConcurrency} cores`,
      `${screen.width}x${screen.height}`,
      gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
    ].join(", ");
  });
  await context.close();
  return machine;
}

const first = await readMachine();
const second = await readMachine();
const identity = await readFile(join(profile, "apostate", "identity"), "utf8");
console.log("identity file ", identity.trim());
console.log("first launch  ", first);
console.log("second launch ", second);
console.log("same machine  ", first === second);
