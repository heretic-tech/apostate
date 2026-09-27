import { launch } from "@heretic-tech/apostate";

const browser = await launch({ fingerprint: 42, fingerprintPlatform: "windows" });
const page = await browser.newPage();
await page.goto("https://example.com");
const values = await page.evaluate(() => {
  const gl = document.createElement("canvas").getContext("webgl");
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    cores: navigator.hardwareConcurrency,
    memory: navigator.deviceMemory,
    screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
    gpu: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
  };
});
for (const [name, value] of Object.entries(values)) console.log(name.padEnd(10), value);
await browser.close();
