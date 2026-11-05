import assert from "node:assert/strict";
import test from "node:test";

import {
  pnpmCommand,
  pythonCommand
} from "../lib/runtime-platform.mjs";

test("uses the Windows Python default on win32", () => {
  assert.equal(pythonCommand("win32", {}), "D:\\Python\\python.exe");
});

test("uses python3 on darwin", () => {
  assert.equal(pythonCommand("darwin", {}), "python3");
});

test("honors PKULAW_PYTHON on every platform", () => {
  assert.equal(
    pythonCommand("darwin", {
      PKULAW_PYTHON: "/opt/python/bin/python3"
    }),
    "/opt/python/bin/python3"
  );
  assert.equal(
    pythonCommand("win32", {
      PKULAW_PYTHON: "C:\\Python\\python.exe"
    }),
    "C:\\Python\\python.exe"
  );
});

test("uses pnpm.cmd on Windows and pnpm on macOS", () => {
  assert.equal(pnpmCommand("win32", {}), "pnpm.cmd");
  assert.equal(pnpmCommand("darwin", {}), "pnpm");
  assert.equal(
    pnpmCommand("darwin", { PNPM_EXECUTABLE: "/usr/local/bin/pnpm" }),
    "/usr/local/bin/pnpm"
  );
});
