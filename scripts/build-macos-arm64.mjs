import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import {
  assertMacArm64Host,
  macDmgArtifacts
} from "./lib/build-host.mjs";

assertMacArm64Host(process.platform, process.arch);

const root = path.resolve(import.meta.dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const releaseRoot = path.join(root, "release", "macos");
const engineBuildRoot = path.join(releaseRoot, "engine");
const workRoot = path.join(root, "build", "pyinstaller-mac");
const packageInfo = JSON.parse(
  await fsp.readFile(path.join(appRoot, "package.json"), "utf8")
);
const version = String(packageInfo.version || "0.0.0");
const dmgArtifacts = macDmgArtifacts(releaseRoot, version);
const python = path.join(
  root,
  ".packaging-venv",
  "bin",
  "python"
);
const pnpm = process.env.PNPM_EXECUTABLE || "pnpm";

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: process.env,
    windowsHide: true
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${path.basename(command)} exited with code ${result.status ?? "unknown"}`
    );
  }
}

async function sha256(filePath) {
  const digest = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(filePath)) {
    digest.update(chunk);
  }
  return digest.digest("hex");
}

if (!fs.existsSync(python)) {
  throw new Error(
    `macOS packaging Python is missing: ${python}. Create .packaging-venv and install engine/requirements-build.txt first.`
  );
}

await fsp.mkdir(releaseRoot, { recursive: true });
await fsp.rm(engineBuildRoot, { recursive: true, force: true });
await fsp.rm(workRoot, { recursive: true, force: true });
await fsp.rm(dmgArtifacts.dmg, { force: true });
await fsp.rm(dmgArtifacts.checksum, { force: true });

run(pnpm, ["--dir", appRoot, "build"]);
run(python, [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--distpath",
  engineBuildRoot,
  "--workpath",
  workRoot,
  path.join(root, "engine", "pkulaw-engine.spec")
]);

const engineExecutable = path.join(
  engineBuildRoot,
  "pkulaw-engine",
  "pkulaw-engine"
);
if (!fs.existsSync(engineExecutable)) {
  throw new Error(
    `PyInstaller did not produce an Apple Silicon engine: ${engineExecutable}`
  );
}
await fsp.chmod(engineExecutable, 0o755);

run(pnpm, [
  "--dir",
  appRoot,
  "exec",
  "electron-builder",
  "--mac",
  "dmg",
  "--arm64",
  "--config",
  "electron-builder.mac.yml"
]);

const dmgPath = dmgArtifacts.dmg;
if (!fs.existsSync(dmgPath)) {
  throw new Error(`Electron Builder did not produce ${dmgPath}`);
}
const digest = await sha256(dmgPath);
await fsp.writeFile(`${dmgPath}.sha256`, `${digest}  ${path.basename(dmgPath)}\n`, "utf8");
await fsp.writeFile(
  path.join(releaseRoot, "version.json"),
  JSON.stringify(
    {
      version,
      platform: "darwin",
      arch: "arm64",
      dmg: path.basename(dmgPath),
      sha256: digest,
      builtAt: new Date().toISOString()
    },
    null,
    2
  ),
  "utf8"
);

console.log(
  JSON.stringify(
    {
      ok: true,
      version,
      dmg: dmgPath,
      sha256: digest
    },
    null,
    2
  )
);
