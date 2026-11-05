# Apple Silicon macOS 移植基础实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不回退 Windows 0.2.4 的前提下，完成 Apple 芯片 macOS 移植所需的平台抽象、构建骨架、DOC 转换分支、测试和 Mac Agent 交接材料。

**Architecture:** 保留一套 Electron/React 与 Python 公共业务代码，将后端文件名、开发解释器、系统回收站名称和强制终止信号收敛到 Electron 平台适配层；将测试、Windows 便携构建和 macOS arm64 构建拆成平台明确的 Node 脚本。PyInstaller spec 根据构建平台选择数据文件，macOS 旧版 DOC 增加系统 `textutil` 后备路径。

**Tech Stack:** Electron 45.2.1、React 19.3.1、TypeScript 7.0.2、Python 3.14、PyInstaller 6.22.3、Electron Builder 26.18.0、SQLite schema v6。

**Spec:** `docs/superpowers/specs/2026-11-05-macos-arm64-port-design.md`

## Global Constraints

- 首批只支持 macOS `arm64`；不支持 Intel Mac 或 Universal2。
- Windows 必须继续使用 `pkulaw-engine.exe` 和 `D:\Python\python.exe` 开发默认值。
- macOS 使用无扩展名后端 `pkulaw-engine`，开发解释器默认为 `python3`。
- 非 macOS 执行 Mac 构建脚本必须立即失败，不得生成伪成品。
- macOS 数据目录由 Electron `app.getPath("userData")` 派生；不能写入 `.app` 内部。
- 不迁移 Windows Cookie、Electron Profile、Session 或含绝对路径的 `数据` 目录。
- Windows 阶段不生成或验证 `.app/.dmg`，不验证 Apple 签名、公证或 Gatekeeper。
- 不改变现有下载格式、保留字段和法宝联想功能范围。
- 所有真实凭据、Cookie、数据库和下载结果不得进入 Git。

## Review Focus

- Windows 打包后仍必须找到 `resources/engine/pkulaw-engine.exe`。
- macOS 包内后端必须命名为 `pkulaw-engine`，不能误用 `.exe`。
- 非 macOS 构建机必须拒绝 `build:mac`，避免发布错误平台文件。
- `textutil` 缺失、超时、非零退出或正文不足时必须失败关闭。
- 新增 `.gitattributes` 后 Windows 和 macOS 的工作树都必须保持干净。

---

### Task 1: 跨平台 Node 运行辅助与测试入口

**Files:**
- Create: `scripts/lib/runtime-platform.mjs`
- Create: `scripts/tests/runtime-platform.test.mjs`
- Modify: `scripts/test-all.mjs`
- Modify: `scripts/test-python.mjs`
- Modify: `scripts/test-python-coverage.mjs`
- Modify: `scripts/phase-a-baseline.mjs`
- Modify: `apps/desktop/scripts/smoke.mjs`

**Interfaces:**
- Produces: `pythonCommand(platform: NodeJS.Platform, env: NodeJS.ProcessEnv): string`
- Produces: `pnpmCommand(platform: NodeJS.Platform, env: NodeJS.ProcessEnv): string`
- Consumes: `PKULAW_PYTHON` and `PNPM_EXECUTABLE` environment overrides.

- [ ] **Step 1: Write the failing Node tests**

Create `scripts/tests/runtime-platform.test.mjs` with tests named:

- `uses the Windows Python default on win32`
- `uses python3 on darwin`
- `honors PKULAW_PYTHON on every platform`
- `uses pnpm.cmd on Windows and pnpm on macOS`

The assertions must use literal values:

```js
assert.equal(pythonCommand("win32", {}), "D:\\Python\\python.exe");
assert.equal(pythonCommand("darwin", {}), "python3");
assert.equal(pythonCommand("darwin", { PKULAW_PYTHON: "/opt/python/bin/python3" }), "/opt/python/bin/python3");
assert.equal(pnpmCommand("win32", {}), "pnpm.cmd");
assert.equal(pnpmCommand("darwin", {}), "pnpm");
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
node --test scripts/tests/runtime-platform.test.mjs
```

