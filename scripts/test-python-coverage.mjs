import { spawnSync } from "node:child_process";
import fsp from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const root = path.resolve(import.meta.dirname, "..");
const python = process.env.PKULAW_PYTHON || "D:\\Python\\python.exe";
const engine = path.join(root, "engine");
const smokeConfig = path.join(engine, ".coveragerc-smoke");
const keyModules = [
  "pkulaw_engine/sources/pkulaw.py",
  "pkulaw_engine/sources/selector_profile.py",
  "pkulaw_engine/policy.py",
  "pkulaw_engine/single_task.py",
  "pkulaw_engine/batch_task.py",
  "pkulaw_engine/validators/archive.py",
  "pkulaw_engine/validators/catalog.py"
];

function run(command, args, cwd = root, env = {}) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: {
      ...process.env,
      COVERAGE_PROCESS_START: "",
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      ...env
    },
    shell:
      process.platform === "win32" && /\.(?:cmd|bat)$/i.test(command)
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${path.basename(command)} exited with code ${
        result.status ?? "unknown"
      }`
    );
  }
}

function pnpmCommand() {
  const runtime = process.env.npm_execpath;
  if (runtime && /\.(?:c?js|mjs)$/i.test(runtime)) {
    return {
      command: process.execPath,
      prefix: [runtime]
    };
  }
  return {
    command: process.env.PNPM_EXECUTABLE || "pnpm.cmd",
    prefix: []
  };
}

async function clearCoverageData() {
  for (const entry of await fsp.readdir(engine, { withFileTypes: true })) {
    if (
      entry.isFile() &&
      (entry.name === ".coverage" || entry.name.startsWith(".coverage."))
    ) {
      await fsp.unlink(path.join(engine, entry.name));
    }
  }
}

await clearCoverageData();

run(
  python,
  [
    "-m",
    "coverage",
    "run",
    "--data-file=.coverage.unit",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-v"
  ],
  engine
);

if (process.env.PKULAW_COVERAGE_SKIP_BUILD !== "1") {
  const pnpm = pnpmCommand();
  run(
    pnpm.command,
    [...pnpm.prefix, "--dir", "apps/desktop", "build"],
    root
  );
}

const smokeScript = path.join(root, "apps", "desktop", "scripts", "smoke.mjs");
for (const mode of ["stage2", "stage3", "stage4", "stage5", "stage6"]) {
  run(
    process.execPath,
    [smokeScript, "--mode", mode],
    root,
    {
      COVERAGE_PROCESS_START: smokeConfig
    }
  );
}

const smokeDataFiles = (await fsp.readdir(engine, { withFileTypes: true }))
  .filter(
    (entry) =>
      entry.isFile() &&
      entry.name.startsWith(".coverage.smoke.")
  )
  .map((entry) => entry.name);
if (!smokeDataFiles.length) {
  throw new Error("Electron smoke tests produced no Python coverage data");
}

run(
  python,
  [
    "-m",
    "coverage",
    "combine",
    "--data-file=.coverage",
    ".coverage.unit",
    ...smokeDataFiles
  ],
  engine
);

run(
  python,
  [
    "-m",
    "coverage",
    "json",
    "--data-file=.coverage",
    "-o",
    "coverage.json"
  ],
  engine
);

run(
  python,
  [
    "-m",
    "coverage",
    "html",
    "--data-file=.coverage"
  ],
  engine
);

run(
  python,
  [
    "-m",
    "coverage",
    "report",
    "--data-file=.coverage",
    "--fail-under=85",
    `--include=${keyModules.join(",")}`
  ],
  engine
);

const report = JSON.parse(
  await fsp.readFile(path.join(engine, "coverage.json"), "utf8")
);
const normalizedFiles = new Map(
  Object.entries(report.files || {}).map(([file, value]) => [
    file.replaceAll("\\", "/"),
    value
  ])
);
const moduleSummaries = {};
for (const moduleName of keyModules) {
  const summary = normalizedFiles.get(moduleName)?.summary;
  if (!summary) {
    throw new Error(`Coverage report is missing key module: ${moduleName}`);
  }
  const branches = Number(summary.percent_branches_covered || 0);
  moduleSummaries[moduleName] = {
    branches,
    statements: Number(summary.percent_statements_covered || 0),
    combined: Number(summary.percent_covered || 0)
  };
  if (branches < 85) {
    throw new Error(
      `${moduleName} branch coverage ${branches.toFixed(2)}% is below 85%`
    );
  }
}

const summaryPath = path.join(engine, "coverage-summary.json");
await fsp.writeFile(
  summaryPath,
  JSON.stringify(
    {
      ok: true,
      generatedAt: new Date().toISOString(),
      smokeModes: ["stage2", "stage3", "stage4", "stage5", "stage6"],
      total: report.totals,
      keyModules: moduleSummaries
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
      summary: summaryPath,
      html: path.join(engine, "coverage-html", "index.html")
    },
    null,
    2
  )
);
