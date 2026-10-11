import { defineConfig } from "@playwright/test";

// End-to-end smoke tests against a RUNNING stack: the demo containers, the API on :8000 and the
// frontend on :5173 (see docs/DEMO.md), seeded with `python -m scripts.demo_setup`. They are not part
// of `npm test` (Vitest), so unit runs stay fast and need no services.
//
// Locally this drives the Google Chrome that is already installed (no browser download). In CI, set
// E2E_BROWSER=chromium and run `npx playwright install chromium` first.
const useBundledChromium = process.env.E2E_BROWSER === "chromium";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    ...(useBundledChromium ? {} : { channel: "chrome" }),
    trace: "retain-on-failure",
  },
});