Expected: FAIL because `runtime-platform.mjs` does not exist.

- [ ] **Step 3: Implement the runtime helpers**

Create `scripts/lib/runtime-platform.mjs` exporting the two functions above.
Whitespace-only environment values are treated as absent.

- [ ] **Step 4: Run the test and verify GREEN**

Run the same command. Expected: all four tests pass.

- [ ] **Step 5: Replace hard-coded interpreter and pnpm defaults**

Update the five listed scripts to call these helpers. Preserve all existing
environment variables and command arguments.

- [ ] **Step 6: Add the Node helper tests to the default suite**

Modify `scripts/test-all.mjs` so `node --test scripts/tests/*.test.mjs` runs
before Python tests.

- [ ] **Step 7: Verify the script layer**

Run:

```powershell
node --test scripts/tests/runtime-platform.test.mjs
pnpm --dir apps/desktop typecheck
```

Expected: both commands pass.

- [ ] **Step 8: Commit**

```powershell
git add scripts apps/desktop/scripts/smoke.mjs
git commit -m "build: add cross-platform runtime helpers"
```

### Task 2: Electron 桌面平台适配层

**Files:**
- Create: `apps/desktop/src/main/platform.ts`
- Create: `apps/desktop/tests/platform.test.ts`
- Modify: `apps/desktop/src/main/index.ts`
- Modify: `apps/desktop/src/main/python-service.ts`
- Modify: `apps/desktop/src/main/fixture-server.ts`
- Modify: `apps/desktop/src/main/task-file-cleanup.ts`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/renderer/src/TaskCenterPanel.tsx`
- Modify: `apps/desktop/tests/python-service.test.ts`
- Modify: `apps/desktop/tests/task-file-cleanup.test.ts`

**Interfaces:**
- Produces:

```ts
export interface DesktopPlatformAdapter {
  platform: "win32" | "darwin";
  backendExecutableName: "pkulaw-engine.exe" | "pkulaw-engine";
  developmentPythonPath: string;
  trashLabel: string;
  forceKillSignal: NodeJS.Signals;
}

export function resolveDesktopPlatform(
  platform?: NodeJS.Platform,
  env?: NodeJS.ProcessEnv
): DesktopPlatformAdapter;

export function isBundledEngineConfig(
  platform: NodeJS.Platform,
  candidate: string
): boolean;
```

- Produces: `AppInfo.platform: "win32" | "darwin"` and
  `AppInfo.trashLabel: string`.
- Consumes: `PKULAW_PYTHON` override.

- [ ] **Step 1: Write failing platform tests**

Add tests named:

- `maps Windows engine and Python defaults`
- `maps Apple Silicon engine and python3 defaults`
- `uses SIGKILL as the macOS forced-stop signal`
- `rejects unsupported desktop platforms`

Use literal expectations:

```ts
expect(resolveDesktopPlatform("win32", {})).toMatchObject({
  backendExecutableName: "pkulaw-engine.exe",
  developmentPythonPath: "D:\\Python\\python.exe",
  trashLabel: "Windows 回收站",
  forceKillSignal: "SIGTERM"
});

