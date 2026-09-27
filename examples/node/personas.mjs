import { launch } from "@heretic-tech/apostate";

for (const persona of ["windows", "macos", "linux"]) {
  const browser = await launch({ fingerprint: 42, fingerprintPlatform: persona });
  // queryLocalFonts() lists the fonts a page can see once this permission is granted.
  await browser.contexts()[0].grantPermissions(["local-fonts"]);
  const page = await browser.newPage();
  await page.goto("https://example.com");
  const values = await page.evaluate(async () => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const fonts = await queryLocalFonts();
    return {
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      gpu: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
      screen: `${screen.width}x${screen.height} at ${devicePixelRatio}x`,
      fonts: new Set(fonts.map((font) => font.family)).size,
    };
  });
  console.log(persona);
  for (const [name, value] of Object.entries(values)) console.log(" ", name.padEnd(10), value);
  await browser.close();
}
