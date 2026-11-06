# Post-Task Reset and Artifact-Aware Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reset completed collectors to a blank create form and replace immediate settings reapplication with a pause, preview, preserve-or-permanently-delete, then restart workflow for both task modes.

**Architecture:** Add a two-step backend revision API with an opaque preview token and task-scoped artifact inventory, then connect a shared renderer decision dialog to both collectors. Completion reset remains renderer-local but uses a pure helper so each completed task resets exactly once.

**Tech Stack:** Python 3.14, `unittest`, SQLite, Electron 45, React 19, TypeScript 7, Vitest 5.

**Spec:** [2026-11-06-post-task-reset-and-artifact-aware-settings-design.md](../specs/2026-11-06-post-task-reset-and-artifact-aware-settings-design.md)

## Global Constraints

- Applies equally to single and batch collection.
- Only `completed` causes automatic form reset; paused, waiting, failed, partial, and stopped do not.
- Reset uses existing per-mode defaults and the application-provided default output directory.
- A completed task remains in task history.
- Editing settings requires the user to pause first and then manually open task settings.
- Pausing alone never opens task settings or the artifact decision dialog.
- Saving settings creates a preview and does not apply or restart automatically.
- Artifact policy is exactly `preserve` or `delete`.
- `delete` is permanent and immediately frees space; no recycle bin or quarantine.
- Delete only current-task registered artifacts; never a shared file, output root, unregistered file, database, or log.
- If permanent deletion is incomplete, do not change settings and do not restart.
- Both outcomes preserve the same task ID.
- Do not add CAPTCHA bypasses or alter existing search semantics.

## Review Focus

- A completed-task event repeated by polling must not clear a new draft twice; test in both panels.
- A shared or unregistered output file must block permanent deletion without deleting other files; test at storage and application boundaries.
- A stale preview token after task changes must not apply settings; test in the application layer.
- A delete failure halfway through must leave the task paused with the new configuration unapplied; test with an injected unlink failure.
- Preserve mode must clear scan progress but retain completed artifacts and skip repeat downloads; test single and batch resume metadata.

---

## File Structure

- Create `engine/pkulaw_engine/settings_revision.py`: preview/token types and validation helpers.
- Modify `engine/pkulaw_engine/storage.py`: task-scoped artifact inventory, permanent deletion, and result-table cleanup.
- Modify `engine/pkulaw_engine/application.py`: prepare/commit settings revision API.
- Modify `apps/desktop/src/shared/protocol.ts`, `preload/index.ts`, and `main/ipc.ts`: typed revision APIs.
- Create `apps/desktop/src/renderer/src/collector-reset.ts`: pure completion transition and default reset helpers.
- Create `apps/desktop/src/renderer/src/TaskSettingsRevisionDialog.tsx`: artifact choice and final destructive confirmation.
- Modify `SingleCollectorPanel.tsx`, `BatchCollectorPanel.tsx`, and `TaskSettingsPanel.tsx`: pause first, prepare on save, and completion reset.
- Test: `engine/tests/test_settings_revision.py`
- Test: `engine/tests/test_settings_artifact_deletion.py`
- Test: `engine/tests/test_task_settings_revision.py`
- Test: `apps/desktop/tests/collector-reset.test.ts`
- Test: `apps/desktop/tests/task-settings-revision-dialog.test.ts`

## Task 1: Add Pure Settings Revision Contracts

**Files:**
- Create: `engine/pkulaw_engine/settings_revision.py`
- Test: `engine/tests/test_settings_revision.py`

**Interfaces:**
- Consumes: task ID, task updated time, config generation, normalized input, output directory, and artifact summary.
- Produces:
  - `ArtifactPolicy = Literal["preserve", "delete"]`
  - `ArtifactSummary(completed_policy_count, completed_batch_count, registered_file_count, eligible_file_count, protected_file_count, directory_count, estimated_freed_bytes)`
  - `SettingsRevisionPreview(token, changed, generation, artifact_summary, protected_files)`
  - `build_settings_revision_token(task_id, base_generation, base_updated_at, input, output_dir) -> str`
  - `validate_artifact_policy(value) -> ArtifactPolicy`

