import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import {
  pnpmCommand,
  pythonCommand
} from "./lib/runtime-platform.mjs";

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

run(process.execPath, ["--test", "scripts/tests"], root);

const python = pythonCommand(process.platform, process.env);
run(python, ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"], path.join(root, "engine"));

const pnpmRuntime = process.env.npm_execpath;
if (pnpmRuntime && /\.(?:c?js|mjs)$/i.test(pnpmRuntime)) {
  run(process.execPath, [pnpmRuntime, "--dir", "apps/desktop", "test"], root);
  run(process.execPath, [pnpmRuntime, "--dir", "apps/desktop", "typecheck"], root);
} else {
  const pnpm = pnpmCommand(process.platform, process.env);
  run(pnpm, ["--dir", "apps/desktop", "test"], root);
  run(pnpm, ["--dir", "apps/desktop", "typecheck"], root);
}
