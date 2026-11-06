# Batch Download and Local Processing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Begin local processing of each validated 50-policy batch while the browser sequentially downloads the next batch, without concurrent北大法宝 browser access.

**Architecture:** Add an offline-only batch-processing mode to the existing data manager, then introduce a durable single-worker queue. The batch manager submits each committed artifact to the queue after ZIP/catalog validation; browser work remains strictly sequential, while at most one local processing job runs concurrently in the background.

**Tech Stack:** Python 3.14, `unittest`, SQLite processing runs, Electron task events, `fflate` ZIP validation, existing document processors.

**Spec:** [2026-11-06-adaptive-captcha-volume-remediation-design.md](../specs/2026-11-06-adaptive-captcha-volume-remediation-design.md)

## Global Constraints

- Apply this plan only after the adaptive-search C+→A plan passes its phase gate.
- At most one北大法宝 browser action may run at a time.
- At most one local batch-processing worker may run at a time.
- Pipeline processing must not use `BrowserBridge`, browser leases, CAPTCHA handling, or network fallback.
- A batch enters processing only after ZIP and catalog validation succeeds.
- Local processing failure must not invalidate a valid downloaded ZIP or trigger a second download.
- Existing manual “data.start” behavior remains unchanged unless phase-one browser access succeeds.
- Processing runs remain recoverable through SQLite and use stable batch keys for idempotency.
- No CAPTCHA, login, Cookie, quota, or private-key material is written to logs.

## Review Focus

- A valid ZIP whose local Word conversion fails must remain downloaded and must be retried locally without a network request.
- A pipeline worker submitting the same batch twice must return the existing processing run.
- A browser download and local processing overlap must never create a second browser lease request.
- Application restart with a pending queue item must resume or mark that item recoverable without duplicating outputs.
- A mixed task containing old completed batches and new pipeline batches must process each batch once.

---

## File Structure

- Create `engine/pkulaw_engine/batch_processing_queue.py`: durable single-worker queue and batch-run orchestration.
- Modify `engine/pkulaw_engine/processing_task.py`: batch-key filtering and offline-only mode.
- Modify `engine/pkulaw_engine/batch_task.py`: enqueue after validated batch commit.
- Modify `engine/pkulaw_engine/application.py`: construct and recover the queue.
- Modify `engine/pkulaw_engine/storage.py`: store pipeline state in task checkpoints or batch records without a schema change if existing JSON columns suffice.
- Modify `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`: show pipeline status.
- Test: `engine/tests/test_batch_processing_queue.py`
- Test: `engine/tests/test_processing_task.py`
- Test: `apps/desktop/tests/batch-pipeline-ui.test.ts`

## Task 1: Add Batch-Scoped Offline Processing

**Files:**
- Modify: `engine/pkulaw_engine/processing_task.py`
- Modify: `engine/tests/test_processing_task.py`

**Interfaces:**
- Consumes: `DataProcessingManager.start(task_id, options, lease_id="")`.
- Produces: options:
  - `batchKeys: list[str]`
  - `allowBrowserFallback: bool`

When `batchKeys` is present, processing includes only batches whose `batchKey` is in that set. When `allowBrowserFallback` is false, the processor must not instantiate or query browser-dependent fallbacks; missing information becomes a per-document failure or review state, not a network action.

- [ ] **Step 1: Write failing tests**

Add tests named:

```text
test_processing_batch_keys_selects_only_requested_batches
test_offline_processing_never_uses_browser_bridge
test_offline_processing_failure_preserves_source_zip
test_default_processing_mode_still_allows_existing_behavior
```

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_processing_task.py" -v
Pop-Location
```

Expected: FAIL because the options are ignored.

- [ ] **Step 3: Implement option parsing and batch filtering**

Parse options once at run creation, reject unknown/malformed batch keys with `INVALID_BATCH_KEYS`, persist the normalized options, and pass an offline-capability flag into the processor context.

- [ ] **Step 4: Run focused tests**

Run the command from Step 2.

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/processing_task.py engine/tests/test_processing_task.py
git commit -m "feat: add offline batch processing scope"
```

## Task 2: Add the Durable Single-Worker Queue

**Files:**
- Create: `engine/pkulaw_engine/batch_processing_queue.py`
- Test: `engine/tests/test_batch_processing_queue.py`

**Interfaces:**
- Consumes: `DataProcessingManager` and `TaskStore`.
- Produces:
  - `BatchProcessingQueue(database_path, data_manager, event_sink)`
  - `submit(task_id: str, batch_key: str) -> str`
  - `recover(task_id: str) -> None`
  - `wait_idle(task_id: str, timeout_seconds: float) -> None`
  - checkpoint map `batchPipeline[batchKey] = {status, processingId, submittedAt, completedAt, error}`

Statuses are `queued`, `running`, `completed`, and `failed`. `submit` is idempotent for an already queued, running, or completed batch key.

