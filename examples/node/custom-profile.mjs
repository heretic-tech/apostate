import { launch } from "@heretic-tech/apostate";

// Only the sections named here change. Every other value is the host's.
const profile = {
  cpu: { logical_cores: 4 },
  memory: { total_bytes: 8 * 1024 ** 3 },
  screen: {
    width: 1366, height: 768,
    avail_left: 0, avail_top: 0, avail_width: 1366, avail_height: 728,
    device_pixel_ratio: 1, color_depth: 24,
  },
  locale: { application: "en-GB", timezone: "Europe/London" },
};

const browser = await launch({ profile, geoip: false });
const page = await browser.newPage();
await page.goto("https://example.com");
const values = await page.evaluate(() => ({
  cores: navigator.hardwareConcurrency,
  memory: navigator.deviceMemory,
  screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
  pixelRatio: devicePixelRatio,
  languages: navigator.languages,
  timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
}));
for (const [name, value] of Object.entries(values)) console.log(name.padEnd(10), value);
await browser.close();
