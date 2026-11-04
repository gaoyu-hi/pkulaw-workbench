# Pkulaw Usability and Search Semantics Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct Pkulaw search-field semantics and remove the confirmed form,
pause, year-filter, and captcha-recovery friction while preserving the fixed
one-screen workspace.

**Architecture:** Introduce one explicit search-mode contract shared by preview,
single, and batch collectors; then treat collector forms as fixed-height panels
with a compact body and persistent action footer. Pause/error presentation is
reduced to one valid action plus an expandable detail surface, and captcha recovery
follows the original Py tools' reliable “keep the worker waiting” model with a
longer, multi-sample clear check.

**Tech Stack:** Python 3.14 engine, SQLite, Electron 45, React 19, TypeScript,
Vitest, unittest, Electron fixture smoke, PyInstaller portable build.

**Spec:** `docs/superpowers/specs/2026-11-03-pkulaw-usability-and-search-semantics-remediation-design.md`

## Global Constraints

- Single-task modes are exactly: 关键词=`Title + Exact`,
  全文=`FullText + Exact`, 精确完整标题=`Title + Exact` with candidate disambiguation.
- Batch modes are exactly: 关键词=`Title + Exact`, 全文=`FullText + Exact`.
- Never silently use FullText for 关键词 and never accept Fuzzy fallback.
- New tasks carry `searchSemanticsVersion=2`; legacy keyword tasks must not resume
  silently under the new Title meaning.
- New task query textareas start empty; no “人工智能” or sample default.
- Preserve user-entered drafts; only migrate known product defaults.
- 1536×864 logical viewport must not create outer application scrolling.
- Primary start/apply actions must remain visible without outer scrolling.
- Year mode and province are separate full-width rows.
- Only one continuation-style primary action may be enabled at a time.
- Never solve, recognize, bypass, or auto-submit captcha/login/quota controls.
- Preserve completed files and trusted scan checkpoints during recovery.

## Review Focus

- A keyword search visibly remains Title+Exact while a full-text search visibly
  becomes FullText+Exact; Pkulaw fuzzy fallback must never download.
- Two same-title policies in keyword mode remain separate by stable policy ID,
  while exact-full-title mode still requests a candidate decision.
- A requested range starting at 1949 begins at the first available year instead
  of pausing when older years do not exist.
- At short supported viewport heights, start/apply actions remain visible with no
  outer scroll and no hidden pause explanation.
- A transient captcha overlay after manual completion does not stop the worker or
  create multiple competing continue buttons.

---

### Task 1: Correct the engine search-mode contract

**Files:**
- Create: `engine/pkulaw_engine/sources/search_mode.py`
- Modify: `engine/pkulaw_engine/sources/search_transaction.py`
- Modify: `engine/pkulaw_engine/sources/pkulaw.py`
- Modify: `engine/pkulaw_engine/single_task.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `engine/pkulaw_engine/collector_preview.py`
- Modify: `engine/pkulaw_engine/application.py`
- Modify: `engine/pkulaw_engine/task_settings_revision.py`
- Create: `engine/tests/test_search_mode_contract.py`
- Test: `engine/tests/test_pkulaw_adapter_operations.py`
- Test: `engine/tests/test_collector_preview.py`
- Test: `engine/tests/test_scan_resume.py`
- Test: `engine/tests/test_batch_scan_resume.py`
- Test: `engine/tests/test_task_settings_revision.py`

**Interfaces:**
- Produces: `SearchModeSpec(search_keyword_type: str, match_type: str, identity_mode: str)`
- Produces: `resolve_search_mode(match_mode: str) -> SearchModeSpec`
- Produces: `is_exact_title_mode(match_mode: str) -> bool`
- Produces: `SEARCH_SEMANTICS_VERSION = 2`
- Consumes: existing match values `keyword`, `title`; adds `fulltext`.

- [ ] **Step 1: Write failing search-mode contract tests**

```python
def test_search_modes_map_to_exact_pkulaw_fields():
    assert resolve_search_mode("keyword").search_keyword_type == "Title"
    assert resolve_search_mode("fulltext").search_keyword_type == "FullText"
    assert resolve_search_mode("title").search_keyword_type == "Title"
    assert resolve_search_mode("keyword").match_type == "Exact"
    assert resolve_search_mode("fulltext").match_type == "Exact"
    assert resolve_search_mode("title").match_type == "Exact"

