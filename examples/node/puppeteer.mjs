import { launch } from "@heretic-tech/apostate";

// Needs puppeteer-core installed next to the package: npm install puppeteer-core
const browser = await launch({ fingerprint: 42, fingerprintPlatform: "windows", driver: "puppeteer-core" });
const page = await browser.newPage();
await page.goto("https://example.com");
console.log("driver    ", browser.apostateDriverName);
console.log("version   ", await browser.version());
console.log("platform  ", await page.evaluate(() => navigator.platform));
console.log("userAgent ", await page.evaluate(() => navigator.userAgent));
await browser.close();
