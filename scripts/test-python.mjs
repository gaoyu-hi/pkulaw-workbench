import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { pythonCommand } from "./lib/runtime-platform.mjs";

const root = path.resolve(import.meta.dirname, "..");
const python = pythonCommand(process.platform, process.env);
const result = spawnSync(
  python,
  ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"],
  {
    cwd: path.join(root, "engine"),
    stdio: "inherit",
    env: process.env
  }
);
if (result.error) throw result.error;
process.exit(result.status ?? 1);
