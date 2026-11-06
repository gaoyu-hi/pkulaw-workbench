# Adaptive Captcha Volume Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Windows collection reuse one keyword search per query/library, automatically switch year filters until the source boundary, then continue only the unfinished scopes after an explicit A-phase confirmation.

**Architecture:** Add a deterministic search-action governor and an adaptive-year planner, then split `PkulawAdapter.search()` into an initial keyword submission and reusable year-filter switches. Wire the same C+→A state machine into single and batch managers, expose one boundary continuation action in the UI, and preserve legacy task behavior unless the task explicitly uses search strategy version 2.

**Tech Stack:** Python 3.14, `unittest`, SQLite task checkpoints, Electron 45, React 19, TypeScript 7, Vitest 5, Electron BrowserView/CDP.

**Spec:** [2026-11-06-adaptive-captcha-volume-remediation-design.md](../specs/2026-11-06-adaptive-captcha-volume-remediation-design.md)

## Global Constraints

- New tasks use `searchStrategyVersion: 2`; tasks without that field retain legacy planning behavior.
- A version-2 task submits the complete keyword form at most once per keyword and sublibrary.
- Central and local are still searched separately.
- Year switches reuse the active query, match mode, library, province, timeliness, and page size.
- Empty available years are skipped.
- Normal result-operation spacing is 1000 ms measured from the previous completed action; page loading itself already satisfies the interval when it takes at least 1000 ms.
- After CAPTCHA clears, the next result operation waits at least 10000 ms unless the original request is already known to have succeeded.
- A second CAPTCHA within the next five result operations releases the task to `CAPTURE_BOUNDARY_REACHED`.
- A boundary task remains `waiting_manual`; it is not failed, stopped, duplicated, or restarted from page 1.
- A-phase continuation updates the same task and processes only missing year/province scopes.
- Existing titles, search modes, output structure, selectors, quotas, login gates, and ZIP validation remain unchanged.
- Do not recognize, solve, bypass, submit, log, or transmit reusable CAPTCHA, login, Cookie, or quota material.
- Only one task may operate the北大法宝 browser surface at a time.
- Keep search planners pure; browser operations remain in `PkulawAdapter`, task lifecycle remains in the single/batch managers.

## Review Focus

- A result row without a parseable publication date must never make a requested year look complete; it must remain in the missing-year calculation or be reported as unresolved.
- A requested range beginning before the first real available year must use the first real year and persist the adjustment without changing the user's original selection.
- A zero-result keyword/library scope must produce an empty result without year-switch actions.
- A CAPTCHA raised after the browser already produced the requested result must not cause a second keyword submission.
- A process restart while `CAPTURE_BOUNDARY_REACHED` must expose the same completed and remaining scopes and must not create a new task.

---

## File Structure

- Create `engine/pkulaw_engine/search_governor.py`: result-operation pacing, counters, and repeated-CAPTCHA circuit decision.
- Create `engine/pkulaw_engine/capture_boundary.py`: serializable collection boundary and typed exception.
- Create `engine/pkulaw_engine/selections/adaptive_years.py`: pure year coverage and action planning.
- Modify `engine/pkulaw_engine/selections/years.py`: add action kind and initial-search expansion without removing legacy expansion.
- Modify `engine/pkulaw_engine/sources/pkulaw.py`: expose same-session year switching.
- Modify `engine/pkulaw_engine/single_task.py`: single-task C+→A orchestration.
- Modify `engine/pkulaw_engine/batch_task.py`: batch-task C+→A orchestration.
- Modify `engine/pkulaw_engine/application.py`: expose boundary continuation requests.
- Modify `apps/desktop/src/shared/protocol.ts` and `apps/desktop/src/preload/index.ts`: continuation API.
- Modify `apps/desktop/src/main/ipc.ts`: bridge boundary continuation.
- Modify `apps/desktop/src/renderer/src/task-presentation.ts`: boundary action labels and details.
- Modify `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`, `BatchCollectorPanel.tsx`, and `TaskCenterPanel.tsx`: display and invoke the A-phase action.
- Modify `deployment/使用手册.md`, `deployment/故障排查.md`, and `docs/PROJECT_STATE.md`: user-visible behavior and recovery semantics.

## Task 1: Add the Search Action Governor

