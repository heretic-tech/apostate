import { test as base, expect } from "patchright/test";
import { launch } from "@heretic-tech/apostate";

export const appUrl = process.env.APP_URL ?? "http://127.0.0.1:8767/";

export const test = base.extend({
  // One browser per worker. A fixed seed gives every run the same machine, so a failure reproduces.
  apostate: [async ({}, use) => {
    const browser = await launch({
      fingerprint: 42,
      fingerprintPlatform: "windows",
      locale: "en-US",
      timezone: "America/New_York",
      geoip: false,
    });
    await use(browser);
    await browser.close();
  }, { scope: "worker" }],

  // Replaces Playwright Test's own page, which would open in an off-the-record context.
  page: async ({ apostate }, use) => {
    await apostate.contexts()[0].clearCookies();
    const page = await apostate.newPage();
    await use(page);
    await page.close();
  },
});

export { expect };
