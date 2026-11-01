import base from "./playwright.config";

// Same servers as the mock config, but the backend runs the recorded
// provider so the header badge and the unknown-file failure can be
// tested without any dataset files or API keys.
export default {
  ...base,
  testDir: "./e2e-recorded",
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
