import assert from "node:assert/strict";
import test from "node:test";

import { assertMacArm64Host } from "../lib/build-host.mjs";

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
