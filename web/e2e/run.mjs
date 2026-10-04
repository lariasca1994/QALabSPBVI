import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webDirectory = dirname(dirname(fileURLToPath(import.meta.url)));
const temporaryDirectory = mkdtempSync(join(tmpdir(), "qalabspbvi-e2e-"));
const playwrightCli = resolve(
  webDirectory,
  "node_modules",
  "@playwright",
  "test",
  "cli.js",
);

try {
  const child = spawn(process.execPath, [playwrightCli, "test"], {
    cwd: webDirectory,
    env: {
      ...process.env,
      QALAB_E2E_DATA_DIR: temporaryDirectory,
    },
    stdio: "inherit",
  });

  const exitCode = await new Promise((resolveExit, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (signal) {
        reject(new Error(`Playwright terminó por la señal ${signal}.`));
        return;
      }
      resolveExit(code ?? 1);
    });
  });
  process.exitCode = exitCode;
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true, maxRetries: 10, retryDelay: 250 });
}
