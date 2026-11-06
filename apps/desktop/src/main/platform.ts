import path from "node:path";

export interface DesktopPlatformAdapter {
  platform: "win32" | "darwin";
  backendExecutableName: "pkulaw-engine.exe" | "pkulaw-engine";
  developmentPythonPath: string;
  trashLabel: string;
  forceKillSignal: NodeJS.Signals;
}

export function resolveDesktopPlatform(
  platform: NodeJS.Platform = process.platform,
  env: NodeJS.ProcessEnv = process.env
): DesktopPlatformAdapter {
  const configuredPython = String(env.PKULAW_PYTHON || "").trim();

  if (platform === "win32") {
    return {
      platform,
      backendExecutableName: "pkulaw-engine.exe",
      developmentPythonPath:
        configuredPython || "D:\\Python\\python.exe",
      trashLabel: "Windows 回收站",
      forceKillSignal: "SIGTERM"
    };
  }

  if (platform === "darwin") {
    return {
      platform,
      backendExecutableName: "pkulaw-engine",
      developmentPythonPath: configuredPython || "python3",
      trashLabel: "废纸篓",
      forceKillSignal: "SIGKILL"
    };
  }

  throw new Error(`UNSUPPORTED_DESKTOP_PLATFORM: ${platform}`);
}

export function isBundledEngineConfig(
  platform: NodeJS.Platform,
  candidate: string
): boolean {
  const value = String(candidate || "");
  if (platform === "win32") {
    return path.win32.basename(value).toLowerCase() === "pkulaw-engine.exe";
  }
  if (platform === "darwin") {
    return path.posix.basename(value) === "pkulaw-engine";
  }
  return false;
}
