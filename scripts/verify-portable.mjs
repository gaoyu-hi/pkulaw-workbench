import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import process from "node:process";

if (process.platform !== "win32") {
  throw new Error(
    `WINDOWS_PORTABLE_BUILD_REQUIRED: received ${process.platform}/${process.arch}`
  );
}

const root = path.resolve(import.meta.dirname, "..");
const outputRoot = path.resolve(root, "..", "..", "outputs");
const cleanRoomRoot = path.join(root, "clean-room");
const packageInfo = JSON.parse(
  await fsp.readFile(
    path.join(root, "apps", "desktop", "package.json"),
    "utf8"
  )
);
const expectedVersion = String(packageInfo.version || "");
const now = new Date();
const dateStamp =
  `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(
    now.getDate()
  ).padStart(2, "0")}`;

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function latestPortableZip() {
  const files = (await fsp.readdir(outputRoot))
    .filter(
      (name) =>
        name.startsWith("北大法宝采集器_Windows便携版_") &&
        name.includes(`_${expectedVersion}_`) &&
        name.endsWith(".zip")
    )
    .sort();
  if (!files.length) {
    throw new Error("Portable ZIP not found; run pnpm build:portable first");
  }
  return path.join(outputRoot, files.at(-1));
}

async function sha256(filePath) {
  const hash = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(filePath)) hash.update(chunk);
  return hash.digest("hex");
}

function run(command, args) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    windowsHide: true
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} exited with code ${result.status}`);
  }
}

function findResidualProcesses(dataDirectory) {
  const escapedDataDirectory = dataDirectory.replaceAll("'", "''");
  const processCheck = spawnSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-Command",
      `Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('法宝工作台.exe','pkulaw-engine.exe','electron.exe','python.exe') -and $_.CommandLine -like '*${escapedDataDirectory}*' } | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress`
    ],
    { encoding: "utf8", windowsHide: true }
  );
  return (processCheck.stdout || "").trim();
}

async function waitForFile(filePath, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(filePath)) return;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Timed out waiting for ${filePath}`);
}

