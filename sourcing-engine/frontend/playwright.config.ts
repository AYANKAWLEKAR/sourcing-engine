import { defineConfig, devices } from "@playwright/test";

// Brings up BOTH servers the E2E run needs:
//   1. the seeded demo API on :8000 (python scripts/serve_demo_ui.py, run from ../)
//   2. the Next.js dev server on :3000, proxying /api → :8000
// then runs the specs in ./e2e against http://127.0.0.1:3000.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "cd .. && .venv/bin/python scripts/serve_demo_ui.py",
      url: "http://127.0.0.1:8000/runs",
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run dev",
      url: "http://127.0.0.1:3000",
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
      env: { API_PROXY_TARGET: "http://127.0.0.1:8000" },
    },
  ],
});