def test_unknown_mode_fails_closed():
    with pytest.raises(ValidationError) as error:
        resolve_search_mode("fuzzy")
    assert error.value.code == "INVALID_MATCH_MODE"
```

- [ ] **Step 2: Run the new test and verify failure**

Run:

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_search_mode_contract -v
```

Expected: FAIL because `search_mode` does not exist.

- [ ] **Step 3: Implement `resolve_search_mode` and migrate identity checks**

Implement `search_mode.py`; in `search_transaction.py`, derive
`expected_search_types` from the resolved field only. Replace every
`match_mode == "keyword"` identity branch with `identity_mode == "keyword"` so
`fulltext` uses stable-ID grouping while `title` uses candidate grouping.

- [ ] **Step 4: Write failing adapter mode tests**

Assert the submitted form contains:

```python
assert form["SearchKeywordType"] == "Title"
assert form["MatchType"] == "Exact"
```

for keyword; the analogous `FullText` value for fulltext; and that a page showing
Fuzzy is rejected with no detail open or download intent.

- [ ] **Step 5: Run adapter tests and update implementation**

Run:

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_pkulaw_adapter_operations -v
```

Expected after implementation: PASS.

- [ ] **Step 6: Verify single, batch, preview, and resume contracts**

Run:

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_search_mode_contract tests.test_collector_preview tests.test_scan_resume tests.test_batch_scan_resume -v
```

Expected: PASS; batch accepts keyword/fulltext and rejects exact-title mode.

- [ ] **Step 7: Add legacy-task safety tests and implementation**

Create a v1 task input with `matchMode=keyword` and no semantics version. Assert:

```python
assert error.code == "SEARCH_SEMANTICS_UPGRADE_REQUIRED"
```

before `single.start`, `single.retry`, or `batch.start` can reuse its scan journal.
Applying Task Settings with a valid v2 mode succeeds under the same task ID,
increments `configGeneration`, and clears old scan-derived checkpoint state while
preserving completed files.

- [ ] **Step 8: Run legacy and revision tests**

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_search_mode_contract tests.test_task_settings_revision tests.test_storage -v
Set-Location ..
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add engine/pkulaw_engine/sources/search_mode.py engine/pkulaw_engine/sources/search_transaction.py engine/pkulaw_engine/sources/pkulaw.py engine/pkulaw_engine/single_task.py engine/pkulaw_engine/batch_task.py engine/pkulaw_engine/collector_preview.py engine/pkulaw_engine/application.py engine/pkulaw_engine/task_settings_revision.py engine/tests/test_search_mode_contract.py engine/tests/test_pkulaw_adapter_operations.py engine/tests/test_collector_preview.py engine/tests/test_scan_resume.py engine/tests/test_batch_scan_resume.py engine/tests/test_task_settings_revision.py
git commit -m "fix: enforce exact pkulaw search mode semantics"
```

---

### Task 2: Add three UI modes and empty fresh-query defaults

**Files:**
- Modify: `apps/desktop/src/shared/protocol.ts`
- Create: `apps/desktop/src/renderer/src/collector-search-mode.ts`
- Modify: `apps/desktop/src/renderer/src/collector-workflow.ts`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskSettingsPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Modify: `apps/desktop/src/renderer/src/DataPanel.tsx`
- Test: `apps/desktop/tests/collector-panels.test.ts`
- Test: `apps/desktop/tests/task-settings-panel.test.ts`
- Test: `apps/desktop/tests/task-runtime.test.ts`

**Interfaces:**
- Produces: `CollectorMatchMode = "keyword" | "fulltext" | "title"`
- Produces: `collectorSearchModeOptions(mode: TaskMode): SearchModeOption[]`
- Produces: `migrateCollectorQueryDraft(stored: unknown): string`
- `SearchModeOption` is `{ value, label, detail }`.

- [ ] **Step 1: Write failing UI tests**

Assert:

- single options are exactly 关键词 / 全文 / 精确完整标题；
- batch options are exactly 关键词 / 全文；
- fresh single and batch textareas are empty;
- fresh textareas have no non-empty `placeholder`;
- stored “人工智能” product default migrates to empty;
- an intentional stored `碳中和` draft remains;
- Task Settings exposes the same mode set as its collector;
- runtime labels show Title for keyword, FullText for fulltext, and exact-title
  candidate mode for title.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/collector-panels.test.ts tests/task-settings-panel.test.ts tests/task-runtime.test.ts
```