- [ ] **Step 1: Write failing queue tests**

Assertions:

```python
assert queue.submit(task_id, "batch-1") == queue.submit(task_id, "batch-1")
assert data_manager.start_calls == 1
assert queue.snapshot(task_id)["batch-1"]["status"] == "running"
```

Add tests for single-worker serialization, failure isolation, restart recovery, and no browser lease.

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_batch_processing_queue.py" -v
Pop-Location
```

Expected: FAIL because the queue does not exist.

- [ ] **Step 3: Implement the queue**

Use one daemon worker and a condition variable. Persist state before starting a run and after every terminal transition. Call `DataProcessingManager.start` with exactly:

```python
{
    "batchKeys": [batch_key],
    "allowBrowserFallback": False,
}
```

- [ ] **Step 4: Run focused tests**

Run the command from Step 2.

Expected: all queue tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/batch_processing_queue.py engine/tests/test_batch_processing_queue.py
git commit -m "feat: add durable batch processing queue"
```

## Task 3: Enqueue Validated Batches Without Blocking Browser Progress

**Files:**
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `engine/pkulaw_engine/application.py`
- Test: `engine/tests/test_adaptive_batch_task.py`
- Test: `engine/tests/test_batch_processing_queue.py`

**Interfaces:**
- Consumes: `BatchProcessingQueue.submit`.
- Produces: `BatchTaskManager(..., batch_processing_queue: BatchProcessingQueue | None = None)`.

The queue callback runs immediately after ZIP/catalog verification and batch checkpoint commit. It must not wait for processing before returning to the browser download loop. If no queue is injected, behavior remains unchanged.

- [ ] **Step 1: Write failing integration tests**

Assertions:

```text
validated batch-1 -> queue submitted before next browser page action
invalid batch-1  -> queue not submitted
browser action 2 can start while batch-1 processing is still running
no second browser lease is requested by the processing worker
```

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_batch_processing_queue.py" -v
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_adaptive_batch_task.py" -v
Pop-Location
```

Expected: FAIL because the batch manager does not submit validated batches.

- [ ] **Step 3: Implement the post-validation submit hook**

Place the submit call after artifact validation and checkpoint durability, and before advancing to the next batch. Log `batch.pipeline.queued` with task ID and batch key only.

- [ ] **Step 4: Run focused tests**

Run the commands from Step 2.

Expected: all integration tests pass.

- [ ] **Step 5: Commit**

```powershell
git add engine/pkulaw_engine/batch_task.py engine/pkulaw_engine/application.py engine/tests/test_adaptive_batch_task.py engine/tests/test_batch_processing_queue.py
git commit -m "feat: pipeline validated batches into local processing"
```

## Task 4: Show Pipeline Status and Verify Recovery

**Files:**
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `deployment/使用手册.md`
- Test: `apps/desktop/tests/batch-pipeline-ui.test.ts`
- Test: `engine/tests/test_batch_processing_queue.py`

**Interfaces:**
- Consumes: `batchPipeline` checkpoint state and `batch.pipeline.*` events.
- Produces: task-level counters for queued, running, completed, and failed local batches.

- [ ] **Step 1: Write failing UI and recovery tests**

UI expectations:

```ts
expect(html).toContain("本地处理");
expect(html).toContain("已完成 2 批");
expect(html).toContain("等待处理 1 批");
```

Recovery expectations:

```text
restart with one queued and one running item
-> running item becomes recoverable
-> queued item is submitted first
-> no batch key is processed twice
```

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
pnpm --dir apps/desktop test -- batch-pipeline-ui.test.ts
Push-Location engine
D:\Python\python.exe -X utf8 -m unittest discover -s tests -p "test_batch_processing_queue.py" -v
Pop-Location
```

Expected: FAIL because pipeline state is not rendered or recovered.

- [ ] **Step 3: Implement the status display and recovery behavior**

The batch task remains the single user-visible task. Do not require the user to start data processing manually after the pipeline has been enabled. Failed local processing remains available through the existing data-processing retry surface.

- [ ] **Step 4: Update the manual**

Document that browser downloads remain sequential, local conversion may run concurrently, and local processing failure never causes a second website download.

- [ ] **Step 5: Run the complete verification suite**

```powershell
pnpm test
pnpm build
```

Expected: all tests, typechecking, and the production build pass.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/src/renderer/src/BatchCollectorPanel.tsx deployment/使用手册.md apps/desktop/tests/batch-pipeline-ui.test.ts engine/tests/test_batch_processing_queue.py
git commit -m "feat: expose batch processing pipeline status"
```

## Phase Gate

Do not enable the queue by default until the adaptive-search plan is released and verified. A user-authorized live run must confirm that:

- only one browser action runs at a time;
- local processing starts before the next ZIP finishes;
- valid ZIPs survive local processing failures;
- no duplicate download or duplicate processing output is created.
