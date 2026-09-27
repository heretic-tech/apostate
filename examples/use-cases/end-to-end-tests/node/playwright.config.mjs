import { defineConfig } from "patchright/test";

export default defineConfig({
  testDir: ".",
  // The app under test. Set APP_URL to test a running site instead, such as staging.
  webServer: process.env.APP_URL ? undefined : {
    command: "python3 ../app.py 8767",
    url: "http://127.0.0.1:8767/",
    reuseExistingServer: true,
  },
});