- [ ] **Step 1: Write failing contract tests**

Assert deterministic token generation, exact policy validation, JSON serialization, integer normalization, and rejection of `None`, `trash`, or unknown policy strings.

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_settings_revision.py" -v
Pop-Location
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the contracts**

Use a SHA-256 token over the canonical JSON of task ID, base generation, base update time, normalized input, and output directory. Use plain dataclasses compatible with the project's existing serialization style.

- [ ] **Step 4: Run focused tests**

Run the command from Step 2.

Expected: all contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/settings_revision.py engine/tests/test_settings_revision.py
git commit -m "feat: add task settings revision contracts"
```

## Task 2: Build Task-Scoped Artifact Preview and Permanent Delete

**Files:**
- Modify: `engine/pkulaw_engine/storage.py`
- Test: `engine/tests/test_settings_artifact_deletion.py`

**Interfaces:**
- Consumes: a paused task ID.
- Produces:
  - `settings_artifact_preview(task_id: str) -> dict[str, Any]` with
    `artifactSummary` matching the Task 1 `ArtifactSummary.to_dict()` shape.
  - `permanently_delete_task_outputs(task_id: str) -> dict[str, Any]`
  - `clear_task_results(task_id: str) -> dict[str, int]`

`settings_artifact_preview` returns registered, eligible, and protected files plus counts and byte totals. `permanently_delete_task_outputs` hard-deletes only eligible files, prunes task-owned empty directories, and returns `deletedFiles`, `deletedDirectories`, `freedBytes`, `failedFiles`, and `protectedFiles`. `clear_task_results` removes items, batches, processing runs, documents, artifacts, and the task-specific scan-journal cache file while keeping the task and its config history.

- [ ] **Step 1: Write failing storage tests**

Create tasks with a downloaded file, shared file, unregistered sibling, batch artifact, processing run, and nested task directory. Assert:

```python
preview["artifactSummary"]["estimatedFreedBytes"] == expected_bytes
deleted = store.permanently_delete_task_outputs(task_id)
assert deleted["protectedFiles"] == []
assert not owned_file.exists()
assert shared_file.exists()
assert unregistered_file.exists()
assert store.get_task(task_id) is not None
assert store.list_task_items(task_id) == []
```

Add an injected unlink failure and a paused-task acceptance case.

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_settings_artifact_deletion.py" -v
Pop-Location
```

Expected: FAIL because the methods do not exist.

- [ ] **Step 3: Implement inventory, permanent delete, and result cleanup**

Reuse the ownership/path-safety rules from `cleanup_preview`, but allow a `paused` task and do not route files through the recycle bin. Protected or shared existing files are reported and block commit; failed unlinks are returned without claiming success. Prune a directory only if it is below the task output root, not equal to the root, and has no remaining files.

- [ ] **Step 4: Run focused storage tests**

Run the command from Step 2.

Expected: all artifact deletion tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/storage.py engine/tests/test_settings_artifact_deletion.py
git commit -m "feat: add permanent task artifact disposal"
```

## Task 3: Add Prepare and Commit Settings Revision APIs

**Files:**
- Modify: `engine/pkulaw_engine/application.py`
- Modify: `engine/pkulaw_engine/task_settings_revision.py`
- Test: `engine/tests/test_task_settings_revision.py`

**Interfaces:**
- Consumes: `settings_artifact_preview`, `permanently_delete_task_outputs`, `clear_task_results`, `build_task_revision`, and `SettingsRevisionPreview`.
- Produces:
  - `prepareTaskSettingsRevision(taskId, input, outputDir) -> SettingsRevisionPreview`
  - `commitTaskSettingsRevision(taskId, token, artifactPolicy, operationId) -> TaskSettingsUpdateResult`
  - checkpoint key `pendingSettingsRevision`

Prepare accepts only `paused`, computes the changed revision without writing it, stores the pending revision under a token, and returns the artifact summary. Commit validates token, generation, update time, paused status, and protected files; applies the chosen policy; updates task input; and starts the same task.

- [ ] **Step 1: Write failing application tests**

Tests:

```text
test_prepare_requires_paused_task
test_prepare_does_not_change_input_or_start_task
test_commit_preserve_clears_scan_but_keeps_completed_artifacts
test_commit_delete_removes_results_then_starts_same_task
test_commit_rejects_stale_preview_token
test_delete_failure_keeps_old_settings_and_paused_status
test_shared_result_blocks_permanent_delete
```

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_task_settings_revision.py" -v
Pop-Location
```