**Files:**
- Create: `engine/pkulaw_engine/search_governor.py`
- Test: `engine/tests/test_search_governor.py`

**Interfaces:**
- Consumes: monotonic clock and sleep callables injected by task managers.
- Produces:
  - `SearchActionKind = Literal["keyword-submission", "year-filter-switch"]`
  - `SearchActionGovernor(clock, sleep, minimum_interval_seconds=1.0, captcha_interval_seconds=10.0, captcha_window_actions=5)`
  - `before_result_action(kind: SearchActionKind) -> None`
  - `after_result_action(completed_at: float | None = None) -> None`
  - `note_captcha_cleared(observed_at: float | None = None) -> bool`
  - `snapshot() -> dict[str, int | float | None]`

- [ ] **Step 1: Write failing governor tests**

```python
class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds

def test_second_action_waits_only_the_remaining_interval() -> None:
    clock = FakeClock(100.0)
    sleeps: list[float] = []
    governor = SearchActionGovernor(clock=clock, sleep=sleeps.append)
    governor.after_result_action()
    clock.advance(0.4)
    governor.before_result_action("year-filter-switch")
    assert sleeps == [0.6]

def test_second_captcha_within_five_actions_releases_boundary() -> None:
    governor = SearchActionGovernor(clock=FakeClock(), sleep=lambda _: None)
    assert governor.note_captcha_cleared() is False
    for _ in range(4):
        governor.after_result_action()
        governor.before_result_action("year-filter-switch")
    assert governor.note_captcha_cleared() is True
```

Add tests proving:

- no wait occurs before the first action;
- no extra wait occurs when the previous action already took at least 1000 ms;
- CAPTCHA clearing forces a 10000 ms wait;
- page actions do not increment keyword-submission count;
- snapshot exposes `keyword_submissions`, `year_filter_switches`, `captcha_waits`, and `released_to_boundary`.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_search_governor.py" -v
Pop-Location
```

Expected: FAIL because `search_governor.py` does not exist.

- [ ] **Step 3: Implement `SearchActionGovernor`**

Implement elapsed-time arithmetic using injected `clock` and `sleep`. Increment counters in `before_result_action`, reset the CAPTCHA cooldown only in `note_captcha_cleared`, and return `True` only when two CAPTCHA-clear events occur within the configured action window. Waiting must not use a real clock in tests.

- [ ] **Step 4: Run the focused tests**

Run the command from Step 2.

Expected: all governor tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/search_governor.py engine/tests/test_search_governor.py
git commit -m "feat: add search action governor"
```

## Task 2: Add Adaptive Year Coverage and Boundary Models

**Files:**
- Create: `engine/pkulaw_engine/selections/adaptive_years.py`
- Create: `engine/pkulaw_engine/capture_boundary.py`
- Modify: `engine/pkulaw_engine/selections/years.py`
- Test: `engine/tests/test_adaptive_years.py`
- Test: `engine/tests/test_capture_boundary.py`

**Interfaces:**
- Consumes: `YearSelection`, available year entries shaped as `{"value": str, "count": int}`, collected counts keyed by year, and result rows containing `publicationDate`.
- Produces:
  - `SearchCombination.action_kind: str = "keyword-submission"`
  - `AdaptiveYearPlan(requested_selection, available_counts, collected_counts, completed_years, missing_years, unresolved_years)`
  - `plan_adaptive_years(selection, entries, collected_counts) -> AdaptiveYearPlan`
  - `build_year_switch_combinations(plan, query_index, library_index, start_index=0) -> list[SearchCombination]`
  - `publication_year_from_row(row) -> int | None`
  - `row_matches_year_selection(row, selection) -> bool`
  - `CaptureBoundary(reason, query, library, year, province, completed_policy_count, remaining_scopes, estimated_result_operations, detected_at)`
  - `CaptureBoundaryRequired(code="CAPTURE_BOUNDARY_REACHED")`

- [ ] **Step 1: Write failing planner and boundary tests**

