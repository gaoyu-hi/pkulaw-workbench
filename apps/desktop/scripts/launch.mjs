import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import process from "node:process";

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

const appRoot = path.resolve(import.meta.dirname, "..");
const workbenchRoot = path.resolve(appRoot, "../..");
const debugPort = await getFreePort();
const stage2Fixture = process.argv.includes("--stage2") ||
  process.env.PKULAW_STAGE2_FIXTURE === "1";
const stage3Fixture = process.argv.includes("--stage3") ||
  process.env.PKULAW_STAGE3_FIXTURE === "1";
const stage4Fixture = process.argv.includes("--stage4") ||
  process.env.PKULAW_STAGE4_FIXTURE === "1";
const stage5Fixture = process.argv.includes("--stage5") ||
  process.env.PKULAW_STAGE5_FIXTURE === "1";
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
      PKULAW_DEBUG_PORT: String(debugPort),
      PKULAW_STAGE2_FIXTURE: stage2Fixture ? "1" : "0",
      PKULAW_STAGE3_FIXTURE: stage3Fixture ? "1" : "0",
      PKULAW_STAGE4_FIXTURE: stage4Fixture ? "1" : "0",
      PKULAW_STAGE5_FIXTURE: stage5Fixture ? "1" : "0",
      PKULAW_ENGINE_DIR: process.env.PKULAW_ENGINE_DIR || path.join(workbenchRoot, "engine")
    }
  }
);

child.once("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
