import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import process from "node:process";

const appRoot = path.resolve(import.meta.dirname, "..");
const workbenchRoot = path.resolve(appRoot, "../..");
const modeIndex = process.argv.indexOf("--mode");
const mode = modeIndex >= 0 ? process.argv[modeIndex + 1] : "stage2";
if (!["stage2", "stage3", "stage4", "stage5", "stage6"].includes(mode)) {
  throw new Error(`Unsupported smoke mode: ${mode}`);
}
const dataDirectory = path.join(appRoot, "runtime", `smoke-${mode}`);
const reportPath = path.join(dataDirectory, "smoke-report.json");
const screenshotPath = path.join(dataDirectory, "smoke.png");
await fs.rm(dataDirectory, { recursive: true, force: true });
await fs.mkdir(dataDirectory, { recursive: true });

const debugPort = await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.unref();
  server.once("error", reject);
  server.listen(0, "127.0.0.1", () => {
    const port = server.address().port;
    server.close(() => resolve(port));
  });
});

const electronCli = path.join(appRoot, "node_modules", "electron", "cli.js");
const child = spawn(
  process.execPath,
  [
    electronCli,
    path.join(appRoot, "out/main/index.js"),
    `--remote-debugging-port=${debugPort}`,
    "--remote-debugging-address=127.0.0.1"
  ],
  {
    cwd: appRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      PKULAW_SMOKE: "1",
      PKULAW_SMOKE_MODE: mode,
      PKULAW_STAGE2_FIXTURE: mode === "stage2" ? "1" : "0",
      PKULAW_STAGE3_FIXTURE: mode === "stage3" ? "1" : "0",
      PKULAW_STAGE4_FIXTURE: mode === "stage4" ? "1" : "0",
      PKULAW_STAGE5_FIXTURE: mode === "stage5" ? "1" : "0",
      PKULAW_STAGE6_FIXTURE: mode === "stage6" ? "1" : "0",
      PKULAW_DEBUG_PORT: String(debugPort),
      PKULAW_SMOKE_FAKE_SHELL_OPEN: mode === "stage6" ? "1" : "0",
      PKULAW_DATA_DIR: dataDirectory,
      PKULAW_ENGINE_DIR: path.join(workbenchRoot, "engine"),
      PKULAW_PYTHON: process.env.PKULAW_PYTHON || "D:\\Python\\python.exe",
      PKULAW_SMOKE_REPORT: reportPath,
      PKULAW_SMOKE_SCREENSHOT: screenshotPath
    }
  }
);

const exitCode = await new Promise((resolve) => child.once("exit", (code) => resolve(code ?? 1)));
if (exitCode !== 0) {
  process.exit(exitCode);
}

const report = JSON.parse(await fs.readFile(reportPath, "utf8"));
if (!report.ok) {
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}

console.log(`Smoke test passed. Screenshot: ${screenshotPath}`);
