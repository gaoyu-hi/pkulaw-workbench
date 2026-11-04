import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";

const root = path.resolve(import.meta.dirname, "..");

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: process.env
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

const python = process.env.PKULAW_PYTHON || "D:\\Python\\python.exe";
run(python, ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"], path.join(root, "engine"));

const pnpmRuntime = process.env.npm_execpath;
if (pnpmRuntime && /\.(?:c?js|mjs)$/i.test(pnpmRuntime)) {
  run(process.execPath, [pnpmRuntime, "--dir", "apps/desktop", "test"], root);
  run(process.execPath, [pnpmRuntime, "--dir", "apps/desktop", "typecheck"], root);
} else {
  const pnpm = process.env.PNPM_EXECUTABLE || "pnpm.cmd";
  run(pnpm, ["--dir", "apps/desktop", "test"], root);
  run(pnpm, ["--dir", "apps/desktop", "typecheck"], root);
}
