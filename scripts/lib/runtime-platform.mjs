function configuredValue(value) {
  const normalized = String(value || "").trim();
  return normalized || "";
}

export function pythonCommand(platform, env = process.env) {
  const configured = configuredValue(env.PKULAW_PYTHON);
  if (configured) return configured;
  return platform === "win32" ? "D:\\Python\\python.exe" : "python3";
}

export function pnpmCommand(platform, env = process.env) {
  const configured = configuredValue(env.PNPM_EXECUTABLE);
  if (configured) return configured;
  return platform === "win32" ? "pnpm.cmd" : "pnpm";
}