Expected: FAIL on missing fulltext option and preset query text.

- [ ] **Step 3: Implement the shared UI mode contract**

Add the type and option helper. Change default query values to `""`; migrate only
the exact legacy default `"人工智能"` to empty. Preserve other stored values.

- [ ] **Step 4: Update all mode labels and candidate behavior**

Use the three-mode contract in Single and Task Settings, two modes in Batch.
Exact-full-title remains single-only and retains candidate selection.

- [ ] **Step 5: Run tests and typecheck**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/collector-panels.test.ts tests/task-settings-panel.test.ts tests/task-runtime.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/src/shared/protocol.ts apps/desktop/src/renderer/src/collector-search-mode.ts apps/desktop/src/renderer/src/collector-workflow.ts apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskSettingsPanel.tsx apps/desktop/src/renderer/src/TaskRuntimePanel.tsx apps/desktop/src/renderer/src/DataPanel.tsx apps/desktop/tests/collector-panels.test.ts apps/desktop/tests/task-settings-panel.test.ts apps/desktop/tests/task-runtime.test.ts
git commit -m "feat: add full text and title collector modes"
```

---

### Task 3: Compact creation forms and pin primary actions

**Files:**
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskSettingsPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Test: `apps/desktop/tests/collector-panels.test.ts`
- Test: `apps/desktop/tests/task-settings-panel.test.ts`
- Test: `apps/desktop/tests/task-workspace-frame.test.ts`
- Test: `apps/desktop/src/main/index.ts` Stage 6 smoke

**Interfaces:**
- Produces CSS/test hooks: `collector-form-shell`, `collector-form-body`,
  `collector-action-footer`, `collector-year-row`, `collector-province-row`.

- [ ] **Step 1: Write failing markup/layout tests**

Assert Single, Batch, and Task Settings contain:

```text
collector-form-shell
collector-form-body
collector-action-footer
collector-year-row
collector-province-row
```

Assert the action footer follows the body in DOM and the start/apply button is
inside it. Assert the resolved “创建采集任务” header contains at most one short
guidance line.

- [ ] **Step 2: Run tests and verify failure**

```powershell
pnpm --dir apps/desktop vitest run tests/collector-panels.test.ts tests/task-settings-panel.test.ts tests/task-workspace-frame.test.ts
```

Expected: FAIL on missing layout hooks/order.

- [ ] **Step 3: Refactor creation markup**

Remove duplicate eyebrow/header prose and the long identity explanation. Keep one
safety/behavior hint. Put year mode on its own row and province on the next row.
Reduce query textarea height without preventing multiline editing.

- [ ] **Step 4: Implement fixed-height form CSS and action footer**

Use a grid with `grid-template-rows:auto minmax(0,1fr) auto`. The footer remains
visible; no new outer scrolling. Compact controls at the 1536×864 baseline.

- [ ] **Step 5: Extend Stage 6 smoke**

At 1536×864 assert:

- `scrollHeight <= innerHeight + 1`;
- start/apply button rectangle is inside the viewport;
- year mode and province have separate row tops;
- action feedback/button remains visible in settings mode.

- [ ] **Step 6: Run verification**

```powershell
pnpm --dir apps/desktop vitest run tests/collector-panels.test.ts tests/task-settings-panel.test.ts tests/task-workspace-frame.test.ts
pnpm --dir apps/desktop typecheck
pnpm --dir apps/desktop smoke:stage6
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskSettingsPanel.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/collector-panels.test.ts apps/desktop/tests/task-settings-panel.test.ts apps/desktop/tests/task-workspace-frame.test.ts apps/desktop/src/main/index.ts
git commit -m "fix: keep collector actions visible in one screen"
```

---

### Task 4: Equalize the three Overview entry buttons

**Files:**
- Modify: `apps/desktop/src/renderer/src/InstitutionWorkspace.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Test: `apps/desktop/tests/overview-workspace.test.ts`
- Test: Stage 6 smoke in `apps/desktop/src/main/index.ts`

