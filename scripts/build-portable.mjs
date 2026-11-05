import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import process from "node:process";

if (process.platform !== "win32") {
  throw new Error(
    `WINDOWS_PORTABLE_BUILD_REQUIRED: received ${process.platform}/${process.arch}`
  );
}

const root = path.resolve(import.meta.dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const releaseRoot = path.join(root, "release");
const engineBuildRoot = path.join(releaseRoot, "engine");
const stageRoot = path.join(releaseRoot, "PkulawWorkbench-portable");
const portableDataPath = path.join(stageRoot, "数据");
const portableDataBackup = path.join(
  releaseRoot,
  ".portable-data-preserve-current"
);
const outputRoot = path.resolve(root, "..", "..", "outputs");
const packageInfo = JSON.parse(
  await fsp.readFile(path.join(appRoot, "package.json"), "utf8")
);
const version = String(packageInfo.version || "0.0.0");
const now = new Date();
const dateStamp = [
  now.getFullYear(),
  String(now.getMonth() + 1).padStart(2, "0"),
  String(now.getDate()).padStart(2, "0")
].join("");
const zipName = `北大法宝采集器_Windows便携版_${version}_${dateStamp}.zip`;
const zipPath = path.join(outputRoot, zipName);

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: process.env,
    windowsHide: true,
    shell:
      process.platform === "win32" && /\.(?:cmd|bat)$/i.test(command)
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${path.basename(command)} exited with code ${result.status ?? "unknown"}`
    );
  }
}

function assertInside(candidate, parent) {
  const resolvedCandidate = path.resolve(candidate);
  const resolvedParent = path.resolve(parent) + path.sep;
  if (!resolvedCandidate.startsWith(resolvedParent)) {
    throw new Error(`Refusing to modify path outside release root: ${candidate}`);
  }
  return resolvedCandidate;
}

async function copyDirectory(source, destination) {
  await fsp.mkdir(path.dirname(destination), { recursive: true });
  await fsp.cp(source, destination, {
    recursive: true,
    force: true,
    dereference: true,
    errorOnExist: false
  });
}

async function fileSha256(filePath) {
  const hash = crypto.createHash("sha256");
  const stream = fs.createReadStream(filePath);
  for await (const chunk of stream) hash.update(chunk);
  return hash.digest("hex");
}

const pnpm = process.env.PNPM_EXECUTABLE || "pnpm.cmd";
const packagingPython =
  process.env.PKULAW_PACKAGING_PYTHON ||
  path.join(root, ".packaging-venv", "Scripts", "python.exe");
const python = fs.existsSync(packagingPython) ? packagingPython : "python";

await fsp.mkdir(releaseRoot, { recursive: true });
await fsp.mkdir(outputRoot, { recursive: true });

if (!process.argv.includes("--skip-build")) {
  run(pnpm, ["--dir", appRoot, "build"]);
  try {
    run(python, ["-c", "import PyInstaller"]);
  } catch {
    throw new Error(
      `PyInstaller is unavailable. Create a build environment with:\n` +
        `python -m venv .packaging-venv\n` +
        `.packaging-venv\\Scripts\\python.exe -m pip install -r engine\\requirements-build.txt`
    );
  }
  await fsp.rm(path.join(engineBuildRoot, "pkulaw-engine"), {
    recursive: true,
    force: true
  });
  run(python, [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath",
    engineBuildRoot,
    "--workpath",
    path.join(root, "build", "pyinstaller"),
    path.join(root, "engine", "pkulaw-engine.spec")
  ]);
}

const preservePortableData = fs.existsSync(portableDataPath);
if (preservePortableData) {
  if (fs.existsSync(portableDataBackup)) {
    throw new Error(
      `Refusing to overwrite an existing portable data backup: ${portableDataBackup}`
    );
  }
  await fsp.cp(portableDataPath, portableDataBackup, {
    recursive: true,
    force: false,
    errorOnExist: true
  });
}

await fsp.rm(assertInside(stageRoot, releaseRoot), {
  recursive: true,
  force: true
});

const electronDist = path.join(appRoot, "node_modules", "electron", "dist");
if (!fs.existsSync(path.join(electronDist, "electron.exe"))) {
  throw new Error(`Electron runtime not found: ${electronDist}`);
}
await fsp.mkdir(stageRoot, { recursive: true });
await fsp.cp(electronDist, stageRoot, {
  recursive: true,
  force: true,
  dereference: true
});

const executablePath = path.join(stageRoot, "electron.exe");
const productExecutablePath = path.join(stageRoot, "法宝工作台.exe");
await fsp.rename(executablePath, productExecutablePath);
await fsp.rm(path.join(stageRoot, "resources", "default_app.asar"), {
  force: true
});

const packagedAppRoot = path.join(stageRoot, "resources", "app");
await fsp.mkdir(packagedAppRoot, { recursive: true });
await fsp.writeFile(
  path.join(packagedAppRoot, "package.json"),
  JSON.stringify(
    {
      name: "pkulaw-workbench",
      productName: "法宝工作台",
      version,
      description: "北大法宝政策采集与数据治理工作台",
      main: "out/main/index.js",
      private: true
    },
    null,
    2
  ),
  "utf8"
);
await copyDirectory(path.join(appRoot, "out"), path.join(packagedAppRoot, "out"));

const fflateSource = await fsp.realpath(
  path.join(appRoot, "node_modules", "fflate")
);
await copyDirectory(
  fflateSource,
  path.join(packagedAppRoot, "node_modules", "fflate")
);

const builtEngine = path.join(engineBuildRoot, "pkulaw-engine");
if (!fs.existsSync(path.join(builtEngine, "pkulaw-engine.exe"))) {
  throw new Error("PyInstaller did not produce pkulaw-engine.exe");
}
await copyDirectory(
  builtEngine,
  path.join(stageRoot, "resources", "engine")
);

const launcher = [
  "@echo off",
  "chcp 65001 >nul",
  "setlocal",
  "cd /d \"%~dp0\"",
  "if not exist \"%~dp0数据\" mkdir \"%~dp0数据\"",
  "if not exist \"%~dp0数据\\Electron-Profile\" mkdir \"%~dp0数据\\Electron-Profile\"",
  "set \"PKULAW_DATA_DIR=%~dp0数据\"",
  "for /f \"usebackq delims=\" %%P in (`powershell.exe -NoProfile -NonInteractive -Command \"$l=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0);$l.Start();$p=$l.LocalEndpoint.Port;$l.Stop();$p\"`) do set \"PKULAW_DEBUG_PORT=%%P\"",
  "if not defined PKULAW_DEBUG_PORT (",
  "  echo 无法分配本机调试端口，请检查 PowerShell 是否可用。",
  "  pause",
  "  exit /b 1",
  ")",
  "start \"\" \"%~dp0法宝工作台.exe\" --user-data-dir=\"%~dp0数据\\Electron-Profile\" --remote-debugging-port=%PKULAW_DEBUG_PORT% --remote-debugging-address=127.0.0.1",
  "endlocal",
  ""
].join("\r\n");
await fsp.writeFile(
  path.join(stageRoot, "启动法宝工作台.cmd"),
  "\uFEFF" + launcher,
  "utf8"
);