```python
def test_plan_uses_real_years_and_only_missing_nonempty_years() -> None:
    selection = YearSelection(mode="range", start_year=1949, end_year=2023)
    plan = plan_adaptive_years(
        selection,
        [{"value": "2022", "count": 1}, {"value": "2023", "count": 2}],
        {"2022": 1},
    )
    assert plan.completed_years == ("2022",)
    assert plan.missing_years == ("2023",)
    assert plan.available_counts == {"2022": 1, "2023": 2}

def test_unknown_publication_date_is_not_treated_as_in_range() -> None:
    selection = YearSelection(mode="range", start_year=2020, end_year=2023)
    assert publication_year_from_row({"publicationDate": ""}) is None
    assert row_matches_year_selection({"publicationDate": ""}, selection) is False

def test_missing_years_become_year_filter_actions() -> None:
    selection = YearSelection(mode="range", start_year=1949, end_year=2023)
    plan = plan_adaptive_years(
        selection,
        [{"value": "2022", "count": 1}, {"value": "2023", "count": 2}],
        {"2022": 1},
    )
    actions = build_year_switch_combinations(plan, 0, 0, start_index=3)
    assert [item.year for item in actions] == ["2023"]
    assert [item.action_kind for item in actions] == ["year-filter-switch"]
```

Add tests for a requested start earlier than the first real year, a zero-count year, malformed counts, and serialization of the exact boundary error code.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_adaptive_years.py" -v
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_capture_boundary.py" -v
Pop-Location
```

Expected: FAIL because both modules are absent.

- [ ] **Step 3: Implement the pure plan and boundary models**

`plan_adaptive_years` must intersect the requested range with real available years, ignore years whose declared count is zero, mark a year complete only when collected count is at least its declared count, and list under-collected years as missing. `build_year_switch_combinations` must emit one `year-filter-switch` combination per missing year in ascending year order. `publication_year_from_row` must accept only four-digit years in the configured 1949–current-year range. `CaptureBoundaryRequired.to_checkpoint()` must expose `CAPTURE_BOUNDARY_REACHED` and a JSON-serializable boundary record.

- [ ] **Step 4: Run the focused tests**

Run the commands from Step 2.

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/selections/adaptive_years.py engine/pkulaw_engine/selections/years.py engine/pkulaw_engine/capture_boundary.py engine/tests/test_adaptive_years.py engine/tests/test_capture_boundary.py
git commit -m "feat: add adaptive year coverage planning"
```

## Task 3: Reuse the Active Search Session for Year Changes

**Files:**
- Modify: `engine/pkulaw_engine/sources/pkulaw.py`
- Test: `engine/tests/test_pkulaw_adapter_operations.py`

**Interfaces:**
- Consumes: an active `self.current_search_session` and a real available year value.
- Produces: `PkulawAdapter.select_year(year: str) -> dict[str, Any]`.

`select_year` must preserve the current query, match mode, library, province, timeliness, and page size, use the existing filter transaction machinery, return page 1, and update `current_search_request`, `current_search_session`, and `search_url`. It must raise `YearFilterUnavailable` if no compatible active session exists or the year is unavailable.

- [ ] **Step 1: Write failing adapter tests**

Add tests asserting:

```python
adapter.search(keyword="碳中和", match_mode="keyword", library="central", year="全部年份", province="全部省份", timeliness="全部（仅记录元数据，不筛选）", page_size=100)
adapter.select_year("2022")
assert adapter.submit_search_calls == 1
assert adapter.year_filter_calls == ["2022"]
assert state["searchSession"]["query"] == "碳中和"
assert state["page"] == 1
```

Add rejection tests for no active session, a changed library, and a missing year option.

- [ ] **Step 2: Run the focused adapter tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_pkulaw_adapter_operations.py" -v
Pop-Location
```

Expected: FAIL because `select_year` does not exist.

- [ ] **Step 3: Implement `PkulawAdapter.select_year`**

Use `_run_refinement`, `_apply_filter_action("year", year)`, and the active request/session. Do not call `_submit_search`, `_ensure_library`, or `_ensure_search_page`.

- [ ] **Step 4: Run the focused adapter tests**

Run the command from Step 2.

Expected: all adapter-operation tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/sources/pkulaw.py engine/tests/test_pkulaw_adapter_operations.py
git commit -m "feat: reuse search session for year filters"
```

## Task 4: Introduce Versioned Adaptive Search Plans