**Interfaces:**
- Produces class hook: `overview-quick-action`.
- All three buttons use `primary-button overview-quick-action`.

- [ ] **Step 1: Write failing button-parity test**

Assert all three buttons carry `overview-quick-action` and the same base button
class `primary-button`; no action is wrapped in a separate large-button block.

- [ ] **Step 2: Run and verify failure**

```powershell
pnpm --dir apps/desktop vitest run tests/overview-workspace.test.ts
```

Expected: FAIL because the first action is visually distinct.

- [ ] **Step 3: Implement equal visual treatment**

Give all three the same width, height, padding, typography, and visual weight.
Use a three-column wide layout and a stacked layout below the breakpoint.

- [ ] **Step 4: Add smoke measurement**

Assert all three button heights differ by no more than 1px and their widths differ
by no more than 2px at 1536×864.

- [ ] **Step 5: Verify and commit**

```powershell
pnpm --dir apps/desktop vitest run tests/overview-workspace.test.ts
pnpm --dir apps/desktop typecheck
pnpm --dir apps/desktop smoke:stage6
git add apps/desktop/src/renderer/src/InstitutionWorkspace.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/overview-workspace.test.ts apps/desktop/src/main/index.ts
git commit -m "fix: equalize overview entry actions"
```

---

### Task 5: Normalize exact-title searches to the available year window

**Files:**
- Modify: `engine/pkulaw_engine/selections/years.py`
- Modify: `engine/pkulaw_engine/single_task.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `engine/pkulaw_engine/collector_preview.py`
- Modify: `apps/desktop/src/renderer/src/task-presentation.ts`
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Test: `engine/tests/test_years.py`
- Test: `engine/tests/test_scan_resume.py`
- Test: `engine/tests/test_batch_scan_resume.py`
- Test: `engine/tests/test_collector_preview.py`
- Test: `apps/desktop/tests/task-runtime.test.ts`

**Interfaces:**
- Produces: `effective_year_window(selection: YearSelection, available: Iterable[str]) -> EffectiveYearWindow`
- `EffectiveYearWindow` exposes `selection: YearSelection`,
  `original: dict[str, Any]`, `effective: dict[str, Any]`, `adjusted: bool`.

- [ ] **Step 1: Write failing pure tests**

```python
available = ["2026", "2025", "2024", "2023", "2022"]
window = effective_year_window(
    YearSelection(mode="range", start_year=1949, end_year=2026),
    available,
)
assert window.selection.start_year == 2022
assert window.selection.end_year == 2026
assert window.adjusted is True
```

Also cover no overlap, empty tree, single unavailable year, and current-year cap.

- [ ] **Step 2: Run and verify failure**

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_years -v
```

Expected: FAIL because the helper is missing.

- [ ] **Step 3: Implement the year-window helper**

Parse only real four-digit years, intersect the requested range, and preserve the
original/effective payloads for checkpoint audit.

- [ ] **Step 4: Apply the helper before expanding combinations**

Store:

```text
originalYearSelection
effectiveYearSelection
yearWindowAdjustment
```

in preview and task checkpoints. Display the effective start year in task detail.
Task detail shows both the requested and effective range and explains any adjustment.

- [ ] **Step 5: Add regression tests**

Assert a 1949–2026 request with 2022 as the earliest available year starts at
2022, does not enter `waiting_manual`, and resumes from the correct year after
checkpoint reload.

- [ ] **Step 6: Run verification and commit**

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_years tests.test_scan_resume tests.test_batch_scan_resume tests.test_collector_preview -v
Set-Location ..
pnpm --dir apps/desktop vitest run tests/task-runtime.test.ts
git add engine/pkulaw_engine/selections/years.py engine/pkulaw_engine/single_task.py engine/pkulaw_engine/batch_task.py engine/pkulaw_engine/collector_preview.py engine/tests/test_years.py engine/tests/test_scan_resume.py engine/tests/test_batch_scan_resume.py engine/tests/test_collector_preview.py
git add apps/desktop/src/renderer/src/task-presentation.ts apps/desktop/src/renderer/src/TaskRuntimePanel.tsx apps/desktop/tests/task-runtime.test.ts
git commit -m "fix: start year ranges at the earliest available year"
```

---

### Task 6: Make pause details readable and actions unique

**Files:**
- Create: `apps/desktop/src/renderer/src/TaskPauseDetailDialog.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Modify: `apps/desktop/src/renderer/src/task-presentation.ts`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Test: `apps/desktop/tests/task-runtime.test.ts`
- Test: `apps/desktop/tests/task-workspace-frame.test.ts`
- Test: `apps/desktop/tests/collector-panels.test.ts`

