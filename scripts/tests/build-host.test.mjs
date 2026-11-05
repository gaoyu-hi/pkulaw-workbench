import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  assertMacArm64Host,
  macDmgArtifacts
} from "../lib/build-host.mjs";

test("accepts darwin arm64", () => {
  assert.doesNotThrow(() => assertMacArm64Host("darwin", "arm64"));
});

test("rejects win32 arm64", () => {
  assert.throws(
    () => assertMacArm64Host("win32", "arm64"),
    /MAC_ARM64_BUILD_HOST_REQUIRED/
  );
});

test("rejects darwin x64", () => {
  assert.throws(
    () => assertMacArm64Host("darwin", "x64"),
    /MAC_ARM64_BUILD_HOST_REQUIRED/
  );
});

test("computes canonical arm64 DMG artifact paths", () => {
  const releaseRoot = path.join("tmp", "release", "macos");
  const artifacts = macDmgArtifacts(releaseRoot, "0.2.4");
  const dmgName = "PkulawWorkbench-0.2.4-macOS-arm64.dmg";

  assert.equal(artifacts.dmg, path.join(releaseRoot, dmgName));
  assert.equal(
    artifacts.checksum,
    path.join(releaseRoot, `${dmgName}.sha256`)
  );
});