**Files:**
- Modify: `engine/pkulaw_engine/selections/years.py`
- Modify: `engine/pkulaw_engine/single_task.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskSettingsPanel.tsx`
- Test: `engine/tests/test_task_manager_controls.py`
- Test: `apps/desktop/tests/task-settings-panel.test.ts`

**Interfaces:**
- Consumes: `searchStrategyVersion` from task input.
- Produces:
  - `SingleTaskConfig.search_strategy_version: int = 1`
  - `BatchTaskConfig.search_strategy_version: int = 1`
  - `initial_adaptive_combinations(queries, libraries, selection) -> list[SearchCombination]`

For a version-2 range, `initial_adaptive_combinations` returns one `year="全部年份"` combination per query/library. For version-1 or single/all behavior, return the existing combinations. New renderer-created tasks include `searchStrategyVersion: 2`; task settings edits retain or add version 2.

- [ ] **Step 1: Write failing versioning and expansion tests**

Assert that:

```python
assert len(initial_adaptive_combinations(("碳中和",), ("central", "local"), range_1949_to_2026)) == 2
assert [item.year for item in initial_adaptive_combinations(...)] == ["全部年份", "全部年份"]
assert item.action_kind == "keyword-submission"
```

Add tests proving a legacy task without the field resolves to version 1 and a newly submitted renderer task resolves to version 2.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_task_manager_controls.py" -v
Pop-Location
pnpm --dir apps/desktop test -- task-settings-panel.test.ts
```

Expected: FAIL because version fields and adaptive expansion are absent.

- [ ] **Step 3: Implement version parsing and initial combinations**

Add backward-compatible defaults of `1`. New task forms send `2`. Ensure `SearchCombination.checkpoint()` persists the `action_kind` introduced in Task 2 so a resumed task can distinguish a keyword submission from a year-filter switch.

- [ ] **Step 4: Run the focused tests**

Run the commands from Step 2.

Expected: all version and expansion tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/selections/years.py engine/pkulaw_engine/single_task.py engine/pkulaw_engine/batch_task.py apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskSettingsPanel.tsx engine/tests/test_task_manager_controls.py apps/desktop/tests/task-settings-panel.test.ts
git commit -m "feat: version adaptive search strategy"
```

## Task 5: Implement the Batch C+ Fast Path and Automatic Year Completion

**Files:**
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `engine/pkulaw_engine/selections/adaptive_years.py`
- Test: `engine/tests/test_adaptive_batch_task.py`

**Interfaces:**
- Consumes: version-2 combinations, `PkulawAdapter.select_year`, `SearchActionGovernor`, and `AdaptiveYearPlan`.
- Produces: checkpoint field `adaptiveSearch` containing `keywordSubmissions`, `yearFilterSwitches`, `captchaWaits`, `completedYears`, `missingYears`, `fallbackMode`, and `boundary`.

For a version-2 range:

1. Run one all-years search per query/library.
2. Read year counts and construct an `AdaptiveYearPlan`.
3. If pagination is complete, scan and filter rows by the requested year range.
4. If pagination is incomplete, still retain rows read from accessible pages.
5. Automatically call `select_year` for each missing nonempty year.
6. Use `search()` again only when switching query or sublibrary.
7. Raise `CaptureBoundaryRequired` when a single remaining year scope still cannot fit the source result window.

- [ ] **Step 1: Write failing batch integration tests**

Add tests named:

```text
test_batch_v2_range_submits_keyword_once_per_sublibrary
test_batch_v2_range_switches_only_missing_years
test_batch_v2_zero_result_scope_does_not_switch_year
test_batch_v2_boundary_records_remaining_scope
test_batch_v2_captcha_reuses_successful_original_result
```

The first test uses `碳中和`, central+local, 1949–2026, a one-page result per library, and asserts exactly two `adapter.search()` calls and zero `select_year()` calls. The second asserts exactly two search calls and only missing years passed to `select_year()`. The boundary test asserts `waiting_manual`, `CAPTURE_BOUNDARY_REACHED`, and no batch download.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_adaptive_batch_task.py" -v
Pop-Location
```

Expected: FAIL because batch orchestration still expands all years.

- [ ] **Step 3: Implement batch C+ orchestration**

Gate the new path on `config.search_strategy_version >= 2`. Keep the current path unchanged for version 1. Use `SearchActionGovernor` around every `adapter.search()` and `adapter.select_year()`. Persist activity and year coverage before scanning pages and after each completed year. Preserve existing batch deduplication and scan-journal identities.

- [ ] **Step 4: Run the focused and existing batch tests**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_adaptive_batch_task.py" -v
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_batch_scan_resume.py" -v
Pop-Location
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/batch_task.py engine/pkulaw_engine/selections/adaptive_years.py engine/tests/test_adaptive_batch_task.py
git commit -m "feat: add adaptive batch search path"
```