expect(resolveDesktopPlatform("darwin", {})).toMatchObject({
  backendExecutableName: "pkulaw-engine",
  developmentPythonPath: "python3",
  trashLabel: "废纸篓",
  forceKillSignal: "SIGKILL"
});
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
pnpm --dir apps/desktop exec vitest run tests/platform.test.ts
```

Expected: FAIL because `platform.ts` is absent.

- [ ] **Step 3: Implement the adapter**

Support only `win32` and `darwin`. `PKULAW_PYTHON` overrides the development
interpreter. Unknown platforms throw
`UNSUPPORTED_DESKTOP_PLATFORM: <platform>`.

- [ ] **Step 4: Verify GREEN**

Run the same targeted Vitest command. Expected: PASS.

- [ ] **Step 5: Integrate backend resolution**

Replace hard-coded `.exe` names and `D:\Python\python.exe` in
`resolveEngineDirectory`, `stageBundledEngine`, and `resolveBackendLaunch`
with the adapter. Preserve the Windows behavior and environment overrides.
Replace the fixture server's bare `python` fallback with `python3` on macOS
and the existing Windows value on Windows.

- [ ] **Step 6: Integrate forced termination**

Add optional `forceKillSignal: NodeJS.Signals = "SIGTERM"` to
`PythonServiceOptions`. Use it only in the force-stop fallback after graceful
shutdown times out.

- [ ] **Step 7: Integrate platform-facing trash labels**

Add `platform` and `trashLabel` to `AppInfo`. Use `appInfo.trashLabel` in task
cleanup UI text. Make `TrashCleanupError` use a platform-neutral “系统回收站”
message; the UI supplies the exact label.

- [ ] **Step 8: Extend tests for regressions**

Add assertions to `python-service.test.ts` that the configured force signal is
used after graceful shutdown times out. Add a Windows case to
`task-file-cleanup.test.ts` asserting no loss of safety path checks.

- [ ] **Step 9: Verify integration**

Run:

```powershell
pnpm --dir apps/desktop exec vitest run tests/platform.test.ts tests/python-service.test.ts tests/task-file-cleanup.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS.

- [ ] **Step 10: Commit**

```powershell
git add apps/desktop/src/main apps/desktop/src/shared/protocol.ts apps/desktop/src/renderer/src/TaskCenterPanel.tsx apps/desktop/tests
git commit -m "refactor: isolate desktop platform behavior"
```

### Task 3: macOS textutil 旧版 DOC 后备转换

**Files:**
- Modify: `engine/pkulaw_engine/processors/documents.py`
- Modify: `engine/tests/test_processing.py`
- Modify: `engine/pkulaw_engine/diagnostics.py`

**Interfaces:**
- Produces: `_run_textutil(path: Path, executable: str) -> DocumentExtraction`
- Selection order on macOS: bundled antiword if available, system antiword,
  `textutil`, LibreOffice, Word COM.

- [ ] **Step 1: Write failing tests**

Add tests:

- `test_textutil_converts_legacy_doc_and_records_provenance`
- `test_textutil_timeout_fails_closed`
- `test_textutil_empty_output_fails_closed`
- `test_diagnostics_reports_textutil_as_macos_converter`

Mock only `subprocess.run`, `shutil.which`, and platform-specific executable
selection. Assert real `DocumentExtraction` fields:

```python
self.assertEqual(result.artifact_method, "textutil")
self.assertEqual(result.converter_name, "textutil")
self.assertTrue(result.converter_version)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
..\.packaging-venv\Scripts\python.exe -m unittest tests.test_processing.ProcessingTests.test_textutil_converts_legacy_doc_and_records_provenance -v
```

Expected: FAIL because `_run_textutil` is absent.

- [ ] **Step 3: Implement textutil conversion**

Use:

```python
["textutil", "-convert", "txt", "-stdout", str(path)]
```

with UTF-8 decoding, existing timeout limits and existing body validation.
Record converter version as `platform.mac_ver()[0]` or `"system"`.

- [ ] **Step 4: Integrate fallback order and diagnostics**

Add `textutil` to the macOS fallback chain and diagnostics. Do not change the
Windows selection order.

- [ ] **Step 5: Verify GREEN**

Run:

```powershell
..\.packaging-venv\Scripts\python.exe -m unittest tests.test_processing tests.test_diagnostics -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add engine/pkulaw_engine/processors/documents.py engine/pkulaw_engine/diagnostics.py engine/tests/test_processing.py
git commit -m "feat: add macOS textutil DOC fallback"
```

### Task 4: 平台条件 PyInstaller 数据打包

**Files:**
- Create: `engine/pkulaw_engine/build_support.py`
- Create: `engine/tests/test_build_support.py`
- Modify: `engine/pkulaw-engine.spec`

**Interfaces:**
- Produces: `engine_data_entries(engine_root: Path, platform: str) -> list[tuple[str, str]]`
- Windows entries include selector/profile files and all existing
  `vendor/antiword/win-x64` resources.
- Darwin entries include selector/profile files only.

