import { defineConfig } from "@playwright/test";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryDirectory = resolve(webDirectory, "..");
const e2eDataDirectory = process.env.QALAB_E2E_DATA_DIR;
const pythonCommand = process.env.QALAB_E2E_PYTHON ?? "python";
if (!e2eDataDirectory) {
  throw new Error("Ejecutá Playwright con npm run test:e2e para aislar sus datos temporales.");
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  outputDir: join(e2eDataDirectory, "test-results"),
  timeout: 45_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: "http://127.0.0.1:5174",
    browserName: "chromium",
    headless: true,
    trace: "off",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `"${pythonCommand}" -m uvicorn tests.e2e_server:app --host 127.0.0.1 --port 8010`,
      cwd: repositoryDirectory,
      url: "http://127.0.0.1:8010/health",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { ...process.env, QALAB_E2E_DATA_DIR: e2eDataDirectory },
    },
    {
      command: "npm run dev -- --config vite.e2e.config.ts",
      cwd: webDirectory,
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