async function waitForText(filePath, text, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(filePath)) {
      const content = await fsp.readFile(filePath, "utf8");
      if (content.includes(text)) return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${text} in ${filePath}`);
}

const zipPath = process.env.PKULAW_PORTABLE_ZIP
  ? path.resolve(process.env.PKULAW_PORTABLE_ZIP)
  : await latestPortableZip();
if (!fs.existsSync(zipPath)) throw new Error(`ZIP not found: ${zipPath}`);
const portableSha256 = await sha256(zipPath);
const checksumPath = `${zipPath}.sha256`;
const recordedSha256 = (
  await fsp.readFile(checksumPath, "utf8")
).trim().split(/\s+/)[0];
if (recordedSha256 !== portableSha256) {
  throw new Error(
    `Portable SHA-256 mismatch: calculated=${portableSha256} recorded=${recordedSha256}`
  );
}

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const extractionRoot = path.join(
  cleanRoomRoot,
  `${expectedVersion} 发布隔离 首次启动`,
  "较长的中文目录名称 ".repeat(3).trim(),
  stamp
);
await fsp.mkdir(extractionRoot, { recursive: true });
run("tar.exe", ["-xf", zipPath, "-C", extractionRoot]);

const portableRoot = path.join(extractionRoot, "PkulawWorkbench-portable");
const executable = path.join(portableRoot, "法宝工作台.exe");
const engineExecutable = path.join(
  portableRoot,
  "resources",
  "engine",
  "pkulaw-engine.exe"
);
if (!fs.existsSync(executable) || !fs.existsSync(engineExecutable)) {
  throw new Error("Portable runtime is incomplete");
}
const versionInfo = JSON.parse(
  await fsp.readFile(path.join(portableRoot, "版本信息.json"), "utf8")
);
const packagedInfo = JSON.parse(
  await fsp.readFile(
    path.join(portableRoot, "resources", "app", "package.json"),
    "utf8"
  )
);
if (
  !expectedVersion ||
  versionInfo.version !== expectedVersion ||
  packagedInfo.version !== expectedVersion
) {
  throw new Error(
    `Portable version mismatch: expected=${expectedVersion} metadata=${versionInfo.version} app=${packagedInfo.version}`
  );
}
const requiredDeploymentFiles = [
  "README_快速开始.md",
  "安装与卸载.md",
  "使用手册.md",
  "复现与构建.md",
  "依赖说明.md",
  "故障排查.md",
  "数据备份与恢复.md",
  "发布与验收.md",
  "Python依赖版本.txt",
  "Python完整依赖锁定.txt",
  "启动法宝工作台.cmd"
];
const missingDeploymentFiles = requiredDeploymentFiles.filter(
  (name) => !fs.existsSync(path.join(portableRoot, name))
);
if (missingDeploymentFiles.length) {
  throw new Error(
    `Portable deployment files are missing: ${missingDeploymentFiles.join(", ")}`
  );
}
const dependencyLock = await fsp.readFile(
  path.join(portableRoot, "Python完整依赖锁定.txt"),
  "utf8"
);
for (const expectedDependency of [
  "DrissionPage==4.1.1.4",
  "python-docx==1.2.0",
  "openpyxl==3.1.5",
  "xlrd==2.0.2",
  "pyinstaller==6.22.3"
]) {
  if (!dependencyLock.includes(expectedDependency)) {
    throw new Error(`Pinned dependency is missing: ${expectedDependency}`);
  }
}
const packagedNodeModules = (
  await fsp.readdir(
    path.join(portableRoot, "resources", "app", "node_modules")
  )
).sort();
if (packagedNodeModules.join("|") !== "fflate") {
  throw new Error(
    `Unexpected packaged node_modules: ${packagedNodeModules.join(", ")}`
  );
}
for (const forbidden of ["tests", "runtime", "fixtures"]) {
  if (
    fs.existsSync(
      path.join(portableRoot, "resources", "app", forbidden)
    )
  ) {
    throw new Error(`Development artifact leaked into portable app: ${forbidden}`);
  }
}

const missingRuntimeData = path.join(
  extractionRoot,
  "缺少内置后端 数据"
);
await fsp.mkdir(missingRuntimeData, { recursive: true });
const missingRuntimeReport = path.join(
  missingRuntimeData,
  "missing-runtime-report.json"
);
const missingRuntimePort = await getFreePort();
const renamedEngine = `${engineExecutable}.missing`;
await fsp.rename(engineExecutable, renamedEngine);
let missingRuntimeExitCode = 1;
let missingRuntimeSmoke;
try {
  const missingRuntimeEnvironment = {
    ...process.env,
    PKULAW_SMOKE: "1",
    PKULAW_DEBUG_PORT: String(missingRuntimePort),
    PKULAW_DATA_DIR: missingRuntimeData,
    PKULAW_SMOKE_REPORT: missingRuntimeReport
  };
  delete missingRuntimeEnvironment.PKULAW_PYTHON;
  delete missingRuntimeEnvironment.PKULAW_ENGINE_DIR;
  delete missingRuntimeEnvironment.PKULAW_ALLOW_PACKAGED_FIXTURE;
  const missingRuntimeApp = spawn(
    executable,
    [
      `--user-data-dir=${path.join(missingRuntimeData, "electron-profile")}`,
      `--remote-debugging-port=${missingRuntimePort}`,
      "--remote-debugging-address=127.0.0.1"
    ],
    {
      cwd: portableRoot,
      env: missingRuntimeEnvironment,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"]
    }
  );
  missingRuntimeExitCode = await new Promise((resolve) =>
    missingRuntimeApp.once("exit", (code) => resolve(code ?? 1))
  );
  await waitForFile(missingRuntimeReport, 30_000);
  missingRuntimeSmoke = JSON.parse(
    await fsp.readFile(missingRuntimeReport, "utf8")
  );
} finally {
  await fsp.rename(renamedEngine, engineExecutable);
}
if (
  missingRuntimeSmoke?.ok !== false ||
  !String(missingRuntimeSmoke?.error || "").includes(
    "Unable to locate Python engine directory"
  )
) {
  throw new Error(
    `Missing bundled runtime was not reported clearly: ${JSON.stringify(
      { missingRuntimeExitCode, missingRuntimeSmoke },
      null,
      2
    )}`
  );
}
const missingRuntimeResiduals = findResidualProcesses(missingRuntimeData);
if (missingRuntimeResiduals && missingRuntimeResiduals !== "null") {
  throw new Error(
    `Missing-runtime test left residual processes: ${missingRuntimeResiduals}`
  );
}

const dataDirectory = path.join(extractionRoot, "首次启动 数据");
const screenshotPath = path.join(dataDirectory, "stage6-settings.png");
const reportPath = path.join(dataDirectory, "smoke-report.json");
const debugPort = await getFreePort();
const childEnvironment = {
  ...process.env,
  PKULAW_SMOKE: "1",
  PKULAW_SMOKE_MODE: "stage6",
  PKULAW_STAGE2_FIXTURE: "0",
  PKULAW_STAGE3_FIXTURE: "0",
  PKULAW_STAGE4_FIXTURE: "0",
  PKULAW_STAGE5_FIXTURE: "0",
  PKULAW_STAGE6_FIXTURE: "1",
  PKULAW_ALLOW_PACKAGED_FIXTURE: "1",
  PKULAW_SMOKE_FAKE_SHELL_OPEN: "1",
  PKULAW_DEBUG_PORT: String(debugPort),
  PKULAW_DATA_DIR: dataDirectory,
  PKULAW_SMOKE_REPORT: reportPath,
  PKULAW_SMOKE_SCREENSHOT: screenshotPath
};
delete childEnvironment.PKULAW_PYTHON;
delete childEnvironment.PKULAW_ENGINE_DIR;
delete childEnvironment.ELECTRON_RUN_AS_NODE;

const child = spawn(
  executable,
  [
    `--user-data-dir=${path.join(dataDirectory, "electron-profile")}`,
    `--remote-debugging-port=${debugPort}`,
    "--remote-debugging-address=127.0.0.1"
  ],
  {
  cwd: portableRoot,
  env: childEnvironment,
  windowsHide: true,
  stdio: ["ignore", "pipe", "pipe"]
  }
);
const stdout = [];
const stderr = [];
child.stdout.setEncoding("utf8");
child.stderr.setEncoding("utf8");
child.stdout.on("data", (chunk) => stdout.push(chunk));
child.stderr.on("data", (chunk) => stderr.push(chunk));

let timeoutTimer;
const exitResult = await Promise.race([
  new Promise((resolve) =>
    child.once("exit", (code, signal) =>
      resolve({ code: code ?? 1, signal, timedOut: false })
    )
  ),
  new Promise((resolve) => {
    timeoutTimer = setTimeout(() => {
      child.kill();
      resolve({ code: 124, signal: "timeout", timedOut: true });
    }, 120_000);
  })
]);
clearTimeout(timeoutTimer);

await new Promise((resolve) => setTimeout(resolve, 1_000));
if (!fs.existsSync(reportPath)) {
  throw new Error(
    `Portable smoke produced no report.\nstdout:\n${stdout.join("")}\nstderr:\n${stderr.join("")}`
  );
}
const smokeReport = JSON.parse(await fsp.readFile(reportPath, "utf8"));
if (!smokeReport.ok || exitResult.code !== 0) {
  throw new Error(
    `Portable smoke failed: ${JSON.stringify(
      { exitResult, smokeReport, stdout: stdout.join(""), stderr: stderr.join("") },
      null,
      2
    )}`
  );
}
if (!smokeReport.packaged || smokeReport.backendMode !== "bundled") {
  throw new Error("Portable smoke did not use the bundled Electron/Python runtime");
}
if (
  smokeReport.backendState?.engineVersion !== expectedVersion ||
  smokeReport.diagnostics?.schemaVersion !== 6 ||
  smokeReport.diagnostics?.databaseIntegrity !== "ok"
) {
  throw new Error(
    `Portable runtime version/schema mismatch: ${JSON.stringify({
      expectedVersion,
      engineVersion: smokeReport.backendState?.engineVersion,
      schemaVersion: smokeReport.diagnostics?.schemaVersion,
      databaseIntegrity: smokeReport.diagnostics?.databaseIntegrity
    })}`
  );
}

const lingering = findResidualProcesses(dataDirectory);
if (lingering && lingering !== "null") {
  throw new Error(`Portable test left residual processes: ${lingering}`);
}

const launcherPath = path.join(portableRoot, "启动法宝工作台.cmd");
const launcherDataDirectory = path.join(portableRoot, "数据");
const launcherReportPath = path.join(launcherDataDirectory, "launcher-smoke-report.json");
const launcherScreenshotPath = path.join(launcherDataDirectory, "launcher-stage6.png");
const launcherEnvironment = {
  ...childEnvironment,
  PKULAW_SMOKE_REPORT: launcherReportPath,
  PKULAW_SMOKE_SCREENSHOT: launcherScreenshotPath,
  PKULAW_LAUNCHER_PATH: launcherPath,
  PKULAW_LAUNCHER_CWD: portableRoot
};
delete launcherEnvironment.PKULAW_DATA_DIR;
delete launcherEnvironment.PKULAW_DEBUG_PORT;
const launcher = spawn(
  "powershell.exe",
  [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    "$process = Start-Process -FilePath $env:PKULAW_LAUNCHER_PATH -WorkingDirectory $env:PKULAW_LAUNCHER_CWD -PassThru -Wait; exit $process.ExitCode"
  ],
  {
    cwd: portableRoot,
    env: launcherEnvironment,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"]
  }
);
let launcherError = "";
launcher.stderr.setEncoding("utf8");
launcher.stderr.on("data", (chunk) => {
  launcherError += chunk;
});
const launcherExitCode = await new Promise((resolve) =>
  launcher.once("exit", (code) => resolve(code ?? 1))
);
if (launcherExitCode !== 0) {
  throw new Error(`One-click launcher failed (${launcherExitCode}): ${launcherError}`);
}
await waitForFile(launcherReportPath);
await new Promise((resolve) => setTimeout(resolve, 1_500));
const launcherReport = JSON.parse(
  await fsp.readFile(launcherReportPath, "utf8")
);
if (
  !launcherReport.ok ||
  !launcherReport.packaged ||
  launcherReport.backendMode !== "bundled" ||
  !launcherReport.chineseProtocolRoundTrip
) {
  throw new Error(
    `One-click launcher smoke failed: ${JSON.stringify(launcherReport, null, 2)}`
  );
}
const launcherResiduals = findResidualProcesses(launcherDataDirectory);
if (launcherResiduals && launcherResiduals !== "null") {
  throw new Error(
    `One-click launcher left residual processes: ${launcherResiduals}`
  );
}

const recoveryDataDirectory = path.join(extractionRoot, "强制退出 数据");
await fsp.mkdir(recoveryDataDirectory, { recursive: true });
const seedPython =
  process.env.PKULAW_PACKAGING_PYTHON ||
  path.join(root, ".packaging-venv", "Scripts", "python.exe");
const seedScript = [
  "import sys",
  "from pathlib import Path",
  `sys.path.insert(0, ${JSON.stringify(path.join(root, "engine"))})`,
  "from pkulaw_engine.storage import TaskStore",
  "data = Path(sys.argv[1])",
  "store = TaskStore(data / 'pkulaw.sqlite3')",
  "store.create_task(name='强制退出恢复任务', mode='single', task_input={'matchMode': 'title'}, output_dir=str(data / '任务输出'))",
  "store.close()"
].join("; ");
run(
  fs.existsSync(seedPython) ? seedPython : "python",
  ["-c", seedScript, recoveryDataDirectory]
);

const recoveryPort = await getFreePort();
const recoveryLog = path.join(recoveryDataDirectory, "logs", "desktop.jsonl");
const recoveryEnvironment = {
  ...process.env,
  PKULAW_STAGE2_FIXTURE: "0",
  PKULAW_STAGE3_FIXTURE: "0",
  PKULAW_STAGE4_FIXTURE: "0",
  PKULAW_STAGE5_FIXTURE: "0",
  PKULAW_STAGE6_FIXTURE: "1",
  PKULAW_DEBUG_PORT: String(recoveryPort),
  PKULAW_DATA_DIR: recoveryDataDirectory
};
delete recoveryEnvironment.PKULAW_SMOKE;
delete recoveryEnvironment.PKULAW_SMOKE_MODE;
delete recoveryEnvironment.PKULAW_SMOKE_REPORT;
delete recoveryEnvironment.PKULAW_SMOKE_SCREENSHOT;
delete recoveryEnvironment.PKULAW_PYTHON;
delete recoveryEnvironment.PKULAW_ENGINE_DIR;
delete recoveryEnvironment.ELECTRON_RUN_AS_NODE;
const recoveryApp = spawn(
  executable,
  [
    `--user-data-dir=${path.join(recoveryDataDirectory, "electron-profile")}`,
    `--remote-debugging-port=${recoveryPort}`,
    "--remote-debugging-address=127.0.0.1"
  ],
  {
    cwd: portableRoot,
    env: recoveryEnvironment,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"]
  }
);
await waitForText(recoveryLog, '"event":"protocol.ready"');
const killResult = spawnSync(
  "taskkill.exe",
  ["/PID", String(recoveryApp.pid), "/F"],
  { windowsHide: true, encoding: "utf8" }
);
if (killResult.status !== 0) {
  throw new Error(`Unable to force-stop packaged app: ${killResult.stderr}`);
}
if (recoveryApp.exitCode === null) {
  await new Promise((resolve) => recoveryApp.once("exit", resolve));
}
await new Promise((resolve) => setTimeout(resolve, 1_500));
const recoveryResiduals = findResidualProcesses(recoveryDataDirectory);
if (recoveryResiduals && recoveryResiduals !== "null") {
  throw new Error(
    `Forced exit left residual processes: ${recoveryResiduals}`
  );
}

const restartReportPath = path.join(
  recoveryDataDirectory,
  "restart-smoke-report.json"
);
const restartScreenshotPath = path.join(
  recoveryDataDirectory,
  "restart-stage6.png"
);
const restartPort = await getFreePort();
const restartEnvironment = {
  ...childEnvironment,
  PKULAW_DATA_DIR: recoveryDataDirectory,
  PKULAW_DEBUG_PORT: String(restartPort),
  PKULAW_SMOKE_REPORT: restartReportPath,
  PKULAW_SMOKE_SCREENSHOT: restartScreenshotPath
};
const restartApp = spawn(
  executable,
  [
    `--user-data-dir=${path.join(recoveryDataDirectory, "electron-profile")}`,
    `--remote-debugging-port=${restartPort}`,
    "--remote-debugging-address=127.0.0.1"
  ],
  {
    cwd: portableRoot,
    env: restartEnvironment,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"]
  }
);
const restartExitCode = await new Promise((resolve) =>
  restartApp.once("exit", (code) => resolve(code ?? 1))
);
await waitForFile(restartReportPath);
const restartReport = JSON.parse(
  await fsp.readFile(restartReportPath, "utf8")
);
if (restartExitCode !== 0 || !restartReport.ok) {
  throw new Error(
    `Restart after forced exit failed: ${JSON.stringify(restartReport, null, 2)}`
  );
}
const recoveryCheckScript = [
  "import sqlite3,sys",
  "connection = sqlite3.connect(sys.argv[1])",
  "assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'",
  "assert connection.execute(\"SELECT COUNT(*) FROM tasks WHERE name='强制退出恢复任务'\").fetchone()[0] == 1",
  "connection.close()",
  "print('recovery-ok')"
].join("; ");
run(
  fs.existsSync(seedPython) ? seedPython : "python",
  ["-c", recoveryCheckScript, path.join(recoveryDataDirectory, "pkulaw.sqlite3")]
);

const screenshotOutput = path.join(
  outputRoot,
  `${expectedVersion}_UI烟测截图_${dateStamp}.png`
);
const launcherScreenshotOutput = path.join(
  outputRoot,
  `${expectedVersion}_一键启动器烟测截图_${dateStamp}.png`
);
const taskCenterScreenshotOutput = path.join(
  outputRoot,
  `${expectedVersion}_任务中心烟测截图_${dateStamp}.png`
);
const reportOutput = path.join(
  outputRoot,
  `${expectedVersion}_便携包首次启动报告_${dateStamp}.json`
);
await fsp.copyFile(screenshotPath, screenshotOutput);
await fsp.copyFile(launcherScreenshotPath, launcherScreenshotOutput);
await fsp.copyFile(
  String(smokeReport.taskCenterScreenshotPath),
  taskCenterScreenshotOutput
);
await fsp.writeFile(
  reportOutput,
  JSON.stringify(
    {
      ok: true,
      verifiedAt: new Date().toISOString(),
      version: expectedVersion,
      zip: zipPath,
      zipSha256: portableSha256,
      extractionRoot,
      executable,
      engineExecutable,
      dataDirectory,
      exitCode: exitResult.code,
      smokeReport,
      taskCenterScreenshot: taskCenterScreenshotOutput,
      releaseChecks: {
        versionMetadata: true,
        bundledEngineVersion: true,
        sqliteSchemaVersion: 6,
        developmentNodeModulesExcluded: true,
        developmentArtifactsExcluded: true,
        checksumSidecarValidated: true,
        deploymentDocumentsValidated: true,
        dependencyLockValidated: true
      },
      missingRuntimeGuard: {
        validated: true,
        observedExitCode: missingRuntimeExitCode,
        residualProcesses: 0,
        error: missingRuntimeSmoke.error
      },
      residualProcesses: 0,
      launcher: {
        launcherPath,
        dataDirectory: launcherDataDirectory,
        screenshot: launcherScreenshotOutput,
        report: launcherReport,
        residualProcesses: 0
      },
      forcedExitRecovery: {
        dataDirectory: recoveryDataDirectory,
        taskPersisted: true,
        sqliteIntegrity: "ok",
        delayedBackendExit: true,
        restartSmokeOk: true,
        residualProcesses: 0
      }
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
      extractionRoot,
      screenshot: screenshotOutput,
      launcherScreenshot: launcherScreenshotOutput,
      report: reportOutput
    },
    null,
    2
  )
);