## Task 6: Implement Single-Task C+ Parity and CAPTCHA Recovery

**Files:**
- Modify: `engine/pkulaw_engine/single_task.py`
- Test: `engine/tests/test_adaptive_single_task.py`

**Interfaces:**
- Consumes: the same version-2 plan, governor, `select_year`, and boundary models from Tasks 1–5.
- Produces: single-task `adaptiveSearch` checkpoint and `CaptureBoundaryRequired` behavior identical to batch.

Single tasks must use the same one-search-per-scope and missing-year rules. A CAPTCHA retry must first call `read_results()` and compare the current `SearchSession` to the pending request; if the session already matches, return it without resubmitting. Only a non-matching session may retry the search after the governor cooldown.

- [ ] **Step 1: Write failing single-task tests**

Add tests named:

```text
test_single_v2_range_submits_keyword_once_per_sublibrary
test_single_v2_range_switches_missing_years_without_resubmit
test_single_v2_captcha_after_success_does_not_resubmit
test_single_v2_boundary_preserves_completed_items
```

Assert visible `keywordSubmissions`, `yearFilterSwitches`, and `captchaWaits` checkpoint values.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_adaptive_single_task.py" -v
Pop-Location
```

Expected: FAIL because the single manager still expands a range into yearly searches.

- [ ] **Step 3: Implement single-task orchestration**

Use the same version gate and pure planner as batch. Put shared CAPTCHA-reuse behavior in a small helper used by both managers if that avoids divergent comparisons. Do not duplicate year coverage rules.

- [ ] **Step 4: Run the focused and existing single-task tests**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_adaptive_single_task.py" -v
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_scan_resume.py" -v
Pop-Location
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/single_task.py engine/tests/test_adaptive_single_task.py
git commit -m "feat: add adaptive single-task search path"
```

## Task 7: Add Boundary Presentation and Same-Task A-Phase Continuation

**Files:**
- Modify: `engine/pkulaw_engine/single_task.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `engine/pkulaw_engine/application.py`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/preload/index.ts`
- Modify: `apps/desktop/src/main/ipc.ts`
- Modify: `apps/desktop/src/renderer/src/task-presentation.ts`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskCenterPanel.tsx`
- Test: `engine/tests/test_capture_boundary_controls.py`
- Test: `apps/desktop/tests/task-presentation.test.ts`
- Test: `apps/desktop/tests/manual-recovery-ui.test.ts`

**Interfaces:**
- Consumes: `CaptureBoundaryRequired` checkpoint data.
- Produces:
  - `SingleTaskManager.continue_capture_boundary(task_id: str) -> dict[str, Any]`
  - `BatchTaskManager.continue_capture_boundary(task_id: str) -> dict[str, Any]`
  - `single.continue-capture-boundary` and `batch.continue-capture-boundary`
  - renderer `continueCaptureBoundary(taskId: string): Promise<TaskDetails | BatchTaskDetails>`
  - primary action kind `capture-boundary`.

Continuation is accepted only for `status == "waiting_manual"` with `error.code == "CAPTURE_BOUNDARY_REACHED"`. It writes `adaptiveSearch.fallbackMode = "A"` and `boundary.confirmedAt`, then clears the boundary error. If the controller is alive, it wakes that controller. If the controller is absent after restart, the application acquires the browser lease and starts the same task from its persisted checkpoint. Repetition with the same operation identity is idempotent.

- [ ] **Step 1: Write failing backend and UI tests**

Backend assertions:

```python
assert continued["task"]["checkpoint"]["adaptiveSearch"]["fallbackMode"] == "A"
assert continued["task"]["checkpoint"]["adaptiveSearch"]["boundary"]["confirmedAt"]
assert repeated["task"]["updatedAt"] == continued["task"]["updatedAt"]
```

Renderer assertions:

```ts
expect(derivePrimaryTaskAction(boundaryTask, { recovery: null })).toBe("capture-boundary");
expect(collectionActions(boundaryTask, { failedItems: 0 })).toMatchObject({
  redetect: false,
  continue: false
});
```

Add a restart test that loads the boundary checkpoint without a live controller and resumes the same task ID with the same remaining scopes.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_capture_boundary_controls.py" -v
Pop-Location
pnpm --dir apps/desktop test -- task-presentation.test.ts manual-recovery-ui.test.ts
```

