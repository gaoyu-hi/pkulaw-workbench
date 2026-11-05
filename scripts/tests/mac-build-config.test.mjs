import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const configPath = path.resolve(
  import.meta.dirname,
  "..",
  "..",
  "apps",
  "desktop",
  "electron-builder.mac.yml"
);

test("packages the PyInstaller engine at resources/engine", () => {
  const config = fs.readFileSync(configPath, "utf8");

  assert.match(
    config,
    /from:\s*\.\.\/\.\.\/release\/macos\/engine\/pkulaw-engine\s*\n\s+to:\s*engine/
  );
});

test("does not disable Developer ID signing and hardened runtime", () => {
  const config = fs.readFileSync(configPath, "utf8");

  assert.doesNotMatch(config, /identity:\s*null/);
  assert.doesNotMatch(config, /hardenedRuntime:\s*false/);
});