**Interfaces:**
- Produces: `derivePrimaryTaskAction(task, recovery) -> TaskPrimaryAction`
- `TaskPrimaryAction` is `"continue" | "resume-from-checkpoint" | "redetect" |
  "page-limit" | "scale" | "candidate" | "none"`.
- Produces: `<TaskPauseDetailDialog detail={TaskPauseDetail} onClose>`.

- [ ] **Step 1: Write failing tests**

Assert:

- `YEAR_FILTER_UNAVAILABLE` never produces `continue`;
- waiting-live manual produces only `continue`;
- failed auto-recovery produces only `redetect`;
- stopped scan with journal produces only `resume-from-checkpoint`;
- action click sets visible `正在处理`;
- failed action sets a visible error banner;
- “查看完整原因” exposes full code/message/checkpoint.

- [ ] **Step 2: Run and verify failure**

```powershell
pnpm --dir apps/desktop vitest run tests/task-runtime.test.ts tests/task-workspace-frame.test.ts tests/collector-panels.test.ts
```

Expected: FAIL on multiple actions and missing detail dialog.

- [ ] **Step 3: Implement the action planner and pause dialog**

Route rendering through `derivePrimaryTaskAction`; hide all other continuation
buttons. Put the action result banner above the task content so clipping cannot
hide feedback.

- [ ] **Step 4: Implement pause detail presentation**

Keep the pause card short and add “查看完整原因”. The dialog may scroll internally
but the application shell remains fixed.

- [ ] **Step 5: Verify and commit**

```powershell
pnpm --dir apps/desktop vitest run tests/task-runtime.test.ts tests/task-workspace-frame.test.ts tests/collector-panels.test.ts
pnpm --dir apps/desktop typecheck
git add apps/desktop/src/renderer/src/TaskPauseDetailDialog.tsx apps/desktop/src/renderer/src/TaskRuntimePanel.tsx apps/desktop/src/renderer/src/task-presentation.ts apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/task-runtime.test.ts apps/desktop/tests/task-workspace-frame.test.ts apps/desktop/tests/collector-panels.test.ts
git commit -m "fix: make paused task details and actions unambiguous"
```

---

### Task 7: Stabilize automatic captcha recovery

