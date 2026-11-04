import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const outputRoot = path.resolve(root, "..", "..", "outputs");
const now = new Date();
const dateStamp =
  `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(
    now.getDate()
  ).padStart(2, "0")}`;
const zipPath = path.join(
  outputRoot,
  `北大法宝采集器_源码与构建脚本_${dateStamp}.zip`
);

const result = spawnSync(
  "tar.exe",
  [
    "-a",
    "-c",
    "-f",
    zipPath,
    "-C",
    root,
    "--exclude=node_modules",
    "--exclude=*/node_modules",
    "--exclude=.packaging-venv",
    "--exclude=build",
    "--exclude=release",
    "--exclude=clean-room",
    "--exclude=outputs",
    "--exclude=*/outputs",
    "--exclude=*/__pycache__",
    "--exclude=*.pyc",
    "--exclude=apps/desktop/out",
    "--exclude=apps/desktop/runtime",
    "--exclude=spikes/*/runtime",
    "--exclude=.coverage*",
    "--exclude=*/.coverage*",
    "--exclude=coverage.json",
    "--exclude=coverage-summary.json",
    "--exclude=coverage-*.json",
    "--exclude=coverage-html",
    "--exclude=*/coverage-html",
    "--exclude=*/coverage-*.json",
    "--exclude=apps/desktop/coverage",
    "--exclude=*.tsbuildinfo",
    "--exclude=./$null",
    "--exclude=docs/0.2.2_PHASE_H_SOURCE_FILE_LIST.txt",
    "."
  ],
  { stdio: "inherit", windowsHide: true }
);
if (result.error) throw result.error;
if (result.status !== 0) {
  throw new Error(`tar.exe exited with code ${result.status}`);
}

const hash = crypto.createHash("sha256");
for await (const chunk of fs.createReadStream(zipPath)) hash.update(chunk);
const sha256 = hash.digest("hex");
await fs.promises.writeFile(
  `${zipPath}.sha256`,
  `${sha256}  ${path.basename(zipPath)}\n`,
  "utf8"
);
console.log(JSON.stringify({ ok: true, sourceZip: zipPath, sha256 }, null, 2));
