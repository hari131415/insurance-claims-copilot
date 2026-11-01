import base from "./playwright.config";

// Local-only screenshot tool, not run in CI: it needs the generated
// dataset (evals/data, gitignored; rebuild with evals/generate.py)
// and the recorded provider, so every shot shows real Claude outputs.
// Run: npm run screenshots
//
// The full Chromium build (channel "chromium") instead of the
// headless shell: the shell has no PDF viewer, so the scanned-page
// preview iframe would screenshot blank.
export default {
  ...base,
  testDir: "./screenshots",
  use: {
    ...base.use,
    launchOptions: { channel: "chromium" },
  },
  webServer: [
    {
      command: "./e2e/run-backend.sh",
      url: "http://localhost:8000/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120000,
      env: { LLM_PROVIDER: "recorded" },
    },
    {
      command: "npm run preview -- --port 5173",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120000,
    },
  ],
};