- [ ] **Step 1: Write failing tests**

Add tests:

- `test_windows_pyinstaller_datas_include_antiword`
- `test_macos_pyinstaller_datas_exclude_windows_antiword`
- `test_unknown_pyinstaller_platform_fails_closed`

Hand-check returned `(source, destination)` tuples with literal destination
paths.

- [ ] **Step 2: Verify RED**

Run:

```powershell
..\.packaging-venv\Scripts\python.exe -m unittest tests.test_build_support -v
```

Expected: FAIL because `build_support.py` is absent.

- [ ] **Step 3: Implement build-support entries**

Return only existing source paths. Reject unsupported platforms with
`UNSUPPORTED_PYINSTALLER_PLATFORM: <platform>`.

- [ ] **Step 4: Update the spec**

Import `engine_data_entries` from the engine source tree and initialize
`datas` from it. Keep collect-data-files and hidden-import behavior unchanged.

- [ ] **Step 5: Verify GREEN and run PyInstaller**

Run:

```powershell
..\.packaging-venv\Scripts\python.exe -m unittest tests.test_build_support -v
..\.packaging-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath ..\release\engine-test --workpath ..\build\pyinstaller-test pkulaw-engine.spec
```

Expected: tests pass; Windows executable `pkulaw-engine.exe` is produced.

- [ ] **Step 6: Commit**

```powershell
git add engine/pkulaw_engine/build_support.py engine/tests/test_build_support.py engine/pkulaw-engine.spec
git commit -m "build: select PyInstaller data by platform"
```

### Task 5: Apple 芯片 macOS `.app/.dmg` 构建骨架

**Files:**
- Modify: `package.json`
- Modify: `apps/desktop/package.json`
- Modify: `pnpm-lock.yaml`
- Create: `apps/desktop/electron-builder.mac.yml`
- Create: `scripts/lib/build-host.mjs`
- Create: `scripts/tests/build-host.test.mjs`
- Create: `scripts/build-macos-arm64.mjs`
- Modify: `scripts/build-portable.mjs`
- Modify: `scripts/verify-portable.mjs`

**Interfaces:**
- Produces: `assertMacArm64Host(platform: NodeJS.Platform, arch: string): void`
- Produces: root script `build:mac`
- Windows script remains `build:portable`.
- macOS output:
  `release/macos/PkulawWorkbench-<version>-macOS-arm64.dmg`.

- [ ] **Step 1: Write failing host tests**

Add tests:

- `accepts darwin arm64`
- `rejects win32 arm64`
- `rejects darwin x64`

