import { launch } from "@heretic-tech/apostate";

const proxy = process.env.APOSTATE_PROXY;
if (!proxy) {
  console.log("APOSTATE_PROXY is not set. Set it to a proxy URL, for example "
    + "socks5://user:pass@proxy.example:1080, and run this again.");
  process.exit(0);
}

// With a proxy, the package looks up the proxy's exit and sets the locale and
// timezone from it before the browser starts.
const browser = await launch({ fingerprint: 42, fingerprintPlatform: "windows", proxy });
const page = await browser.newPage();
await page.goto("https://api.ipify.org/?format=json");
console.log("exit IP   ", await page.evaluate(() => JSON.parse(document.body.innerText).ip));
console.log("timezone  ", await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone));
console.log("languages ", await page.evaluate(() => navigator.languages));
await browser.close();