Expected: FAIL because prepare/commit methods and dispatch methods are absent.

- [ ] **Step 3: Implement prepare and commit**

Prepare persists the normalized pending input and artifact summary without mutating active task input. Commit performs policy-specific cleanup before `apply_task_settings_revision`, retries no file operation, and starts only after a fully successful commit. Preserve invokes `build_task_revision` with current completed artifacts retained. Delete clears result tables/files first and then builds a revision with no completed artifacts.

- [ ] **Step 4: Run focused tests and existing settings tests**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_settings_revision.py" -v
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_settings_artifact_deletion.py" -v
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_task_settings_revision.py" -v
Pop-Location
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/application.py engine/pkulaw_engine/task_settings_revision.py engine/tests/test_task_settings_revision.py
git commit -m "feat: add artifact-aware settings revision"
```

## Task 4: Reset Collector Forms After Normal Completion

**Files:**
- Create: `apps/desktop/src/renderer/src/collector-reset.ts`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Test: `apps/desktop/tests/collector-reset.test.ts`

**Interfaces:**
- Consumes: previous task status, current task status, and each collector's existing default values.
- Produces:
  - `shouldResetCollector(previousStatus, currentStatus) -> boolean`
  - `resetSingleCollectorForm(defaultOutputDir, setters) -> void`
  - `resetBatchCollectorForm(defaultOutputDir, setters) -> void`

`shouldResetCollector` returns true only when the same selected task transitions to `completed`. The reset helpers clear query text and restore each form's defaults. Each panel tracks the last reset task ID so repeated polling events do not clear a new draft.

- [ ] **Step 1: Write failing reset tests**

Assert:

```ts
expect(shouldResetCollector("running", "completed")).toBe(true);
expect(shouldResetCollector("paused", "completed")).toBe(true);
expect(shouldResetCollector("completed", "completed")).toBe(false);
expect(shouldResetCollector("running", "failed")).toBe(false);
```

Render or call the reset helper to assert empty queries, all-years, default page size, and default output directory for both modes.

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
pnpm --dir apps/desktop test -- collector-reset.test.ts
```

Expected: FAIL because the reset helper does not exist.

- [ ] **Step 3: Implement the pure helper and panel effects**

Track status transitions in a ref keyed by task ID. On the first normal-completion transition, clear `details`, `selectedTaskId`, and settings state, restore defaults, keep task history, and set a completion notice. Do not reset when a completed task is selected manually after that transition.

- [ ] **Step 4: Run focused tests and typecheck**

```powershell
pnpm --dir apps/desktop test -- collector-reset.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: tests and typecheck pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/desktop/src/renderer/src/collector-reset.ts apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/tests/collector-reset.test.ts
git commit -m "fix: reset collector forms after completion"
```

## Task 5: Add the Pause, Save, and Artifact Decision UI

**Files:**
- Create: `apps/desktop/src/renderer/src/TaskSettingsRevisionDialog.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskSettingsPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/preload/index.ts`
- Modify: `apps/desktop/src/main/ipc.ts`
- Test: `apps/desktop/tests/task-settings-revision-dialog.test.ts`
- Test: `apps/desktop/tests/task-settings-panel.test.ts`