Expected error prefix for rejection:
`MAC_ARM64_BUILD_HOST_REQUIRED`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
node --test scripts/tests/build-host.test.mjs
```

Expected: FAIL because the helper is absent.

- [ ] **Step 3: Implement host guard**

Create `build-host.mjs` with the signature above. Add it to the default Node
helper test command.

- [ ] **Step 4: Verify GREEN**

Run the same command. Expected: PASS.

- [ ] **Step 5: Add Electron Builder**

Run:

```powershell
pnpm --dir apps/desktop add -D electron-builder@26.18.0
```

Add `build:mac` to root `package.json`.

- [ ] **Step 6: Implement the macOS build script**

The script must:

1. call `assertMacArm64Host(process.platform, process.arch)`;
2. build Electron;
3. build the Python engine with the shared spec;
4. copy `pkulaw-engine` as an executable extra resource;
5. build Apple Silicon `.app` and `.dmg`;
6. calculate SHA-256;
7. publish only to `release/macos`.

On non-macOS it must fail before running any build command.

- [ ] **Step 7: Add the Builder configuration**

Set product name `法宝工作台`, app id `cn.pkulaw.workbench`, target
`dmg:arm64`, extra resource `engine`, and include the deployment documents.
Do not include tests, runtime, fixtures, `node_modules` development trees or
Windows antiword.

- [ ] **Step 8: Guard the Windows builder**

`scripts/build-portable.mjs` must throw
`WINDOWS_PORTABLE_BUILD_REQUIRED` before doing work when
`process.platform !== "win32"`.
`scripts/verify-portable.mjs` must throw the same error before computing a
checksum or extracting a package.

- [ ] **Step 9: Verify Windows-side behavior**

Run:

```powershell
node --test scripts/tests/build-host.test.mjs
pnpm --dir apps/desktop typecheck
pnpm build
```

Expected: passes. Do not claim the `.dmg` was built.

- [ ] **Step 10: Commit**

```powershell
git add package.json apps/desktop/package.json pnpm-lock.yaml apps/desktop/electron-builder.mac.yml scripts
git commit -m "build: add macOS arm64 packaging scaffold"
```

### Task 6: Git 行尾规则、Mac 交接文档和 Agent 提示词

**Files:**
- Create: `.gitattributes`
- Create: `docs/MACOS_ARM64_AGENT_HANDOFF.md`
- Modify: `deployment/复现与构建.md`
- Modify: `deployment/使用手册.md`

**Interfaces:**
- `.gitattributes` keeps source LF, Windows command scripts CRLF, and binary
  assets untouched.
- Handoff document contains a copy-ready prompt and acceptance checklist.

- [ ] **Step 1: Add line-ending rules**

Use:

```gitattributes
* text=auto eol=lf
*.cmd text eol=crlf
*.bat text eol=crlf
*.ps1 text eol=crlf
*.png binary
*.jpg binary
*.ico binary
*.zip binary
*.gz binary
*.zst binary
```

- [ ] **Step 2: Renormalize tracked text files**

Run:

```powershell
git add --renormalize .
git status --short
```

Expected: text line endings normalize; Windows `.cmd/.bat/.ps1` retain CRLF.

- [ ] **Step 3: Verify Windows compatibility**

Run:

```powershell
pnpm test
pnpm build
```

Expected: PASS after renormalization.

- [ ] **Step 4: Write the Mac handoff document**

Include:

- clone and branch commands;
- arm64 host verification;
- dependency setup;
- build and verification commands;
- signing and notarization gates;
- no-data-migration rule;
- exact acceptance outputs and failure conditions;
- a copy-ready prompt for the Mac Agent.

- [ ] **Step 5: Update deployment build guidance**

Document the separate Windows and macOS commands and state that Windows
cannot produce the Mac artifact.

- [ ] **Step 6: Commit**

```powershell
git add .gitattributes docs/MACOS_ARM64_AGENT_HANDOFF.md deployment
git commit -m "docs: add macOS arm64 agent handoff"
```

### Task 7: 全量 Windows 回归、提交汇总和 GitHub 推送

**Files:**
- Modify: `docs/MACOS_ARM64_AGENT_HANDOFF.md` only if verification changes a command or limit.

**Interfaces:**
- Consumes all previous tasks.
- Produces a remote `feature/macos-arm64-port` branch and downloadable archive.

- [ ] **Step 1: Run all automated tests**

```powershell
pnpm test
```

Expected: all Python, Node helper, Vitest and typecheck tests pass.

- [ ] **Step 2: Run production build**

```powershell
pnpm build
```

Expected: Electron main, preload and renderer build successfully.

- [ ] **Step 3: Run Stage 6 smoke**

```powershell
pnpm --dir apps/desktop smoke:stage6
```

Expected: PASS.

- [ ] **Step 4: Confirm Mac build refusal on Windows**

```powershell
pnpm build:mac
```

Expected: fails immediately with `MAC_ARM64_BUILD_HOST_REQUIRED`; no `.app` or
`.dmg` file is produced.

- [ ] **Step 5: Inspect the final diff**

```powershell
git status --short
git diff --check
git log --oneline --decorate -10
```

Expected: clean worktree and no whitespace errors.

- [ ] **Step 6: Push the feature branch**

```powershell
git push -u origin feature/macos-arm64-port
```

Expected: remote branch exists.

- [ ] **Step 7: Record the final branch archive URL**

Provide:

```text
https://github.com/gaoyu-hi/pkulaw-workbench/archive/refs/heads/feature/macos-arm64-port.zip
```

Verify it returns HTTP 200 before reporting completion.
