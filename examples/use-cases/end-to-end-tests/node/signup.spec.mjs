import { appUrl, expect, test } from "./fixtures.mjs";

test("signup", async ({ page }) => {
  await page.goto(appUrl);
  await page.fill("#email", "e2e@example.test");
  await page.click("button[type=submit]");
  await expect(page.locator("#result")).toHaveText("Account created for e2e@example.test");
});

test("persona", async ({ page }) => {
  await page.goto(appUrl);
  expect(await page.evaluate(() => navigator.platform)).toBe("Win32");
  expect(await page.evaluate(() => [screen.width, screen.height])).toEqual([1920, 1080]);
});