Expected: FAIL because the boundary action is unknown.

- [ ] **Step 3: Implement backend continuation and UI action**

Add the IPC and renderer method, render the boundary summary and action label `继续完整补采`, and keep `保留已完成结果并结束` wired to the existing stop semantics. Do not route this state through ordinary CAPTCHA auto-recovery.

- [ ] **Step 4: Run the focused tests and typecheck**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_capture_boundary_controls.py" -v
Pop-Location
pnpm --dir apps/desktop test -- task-presentation.test.ts manual-recovery-ui.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: all tests and typecheck pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/single_task.py engine/pkulaw_engine/batch_task.py engine/pkulaw_engine/application.py apps/desktop/src/shared/protocol.ts apps/desktop/src/preload/index.ts apps/desktop/src/main/ipc.ts apps/desktop/src/renderer/src/task-presentation.ts apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskCenterPanel.tsx engine/tests/test_capture_boundary_controls.py apps/desktop/tests/task-presentation.test.ts apps/desktop/tests/manual-recovery-ui.test.ts
git commit -m "feat: add collection boundary continuation"
```

## Task 8: Verify the Complete C+ to A Flow and Update Operational Documentation

**Files:**
- Create: `engine/tests/test_adaptive_search_integration.py`
- Modify: `deployment/使用手册.md`
- Modify: `deployment/故障排查.md`
- Modify: `docs/PROJECT_STATE.md`

**Interfaces:**
- Consumes: all previous task interfaces.
- Produces: end-to-end fixture coverage and user-facing operational instructions.

- [ ] **Step 1: Write failing integration tests**

Add a fixture test covering:

```text
version-2 task
->关键词提交 2 次（中央、地方）
-> 全部年份结果完整
-> 本地筛选 1949—2026
-> 不产生逐年完整检索
-> 形成可下载结果
```

Add a second fixture covering:

```text
version-2 task
-> 首轮结果窗口截断
-> 缺失年份使用年份筛选补齐
-> 单年仍超过窗口
-> waiting_manual / CAPTURE_BOUNDARY_REACHED
-> A 继续
-> 仅处理剩余年份
-> 任务完成
```

- [ ] **Step 2: Run the new integration tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_adaptive_search_integration.py" -v
Pop-Location
```

Expected: FAIL until the complete path is wired.

- [ ] **Step 3: Fix only integration gaps exposed by the tests**

Do not add new tuning constants or broaden accepted selectors. If a failure requires an architecture change, stop and report it instead of layering another retry.

- [ ] **Step 4: Update the manual and troubleshooting text**

Document:

- one keyword submission per keyword/library;
- automatic year switching;
- the 1000 ms/10000 ms pacing policy;
- what `CAPTURE_BOUNDARY_REACHED` means;
- that `继续完整补采` resumes the same task;
- that real CAPTCHAs still require the user and the application does not bypass them.

- [ ] **Step 5: Run the full verification suite**

```powershell
pnpm test
pnpm build
```

Expected:

- all Node/Vitest tests pass;
- all Python tests pass;
- Electron typecheck and production build pass.

- [ ] **Step 6: Commit**

```powershell
git add engine/tests/test_adaptive_search_integration.py deployment/使用手册.md deployment/故障排查.md docs/PROJECT_STATE.md
git commit -m "test: verify adaptive search boundary flow"
```

## Phase Gate

Do not begin the independent download/processing pipeline plan until Tasks 1–8 pass and a user-authorized small real-site run confirms:

- only one keyword submission per keyword/library;
- year changes occur in the same search session;
- no completed policy is downloaded twice;
- a boundary restart resumes the same task;
- no CAPTCHA bypass path is introduced.
