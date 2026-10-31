import { defineConfig, devices } from "@playwright/test";

const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? 8001);
const BACKEND = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? 5174);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    acceptDownloads: true,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Scratch backend: throwaway DB under the OS temp dir, stub LLM (no network),
      // worker in-process so upload -> process -> review runs end to end.
      command: `.venv/bin/python e2e/stub_server.py --port ${BACKEND_PORT}`,
      cwd: "../backend",
      url: `${BACKEND}/api/v1/health`,
      reuseExistingServer: false,
      // A crashed run can leave the child listening; the runner kills the whole tree on restart.
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 3_000 },
      env: { E2E_BACKEND_PORT: String(BACKEND_PORT) },
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${FRONTEND_PORT} --strictPort`,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 3_000 },
      env: { VITE_API_TARGET: BACKEND, E2E_BACKEND_URL: BACKEND },
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