**Files:**
- Modify: `apps/desktop/src/renderer/src/useManualRecovery.ts`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx`
- Modify: `apps/desktop/src/main/fixture-server.ts`
- Modify: `apps/desktop/src/main/index.ts`
- Modify: `apps/desktop/tests/manual-recovery-ui.test.ts`
- Modify: `apps/desktop/tests/task-workspace.test.ts`
- Modify: `engine/pkulaw_engine/browser_bridge.py`
- Test: `engine/tests/test_full_scope_contracts.py`

**Interfaces:**
- Produces constants: `REQUIRED_STABLE_CLEAR_SAMPLES = 3`,
  `CAPTCHA_SETTLE_WINDOW_MS = 2500`, `AUTO_RECOVERY_FAILURE_GRACE_MS = 30000`.
- Browser recheck uses at least 3 samples over approximately 2 seconds.

- [ ] **Step 1: Write failing recovery tests**

Cover:

- one and two clear samples still wait;
- three stable samples recover once;
- transient overlay in the middle resets count;
- `CAPTCHA_VISIBLE` during recheck returns to observation, not permanent failure;
- no worker stop and no duplicate action button;
- after the grace window, exactly one “重新检测并继续” appears.

- [ ] **Step 2: Run and verify failure**

```powershell
pnpm --dir apps/desktop vitest run tests/manual-recovery-ui.test.ts tests/task-workspace.test.ts
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_full_scope_contracts -v
```

Expected: FAIL on the old two-sample/immediate-failure behavior.

- [ ] **Step 3: Implement longer clear detection and graceful retry**

Keep the task worker alive. On `CAPTCHA_VISIBLE`, clear stability and continue
observing. Only show the manual fallback after the grace window.

- [ ] **Step 4: Add a fixture with delayed overlay removal**

The fixture removes the visible overlay after 1.5 seconds while leaving residual
DOM briefly. Smoke must assert automatic recovery without an extra ordinary click.

- [ ] **Step 5: Verify and commit**

```powershell
pnpm --dir apps/desktop vitest run tests/manual-recovery-ui.test.ts tests/task-workspace.test.ts tests/task-workspace-frame.test.ts
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_full_scope_contracts -v
Set-Location ..
pnpm --dir apps/desktop smoke:stage6
git add apps/desktop/src/renderer/src/useManualRecovery.ts apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx apps/desktop/src/main/fixture-server.ts apps/desktop/src/main/index.ts apps/desktop/tests/manual-recovery-ui.test.ts apps/desktop/tests/task-workspace.test.ts engine/pkulaw_engine/browser_bridge.py engine/tests/test_full_scope_contracts.py
git commit -m "fix: recover reliably after manual captcha completion"
```

---

### Task 8: Update documentation and run end-to-end release verification

**Files:**
- Modify: `deployment/使用手册.md`
- Modify: `deployment/README_快速开始.md`
- Modify: `deployment/故障排查.md`
- Modify: `docs/PKULAW_WORKBENCH_0.2.2_TEAM_MANUAL_ZH.md`
- Create: `docs/2026-11-03_USABILITY_SEARCH_REMEDIATION_ACCEPTANCE.md`

**Interfaces:**
- Consumes all prior tasks.

- [ ] **Step 1: Update user-facing documentation**

Document:

- three single modes and two batch modes, with exact website fields;
- fresh empty query fields;
- year/province row layout and visible start action;
- dynamic first available year;
- one pause action and full-detail dialog;
- automatic captcha recovery and the single fallback action.

- [ ] **Step 2: Extend the unified smoke report**

Record:

- exact website field for each mode;
- no Fuzzy/download on mismatch;
- button visibility and equal sizes;
- effective year range;
- unique pause action;
- exactly one auto-recovery event.

- [ ] **Step 3: Run the complete regression**

```powershell
pnpm test
pnpm --dir apps/desktop smoke:stage6
```

Expected: Python, Vitest, TypeScript, and Electron smoke all exit 0.

- [ ] **Step 4: Build and verify the portable package**

```powershell
$env:PKULAW_PACKAGING_PYTHON='C:\Users\85748\Documents\Codex\2026-10-31\co\work\pkulaw_workbench\.packaging-venv\Scripts\python.exe'
pnpm build:portable
pnpm verify:portable
```

Expected: both commands exit 0; record ZIP name and SHA-256.

- [ ] **Step 5: Real-site supervised verification**

The user performs one small supervised check:

1. Keyword mode visibly uses 标题 + 精确.
2. Fulltext mode visibly uses 全文 + 精确.
3. Exact-full-title mode visibly uses 标题 + 精确.
4. Complete one slider and confirm the task resumes automatically without an
   additional ordinary continue click.
5. Do not start a large download.

- [ ] **Step 6: Commit**

```powershell
git add deployment/使用手册.md deployment/README_快速开始.md deployment/故障排查.md docs/PKULAW_WORKBENCH_0.2.2_TEAM_MANUAL_ZH.md docs/2026-11-03_USABILITY_SEARCH_REMEDIATION_ACCEPTANCE.md
git commit -m "docs: explain corrected search and recovery workflow"
```

## Self-Review

- Spec coverage: all eight confirmed issue groups map to Tasks 1–8.
- Search modes: Task 1 owns website semantics; Task 2 owns UI values/labels.
- Layout: Task 3 owns form height/action visibility; Task 4 owns overview parity.
- Year filter: Task 5 owns effective range; Task 6 owns failure presentation.
- Captcha: Task 7 owns stable detection; Task 5 ensures year selection no longer
  masquerades as a captcha/manual-access failure.
- Type consistency: `keyword|fulltext|title`, `SearchModeSpec`,
  `EffectiveYearWindow`, and `TaskPrimaryAction` are used consistently.
- Review Focus inputs each have an owning test in Tasks 1, 3, 5, 6, or 7.
