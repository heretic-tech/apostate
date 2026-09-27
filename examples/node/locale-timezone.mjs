import { launch } from "@heretic-tech/apostate";

// geoip: false skips the lookup; locale and timezone are set here instead.
const browser = await launch({
  fingerprint: 42,
  fingerprintPlatform: "windows",
  locale: "de-DE",
  timezone: "Europe/Berlin",
  geoip: false,
});
const page = await browser.newPage();
const response = await page.goto("https://example.com");
const values = await page.evaluate(() => {
  const noon = new Date(Date.UTC(2026, 0, 15, 12, 0));
  return {
    language: navigator.language,
    languages: navigator.languages,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    intlLocale: Intl.DateTimeFormat().resolvedOptions().locale,
    date: noon.toLocaleString(),
    number: (1234567.891).toLocaleString(),
    utcOffset: -noon.getTimezoneOffset(),
  };
});
for (const [name, value] of Object.entries(values)) console.log(name.padEnd(15), value);
const headers = await response.request().allHeaders();
console.log("Accept-Language".padEnd(15), headers["accept-language"]);
await browser.close();
