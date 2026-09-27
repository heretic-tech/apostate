import { launch } from "@heretic-tech/apostate";

async function session(seed) {
  const browser = await launch({ fingerprint: seed, fingerprintPlatform: "windows" });
  try {
    const page = await browser.newPage();
    await page.goto("https://example.com");
    const machine = await page.evaluate(() => {
      const gl = document.createElement("canvas").getContext("webgl");
      const info = gl.getExtension("WEBGL_debug_renderer_info");
      return [
        `${navigator.hardwareConcurrency} cores`,
        `${screen.width}x${screen.height}`,
        gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
      ].join(", ");
    });
    return [seed, machine];
  } finally {
    await browser.close();
  }
}

const results = await Promise.all([1, 2, 3].map(session));
for (const [seed, machine] of results) console.log(`seed ${seed}  ${machine}`);
