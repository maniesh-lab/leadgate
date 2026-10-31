/**
 * Playwright config for the end-to-end test (e2e/). It drives a real browser against the
 * e2e compose stack (docker-compose.e2e.yml, started by `npm run e2e:up`), whose nginx
 * serves the built app and passes /api to the API on http://localhost:8081.
 * One browser (Chromium) is enough: the test checks the flow, not browser quirks.
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  // The worker needs a few seconds per lead, so give each test room.
  timeout: 90_000,
  expect: { timeout: 10_000 },
  forbidOnly: true,
  use: {
    baseURL: "http://localhost:8081",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