const deploymentDir = path.join(root, "deployment");
for (const entry of await fsp.readdir(deploymentDir, { withFileTypes: true })) {
  if (entry.isFile()) {
    await fsp.copyFile(
      path.join(deploymentDir, entry.name),
      path.join(stageRoot, entry.name)
    );
  }
}

const versionInfo = {
  productName: "法宝工作台",
  version,
  buildDate: new Date().toISOString(),
  electron: fs
    .readFileSync(
      path.join(appRoot, "node_modules", "electron", "dist", "version"),
      "utf8"
    )
    .trim(),
  pythonEngine: "engine/pkulaw-engine.exe",
  portableDataDirectory: "数据",
  runtimeDependencies: {
    DrissionPage: "4.1.1.4",
    "python-docx": "1.2.0",
    openpyxl: "3.1.5",
    xlrd: "2.0.2"
  },
  securityBoundary:
    "不保存账号密码或导出 Cookie；不绕过登录、滑块、验证码或机构额度。"
};
await fsp.writeFile(
  path.join(stageRoot, "版本信息.json"),
  JSON.stringify(versionInfo, null, 2),
  "utf8"
);
await fsp.copyFile(
  path.join(root, "engine", "requirements.txt"),
  path.join(stageRoot, "Python依赖版本.txt")
);
await fsp.copyFile(
  path.join(root, "engine", "requirements-lock.txt"),
  path.join(stageRoot, "Python完整依赖锁定.txt")
);

if (fs.existsSync(zipPath)) await fsp.rm(zipPath, { force: true });
run("tar.exe", [
  "-a",
  "-c",
  "-f",
  zipPath,
  "-C",
  releaseRoot,
  path.basename(stageRoot)
]);
const checksum = await fileSha256(zipPath);
await fsp.writeFile(`${zipPath}.sha256`, `${checksum}  ${path.basename(zipPath)}\n`, "utf8");

run(process.execPath, [path.join(root, "scripts", "package-source.mjs")]);

if (preservePortableData) {
  await fsp.rm(portableDataPath, { recursive: true, force: true });
  await fsp.rename(portableDataBackup, portableDataPath);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      version,
      portableDirectory: stageRoot,
      zip: zipPath,
      sha256: checksum
    },
    null,
    2
  )
);
