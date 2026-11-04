import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = path.resolve(import.meta.dirname, "..");
const reports = path.join(root, "outputs", "0.2.1阶段A红基线");
fs.mkdirSync(reports, { recursive: true });

function run(command, args, cwd, baseline = true) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    env: {
      ...process.env,
      PKULAW_PHASE_A_BASELINE: baseline ? "1" : "0"
    }
  });
  return {
    command: [command, ...args].join(" "),
    status: result.status ?? 1,
    stdout: result.stdout || "",
    stderr: result.stderr || ""
  };
}

const python = process.env.PKULAW_PYTHON || "D:\\Python\\python.exe";
const pnpmRuntime = process.env.npm_execpath;
const pnpmCommand =
  pnpmRuntime && /\.(?:c?js|mjs)$/i.test(pnpmRuntime)
    ? process.execPath
    : process.env.PNPM_EXECUTABLE || "pnpm.cmd";
const pnpmPrefix =
  pnpmRuntime && /\.(?:c?js|mjs)$/i.test(pnpmRuntime)
    ? [pnpmRuntime]
    : [];
const steps = [
  {
    name: "python-known-gaps",
    command: python,
    args: [
      "-X",
      "utf8",
      "-m",
      "unittest",
      "tests.test_phase_a_contracts",
      "-v"
    ],
    cwd: path.join(root, "engine"),
    baseline: true
  },
  {
    name: "node-known-gaps",
    command: pnpmCommand,
    args: [
      ...pnpmPrefix,
      "--dir",
      "apps/desktop",
      "exec",
      "vitest",
      "run",
      "tests/download-manager.contract.test.ts"
    ],
    cwd: root,
    baseline: true
  }
];

const results = steps.map((step) => ({
  name: step.name,
  ...run(
    step.command,
    step.args,
    step.cwd,
    step.baseline
  )
}));

const pythonCoveragePath = path.join(root, "engine", "coverage-summary.json");
const nodeCoveragePath = path.join(
  root,
  "apps",
  "desktop",
  "coverage",
  "coverage-summary.json"
);
const pythonCoverage = JSON.parse(
  fs.readFileSync(pythonCoveragePath, "utf8")
);
const nodeCoverage = JSON.parse(fs.readFileSync(nodeCoveragePath, "utf8"));
results.push(
  {
    name: "python-coverage-evidence",
    command: `read ${pythonCoveragePath}`,
    status: pythonCoverage.ok === true ? 0 : 1,
    stdout: JSON.stringify(pythonCoverage, null, 2),
    stderr: ""
  },
  {
    name: "node-coverage-evidence",
    command: `read ${nodeCoveragePath}`,
    status:
      nodeCoverage.total?.branches?.pct >= 85 &&
      nodeCoverage.total?.lines?.pct >= 85
        ? 0
        : 1,
    stdout: JSON.stringify(nodeCoverage, null, 2),
    stderr: ""
  }
);

const lines = [
  "# Phase A Known-Gap Baseline",
  "",
  `Generated: ${new Date().toISOString()}`,
  "",
  "This command enables the formerly skipped Phase A contract tests and",
  "verifies they pass together with the normal coverage suites.",
  ""
];
for (const result of results) {
  lines.push(`## ${result.name}`, "", `Command: \`${result.command}\``, "", `Exit code: ${result.status}`, "");
  lines.push("```text", result.stdout.trim(), result.stderr.trim(), "```", "");
}
fs.writeFileSync(
  path.join(reports, "phase-a-known-gap-baseline.txt"),
  lines.join("\n"),
  "utf8"
);

const allPassed = results.every((result) => result.status === 0);
for (const result of results) {
  process.stdout.write(
    `${result.command}\n${result.stdout}${result.stderr}\n`
  );
}
if (!allPassed) {
  throw new Error(
    "At least one Phase A contract or coverage suite failed."
  );
}
console.log("All Phase A contract and coverage suites passed.");