**Interfaces:**
- Consumes: `prepareTaskSettingsRevision` and `commitTaskSettingsRevision`.
- Produces renderer methods:
  - `prepareTaskSettingsRevision(taskId, input, operationId): Promise<TaskSettingsRevisionPreview>`
  - `commitTaskSettingsRevision(taskId, token, artifactPolicy, operationId): Promise<TaskSettingsUpdateResult>`

Task settings open only when the task is already `paused`. A normal pause action leaves the task view unchanged and does not open the form. Saving invokes prepare and opens the dialog. Preserve commits immediately. Permanent delete requires a second red confirmation displaying file count, directory count, and formatted freed size.

- [ ] **Step 1: Write failing UI tests**

Assert:

```text
pause action does not open task settings
running task settings click only requests manual pause
paused task settings click opens the form
save button invokes prepare, not apply
dialog shows both policy buttons and artifact counts
delete shows final confirmation before commit
cancel preserves paused task and draft
preserve sends artifactPolicy="preserve"
confirmed delete sends artifactPolicy="delete"
```

Add an error test ensuring `TASK_OUTPUT_DELETE_BLOCKED` lists protected files and does not close the dialog.

- [ ] **Step 2: Run focused UI tests and confirm failure**

```powershell
pnpm --dir apps/desktop test -- task-settings-revision-dialog.test.ts task-settings-panel.test.ts
```

Expected: FAIL because the dialog and APIs are absent.

- [ ] **Step 3: Implement typed IPC and the shared decision dialog**

Keep TaskSettingsPanel responsible for draft editing and validation. Move apply/commit sequencing to the parent collector panels. Gate task-settings opening on `paused`; clicking the settings button while active only informs the user to pause first. The dialog owns only decision state and confirmation; it does not manipulate task files directly.

- [ ] **Step 4: Run focused tests and typecheck**

```powershell
pnpm --dir apps/desktop test -- task-settings-revision-dialog.test.ts task-settings-panel.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: UI tests and typecheck pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/desktop/src/renderer/src/TaskSettingsRevisionDialog.tsx apps/desktop/src/renderer/src/TaskSettingsPanel.tsx apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/shared/protocol.ts apps/desktop/src/preload/index.ts apps/desktop/src/main/ipc.ts apps/desktop/tests/task-settings-revision-dialog.test.ts apps/desktop/tests/task-settings-panel.test.ts
git commit -m "feat: add artifact-aware task settings flow"
```

## Task 6: Verify Both Collectors End to End and Update Documentation

**Files:**
- Create: `apps/desktop/tests/collector-lifecycle-integration.test.ts`
- Modify: `deployment/使用手册.md`
- Modify: `deployment/故障排查.md`
- Modify: `docs/PROJECT_STATE.md`

- [ ] **Step 1: Write failing cross-mode integration tests**

Cover:

```text
single completed -> blank create form; task remains selectable
batch completed -> blank create form; task remains selectable
single paused -> revise preserve -> same task restarted
single paused -> revise delete -> files removed and same task restarted
batch paused -> revise preserve -> same task restarted
batch paused -> revise delete -> files removed and same task restarted
```

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
pnpm --dir apps/desktop test -- collector-lifecycle-integration.test.ts
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_task_settings_revision.py" -v
Pop-Location
```

Expected: FAIL until the complete single/batch integration is wired.

- [ ] **Step 3: Fix only integration gaps**

Do not add a second deletion implementation or another settings schema. If a cross-mode difference requires separate semantics, report it instead of hiding it behind a shared helper.

- [ ] **Step 4: Update the manual and troubleshooting guide**

Document the two artifact policies, permanent deletion, shared-file protection, completion reset, and expected default values for both collectors.

- [ ] **Step 5: Run full verification**

```powershell
pnpm test
pnpm build
```

Expected: all Python and Vitest tests pass, typecheck passes, and production build succeeds.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/tests/collector-lifecycle-integration.test.ts deployment/使用手册.md deployment/故障排查.md docs/PROJECT_STATE.md
git commit -m "test: verify collector reset and settings lifecycle"
```
