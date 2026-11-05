import { describe, expect, it } from "vitest";

import {
  isBundledEngineConfig,
  resolveDesktopPlatform
} from "../src/main/platform";

describe("desktop platform adapter", () => {
  it("maps Windows engine and Python defaults", () => {
    expect(resolveDesktopPlatform("win32", {})).toMatchObject({
      platform: "win32",
      backendExecutableName: "pkulaw-engine.exe",
      developmentPythonPath: "D:\\Python\\python.exe",
      trashLabel: "Windows 回收站",
      forceKillSignal: "SIGTERM"
    });
  });

  it("maps Apple Silicon engine and python3 defaults", () => {
    expect(resolveDesktopPlatform("darwin", {})).toMatchObject({
      platform: "darwin",
      backendExecutableName: "pkulaw-engine",
      developmentPythonPath: "python3",
      trashLabel: "废纸篓",
      forceKillSignal: "SIGKILL"
    });
  });

  it("uses SIGKILL as the macOS forced-stop signal", () => {
    expect(resolveDesktopPlatform("darwin", {}).forceKillSignal).toBe(
      "SIGKILL"
    );
  });

  it("honors PKULAW_PYTHON and recognizes only the platform backend name", () => {
    const adapter = resolveDesktopPlatform("darwin", {
      PKULAW_PYTHON: "/opt/homebrew/bin/python3.14"
    });

    expect(adapter.developmentPythonPath).toBe(
      "/opt/homebrew/bin/python3.14"
    );
    expect(isBundledEngineConfig("darwin", "/tmp/pkulaw-engine")).toBe(
      true
    );
    expect(
      isBundledEngineConfig("darwin", "/tmp/pkulaw-engine.exe")
    ).toBe(false);
    expect(
      isBundledEngineConfig("win32", "C:\\engine\\PKULAW-ENGINE.EXE")
    ).toBe(true);
  });

  it("rejects unsupported desktop platforms", () => {
    expect(() => resolveDesktopPlatform("linux", {})).toThrow(
      "UNSUPPORTED_DESKTOP_PLATFORM: linux"
    );
  });
});
