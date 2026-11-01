import { defineConfig } from "@playwright/test";

// The backend serves :8000 (mock provider, throwaway database via
// e2e/run-backend.sh) and the frontend preview serves :5173, which
// matches the backend's default CORS origin. Build first:
//   npm run build && npm run e2e
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  webServer: [
    {
      command: "./e2e/run-backend.sh",
      url: "http://localhost:8000/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120000,
    },
    {
      command: "npm run preview -- --port 5173",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120000,
    },
  ],
});
