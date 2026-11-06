from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from .atomic_publish import (
    AtomicPublishError,
    prepare_publish_directory,
    publish_directory,
    verify_published_directory,
)
from .batch_execution import BatchExecutionContext, BatchExecutionContextError
from .browser_lease import BrowserLeaseManager
from .browser_bridge import BrowserBridge
from .catalog_formats import (
    CatalogFormatError,
    CatalogPreparation,
    prepare_catalog_output,
    resolve_catalog_format,
)
from .capture_boundary import (
    CaptureBoundary,
    CaptureBoundaryRequired,
)
from .collector_preview import checkpoint_preview_snapshot
from .controls.manual_resume import checkpoint_after_manual_continue
from .controls.task_control import TaskControl, TaskStopped as BatchStopped
from .download_intent import DownloadIntentExhausted
from .logging_utils import JsonLineLogger
from .policy import (
    keyword_result_identity,
    normalize_title,
    safe_filename,
)
from .progress import (
    batch_batch_overall,
    batch_scan_overall,
    build_progress_snapshot,
)
from .scan_journal import ScanJournal, ScanJournalError, config_fingerprint
from .search_governor import (
    SearchActionGovernor,
    SearchActionKind,
)
from .search_window import (
    YearPartitionRequired,
    assess_result_window,
)
from .sources.pkulaw import (
    InstitutionQuotaReached,
    FilterNotApplied,
    LoginRequired,
    ManualActionRequired,
    PageSizeNotApplied,
    PkulawAdapter,
    ResultGroupingUnavailable,
    ResultWindowTruncated,
    ResultsStale,
    SearchLibraryMismatch,
    SelectorProfileMismatch,
    SelectorUnstable,
    YearFilterUnavailable,
)
from .sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
    SearchSessionError,
    checkpoint_requires_controlled_research,
    require_search_session,
)
from .sources.search_mode import (
    ensure_current_search_semantics,
    resolve_search_mode,
)
from .selections.years import (
    ALL_YEARS_LABEL,
    SearchCombination,
    YearSelection,
    build_year_window_checkpoint,
    effective_year_window,
    expand_search_combinations,
    expand_search_combinations_for_year_windows,
    initial_adaptive_combinations,
    finalize_year_stats,
    mark_current_year_status,
    parse_year_selection,
)
from .selections.adaptive_years import (
    plan_adaptive_years,
    publication_year_from_row,
    row_matches_year_selection,
    build_year_switch_combinations,
)
from .selections.pagination import (
    PageSelection,
    page_signature,
    pagination_transition_error,
    parse_page_selection,
    restore_page_selection,
    should_pause_for_page_limit,
)
from .storage import TaskStore, ValidationError
from .sources.library_scope import province_for_library
from .manual_recovery import ACCESS_WAIT_CODES
from .validators.archive import ArchiveValidationError, validate_zip
from .validators.catalog import CatalogValidationError, validate_catalog

SCALE_CONFIRM_PAGE_THRESHOLD = 20
SCALE_CONFIRM_ROW_THRESHOLD = 500
SCALE_CONFIRM_BATCH_THRESHOLD = 5


@dataclass(frozen=True)
class BatchTaskConfig:
    queries: tuple[str, ...]
    match_mode: str
    library: str
    year_selection: YearSelection
    province: str
    timeliness: str
    page_size: int
    page_selection: PageSelection
    output_dir: Path
    catalog_format: str
    full_format: str
    search_strategy_version: int = 1

    def province_for(self, library: str) -> str:
        return province_for_library(library, self.province)


class BatchTaskManager:
    def __init__(
        self,
        database_path: Path,
        browser_bridge: BrowserBridge,
        logger: JsonLineLogger,
        event_sink: Callable[[str, dict[str, Any]], None],
        browser_lease: BrowserLeaseManager | None = None,
    ) -> None:
        self.database_path = database_path
        self.browser_bridge = browser_bridge
        self.logger = logger
        self.event_sink = event_sink
        self.browser_lease = browser_lease
        self.controllers: dict[str, TaskControl] = {}
        self.lock = Lock()

    def start(
        self,
        task_id: str,
        retry_failed: bool = False,
        lease_id: str = "",
    ) -> dict[str, Any]:
        with self.lock:
            existing = self.controllers.get(task_id)
            if existing and existing.thread and existing.thread.is_alive():
                return self.details(task_id)
            if self.browser_lease is not None:
                self.browser_lease.validate(lease_id, task_id, "batch")
            store = TaskStore(self.database_path)
            try:
                task = store.get_task(task_id)
                if task is None:
                    raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
                if task["mode"] != "batch":
                    raise ValidationError("TASK_MODE_MISMATCH", "该任务不是批量任务")
                ensure_current_search_semantics(
                    dict(task.get("input") or {})
                )
                if retry_failed:
                    store.reset_task_batches_for_retry(task_id)
                    store.reset_task_items_for_retry(task_id)
                config = self._parse_config(task)
            finally:
                store.close()

            controller = TaskControl(task_id)
            thread = threading.Thread(
                target=self._run_with_lease,
                args=(task_id, config, controller, lease_id),
                name=f"batch-task-{task_id[:8]}",
                daemon=True,
            )
            controller.thread = thread
            self.controllers[task_id] = controller
            try:
                thread.start()
            except Exception:
                self.controllers.pop(task_id, None)
                raise
            return self.details(task_id)

    def restart_after_access_wait(
        self,
        task_id: str,
        lease_id: str = "",
    ) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError(
                    "TASK_NOT_FOUND",
                    f"task {task_id} was not found",
                )
            if task["mode"] != "batch":
                raise ValidationError(
                    "TASK_MODE_MISMATCH",
                    "该任务不是批量任务",
                )
            error = task.get("error")
            error_code = (
                str(error.get("code") or "")
                if isinstance(error, dict)
                else ""
            )
            if (
                task["status"] not in {"waiting_login", "waiting_manual"}
                or error_code not in ACCESS_WAIT_CODES
            ):
                raise ValidationError(
                    "MANUAL_RECOVERY_NOT_ALLOWED",
                    "当前任务不是可自动恢复的登录或验证码等待状态",
                )
        finally:
            store.close()
        return self.start(
            task_id,
            retry_failed=False,
            lease_id=lease_id,
        )

    def stop(self, task_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            with self.lock:
                controller = self.controllers.get(task_id)
            if task["status"] in {"stopping", "stopped"}:
                return {
                    "task": task,
                    "batches": store.list_task_batches(task_id),
                    "items": store.list_task_items(task_id),
                }
            if not controller or not controller.thread or not controller.thread.is_alive():
                if task["status"] in {
                    "preflight", "running", "pausing", "paused",
                    "waiting_login", "waiting_manual", "verifying",
                }:
                    checkpoint = dict(task.get("checkpoint") or {})
                    checkpoint.update(
                        {
                            "interruptedPreviousStatus": task["status"],
                            "stopRequestedAt": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                    )
                    store.update_task(
                        task_id,
                        status="stopped",
                        current_stage="stopped",
                        error={
                            "code": "APPLICATION_INTERRUPTED",
                            "message": "任务线程已不存在，已按异常中断安全停止",
                        },
                        checkpoint=checkpoint,
                    )
                return self.details(task_id)
            stop_requested = False
            with controller.condition:
                if controller.request_stop():
                    checkpoint = dict(task.get("checkpoint") or {})
                    checkpoint.update(
                        {
                            "stopRequestedAt": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                            "stopPreviousStatus": task["status"],
                        }
                    )
                    self._update_task(
                        store,
                        task_id,
                        status="stopping",
                        current_stage="stopping",
                        checkpoint=checkpoint,
                    )
                    stop_requested = True
            if stop_requested:
                self._emit(
                    "batch.stopping",
                    {"taskId": task_id, "previousStatus": task["status"]},
                )
        finally:
            store.close()
        return self.details(task_id)

    def pause(self, task_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if task["status"] in {"pausing", "paused"}:
                return {
                    "task": task,
                    "batches": store.list_task_batches(task_id),
                    "items": store.list_task_items(task_id),
                }
            if task["status"] not in {"preflight", "running", "verifying"}:
                raise ValidationError("TASK_NOT_PAUSABLE", "当前批量任务状态不能暂停")
            with self.lock:
                controller = self.controllers.get(task_id)
            if not controller or not controller.thread or not controller.thread.is_alive():
                raise ValidationError("TASK_NOT_RUNNING", "该批量任务没有正在运行的工作线程")
            pause_requested = False
            with controller.condition:
                if controller.request_pause("用户手动暂停"):
                    checkpoint = dict(task.get("checkpoint") or {})
                    checkpoint.update(
                        {
                            "pauseKind": "manual",
                            "pauseReason": "用户手动暂停",
                            "pauseRequestedStage": task["currentStage"],
                            "pauseRequestedAt": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                    )
                    self._update_task(
                        store,
                        task_id,
                        status="pausing",
                        current_stage="pausing",
                        checkpoint=checkpoint,
                    )
                    pause_requested = True
            if pause_requested:
                self._emit(
                    "batch.pause-requested",
                    {"taskId": task_id, "reason": "用户手动暂停"},
                )
        finally:
            store.close()
        return self.details(task_id)

    def resume(self, task_id: str, lease_id: str = "") -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(task_id)
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if not controller or not controller.thread or not controller.thread.is_alive():
                if task["status"] in {"paused", "stopped"}:
                    self._validate_resume_for_task()
                    return self.start(task_id, lease_id=lease_id)
                raise ValidationError("TASK_NOT_PAUSED", "该批量任务当前不处于暂停状态")
            if task["status"] in {"preflight", "running"} and not controller.paused:
                return {
                    "task": task,
                    "batches": store.list_task_batches(task_id),
                    "items": store.list_task_items(task_id),
                }
            if task["status"] not in {"pausing", "paused"}:
                raise ValidationError("TASK_NOT_PAUSED", "该批量任务当前不处于暂停状态")
            self._validate_resume_for_task()
            resume_requested = False
            with controller.condition:
                if controller.request_resume():
                    checkpoint = {
                        key: value
                        for key, value in dict(task.get("checkpoint") or {}).items()
                        if key not in {
                            "pauseKind",
                            "pauseReason",
                            "pauseRequestedStage",
                            "pauseRequestedAt",
                            "pausedAt",
                            "pausedStage",
                        }
                    }
                    checkpoint = mark_current_year_status(checkpoint, "running")
                    checkpoint["resumedAt"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    )
                    self._update_task(
                        store,
                        task_id,
                        status="running",
                        current_stage="resumed",
                        clear_error=True,
                        checkpoint=checkpoint,
                    )
                    resume_requested = True
            if resume_requested:
                self._emit("batch.resumed", {"taskId": task_id})
        finally:
            store.close()
        return self.details(task_id)

    def continue_to_last(self, task_id: str) -> dict[str, Any]:
        return self._continue_checkpoint_gate(
            task_id,
            expected_error="PAGE_LIMIT_REACHED",
            checkpoint_updates={
                "pageMode": "last",
                "pageSelection": {"mode": "last"},
                "pageLimitGate": False,
                "pageLimitDecision": "continue_to_last",
            },
            event_name="batch.page-limit-continue",
        )

    def confirm_scale(self, task_id: str) -> dict[str, Any]:
        return self._continue_checkpoint_gate(
            task_id,
            expected_error="BATCH_SCALE_CONFIRMATION_REQUIRED",
            checkpoint_updates={
                "scaleConfirmed": True,
                "scaleConfirmedAt": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            },
            event_name="batch.scale-confirmed",
        )

    def _continue_checkpoint_gate(
        self,
        task_id: str,
        *,
        expected_error: str,
        checkpoint_updates: dict[str, Any],
        event_name: str,
    ) -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(task_id)
        if not controller or not controller.thread or not controller.thread.is_alive():
            raise ValidationError("TASK_NOT_WAITING", "该批量任务当前没有等待人工决策")
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if (
                task["status"] != "waiting_manual"
                or (task.get("error") or {}).get("code") != expected_error
            ):
                raise ValidationError(
                    "TASK_DECISION_NOT_AVAILABLE",
                    f"当前没有待处理决策：{expected_error}",
                )
            with controller.condition:
                checkpoint = dict(task.get("checkpoint") or {})
                checkpoint.update(checkpoint_updates)
                checkpoint["decisionUpdatedAt"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                self._update_task(
                    store,
                    task_id,
                    checkpoint=checkpoint,
                )
                controller.request_continue()
            self._emit(
                event_name,
                {"taskId": task_id, **checkpoint_updates},
            )
        finally:
            store.close()
        return self.details(task_id)

    def continue_task(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(task_id)
        if not controller or not controller.thread or not controller.thread.is_alive():
            raise ValidationError("TASK_NOT_WAITING", "该批量任务当前没有等待人工继续")
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if task["status"] not in {"waiting_login", "waiting_manual"}:
                return {
                    "task": task,
                    "batches": store.list_task_batches(task_id),
                    "items": store.list_task_items(task_id),
                }
            error_code = str((task.get("error") or {}).get("code") or "")
            if error_code in {
                "PAGE_LIMIT_REACHED",
                "BATCH_SCALE_CONFIRMATION_REQUIRED",
            }:
                raise ValidationError(
                    error_code,
                    (
                        "请选择“继续到尾页”或停止任务"
                        if error_code == "PAGE_LIMIT_REACHED"
                        else "请先确认批量规模"
                    ),
                )
            if controller.request_continue():
                self._emit(
                    "batch.manual-continue-requested",
                    {"taskId": task_id, "status": task["status"]},
                )
        finally:
            store.close()
        return self.details(task_id)

    def continue_capture_boundary(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(task_id)
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if task["mode"] != "batch":
                raise ValidationError(
                    "TASK_MODE_MISMATCH",
                    "该任务不是批量任务",
                )
            checkpoint = dict(task.get("checkpoint") or {})
            adaptive = dict(checkpoint.get("adaptiveSearch") or {})
            boundary = dict(adaptive.get("boundary") or {})
            if adaptive.get("fallbackMode") == "A" and boundary.get("confirmedAt"):
                return self.details(task_id)
            if (
                task["status"] != "waiting_manual"
                or (task.get("error") or {}).get("code")
                != "CAPTURE_BOUNDARY_REACHED"
            ):
                raise ValidationError(
                    "CAPTURE_BOUNDARY_NOT_AVAILABLE",
                    "当前没有待确认的完整采集边界",
                )
            boundary["confirmedAt"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(),
            )
            adaptive.update(
                {
                    "fallbackMode": "A",
                    "boundary": boundary,
                    "currentAction": "capture-boundary-continued",
                }
            )
            checkpoint["adaptiveSearch"] = adaptive
            self._update_task(
                store,
                task_id,
                current_stage="capture-boundary-continued",
                clear_error=True,
                checkpoint=checkpoint,
            )
            if controller and controller.thread and controller.thread.is_alive():
                controller.request_continue()
                self._emit(
                    "batch.capture-boundary-continue",
                    {"taskId": task_id},
                )
        finally:
            store.close()
        return self.details(task_id)

    def has_live_controller(self, task_id: str) -> bool:
        with self.lock:
            controller = self.controllers.get(task_id)
        return bool(
            controller
            and controller.thread
            and controller.thread.is_alive()
        )

    def continue_active(self) -> None:
        with self.lock:
            controllers = list(self.controllers.values())
        store = TaskStore(self.database_path)
        try:
            for controller in controllers:
                if not controller.thread or not controller.thread.is_alive():
                    continue
                task = store.get_task(controller.task_id)
                if task is None or task["status"] not in {
                    "waiting_login",
                    "waiting_manual",
                }:
                    continue
                error_code = str((task.get("error") or {}).get("code") or "")
                if error_code in {
                    "PAGE_LIMIT_REACHED",
                    "BATCH_SCALE_CONFIRMATION_REQUIRED",
                }:
                    continue
                controller.request_continue()
        finally:
            store.close()

    def details(self, task_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            return {
                "task": task,
                "batches": store.list_task_batches(task_id),
                "items": store.list_task_items(task_id),
                "timeline": store.list_task_events(task_id),
            }
        finally:
            store.close()

    def stop_all(self) -> None:
        with self.lock:
            controllers = list(self.controllers.values())
        for controller in controllers:
            controller.request_stop()
        for controller in controllers:
            if controller.thread:
                controller.thread.join(timeout=5)

    def has_active(self) -> bool:
        with self.lock:
            return any(
                controller.thread and controller.thread.is_alive()
                for controller in self.controllers.values()
            )

    def has_active_task(self, task_id: str) -> bool:
        with self.lock:
            controller = self.controllers.get(task_id)
            return bool(
                controller
                and controller.thread
                and controller.thread.is_alive()
            )

    def _run_with_lease(
        self,
        task_id: str,
        config: BatchTaskConfig,
        controller: TaskControl,
        lease_id: str,
    ) -> None:
        try:
            self._run(task_id, config, controller)
        finally:
            if self.browser_lease is not None and lease_id:
                self.browser_lease.release(lease_id)

    def _run(
        self,
        task_id: str,
        config: BatchTaskConfig,
        controller: TaskControl,
    ) -> None:
        store = TaskStore(self.database_path)
        adapter = PkulawAdapter(self.browser_bridge, self.logger)
        try:
            task_input = dict(
                ((store.get_task(task_id) or {}).get("input") or {})
            )
            preview_snapshot = checkpoint_preview_snapshot(task_input)
            pre_scan_estimate = self._build_scale_estimate(
                config=config,
                total_pages=max(
                    1,
                    len(config.queries)
                    * (2 if config.library == "both" else 1)
                    * len(config.year_selection.years),
                ),
                total_rows=0,
                total_batches=0,
                year_count=len(config.year_selection.years),
                stage="before_scan",
                basis="combination_floor",
                confidence="structural",
            )
            preflight_snapshot = build_progress_snapshot(
                stage="preflight",
                overall=0.02,
            )
            self._update_task(
                store,
                task_id,
                status="preflight",
                current_stage="preflight",
                progress=preflight_snapshot.overall,
                clear_error=True,
                checkpoint={
                    **((store.get_task(task_id) or {}).get("checkpoint") or {}),
                    "config": self._config_payload(config),
                    "scaleEstimate": pre_scan_estimate,
                    "progressSnapshot": preflight_snapshot.to_dict(),
                    **(
                        {"previewSnapshot": preview_snapshot}
                        if preview_snapshot
                        else {}
                    ),
                },
            )
            self._safe_boundary(
                store, controller, task_id, current_action="preflight"
            )
            browser_state = self.browser_bridge.state()
            if browser_state.get("captchaVisible"):
                self._wait_for_manual(
                    store,
                    controller,
                    task_id,
                    "检测到滑块或人机验证，请人工处理后继续",
                    "waiting_manual",
                )
            elif browser_state.get("loginVisible") or not browser_state.get("authenticated"):
                self._wait_for_manual(
                    store,
                    controller,
                    task_id,
                    "请在内置浏览器完成机构登录后继续",
                    "waiting_login",
                )
            controller.check_stop()
            self._safe_boundary(
                store, controller, task_id, current_action="preflight-complete"
            )
            self._update_task(
                store,
                task_id,
                status="running",
                current_stage="preparing-batches",
                clear_error=True,
            )

            try:
                scans = self._prepare_batches(
                    store,
                    task_id,
                    config,
                    adapter,
                    controller,
                )
            except YearPartitionRequired as partition:
                partition_checkpoint = dict(
                    (store.get_task(task_id) or {}).get("checkpoint")
                    or {}
                )
                partition_checkpoint["yearPartition"] = (
                    partition.to_dict()
                )
                partition_checkpoint["resultWindowProbe"] = {
                    **dict(
                        partition_checkpoint.get("resultWindowProbe")
                        or {}
                    ),
                    "status": "direct_page_rejected",
                    "code": "RESULT_WINDOW_TRUNCATED",
                    "detectedAt": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                }
                partition_checkpoint["currentAction"] = (
                    "year-partition-scan"
                )
                self._update_task(
                    store,
                    task_id,
                    status="running",
                    current_stage="year-partition-scan",
                    clear_error=True,
                    checkpoint=partition_checkpoint,
                )
                self._emit(
                    "batch.year-partition-required",
                    {
                        "taskId": task_id,
                        **partition.to_dict(),
                        "downloadsStarted": 0,
                    },
                )
                scans = self._prepare_batches(
                    store,
                    task_id,
                    config,
                    adapter,
                    controller,
                )
            except CaptureBoundaryRequired as boundary:
                boundary_checkpoint = dict(
                    (store.get_task(task_id) or {}).get("checkpoint")
                    or {}
                )
                boundary_checkpoint["adaptiveSearch"] = {
                    **(boundary_checkpoint.get("adaptiveSearch") or {}),
                    **(
                        dict(getattr(boundary, "adaptive_search", None) or {})
                    ),
                    "fallbackMode": "A",
                    "boundary": boundary.boundary.to_checkpoint(),
                    "currentAction": "capture-boundary",
                }
                self._update_task(
                    store,
                    task_id,
                    status="waiting_manual",
                    current_stage="capture-boundary",
                    error={
                        "code": boundary.code,
                        "message": str(boundary),
                    },
                    checkpoint=boundary_checkpoint,
                )
                self._emit(
                    "batch.capture-boundary-reached",
                    {
                        "taskId": task_id,
                        "boundary": boundary.boundary.to_checkpoint(),
                    },
                )
                return
            self._ensure_processing_selector_profile(adapter)
            self._restore_adapter_search_context(
                adapter,
                store,
                task_id,
                config,
            )
            batches = store.list_task_batches(task_id)
            self._emit(
                "batch.batches-prepared",
                {
                    "taskId": task_id,
                    "batchCount": len(batches),
                    "scans": scans,
                    "checkpoint": store.get_task(task_id).get("checkpoint", {}),
                },
            )

            checkpoint_after_scan = dict(
                (store.get_task(task_id) or {}).get("checkpoint") or {}
            )
            execution = BatchExecutionContext(
                list(checkpoint_after_scan.get("retrievalContexts") or [])
            )
            current_search_session: SearchSession | None = None
            scanned_session = adapter.current_search_session
            if scanned_session is not None:
                scanned_key = (
                    scanned_session.query,
                    scanned_session.library,
                    scanned_session.year,
                )
                if scanned_key in execution.contexts:
                    try:
                        execution.bind_current(scanned_key, scanned_session)
                        current_search_session = scanned_session
                    except BatchExecutionContextError:
                        current_search_session = None
            processing_search_count = 0
            reuse_count = 0
            forward_count = 0
            for batch_index, batch in enumerate(batches, start=1):
                self._safe_boundary(
                    store,
                    controller,
                    task_id,
                    current_action="next-batch",
                    checkpoint={
                        "currentBatchKey": batch["batchKey"],
                        "currentBatchIndex": batch_index,
                        "batchCount": len(batches),
                        "currentQuery": batch["query"],
                        "currentLibrary": batch["library"],
                        "currentPage": batch["page"],
                        "currentRowStart": batch["rowStart"],
                        "currentRowEnd": batch["rowEnd"],
                    },
                )
                if batch["status"] == "completed":
                    integrity_error = self._completed_batch_integrity_error(batch)
                    if integrity_error is None:
                        continue
                    store.upsert_task_batch(
                        task_id,
                        batch_key=batch["batchKey"],
                        query=batch["query"],
                        library=batch["library"],
                        page=batch["page"],
                        row_start=batch["rowStart"],
                        row_end=batch["rowEnd"],
                        status="failed",
                        progress=0,
                        catalog_format=batch["catalogFormat"],
                        full_format=batch["fullFormat"],
                        selected=batch["selected"],
                        catalog_path=str(batch.get("catalogPath") or ""),
                        zip_path=str(batch.get("zipPath") or ""),
                        report_path=str(batch.get("reportPath") or ""),
                        error=integrity_error,
                    )
                    self._set_batch_items_status(
                        store,
                        task_id,
                        list(batch.get("selected") or []),
                        "failed",
                        "batch-artifact-invalid",
                        integrity_error,
                    )
                    self._emit(
                        "batch.artifact-invalid",
                        {
                            "taskId": task_id,
                            "batchKey": batch["batchKey"],
                            "error": integrity_error,
                        },
                    )
                    continue
                if batch["status"] == "failed":
                    continue

                selected_rows = list(batch.get("selected") or [])
                batch_year = str(
                    batch.get("year")
                    or (selected_rows[0].get("year") if selected_rows else "")
                    or config.year_selection.years[0]
                )
                batch_partition_key = str(
                    batch.get("partitionKey")
                    or (
                        selected_rows[0].get("partitionKey")
                        if selected_rows
                        else ""
                    )
                    or ""
                )
                batch_province = str(
                    batch.get("searchedProvince")
                    or (
                        selected_rows[0].get("searchedProvince")
                        if selected_rows
                        else ""
                    )
                    or config.province_for(str(batch["library"]))
                )
                batch_timeliness = str(
                    batch.get("searchedTimeliness")
                    or (
                        selected_rows[0].get("searchedTimeliness")
                        if selected_rows
                        else ""
                    )
                    or config.timeliness
                )
                batch_scope_key = (
                    f"{batch_year}|{batch_partition_key}"
                    if batch_partition_key
                    else batch_year
                )
                search_key = (
                    batch["query"],
                    batch["library"],
                    batch_scope_key,
                )
                decision = execution.decide(
                    search_key,
                    int(batch["page"]),
                )
                if decision.action == "research":
                    searched_state = self._with_manual_retry(
                        store,
                        controller,
                        task_id,
                        lambda batch=batch: adapter.search(
                            keyword=batch["query"],
                            match_mode=config.match_mode,
                            library=batch["library"],
                            year=batch_year,
                            province=batch_province,
                            timeliness=batch_timeliness,
                            page_size=config.page_size,
                        ),
                        action_name="search",
                    )
                    current_search_session = self._require_search_state(
                        searched_state,
                        config=config,
                        query=batch["query"],
                        library=batch["library"],
                        year=batch_year,
                        province=batch_province,
                        timeliness=batch_timeliness,
                        expected_page=1,
                    )
                    execution.bind_current(search_key, current_search_session)
                    processing_search_count += 1
                    self._emit(
                        "batch.context-researched",
                        {
                            "taskId": task_id,
                            "batchKey": batch["batchKey"],
                            "reason": decision.reason,
                            "currentPage": decision.current_page,
                            "targetPage": decision.target_page,
                        },
                    )
                if (
                    execution.current_session is not None
                    and execution.current_session.page < int(batch["page"])
                ):
                    page_state = self._with_manual_retry(
                        store,
                        controller,
                        task_id,
                        lambda page=int(batch["page"]): adapter.go_to_page(page),
                        action_name="next-page",
                    )
                    current_search_session = self._require_search_state(
                        page_state,
                        config=config,
                        query=batch["query"],
                        library=batch["library"],
                        year=batch_year,
                        province=batch_province,
                        timeliness=batch_timeliness,
                        expected_page=int(batch["page"]),
                    )
                    execution.accept_forward_page(
                        search_key,
                        current_search_session,
                    )
                    forward_count += 1
                    self._emit(
                        "batch.context-forwarded",
                        {
                            "taskId": task_id,
                            "batchKey": batch["batchKey"],
                            "targetPage": int(batch["page"]),
                        },
                    )
                current_state = adapter.read_results()
                current_search_session = self._require_search_state(
                    current_state,
                    config=config,
                    query=batch["query"],
                    library=batch["library"],
                    year=batch_year,
                    province=batch_province,
                    timeliness=batch_timeliness,
                    expected_page=int(batch["page"]),
                )
                if (
                    execution.active_key == search_key
                    and execution.current_session is not None
                    and execution.current_session.page == int(batch["page"])
                ):
                    reuse_count += 1
                execution.current_session = current_search_session
                self._update_task(
                    store,
                    task_id,
                    checkpoint={
                        **(store.get_task(task_id).get("checkpoint") or {}),
                        "activeBatchExecutionContext": execution.checkpoint(),
                        "batchProcessingSearchCount": processing_search_count,
                        "batchForwardCount": forward_count,
                        "batchReuseCount": reuse_count,
                    },
                )

                try:
                    self._process_batch(
                        store,
                        task_id,
                        batch,
                        batch_index,
                        len(batches),
                        config,
                        adapter,
                        controller,
                        current_search_session,
                    )
                except (
                    InstitutionQuotaReached,
                    LoginRequired,
                    ManualActionRequired,
                    ResultsStale,
                    DownloadIntentExhausted,
                    ResultWindowTruncated,
                    SelectorProfileMismatch,
                    SelectorUnstable,
                    BatchExecutionContextError,
                    BatchStopped,
                ):
                    raise
                except Exception as exc:
                    error = {
                        "code": getattr(
                            exc,
                            "code",
                            "BATCH_PROCESSING_FAILED",
                        ),
                        "message": str(exc),
                    }
                    if isinstance(
                        getattr(exc, "details", None),
                        dict,
                    ):
                        error["details"] = dict(exc.details)
                    self._fail_batch(
                        store,
                        task_id,
                        batch,
                        error,
                    )

            batches = store.list_task_batches(task_id)
            controller.check_stop()
            completed = sum(batch["status"] == "completed" for batch in batches)
            failed = sum(batch["status"] == "failed" for batch in batches)
            items = store.list_task_items(task_id)
            pending_review = sum(
                item["status"] == "pending_review" for item in items
            )
            status = (
                "completed"
                if (
                    failed == 0
                    and completed == len(batches)
                    and pending_review == 0
                )
                else "partial"
            )
            final_snapshot = build_progress_snapshot(
                stage=status,
                overall=1.0,
                completed_units=completed + failed,
                total_units=len(batches) + pending_review,
                succeeded=completed,
                failed=failed,
                pending_review=pending_review,
            )
            self._update_task(
                store,
                task_id,
                status=status,
                current_stage=status,
                progress=final_snapshot.overall,
                clear_error=True,
                checkpoint=finalize_year_stats({
                    **store.get_task(task_id).get("checkpoint", {}),
                    "completedBatches": completed,
                    "failedBatches": failed,
                    "totalBatches": len(batches),
                    "progressSnapshot": final_snapshot.to_dict(),
                }),
            )
            self._emit(
                "batch.completed",
                {
                    "taskId": task_id,
                    "status": status,
                    "completedBatches": completed,
                    "failedBatches": failed,
                    "totalBatches": len(batches),
                },
            )
        except BatchStopped:
            checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
            checkpoint["stoppedAt"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            stopped_snapshot = self._progress_snapshot_for_batches(
                store,
                task_id,
                stage="stopped",
                overall=float(
                    (checkpoint.get("progressSnapshot") or {}).get("overall") or 0
                ),
            )
            checkpoint["progressSnapshot"] = stopped_snapshot.to_dict()
            self._update_task(
                store,
                task_id,
                status="stopped",
                current_stage="stopped",
                progress=stopped_snapshot.overall,
                error={"code": "TASK_STOPPED", "message": "任务已停止，批次断点已保留"},
                checkpoint=checkpoint,
            )
            self._emit("batch.stopped", {"taskId": task_id})
        except (
            InstitutionQuotaReached,
            LoginRequired,
            ManualActionRequired,
            YearFilterUnavailable,
            ResultsStale,
            DownloadIntentExhausted,
            ResultWindowTruncated,
            SelectorProfileMismatch,
            SelectorUnstable,
            FilterNotApplied,
            PageSizeNotApplied,
            ResultGroupingUnavailable,
            BatchExecutionContextError,
            ScanJournalError,
        ) as exc:
            status = "waiting_login" if isinstance(exc, LoginRequired) else "stopped"
            checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
            safety_snapshot = self._progress_snapshot_for_batches(
                store,
                task_id,
                stage=status,
                overall=float(
                    (checkpoint.get("progressSnapshot") or {}).get("overall") or 0
                ),
            )
            checkpoint["progressSnapshot"] = safety_snapshot.to_dict()
            self._update_task(
                store,
                task_id,
                status=status,
                current_stage=getattr(exc, "code", "MANUAL_ACTION_REQUIRED"),
                error={"code": getattr(exc, "code", "SOURCE_ACCESS_ERROR"), "message": str(exc)},
                progress=safety_snapshot.overall,
                checkpoint=checkpoint,
            )
            self._emit(
                "batch.safety-stopped",
                {
                    "taskId": task_id,
                    "code": getattr(exc, "code", "SOURCE_ACCESS_ERROR"),
                    "message": str(exc),
                },
            )
        except Exception as exc:
            self.logger.write(
                "error",
                "batch.task_failed",
                "Batch task failed",
                {"taskId": task_id, "error": repr(exc)},
            )
            checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
            failed_snapshot = self._progress_snapshot_for_batches(
                store,
                task_id,
                stage="failed",
                overall=float(
                    (checkpoint.get("progressSnapshot") or {}).get("overall") or 0
                ),
            )
            checkpoint["progressSnapshot"] = failed_snapshot.to_dict()
            self._update_task(
                store,
                task_id,
                status="failed",
                current_stage="failed",
                error={
                    "code": getattr(exc, "code", "BATCH_TASK_FAILED"),
                    "message": str(exc),
                },
                progress=failed_snapshot.overall,
                checkpoint=checkpoint,
            )
            self._emit("batch.failed", {"taskId": task_id, "message": str(exc)})
        finally:
            store.close()
            with self.lock:
                self.controllers.pop(task_id, None)

    def _require_search_state(
        self,
        state: dict[str, Any],
        *,
        config: BatchTaskConfig,
        query: str,
        library: str,
        year: str,
        province: str | None = None,
        timeliness: str | None = None,
        expected_page: int,
    ) -> SearchSession:
        request = SearchRequestIdentity(
            query=query,
            match_mode=config.match_mode,
            library=library,
            year=year,
            province=province or config.province_for(library),
            timeliness=timeliness or config.timeliness,
            requested_page_size=config.page_size,
        )
        try:
            return require_search_session(
                state,
                request,
                expected_page=expected_page,
                require_refinements=True,
            )
        except SearchSessionError as exc:
            raise ResultsStale(str(exc)) from exc

    def _prepare_batches(
        self,
        store: TaskStore,
        task_id: str,
        config: BatchTaskConfig,
        adapter: PkulawAdapter,
        controller: TaskControl,
        *,
        _restart_after_universe_change: bool = True,
    ) -> list[dict[str, Any]]:
        libraries = ["central", "local"] if config.library == "both" else [config.library]
        combinations: list[SearchCombination]
        scans: list[dict[str, Any]] = []
        desired_batches: list[dict[str, Any]] = []
        retrieval_pages: dict[
            tuple[str, str, str, str],
            dict[int, dict[str, Any]],
        ] = {}
        retrieval_batch_keys: dict[
            tuple[str, str, str, str],
            list[str],
        ] = {}
        seen_policy_ids: set[str] = set()
        policy_ids_by_scope: dict[tuple[str, str, str], set[str]] = {}
        pending_review_rows: dict[str, dict[str, Any]] = {}
        year_stats: dict[str, dict[str, Any]] = {}
        combination_totals: list[dict[str, Any]] = []
        page_mode = config.page_selection.mode
        first_result_projection: dict[str, Any] | None = None
        first_result_projection_checked = False
        initial_checkpoint = dict(
            (store.get_task(task_id) or {}).get("checkpoint") or {}
        )
        year_partition = dict(
            initial_checkpoint.get("yearPartition") or {}
        )
        year_partition = self._resolve_year_partition_plan(
            adapter,
            config,
            year_partition,
            initial_checkpoint,
        )
        if year_partition != dict(
            initial_checkpoint.get("yearPartition") or {}
        ):
            self._update_task(
                store,
                task_id,
                status="running",
                current_stage="year-partition-scan",
                clear_error=True,
                checkpoint={
                    **initial_checkpoint,
                    "yearPartition": year_partition,
                    "currentAction": "year-partition-scan",
                },
            )
        partition_years = [
            str(year)
            for year in year_partition.get("years") or []
            if str(year).strip()
        ]
        if (
            config.year_selection.mode == "all"
            and year_partition.get("status")
            in {"required", "running", "complete"}
            and partition_years
        ):
            combinations = self._year_partition_combinations(
                config,
                libraries,
                partition_years,
                scope_query=str(year_partition.get("query") or ""),
                scope_library=str(
                    year_partition.get("library") or ""
                ),
                province_partitions={
                    str(year): [
                        str(value)
                        for value in values or []
                        if str(value).strip()
                    ]
                    for year, values in dict(
                        year_partition.get("provincePartitions") or {}
                    ).items()
                },
            )
        else:
            if config.year_selection.mode == "range":
                if config.search_strategy_version >= 2:
                    combinations = initial_adaptive_combinations(
                        config.queries,
                        libraries,
                        config.year_selection,
                    )
                else:
                    year_window_metadata = (
                        self._resolve_year_window_metadata(
                            task_id,
                            config,
                            adapter,
                            store,
                            controller,
                            initial_checkpoint,
                            libraries,
                        )
                    )
                    combinations = (
                        expand_search_combinations_for_year_windows(
                            config.queries,
                            libraries,
                            year_window_metadata,
                        )
                    )
            else:
                combinations = expand_search_combinations(
                    config.queries,
                    libraries,
                    config.year_selection,
                )
        adaptive_mode = config.search_strategy_version >= 2
        if adaptive_mode:
            combinations = self._append_capture_boundary_resume_combinations(
                config=config,
                initial_checkpoint=initial_checkpoint,
                combinations=combinations,
            )
        combination_count = len(combinations)
        governor = SearchActionGovernor() if adaptive_mode else None
        adaptive_scope_states: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}
        persisted_adaptive = dict(
            initial_checkpoint.get("adaptiveSearch") or {}
        )
        for scope_key, persisted_scope in (
            persisted_adaptive.get("scopeStates") or {}
        ).items():
            if not isinstance(scope_key, str) or "|" not in scope_key:
                continue
            query, library = scope_key.split("|", 1)
            scope_state = dict(persisted_scope or {})
            adaptive_scope_states[(query, library)] = {
                "collected": dict(scope_state.get("collected") or {}),
                "plan": None,
                "truncated": False,
                "entries": list(scope_state.get("entries") or []),
            }
        adaptive_completed_years: list[str] = []
        adaptive_missing_years: list[str] = []
        adaptive_boundary: dict[str, Any] = {}
        journal = self._scan_journal(task_id, config)
        journal_records = journal.read()
        records_by_combination: dict[
            tuple[str, str, str, str],
            dict[int, dict[str, Any]],
        ] = {}
        for record in journal_records:
            records_by_combination.setdefault(
                (
                    str(record["query"]),
                    str(record["library"]),
                    str(record["effectiveYear"]),
                    str(record.get("partitionKey") or ""),
                ),
                {},
            )[int(record["page"])] = record
        search_recovery_required = checkpoint_requires_controlled_research(
            initial_checkpoint,
            has_existing_items=bool(store.list_task_items(task_id)),
            has_existing_batches=bool(store.list_task_batches(task_id)),
        )
        for combination in combinations:
            query = combination.query
            library = combination.library
            year = combination.year
            adaptive_plan = None
            adaptive_truncated = False
            stats_key = ":".join(
                (
                    str(combination.query_index),
                    library,
                    year,
                    combination.partition_key,
                )
            )
            stats = year_stats.setdefault(
                stats_key,
                {
                    "query": query,
                    "library": library,
                    "year": year,
                    "province": combination.province or config.province_for(library),
                    "timeliness": (
                        combination.timeliness or config.timeliness
                    ),
                    "partitionKey": combination.partition_key,
                    "status": "pending",
                    "resultCount": 0,
                    "rowsRead": 0,
                    "rowsAccepted": 0,
                    "pagesScanned": 0,
                },
            )
            existing_pages = records_by_combination.get(
                (
                    query,
                    library,
                    year,
                    combination.partition_key,
                ),
                {},
            )
            existing_ordered = [
                existing_pages[page]
                for page in sorted(existing_pages)
            ]
            if existing_pages:
                page_numbers = sorted(existing_pages)
                if page_numbers != list(
                    range(1, page_numbers[-1] + 1)
                ):
                    raise ScanJournalError(
                        "SCAN_JOURNAL_IDENTITY_MISMATCH",
                        "批量扫描日志页不连续，拒绝继续旧页",
                    )
                stats["resultCount"] = max(
                    int(record.get("resultCount") or 0)
                    for record in existing_ordered
                )
                stats["pagesScanned"] = len(existing_ordered)
                stats["rowsRead"] = int(
                    stats.get("rowsRead") or 0
                ) + sum(
                    len(record.get("rows") or [])
                    for record in existing_ordered
                )
                for record in existing_ordered:
                    restored_page = int(record["page"])
                    restored_rows = list(record.get("rows") or [])
                    if any(not row.get("number") for row in restored_rows):
                        raise ValidationError(
                            "BATCH_ROW_IDENTITY_INCOMPLETE",
                            "批量扫描日志存在缺少序号的结果",
                        )
                    accepted_rows: list[dict[str, Any]] = []
                    for raw_row in restored_rows:
                        row = dict(raw_row)
                        policy_id = str(row.get("policyId") or "")
                        if not policy_id:
                            identity = keyword_result_identity(row)
                            pending_review_rows.setdefault(
                                identity,
                                {
                                    **row,
                                    "query": query,
                                    "library": library,
                                    "year": year,
                                    "partitionKey": combination.partition_key,
                                    "searchedProvince": (
                                        combination.province
                                        or config.province_for(library)
                                    ),
                                    "searchedTimeliness": (
                                        combination.timeliness
                                        or config.timeliness
                                    ),
                                },
                            )
                            continue
                        if policy_id in seen_policy_ids:
                            continue
                        row["query"] = query
                        row["library"] = library
                        row["year"] = year
                        row["partitionKey"] = combination.partition_key
                        row["searchedProvince"] = (
                            combination.province or config.province_for(library)
                        )
                        row["searchedTimeliness"] = (
                            combination.timeliness or config.timeliness
                        )
                        seen_policy_ids.add(policy_id)
                        policy_ids_by_scope.setdefault(
                            (query, library, year),
                            set(),
                        ).add(policy_id)
                        accepted_rows.append(row)
                    stats["rowsAccepted"] = (
                        int(stats.get("rowsAccepted") or 0)
                        + len(restored_rows)
                    )
                    for offset in range(0, len(accepted_rows), 50):
                        selected = accepted_rows[offset : offset + 50]
                        if not selected:
                            continue
                        identity_text = "|".join(
                            str(row.get("policyId") or "")
                            for row in selected
                        )
                        batch_key = "batch:" + hashlib.sha256(
                            identity_text.encode("utf-8")
                        ).hexdigest()[:20]
                        desired_batches.append(
                            {
                                "batchKey": batch_key,
                                "query": query,
                                "library": library,
                                "year": year,
                                "partitionKey": combination.partition_key,
                                "searchedProvince": (
                                    combination.province
                                    or config.province_for(library)
                                ),
                                "searchedTimeliness": (
                                    combination.timeliness
                                    or config.timeliness
                                ),
                                "page": restored_page,
                                "rowStart": int(selected[0]["number"]),
                                "rowEnd": int(selected[-1]["number"]),
                                "selected": selected,
                            }
                        )
                        retrieval_batch_keys.setdefault(
                            (
                                query,
                                library,
                                year,
                                combination.partition_key,
                            ),
                            [],
                        ).append(batch_key)
                    scans.append(
                        self._scan_from_journal_record(record)
                    )
                    retrieval_pages.setdefault(
                        (
                            query,
                            library,
                            year,
                            combination.partition_key,
                        ),
                        {},
                    )[restored_page] = {
                        "page": restored_page,
                        "searchId": record["searchId"],
                        "resultSignature": record["resultSignature"],
                        "searchIdentity": {
                            "query": query,
                            "library": library,
                            "year": year,
                            "partitionKey": combination.partition_key,
                            "page": restored_page,
                            "searchId": record["searchId"],
                            "resultSignature": record["resultSignature"],
                        },
                    }
                complete_pages = int(
                    existing_ordered[-1].get("totalPages") or 0
                )
                if len(existing_ordered) == complete_pages:
                    stats["status"] = (
                        "no_result"
                        if stats["resultCount"] == 0
                        else "completed"
                    )
                    self._update_task(
                        store,
                        task_id,
                        checkpoint={
                            **(
                                store.get_task(task_id).get(
                                    "checkpoint"
                                )
                                or {}
                            ),
                            **combination.checkpoint(),
                            "yearStats": list(year_stats.values()),
                            "scanJournal": journal.checkpoint_summary(),
                            "currentAction": (
                                "scan-journal-combination-reused"
                            ),
                        },
                    )
                    continue
            stats["status"] = "running"
            scan_snapshot = build_progress_snapshot(
                stage="scan",
                overall=max(
                    0.02,
                    0.20
                    * (combination.combination_index - 1)
                    / max(1, combination_count),
                ),
                completed_units=len(scans),
                total_units=len(scans),
            )
            self._update_task(
                store,
                task_id,
                status="running",
                current_stage="searching",
                progress=scan_snapshot.overall,
                checkpoint={
                    **(store.get_task(task_id).get("checkpoint") or {}),
                    **combination.checkpoint(),
                    "yearMode": config.year_selection.mode,
                    "yearStart": config.year_selection.start_year,
                    "yearEnd": config.year_selection.end_year,
                    "combinationCount": len(combinations),
                    "pageMode": page_mode,
                    "yearStats": list(year_stats.values()),
                    "progressSnapshot": scan_snapshot.to_dict(),
                    **(
                        {
                            "searchRecovery": {
                                "status": "required",
                                "reason": "MISSING_SEARCH_SESSION",
                                "strategy": "controlled_research",
                                "query": query,
                                "library": library,
                                "year": year,
                            }
                        }
                        if search_recovery_required
                        else {}
                    ),
                },
            )
            self._safe_boundary(
                store,
                controller,
                task_id,
                current_action="search",
            )
            try:
                if adaptive_mode:
                    state = self._run_adaptive_result_action(
                        store=store,
                        controller=controller,
                        task_id=task_id,
                        config=config,
                        adapter=adapter,
                        combination=combination,
                        governor=governor,
                        completed_policy_count=(
                            self._adaptive_completed_policy_count(
                                seen_policy_ids
                            )
                        ),
                    )
                else:
                    state = self._with_manual_retry(
                        store,
                        controller,
                        task_id,
                        lambda query=query, library=library, year=year: adapter.search(
                            keyword=query,
                            match_mode=config.match_mode,
                            library=library,
                            year=year,
                            province=combination.province or config.province_for(library),
                            timeliness=(
                                combination.timeliness or config.timeliness
                            ),
                            page_size=config.page_size,
                        ),
                        action_name="search",
                    )
            except SearchLibraryMismatch as exc:
                if config.library != "both":
                    raise
                stats.update(
                    {
                        "status": "no_result",
                        "skipped": True,
                        "skipReason": "TARGET_LIBRARY_AUTO_REDIRECT",
                        "diagnostic": str(exc),
                    }
                )
                self._emit(
                    "batch.combination-skipped",
                    {
                        "taskId": task_id,
                        "query": query,
                        "library": library,
                        "year": year,
                        "reason": "TARGET_LIBRARY_AUTO_REDIRECT",
                        "diagnostic": str(exc),
                        "downloadsStarted": 0,
                    },
                )
                self._update_task(
                    store,
                    task_id,
                    checkpoint={
                        **(store.get_task(task_id).get("checkpoint") or {}),
                        "currentAction": "combination-skipped",
                        "yearStats": list(year_stats.values()),
                        "libraryRedirect": {
                            "query": query,
                            "library": library,
                            "year": year,
                            "actualLibrary": "other-supported-library",
                            "reason": "TARGET_LIBRARY_AUTO_REDIRECT",
                            "consumedRows": 0,
                        },
                    },
                )
                continue
            search_session = self._require_search_state(
                state,
                config=config,
                query=query,
                library=library,
                year=year,
                province=combination.province or config.province_for(library),
                timeliness=(
                    combination.timeliness or config.timeliness
                ),
                expected_page=1,
            )
            result_window = assess_result_window(
                result_count=search_session.result_count,
                reported_total_pages=search_session.reported_total_pages,
                page_size=search_session.actual_page_size,
            )
            self._update_task(
                store,
                task_id,
                checkpoint={
                    **(store.get_task(task_id).get("checkpoint") or {}),
                    "currentPage": int(state.get("page") or 1),
                    "totalPages": max(1, int(state.get("totalPages") or 1)),
                    "searchId": search_session.search_id,
                    "searchIdentity": search_session.to_dict(),
                    "resultWindow": result_window.to_dict(),
                    "resultWindowProbe": {
                        "status": (
                            "pending"
                            if result_window.status == "truncated"
                            else "not_required"
                        ),
                        "page": 0,
                        "reportedTotalPages": (
                            search_session.reported_total_pages
                        ),
                        "effectiveTotalPages": (
                            search_session.total_pages
                        ),
                    },
                },
            )
            if adaptive_mode and config.year_selection.mode == "range":
                scope_key = (query, library)
                scope_state = adaptive_scope_states.setdefault(
                    scope_key,
                    {
                        "collected": {},
                        "plan": None,
                        "truncated": False,
                        "entries": [],
                    },
                )
                collected = dict(scope_state.get("collected") or {})
                entries = adapter.available_filter_entries("year")
                plan = plan_adaptive_years(
                    config.year_selection,
                    entries,
                    collected,
                    query=query,
                    library=library,
                )
                adaptive_plan = plan
                scope_state["plan"] = plan
                scope_state["entries"] = entries
                adaptive_truncated = result_window.status == "truncated"
                scope_state["truncated"] = adaptive_truncated
                if (
                    combination.action_kind == "year-filter-switch"
                    and adaptive_truncated
                ):
                    boundary_record = CaptureBoundary(
                        reason="单年结果仍超过来源可访问窗口",
                        query=query,
                        library=library,
                        year=year,
                        province=(
                            combination.province
                            or config.province_for(library)
                        ),
                        completed_policy_count=(
                            self._adaptive_completed_policy_count(
                                seen_policy_ids
                            )
                        ),
                        remaining_scopes=tuple(
                            self._adaptive_remaining_scopes(
                                adaptive_scope_states,
                                config,
                            )
                        ),
                        estimated_result_operations=1,
                        detected_at=time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(),
                        ),
                    )
                    raise CaptureBoundaryRequired(
                        boundary_record,
                        adaptive_search=self._adaptive_search_checkpoint(
                            governor=governor,
                            adaptive_scope_states=adaptive_scope_states,
                            config=config,
                            boundary=boundary_record.to_checkpoint(),
                        ),
                    )
            self._safe_boundary(
                store,
                controller,
                task_id,
                current_action="after-search",
            )
            page = int(state.get("page") or 1)
            total_pages = int(state.get("totalPages") or 1)
            search_session = self._require_search_state(
                state,
                config=config,
                query=query,
                library=library,
                year=year,
                province=combination.province or config.province_for(library),
                timeliness=(
                    combination.timeliness or config.timeliness
                ),
                expected_page=page,
            )
            resume_record: dict[str, Any] | None = None
            if existing_ordered:
                first_record = existing_ordered[0]
                signature_changed = (
                    search_session.result_signature
                    != str(first_record.get("resultSignature") or "")
                )
                count_changed = (
                    search_session.result_count
                    != int(first_record.get("resultCount") or 0)
                )
                journal_total_pages = int(
                    existing_ordered[-1].get("totalPages") or 0
                )
                pages_changed = bool(
                    journal_total_pages
                    and total_pages != journal_total_pages
                )
                if (
                    int(first_record.get("page") or 0) != 1
                    or signature_changed
                    or count_changed
                    or pages_changed
                ):
                    if _restart_after_universe_change:
                        self._restart_scan_for_changed_universe(
                            store=store,
                            task_id=task_id,
                            journal=journal,
                            previous=first_record,
                            current=search_session,
                            reason=(
                                "result-signature"
                                if signature_changed
                                else "result-count"
                                if count_changed
                                else "total-pages"
                                if pages_changed
                                else "page-sequence"
                            ),
                        )
                        return self._prepare_batches(
                            store,
                            task_id,
                            config,
                            adapter,
                            controller,
                            _restart_after_universe_change=False,
                        )
                    raise ScanJournalError(
                        "SCAN_JOURNAL_IDENTITY_MISMATCH",
                        "批量重新检索后的结果集身份与扫描日志不一致，且不允许再次重启扫描",
                    )
                resume_record = existing_ordered[-1]
                last_completed_page = int(
                    existing_ordered[-1].get("page") or 0
                )
                max_pages = config.page_selection.max_pages
                if (
                    page_mode == "limit"
                    and max_pages is not None
                    and last_completed_page >= max_pages
                ):
                    page_mode = self._wait_for_page_limit(
                        store=store,
                        controller=controller,
                        task_id=task_id,
                        query=query,
                        library=library,
                        year=year,
                        page=last_completed_page,
                        total_pages=total_pages,
                        max_pages=max_pages,
                    )
                resume_page = int(
                    existing_ordered[-1].get("page") or 0
                ) + 1
                state = adapter.go_to_page(resume_page)
                page = int(state.get("page") or 0)
                total_pages = int(
                    state.get("totalPages") or total_pages
                )
                search_session = self._require_search_state(
                    state,
                    config=config,
                    query=query,
                    library=library,
                    year=year,
                    province=combination.province or config.province_for(library),
                    timeliness=(
                        combination.timeliness or config.timeliness
                    ),
                    expected_page=page,
                )
            combination_total = {
                "query": query,
                "library": library,
                "year": year,
                "partitionKey": combination.partition_key,
                "searchedProvince": (
                    combination.province or config.province_for(library)
                ),
                "searchedTimeliness": (
                    combination.timeliness or config.timeliness
                ),
                "combinationIndex": combination.combination_index,
                "page": page,
                "totalPages": total_pages,
            }
            combination_totals.append(combination_total)
            self._update_task(
                store,
                task_id,
                checkpoint={
                    **(store.get_task(task_id).get("checkpoint") or {}),
                    "totalPages": total_pages,
                    "currentPage": page,
                    "unscannedPages": max(0, total_pages - page),
                    "combinationTotals": list(combination_totals),
                    "searchId": search_session.search_id,
                    "searchIdentity": search_session.to_dict(),
                    **(
                        {
                            "searchRecovery": {
                                "status": "completed",
                                "reason": "MISSING_SEARCH_SESSION",
                                "strategy": "controlled_research",
                                "searchId": search_session.search_id,
                                "completedAt": search_session.observed_at,
                            }
                        }
                        if search_recovery_required
                        else {}
                    ),
                },
            )
            while True:
                self._safe_boundary(
                    store,
                    controller,
                    task_id,
                    current_action="read-results",
                )
                rows = list(state.get("rows") or [])
                search_session = self._require_search_state(
                    state,
                    config=config,
                    query=query,
                    library=library,
                    year=year,
                    province=combination.province or config.province_for(library),
                    timeliness=(
                        combination.timeliness or config.timeliness
                    ),
                    expected_page=page,
                )
                if config.match_mode == "title":
                    target = normalize_title(query)
                    rows = [
                        row for row in rows
                        if normalize_title(str(row.get("title") or "")) == target
                    ]
                if (
                    adaptive_mode
                    and config.year_selection.mode == "range"
                    and combination.action_kind == "keyword-submission"
                ):
                    rows = [
                        row
                        for row in rows
                        if row_matches_year_selection(
                            row,
                            config.year_selection,
                        )
                    ]
                if any(not row.get("number") for row in rows):
                    raise ValidationError(
                        "BATCH_ROW_IDENTITY_INCOMPLETE",
                        f"第 {page} 页存在缺少序号的结果",
                    )
                deduped_rows = []
                for row in rows:
                    policy_id = str(row.get("policyId") or "")
                    if not policy_id:
                        identity = keyword_result_identity(row)
                        pending_review_rows.setdefault(
                            identity,
                            {
                                **row,
                                "query": query,
                                "library": library,
                                "year": year,
                                "partitionKey": combination.partition_key,
                                "searchedProvince": (
                                    combination.province
                                    or config.province_for(library)
                                ),
                                "searchedTimeliness": (
                                    combination.timeliness
                                    or config.timeliness
                                ),
                            },
                        )
                        continue
                    if policy_id in seen_policy_ids:
                        continue
                    row["query"] = query
                    row["library"] = library
                    row["year"] = year
                    row["partitionKey"] = combination.partition_key
                    row["searchedProvince"] = (
                        combination.province or config.province_for(library)
                    )
                    row["searchedTimeliness"] = (
                        combination.timeliness or config.timeliness
                    )
                    seen_policy_ids.add(policy_id)
                    policy_ids_by_scope.setdefault(
                        (query, library, year),
                        set(),
                    ).add(policy_id)
                    deduped_rows.append(row)
                rows = deduped_rows
                if (
                    adaptive_mode
                    and config.year_selection.mode == "range"
                ):
                    scope_state = adaptive_scope_states.get(
                        (query, library)
                    )
                    if scope_state is not None:
                        collected = scope_state.setdefault(
                            "collected",
                            {},
                        )
                        for row in rows:
                            row_year = (
                                year
                                if combination.action_kind
                                == "year-filter-switch"
                                else publication_year_from_row(row)
                            )
                            if row_year is not None:
                                key = str(row_year)
                                collected[key] = int(
                                    collected.get(key, 0)
                                ) + 1
                stats["resultCount"] = max(
                    int(stats.get("resultCount") or 0),
                    int(state.get("resultCount") or 0),
                )
                stats["rowsRead"] = (
                    int(stats.get("rowsRead") or 0) + len(rows)
                )
                stats["rowsAccepted"] = int(stats.get("rowsAccepted") or 0) + len(rows)
                stats["pagesScanned"] = int(stats.get("pagesScanned") or 0) + 1
                for offset in range(0, len(rows), 50):
                    selected = rows[offset : offset + 50]
                    if not selected:
                        continue
                    identity_text = "|".join(
                        str(row.get("policyId") or "") for row in selected
                    )
                    batch_key = "batch:" + hashlib.sha256(
                        identity_text.encode("utf-8")
                    ).hexdigest()[:20]
                    desired_batches.append(
                        {
                            "batchKey": batch_key,
                            "query": query,
                            "library": library,
                            "year": year,
                            "partitionKey": combination.partition_key,
                            "searchedProvince": (
                                combination.province
                                or config.province_for(library)
                            ),
                            "searchedTimeliness": (
                                combination.timeliness
                                or config.timeliness
                            ),
                            "page": page,
                            "rowStart": int(selected[0]["number"]),
                            "rowEnd": int(selected[-1]["number"]),
                            "selected": selected,
                        }
                    )
                    retrieval_batch_keys.setdefault(
                        (
                            query,
                            library,
                            year,
                            combination.partition_key,
                        ),
                        [],
                    ).append(batch_key)
                scan = {
                    "query": query,
                    "library": library,
                    "year": year,
                    "partitionKey": combination.partition_key,
                    "searchedProvince": (
                        combination.province or config.province_for(library)
                    ),
                    "searchedTimeliness": (
                        combination.timeliness or config.timeliness
                    ),
                    "yearIndex": combination.year_index,
                    "combinationIndex": combination.combination_index,
                    "page": page,
                    "totalPages": total_pages,
                    "rowCount": len(rows),
                    "resultCount": state.get("resultCount"),
                    "url": state.get("url"),
                    "searchId": search_session.search_id,
                    "resultSignature": search_session.result_signature,
                    "reportedTotalPages": search_session.reported_total_pages,
                    "requestedPageSize": config.page_size,
                    "actualPageSize": search_session.actual_page_size,
                    "groupingMode": search_session.grouping_mode,
                    "observedAt": search_session.observed_at,
                }
                journal_record = {
                    "schemaVersion": 1,
                    "configFingerprint": journal.config_fingerprint,
                    "taskId": task_id,
                    "query": query,
                    "library": library,
                    "effectiveYear": year,
                    "partitionKey": combination.partition_key,
                    "selectedProvince": (
                        combination.province or config.province_for(library)
                    ),
                    "selectedTimeliness": (
                        combination.timeliness or config.timeliness
                    ),
                    "page": page,
                    "totalPages": total_pages,
                    "reportedTotalPages": (
                        search_session.reported_total_pages
                    ),
                    "resultCount": int(state.get("resultCount") or 0),
                    "requestedPageSize": config.page_size,
                    "actualPageSize": search_session.actual_page_size,
                    "groupingMode": search_session.grouping_mode,
                    "searchId": search_session.search_id,
                    "resultSignature": search_session.result_signature,
                    "observedAt": search_session.observed_at,
                    "rows": rows,
                }
                if resume_record is not None:
                    journal_record.update(
                        {
                            "resumedFrom": str(
                                resume_record.get("searchId") or ""
                            ),
                            "resumeEvidence": {
                                "page": int(
                                    resume_record.get("page") or 0
                                ),
                                "searchId": str(
                                    resume_record.get("searchId") or ""
                                ),
                                "resultSignature": str(
                                    resume_record.get(
                                        "resultSignature"
                                    )
                                    or ""
                                ),
                            },
                        }
                    )
                journal.append(journal_record)
                resume_record = None
                scans.append(scan)
                retrieval_pages.setdefault(
                    (
                        query,
                        library,
                        year,
                        combination.partition_key,
                    ),
                    {},
                )[page] = {
                    "page": page,
                    "searchId": search_session.search_id,
                    "resultSignature": search_session.result_signature,
                    "searchIdentity": search_session.to_dict(),
                }
                self._emit(
                    "batch.page-scanned",
                    {
                        "taskId": task_id,
                        **scan,
                        "yearStats": list(year_stats.values()),
                    },
                )
                scan_snapshot = build_progress_snapshot(
                    stage="scan",
                    overall=batch_scan_overall(
                        combination.combination_index,
                        combination_count,
                        page,
                        total_pages,
                    ),
                    completed_units=len(scans),
                    total_units=max(
                        len(scans),
                        total_pages * combination_count,
                    ),
                )
                self._update_task(
                    store,
                    task_id,
                    progress=scan_snapshot.overall,
                    checkpoint={
                        **store.get_task(task_id).get("checkpoint", {}),
                        "page": page,
                        "currentPage": page,
                        "totalPages": total_pages,
                        "unscannedPages": max(0, total_pages - page),
                        "combinationTotals": list(combination_totals),
                        "currentYear": year,
                        "yearStats": list(year_stats.values()),
                        "searchId": search_session.search_id,
                        "searchIdentity": search_session.to_dict(),
                        "progressSnapshot": scan_snapshot.to_dict(),
                        "scanJournal": journal.checkpoint_summary(),
                        "resultWindowProbe": {
                            **(
                                (
                                    store.get_task(task_id).get(
                                        "checkpoint"
                                    )
                                    or {}
                                ).get("resultWindowProbe")
                                or {}
                            ),
                            "status": (
                                "extended"
                                if page
                                > search_session.reported_total_pages
                                else "complete"
                            ),
                            "page": page,
                            "reportedTotalPages": (
                                search_session.reported_total_pages
                            ),
                            "effectiveTotalPages": total_pages,
                            "resultSignature": (
                                search_session.result_signature
                            ),
                            "observedAt": search_session.observed_at,
                        },
                    },
                )
                if first_result_projection is None:
                    projected_rows_per_combination = max(
                        len(rows),
                        int(state.get("resultCount") or 0),
                    )
                    projected_rows = (
                        projected_rows_per_combination * combination_count
                    )
                    projected_pages = total_pages * combination_count
                    first_result_projection = self._build_scale_estimate(
                        config=config,
                        total_pages=projected_pages,
                        total_rows=projected_rows,
                        total_batches=(
                            projected_rows + 49
                        ) // 50,
                        year_count=len(config.year_selection.years),
                        stage="after_first_results",
                        basis="first_result_projection",
                        confidence="projected",
                        combination_count=combination_count,
                        page_mode=page_mode,
                    )
                    first_result_projection.update(
                        {
                            "sampleCombinationIndex": combination.combination_index,
                            "samplePage": page,
                            "sampleTotalPages": total_pages,
                            "sampleResultCount": int(
                                state.get("resultCount") or len(rows)
                            ),
                            "sampleRowCount": len(rows),
                        }
                    )
                    checkpoint = dict(
                        (store.get_task(task_id) or {}).get("checkpoint")
                        or {}
                    )
                    checkpoint["scaleEstimate"] = first_result_projection
                    self._update_task(
                        store,
                        task_id,
                        checkpoint=checkpoint,
                    )
                    self._emit(
                        "batch.scale-estimate",
                        {
                            "taskId": task_id,
                            "scaleEstimate": first_result_projection,
                        },
                    )
                max_pages = config.page_selection.max_pages
                if (
                    page_mode == "limit"
                    and max_pages is not None
                    and should_pause_for_page_limit(
                        page=page,
                        total_pages=total_pages,
                        max_pages=max_pages,
                    )
                ):
                    page_mode = self._wait_for_page_limit(
                        store=store,
                        controller=controller,
                        task_id=task_id,
                        query=query,
                        library=library,
                        year=year,
                        page=page,
                        total_pages=total_pages,
                        max_pages=max_pages,
                    )
                if (
                    first_result_projection is not None
                    and not first_result_projection_checked
                ):
                    self._confirm_large_scale_if_needed(
                        store=store,
                        controller=controller,
                        task_id=task_id,
                        scale_estimate=first_result_projection,
                    )
                    first_result_projection_checked = True
                if (
                    adaptive_mode
                    and config.year_selection.mode == "range"
                    and combination.action_kind == "keyword-submission"
                ):
                    scope_state = adaptive_scope_states[(query, library)]
                    reported_pages = int(
                        search_session.reported_total_pages
                        or total_pages
                    )
                    if adaptive_truncated and page >= reported_pages:
                        self._append_adaptive_missing_year_combinations(
                            config=config,
                            adapter=adapter,
                            combination=combination,
                            scope_state=scope_state,
                            combinations=combinations,
                            adaptive_missing_years=adaptive_missing_years,
                        )
                        break
                if page >= total_pages:
                    if (
                        adaptive_mode
                        and config.year_selection.mode == "range"
                        and combination.action_kind == "keyword-submission"
                    ):
                        scope_state = adaptive_scope_states[(query, library)]
                        self._append_adaptive_missing_year_combinations(
                            config=config,
                            adapter=adapter,
                            combination=combination,
                            scope_state=scope_state,
                            combinations=combinations,
                            adaptive_missing_years=adaptive_missing_years,
                        )
                    stats["status"] = (
                        "no_result"
                        if int(stats.get("resultCount") or 0) == 0
                        else "completed"
                    )
                    break
                self._safe_boundary(
                    store,
                    controller,
                    task_id,
                    current_action="next-page",
                )
                previous_signature = page_signature(list(state.get("rows") or []))
                previous_page = page
                previous_total_pages = total_pages

                def load_next_page() -> dict[str, Any]:
                    next_state = adapter.go_to_page(page + 1)
                    raw_next_total = next_state.get("totalPages")
                    transition_error = pagination_transition_error(
                        previous_page=previous_page,
                        next_page=int(next_state.get("page") or 0),
                        previous_total_pages=previous_total_pages,
                        next_total_pages=(
                            int(raw_next_total)
                            if raw_next_total is not None
                            else previous_total_pages
                        ),
                        previous_signature=previous_signature,
                        next_signature=page_signature(
                            list(next_state.get("rows") or [])
                        ),
                    )
                    if transition_error:
                        reported_total_pages = int(
                            next_state.get("reportedTotalPages")
                            or previous_total_pages
                        )
                        if (
                            int(next_state.get("page") or 0)
                            > reported_total_pages
                        ):
                            raise ResultWindowTruncated(
                                "来源窗口无法提供第 "
                                f"{int(next_state.get('page') or 0)} 页："
                                f"站点仅声明 {reported_total_pages} 页，"
                                "后续页返回重复结果，必须按年份拆分检索"
                            )
                        raise ManualActionRequired(transition_error)
                    return next_state

                try:
                    state = self._with_manual_retry(
                        store,
                        controller,
                        task_id,
                        load_next_page,
                        action_name="next-page",
                    )
                except ResultWindowTruncated as exc:
                    if (
                        config.year_selection.mode == "all"
                        and year_partition.get("status")
                        not in {"required", "running", "complete"}
                    ):
                        available_years = adapter.available_filter_values(
                            "year"
                        )
                        raise YearPartitionRequired(
                            years=available_years,
                            expected_result_count=int(
                                state.get("resultCount") or 0
                            ),
                            query=query,
                            library=library,
                            reason=(
                                "直接受控探测无法覆盖有效页数；"
                                "需要按真实年份拆分"
                            ),
                        ) from exc
                    raise
                self._safe_boundary(
                    store,
                    controller,
                    task_id,
                    current_action="after-next-page",
                )
                page = int(state.get("page") or page)
                search_session = self._require_search_state(
                    state,
                    config=config,
                    query=query,
                    library=library,
                    year=year,
                    province=combination.province or config.province_for(library),
                    timeliness=(
                        combination.timeliness or config.timeliness
                    ),
                    expected_page=page,
                )

        if year_partition.get("yearCounts"):
            scope_query = str(year_partition.get("query") or "")
            scope_library = str(year_partition.get("library") or "")
            partition_year_set = set(partition_years)
            fallback_count = 0
            for record in journal_records:
                record_year = str(record["effectiveYear"])
                if (
                    str(record["query"]) != scope_query
                    or str(record["library"]) != scope_library
                    or record_year not in partition_year_set
                ):
                    continue
                fallback_rows: list[dict[str, Any]] = []
                for raw_row in record.get("rows") or []:
                    row = dict(raw_row)
                    policy_id = str(row.get("policyId") or "")
                    if policy_id:
                        if policy_id in seen_policy_ids:
                            continue
                        seen_policy_ids.add(policy_id)
                        policy_ids_by_scope.setdefault(
                            (scope_library, record_year),
                            set(),
                        ).add(policy_id)
                    else:
                        identity = keyword_result_identity(row)
                        if identity in pending_review_rows:
                            continue
                        pending_review_rows[identity] = {
                            **row,
                            "query": scope_query,
                            "library": scope_library,
                            "year": record_year,
                        }
                    row["query"] = scope_query
                    row["library"] = scope_library
                    row["year"] = record_year
                    row["partitionKey"] = str(
                        record.get("partitionKey") or ""
                    )
                    row["searchedProvince"] = str(
                        record.get("selectedProvince") or ""
                    )
                    row["searchedTimeliness"] = str(
                        record.get("selectedTimeliness") or ""
                    )
                    fallback_rows.append(row)
                if not fallback_rows:
                    continue
                fallback_count += len(fallback_rows)
                partition_key = str(record.get("partitionKey") or "")
                retrieval_pages.setdefault(
                    (scope_query, scope_library, record_year, partition_key),
                    {},
                )[int(record["page"])] = {
                    "page": int(record["page"]),
                    "searchId": record["searchId"],
                    "resultSignature": record["resultSignature"],
                    "searchIdentity": {
                        "query": scope_query,
                        "library": scope_library,
                        "year": record_year,
                        "partitionKey": partition_key,
                        "page": int(record["page"]),
                        "searchId": record["searchId"],
                        "resultSignature": record["resultSignature"],
                    },
                }
                for offset in range(0, len(fallback_rows), 50):
                    selected = fallback_rows[offset : offset + 50]
                    identity_text = "|".join(
                        str(row.get("policyId") or "")
                        or keyword_result_identity(row)
                        for row in selected
                    )
                    batch_key = "batch:" + hashlib.sha256(
                        identity_text.encode("utf-8")
                    ).hexdigest()[:20]
                    desired_batches.append(
                        {
                            "batchKey": batch_key,
                            "query": scope_query,
                            "library": scope_library,
                            "year": record_year,
                            "partitionKey": partition_key,
                            "searchedProvince": str(
                                record.get("selectedProvince") or ""
                            ),
                            "searchedTimeliness": str(
                                record.get("selectedTimeliness") or ""
                            ),
                            "page": int(record["page"]),
                            "rowStart": int(selected[0]["number"]),
                            "rowEnd": int(selected[-1]["number"]),
                            "selected": selected,
                        }
                    )
                    retrieval_batch_keys.setdefault(
                        (
                            scope_query,
                            scope_library,
                            record_year,
                            partition_key,
                        ),
                        [],
                    ).append(batch_key)
                scans.append(self._scan_from_journal_record(record))
                fallback_key = (
                    f"fallback:{scope_query}:{scope_library}:{record_year}"
                )
                fallback_stats = year_stats.setdefault(
                    fallback_key,
                    {
                        "query": scope_query,
                        "library": scope_library,
                        "year": record_year,
                        "partitionKey": partition_key,
                        "status": "completed",
                        "resultCount": int(record.get("resultCount") or 0),
                        "rowsRead": 0,
                        "rowsAccepted": 0,
                        "pagesScanned": 0,
                    },
                )
                fallback_stats["rowsRead"] = (
                    int(fallback_stats.get("rowsRead") or 0)
                    + len(fallback_rows)
                )
                fallback_stats["rowsAccepted"] = (
                    int(fallback_stats.get("rowsAccepted") or 0)
                    + len(fallback_rows)
                )
                fallback_stats["pagesScanned"] = (
                    int(fallback_stats.get("pagesScanned") or 0) + 1
                )
            if fallback_count:
                self.logger.write(
                    "info",
                    "batch.year-partition-journal-supplement",
                    "Supplemented batch year partitions from accepted journal rows",
                    {
                        "query": scope_query,
                        "library": scope_library,
                        "fallbackRows": fallback_count,
                        "downloadsStarted": 0,
                    },
                )

        if (
            config.year_selection.mode == "all"
            and year_partition.get("status")
            in {"required", "running", "complete"}
        ):
            expected_results = int(
                year_partition.get("expectedResultCount") or 0
            )
            partition_stats = [
                stats
                for stats in year_stats.values()
                if (
                    str(stats.get("query") or "")
                    == str(year_partition.get("query") or "")
                    and str(stats.get("library") or "")
                    == str(year_partition.get("library") or "")
                    and str(stats.get("year") or "")
                    in set(partition_years)
                )
            ]
            actual_results = sum(
                int(stats.get("resultCount") or 0)
                for stats in partition_stats
            )
            actual_rows = sum(
                int(stats.get("rowsRead") or 0)
                for stats in partition_stats
            )
            year_counts = {
                str(year): int(count or 0)
                for year, count in dict(
                    year_partition.get("yearCounts") or {}
                ).items()
            }
            scope_query = str(year_partition.get("query") or "")
            scope_library = str(year_partition.get("library") or "")
            scope_province = config.province_for(scope_library)
            current_session = getattr(
                adapter,
                "current_search_session",
                None,
            )
            if (
                year_counts
                and (
                    current_session is None
                    or str(current_session.query or "") != scope_query
                    or str(current_session.library or "") != scope_library
                    or str(current_session.year or "") != "全部年份"
                    or str(current_session.province or "")
                    != scope_province
                )
            ):
                state = adapter.search(
                    keyword=scope_query,
                    match_mode=config.match_mode,
                    library=scope_library,
                    year="全部年份",
                    province=scope_province,
                    timeliness=config.timeliness,
                    page_size=config.page_size,
                )
                current_session = SearchSession.from_dict(
                    state.get("searchSession")
                )
                if (
                    current_session is None
                    or current_session.query != scope_query
                    or current_session.library != scope_library
                    or current_session.year != "全部年份"
                    or current_session.province != scope_province
                ):
                    raise ResultWindowTruncated(
                        "无法建立批量最终年份核验所需的受控检索上下文"
                    )
            current_year_counts = (
                {
                    str(entry["value"]): int(entry.get("count") or 0)
                    for entry in adapter.available_filter_entries("year")
                    if int(entry.get("count") or 0) > 0
                    and str(entry["value"]) in set(partition_years)
                }
                if year_counts
                and hasattr(adapter, "available_filter_entries")
                else {}
            )
            if year_counts and current_year_counts != year_counts:
                raise ResultWindowTruncated(
                    "批量年份结果集在完整扫描期间再次变化："
                    f"计划 {year_counts}，当前 {current_year_counts}"
                )
            journal_policy_ids = {
                year: set()
                for year in partition_years
            }
            for record in journal.read():
                if (
                    str(record["query"]) != scope_query
                    or str(record["library"]) != scope_library
                ):
                    continue
                record_year = str(record["effectiveYear"])
                if record_year not in journal_policy_ids:
                    continue
                for row in record.get("rows") or []:
                    policy_id = str(row.get("policyId") or "")
                    if policy_id:
                        journal_policy_ids[record_year].add(policy_id)
            actual_unique = sum(
                len(journal_policy_ids[year])
                for year in partition_years
            )
            year_mismatches = {
                year: {
                    "expected": expected,
                    "actual": len(journal_policy_ids[year]),
                }
                for year, expected in year_counts.items()
                if len(journal_policy_ids[year]) != expected
            }
            totals_valid = (
                not year_counts
                and actual_results == expected_results
                and actual_rows == expected_results
            ) or (
                bool(year_counts)
                and not year_mismatches
            )
            if not totals_valid:
                raise ResultWindowTruncated(
                    "批量按年份拆分后结果数不一致："
                    f"期望 {expected_results}，"
                    f"结果数合计 {actual_results}，"
                    f"实际读取 {actual_rows}，"
                    f"去重后 {actual_unique}，"
                    f"年份差异 {year_mismatches}"
                )
            year_partition.update(
                {
                    "status": "complete",
                    "actualResultCount": (
                        sum(year_counts.values())
                        if year_counts
                        else actual_results
                    ),
                    "actualRowsRead": actual_rows,
                    "actualUniqueResultCount": len(
                        {
                            policy_id
                            for year in partition_years
                            for policy_id in journal_policy_ids[year]
                        }
                    ),
                    "yearMismatches": year_mismatches,
                    "sourceUniverseDrift": (
                        expected_results
                        - sum(year_counts.values())
                        if year_counts
                        else 0
                    ),
                    "crossYearDuplicateCount": (
                        actual_unique
                        - len(
                            {
                                policy_id
                                for year in partition_years
                                for policy_id in journal_policy_ids[year]
                            }
                        )
                        if year_counts
                        else 0
                    ),
                    "currentYearCounts": current_year_counts,
                    "completedCombinations": len(partition_stats),
                }
            )

        scale_estimate = self._build_scale_estimate(
            config=config,
            total_pages=sum(
                int(item.get("pagesScanned") or 0)
                for item in year_stats.values()
            ),
            total_rows=sum(
                int(item.get("rowsAccepted") or 0)
                for item in year_stats.values()
            ),
            total_batches=len(desired_batches),
            year_count=len({item["year"] for item in year_stats.values()}),
            stage="after_scan_complete",
            basis="actual_scan",
            confidence="actual",
            combination_count=combination_count,
            page_mode=page_mode,
        )
        self._confirm_large_scale_if_needed(
            store=store,
            controller=controller,
            task_id=task_id,
            scale_estimate=scale_estimate,
        )

        existing = {batch["batchKey"]: batch for batch in store.list_task_batches(task_id)}
        desired_keys = {batch["batchKey"] for batch in desired_batches}
        for batch in desired_batches:
            prior = existing.get(batch["batchKey"])
            if prior:
                prior_ids = [str(row.get("policyId") or "") for row in prior["selected"]]
                current_ids = [str(row.get("policyId") or "") for row in batch["selected"]]
                if prior_ids != current_ids:
                    raise ValidationError(
                        "BATCH_CHECKPOINT_IDENTITY_MISMATCH",
                        "恢复断点的批次政策 ID 与当前检索结果不一致",
                    )
                continue
            store.upsert_task_batch(
                task_id,
                batch_key=batch["batchKey"],
                query=batch["query"],
                library=batch["library"],
                page=batch["page"],
                row_start=batch["rowStart"],
                row_end=batch["rowEnd"],
                status="pending",
                progress=0,
                catalog_format=config.catalog_format,
                full_format=config.full_format,
                selected=batch["selected"],
            )
            for row in batch["selected"]:
                store.upsert_task_item(
                    task_id,
                    policy_key=f"id:{str(row['policyId']).lower()}",
                    title=str(row.get("title") or ""),
                    status="pending",
                    payload={"candidate": row, "batchKey": batch["batchKey"]},
                    current_stage="pending",
                )

        for identity, row in pending_review_rows.items():
            store.upsert_task_item(
                task_id,
                policy_key=identity,
                title=str(row.get("title") or ""),
                status="pending_review",
                source_type="待核验",
                payload={
                    "candidate": row,
                    "identityStatus": "pending_review",
                    "identityError": {
                        "code": "STABLE_POLICY_ID_MISSING",
                        "message": "结果行缺少稳定政策 ID，未创建批次或下载",
                    },
                },
                error={
                    "code": "STABLE_POLICY_ID_MISSING",
                    "message": "结果行缺少稳定政策 ID，未创建批次或下载",
                },
                current_stage="pending-review",
            )

        stale_incomplete = [
            batch for key, batch in existing.items()
            if key not in desired_keys and batch["status"] != "completed"
        ]
        if stale_incomplete:
            raise ValidationError(
                "BATCH_RESULTS_CHANGED",
                "当前检索结果不再包含未完成断点中的批次，停止以避免跳过政策",
            )
        retrieval_contexts = [
            {
                "query": query,
                "library": library,
                "year": year,
                "partitionKey": partition_key,
                "scopeKey": (
                    f"{year}|{partition_key}"
                    if partition_key
                    else year
                ),
                "searchId": pages[min(pages)]["searchId"],
                "pages": [
                    pages[page]
                    for page in sorted(pages)
                ],
                "batchKeys": retrieval_batch_keys.get(
                    (query, library, year, partition_key),
                    [],
                ),
                "lastPage": max(pages),
            }
            for (
                query,
                library,
                year,
                partition_key,
            ), pages in retrieval_pages.items()
            if pages
        ]
        if adaptive_mode:
            adaptive_checkpoint = self._adaptive_search_checkpoint(
                governor=governor,
                adaptive_scope_states=adaptive_scope_states,
                config=config,
                boundary=adaptive_boundary,
            )
        else:
            adaptive_checkpoint = {}
        prepared_snapshot = build_progress_snapshot(
            stage="catalog",
            overall=0.20,
            completed_units=0,
            total_units=len(desired_batches),
            succeeded=0,
            failed=0,
            pending_review=0,
        )
        journal_summary = journal.seal()
        self._update_task(
            store,
            task_id,
            status="running",
            current_stage="batches-prepared",
            progress=prepared_snapshot.overall,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "scannedPages": len(scans),
                "batchCount": len(desired_batches),
                "yearStats": list(year_stats.values()),
                "scaleEstimate": scale_estimate,
                "retrievalContexts": retrieval_contexts,
                "scanJournal": journal_summary,
                "scanJournalSealed": True,
                "scanCompleted": True,
                **(
                    {"adaptiveSearch": adaptive_checkpoint}
                    if adaptive_checkpoint
                    else {}
                ),
                **(
                    {"yearPartition": year_partition}
                    if year_partition
                    else {}
                ),
                **(
                    {
                        "resultWindowProbe": {
                            **dict(
                                (
                                    store.get_task(task_id) or {}
                                )
                                .get("checkpoint", {})
                                .get("resultWindowProbe")
                                or {}
                            ),
                            "status": "partitioned",
                            "code": "",
                            "actualResultCount": int(
                                year_partition.get(
                                    "actualResultCount"
                                )
                                or 0
                            ),
                            "completedAt": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(),
                            ),
                        }
                    }
                    if year_partition.get("status") == "complete"
                    else {}
                ),
                "progressSnapshot": prepared_snapshot.to_dict(),
            },
        )
        return scans

    def _resolve_year_window_metadata(
        self,
        task_id: str,
        config: BatchTaskConfig,
        adapter: PkulawAdapter,
        store: TaskStore,
        controller: TaskControl,
        checkpoint: dict[str, Any],
        libraries: list[str],
    ) -> dict[str, Any]:
        stored_keys = (
            "yearWindows",
            "originalYearSelection",
            "effectiveYearSelection",
            "yearWindowAdjustment",
        )
        requested = config.year_selection.to_payload()["yearSelection"]
        window_source = (
            checkpoint
            if all(key in checkpoint for key in stored_keys)
            else (
                checkpoint.get("previewSnapshot")
                if isinstance(checkpoint.get("previewSnapshot"), dict)
                else checkpoint
            )
        )
        stored_windows = window_source.get("yearWindows")
        stored_matches_request = (
            isinstance(stored_windows, list)
            and bool(stored_windows)
            and all(
                isinstance(item, dict)
                and item.get("originalYearSelection") == requested
                for item in stored_windows
            )
        )
        if (
            stored_matches_request
            and all(key in window_source for key in stored_keys)
        ):
            metadata = {
                key: window_source[key]
                for key in stored_keys
            }
            expand_search_combinations_for_year_windows(
                config.queries,
                libraries,
                metadata,
            )
            if not all(key in checkpoint for key in stored_keys):
                self._update_task(
                    store,
                    task_id,
                    current_stage="year-window-normalization",
                    clear_error=True,
                    checkpoint={
                        **(
                            store.get_task(task_id).get("checkpoint")
                            or {}
                        ),
                        **metadata,
                        "currentAction": "year-window-normalized",
                    },
                )
            return metadata

        scopes = []
        for query_index, query in enumerate(config.queries):
            for library_index, library in enumerate(libraries):
                state = self._with_manual_retry(
                    store,
                    controller,
                    task_id,
                    lambda query=query, library=library: adapter.search(
                        keyword=query,
                        match_mode=config.match_mode,
                        library=library,
                        year=ALL_YEARS_LABEL,
                        province=config.province_for(library),
                        timeliness=config.timeliness,
                        page_size=config.page_size,
                    ),
                    action_name="inspect-year-window",
                )
                self._require_search_state(
                    state,
                    config=config,
                    query=query,
                    library=library,
                    year=ALL_YEARS_LABEL,
                    expected_page=1,
                )
                entries = adapter.available_filter_entries("year")
                values = [
                    str(entry.get("value") or "").strip()
                    for entry in entries
                    if str(entry.get("value") or "").strip()
                ]
                try:
                    window = effective_year_window(
                        config.year_selection,
                        values,
                    )
                except ValidationError as exc:
                    error = YearFilterUnavailable(str(exc))
                    error.code = exc.code
                    raise error from exc
                scopes.append(
                    (
                        query_index,
                        library_index,
                        query,
                        library,
                        window,
                    )
                )
        metadata = build_year_window_checkpoint(scopes)
        self._update_task(
            store,
            task_id,
            current_stage="year-window-normalization",
            clear_error=True,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                **metadata,
                "currentAction": "year-window-normalized",
            },
        )
        return metadata

    def _scan_journal(
        self,
        task_id: str,
        config: BatchTaskConfig,
    ) -> ScanJournal:
        return ScanJournal(
            self.database_path.parent
            / "cache"
            / "task-scans"
            / f"{safe_filename(task_id, 80)}.jsonl",
            task_id=task_id,
            config_fingerprint=config_fingerprint(
                self._config_payload(config)
            ),
        )

    def _restart_scan_for_changed_universe(
        self,
        *,
        store: TaskStore,
        task_id: str,
        journal: ScanJournal,
        previous: dict[str, Any],
        current: SearchSession,
        reason: str,
    ) -> Path:
        if (
            store.list_task_items(task_id)
            or store.list_task_batches(task_id)
        ):
            raise ScanJournalError(
                "SCAN_JOURNAL_IDENTITY_MISMATCH",
                "批量结果集已变化但任务已有正式任务项或批次；禁止自动重扫",
            )
        archive_path = journal.quarantine(
            f"RESULT_UNIVERSE_CHANGED:{reason}"
        )
        checkpoint = dict(
            (store.get_task(task_id) or {}).get("checkpoint") or {}
        )
        history = list(checkpoint.get("scanUniverseChanges") or [])
        history.append(
            {
                "reason": reason,
                "previousPage": int(previous.get("page") or 0),
                "previousSearchId": str(
                    previous.get("searchId") or ""
                ),
                "previousResultSignature": str(
                    previous.get("resultSignature") or ""
                ),
                "previousResultCount": int(
                    previous.get("resultCount") or 0
                ),
                "currentSearchId": current.search_id,
                "currentResultSignature": current.result_signature,
                "currentResultCount": current.result_count,
                "archivedJournal": str(archive_path),
                "observedAt": current.observed_at,
                "downloadsStarted": 0,
            }
        )
        checkpoint.update(
            {
                "currentAction": "scan-universe-restarted",
                "scanUniverseChanges": history,
                "searchId": current.search_id,
                "searchIdentity": current.to_dict(),
            }
        )
        self._update_task(
            store,
            task_id,
            status="running",
            current_stage="scanning-new-result-universe",
            clear_error=True,
            checkpoint=checkpoint,
        )
        self._emit(
            "batch.scan-universe-restarted",
            {
                "taskId": task_id,
                "reason": reason,
                "archivedJournal": str(archive_path),
                "previousResultSignature": str(
                    previous.get("resultSignature") or ""
                ),
                "currentResultSignature": current.result_signature,
                "previousResultCount": int(
                    previous.get("resultCount") or 0
                ),
                "currentResultCount": current.result_count,
                "downloadsStarted": 0,
            },
        )
        return archive_path

    @staticmethod
    def _ensure_processing_selector_profile(
        adapter: PkulawAdapter,
    ) -> None:
        if getattr(adapter, "selectors", None):
            return
        try:
            diagnostics = adapter.diagnose_current_page()
        except Exception as exc:
            raise SelectorProfileMismatch(
                f"恢复处理前无法核验当前页面选择器：{exc}"
            ) from exc
        if (
            not diagnostics.get("detected")
            or not diagnostics.get("profileId")
            or not getattr(adapter, "selectors", None)
        ):
            raise SelectorProfileMismatch(
                "恢复处理前当前页面未通过选择器身份核验"
            )

    @staticmethod
    def _restore_adapter_search_context(
        adapter: PkulawAdapter,
        store: TaskStore,
        task_id: str,
        config: BatchTaskConfig,
    ) -> None:
        task = store.get_task(task_id)
        checkpoint = dict((task or {}).get("checkpoint") or {})
        raw_session = checkpoint.get("searchIdentity")
        if not isinstance(raw_session, dict):
            return
        try:
            session = SearchSession.from_dict(raw_session)
        except (KeyError, TypeError, ValueError):
            return
        request = SearchRequestIdentity(
            query=session.query,
            match_mode=session.match_mode or config.match_mode,
            library=session.library,
            year=session.year,
            province=session.province,
            timeliness=session.timeliness,
            requested_page_size=(
                session.requested_page_size or config.page_size
            ),
        )
        adapter.current_search_request = request
        live_session = getattr(adapter, "current_search_session", None)
        if (
            live_session is None
            or not live_session.matches_request(
                request,
                expected_page=live_session.page,
                require_refinements=True,
            )
            or live_session.result_signature != session.result_signature
        ):
            adapter.current_search_session = None
            adapter.search_url = ""
            return
        adapter.search_url = session.url

    def _resolve_year_partition_plan(
        self,
        adapter: PkulawAdapter,
        config: BatchTaskConfig,
        year_partition: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        existing_counts = {
            str(year): int(count or 0)
            for year, count in dict(
                year_partition.get("yearCounts") or {}
            ).items()
        }
        expected_scope_count = int(
            year_partition.get("expectedResultCount") or 0
        )
        existing_plan_plausible = bool(existing_counts) and (
            expected_scope_count <= 0
            or sum(existing_counts.values())
            <= expected_scope_count
            + max(100, int(expected_scope_count * 0.1))
        )
        if (
            config.year_selection.mode != "all"
            or year_partition.get("status") not in {"required", "running"}
            or existing_plan_plausible
            or not hasattr(adapter, "available_filter_entries")
        ):
            return year_partition
        scope_query = str(year_partition.get("query") or "")
        scope_library = str(year_partition.get("library") or "")
        scope_province = config.province_for(scope_library)
        current_session = getattr(adapter, "current_search_session", None)
        if (
            hasattr(adapter, "search")
            and (
                current_session is None
                or str(current_session.library or "") != scope_library
                or str(current_session.year or "") != "全部年份"
                or str(current_session.province or "")
                != scope_province
            )
        ):
            state = adapter.search(
                keyword=scope_query,
                match_mode=config.match_mode,
                library=scope_library,
                year="全部年份",
                province=scope_province,
                timeliness=config.timeliness,
                page_size=config.page_size,
            )
            session = SearchSession.from_dict(state.get("searchSession"))
            if (
                session is None
                or session.query != scope_query
                or session.library != scope_library
                or session.year != "全部年份"
                or session.province != scope_province
            ):
                raise ResultWindowTruncated(
                    "无法建立批量年份窗口计划所需的受控检索上下文"
                )
        try:
            year_entries = adapter.available_filter_entries("year")
            province_entries = adapter.available_filter_entries("province")
        except Exception as exc:
            raise ResultWindowTruncated(
                "批量年份窗口仍超过来源上限，但无法读取省市筛选树："
                f"{exc}"
            ) from exc
        year_counts = {
            str(entry["value"]): int(entry.get("count") or 0)
            for entry in year_entries
            if int(entry.get("count") or 0) > 0
        }
        province_values = [
            str(entry["value"])
            for entry in province_entries
            if int(entry.get("count") or 0) > 0
            and not str(entry["value"]).startswith("全部")
        ]
        provinces = list(dict.fromkeys(province_values))
        result_window = dict(checkpoint.get("resultWindow") or {})
        reported_pages = max(
            1,
            int(result_window.get("reportedTotalPages") or 20),
        )
        capacity = max(
            1,
            int(
                year_partition.get("partitionPageCapacity")
                or int(config.page_size) * reported_pages
            ),
        )
        province_partitions = {
            year: provinces
            for year, count in year_counts.items()
            if count > capacity and provinces
        }
        updated = {
            **year_partition,
            "status": "running",
            "yearCounts": year_counts,
            "provincePartitions": province_partitions,
            "partitionPageCapacity": capacity,
            "reportedTotalPages": reported_pages,
        }
        if not province_partitions:
            return updated
        self.logger.write(
            "info",
            "batch.year-window-partitioned",
            "Batch year result windows split by issuing authority",
            {
                "capacity": capacity,
                "provinceCount": len(provinces),
                "years": sorted(province_partitions),
                "downloadsStarted": 0,
            },
        )
        return updated

    @staticmethod
    def _year_partition_combinations(
        config: BatchTaskConfig,
        libraries: list[str],
        years: list[str],
        *,
        scope_query: str,
        scope_library: str,
        province_partitions: dict[str, list[str]],
    ) -> list[SearchCombination]:
        combinations: list[SearchCombination] = []
        for query_index, query in enumerate(config.queries):
            for library_index, library in enumerate(libraries):
                scoped = (
                    query == scope_query and library == scope_library
                )
                combination_years = (
                    years
                    if scoped
                    else [config.year_selection.years[0]]
                )
                for year_index, year in enumerate(combination_years):
                    provinces = (
                        province_partitions.get(year)
                        if scoped
                        else None
                    )
                    for province in provinces or [""]:
                        combinations.append(
                            SearchCombination(
                                query=query,
                                library=library,
                                year=year,
                                query_index=query_index,
                                library_index=library_index,
                                year_index=year_index,
                                combination_index=(
                                    len(combinations) + 1
                                ),
                                province=province,
                                partition_key=(
                                    f"year={year}|province={province}"
                                    if province
                                    else ""
                                ),
                            )
                        )
        return combinations

    @staticmethod
    def _scan_from_journal_record(
        record: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "query": record["query"],
            "library": record["library"],
            "year": record["effectiveYear"],
            "partitionKey": str(record.get("partitionKey") or ""),
            "searchedProvince": str(
                record.get("selectedProvince") or ""
            ),
            "searchedTimeliness": str(
                record.get("selectedTimeliness") or ""
            ),
            "page": record["page"],
            "totalPages": record["totalPages"],
            "reportedTotalPages": record.get("reportedTotalPages"),
            "resultCount": record["resultCount"],
            "rowCount": len(record.get("rows") or []),
            "searchId": record["searchId"],
            "resultSignature": record["resultSignature"],
            "requestedPageSize": record["requestedPageSize"],
            "actualPageSize": record["actualPageSize"],
            "groupingMode": record["groupingMode"],
            "observedAt": record["observedAt"],
            "restoredFromJournal": True,
        }

    def _verify_batch_page(
        self,
        store: TaskStore,
        task_id: str,
        batch: dict[str, Any],
        selected: list[dict[str, Any]],
        config: BatchTaskConfig,
        adapter: PkulawAdapter,
        expected_session: SearchSession,
    ) -> SearchSession:
        state = adapter.read_results()
        session = self._require_search_state(
            state,
            config=config,
            query=batch["query"],
            library=batch["library"],
            year=str(
                batch.get("year")
                or (selected[0].get("year") if selected else "")
            ),
            province=str(
                batch.get("searchedProvince")
                or (
                    selected[0].get("searchedProvince")
                    if selected
                    else ""
                )
                or config.province_for(str(batch["library"]))
            ),
            timeliness=str(
                batch.get("searchedTimeliness")
                or (
                    selected[0].get("searchedTimeliness")
                    if selected
                    else ""
                )
                or config.timeliness
            ),
            expected_page=int(batch["page"]),
        )
        if (
            session.search_id != expected_session.search_id
            or session.result_signature != expected_session.result_signature
        ):
            raise BatchExecutionContextError(
                "BATCH_RESULTS_CHANGED",
                "目录/ZIP 步骤前页面 SearchSession 或结果签名发生变化",
            )
        verification = adapter.verify_batch_selection(selected)
        if int(verification.get("page") or 0) != int(batch["page"]):
            raise BatchExecutionContextError(
                "BATCH_RESULTS_CHANGED",
                "批次行核验页码与目标批次页码不一致",
            )
        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "batchIdentityVerification": {
                    "batchKey": batch["batchKey"],
                    "searchId": session.search_id,
                    "resultSignature": session.result_signature,
                    "page": session.page,
                    "rowStart": batch["rowStart"],
                    "rowEnd": batch["rowEnd"],
                    "checkedCount": len(verification.get("checked") or []),
                    "expectedCount": int(
                        verification.get("expectedCount") or 0
                    ),
                    "actualCount": int(
                        verification.get("actualCount") or 0
                    ),
                },
            },
        )
        self._emit(
            "batch.identity-verified",
            {
                "taskId": task_id,
                "batchKey": batch["batchKey"],
                "searchId": session.search_id,
                "page": session.page,
                "rowStart": batch["rowStart"],
                "rowEnd": batch["rowEnd"],
                "checkedCount": len(verification.get("checked") or []),
            },
        )
        return session

    @staticmethod
    def _catalog_event_error(
        event: dict[str, Any],
        intent: dict[str, Any],
    ) -> CatalogFormatError:
        actual_file = str(event.get("actualFile") or "")
        actual_suffix = str(event.get("actualSuffix") or "")
        actual_signature = str(event.get("actualSignature") or "")
        actual_path = str(event.get("actualPath") or "")
        reason = str(event.get("reason") or "")
        recovery = str(
            event.get("recovery")
            or "重新触发网站原生目录下载并确认 catalogExcel 只返回 BIFF .xls"
        )
        details = {
            "intentId": intent["intentId"],
            "downloadId": str(event.get("downloadId") or ""),
            "actualFile": actual_file,
            "actualSuffix": actual_suffix,
            "actualSignature": actual_signature,
            "actualPath": actual_path,
            "expectedSuffixes": [".xls"],
            "expectedSignature": "d0cf11e0a1b11ae1",
            "reason": reason,
            "recovery": recovery,
        }
        return CatalogFormatError(
            str(
                event.get("errorCode")
                or "CATALOG_NATIVE_FORMAT_MISMATCH"
            ),
            "网站原生目录格式不符合固定契约。"
            f"intentId={details['intentId']}，"
            f"downloadId={details['downloadId'] or '<未知>'}，"
            f"实际文件={actual_file or '<未知>'}，"
            f"后缀={actual_suffix or '<未知>'}，"
            f"签名={actual_signature or '<不可读>'}，"
            f"路径={actual_path or '<未知>'}，"
            f"原因={reason or '<未知>'}；"
            f"恢复建议：{recovery}",
            details=details,
        )

    def _set_catalog_validation_failure(
        self,
        store: TaskStore,
        task_id: str,
        intent: dict[str, Any],
        error: CatalogFormatError,
    ) -> None:
        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadIntent": {
                    **intent,
                    "downloadId": error.details.get("downloadId"),
                    "path": error.details.get("actualPath"),
                    "status": "mismatch",
                    "matchedDownloads": 0,
                },
                "currentDownloadStatus": "validation_failed",
                "downloadValidationDetail": str(error),
                "downloadValidationDetails": error.details,
            },
        )
        self.browser_bridge.record_download_intent_validation(
            str(intent["intentId"]),
            valid=False,
            actual_kind=str(error.details.get("actualSuffix") or ""),
            detail=str(error),
        )

    def _catalog_preparation_error(
        self,
        exc: Exception,
        event: dict[str, Any],
        intent: dict[str, Any],
        requested_format: str,
        native_path: Path,
        prepared_path: Path,
    ) -> CatalogFormatError:
        try:
            signature = native_path.read_bytes()[:16].hex()
        except OSError:
            signature = ""
        if isinstance(exc, CatalogFormatError):
            code = exc.code
            details = {
                **exc.details,
                "intentId": intent["intentId"],
                "downloadId": str(event.get("downloadId") or ""),
                "requestedFormat": requested_format,
                "actualFile": native_path.name,
                "actualSuffix": native_path.suffix.lower(),
                "actualSignature": signature,
                "actualPath": str(native_path),
                "preparedPath": str(prepared_path),
            }
            recovery = str(
                details.get("recovery")
                or exc.recovery
            )
            details["recovery"] = recovery
            return CatalogFormatError(
                code,
                f"{exc}；intentId={intent['intentId']}，"
                f"downloadId={event.get('downloadId') or '<未知>'}，"
                f"实际文件={native_path.name or '<未知>'}，"
                f"后缀={native_path.suffix.lower() or '<无>'}，"
                f"签名={signature or '<不可读>'}，"
                f"路径={native_path}，恢复建议={recovery}",
                details=details,
            )
        details = {
            "intentId": intent["intentId"],
            "downloadId": str(event.get("downloadId") or ""),
            "requestedFormat": requested_format,
            "actualFile": native_path.name,
            "actualSuffix": native_path.suffix.lower(),
            "actualSignature": signature,
            "actualPath": str(native_path),
            "preparedPath": str(prepared_path),
            "reason": str(exc),
            "recovery": "保留网站原生 XLS，检查目录内容与本地转换器后重试；未通过前不会启动 ZIP",
        }
        return CatalogFormatError(
            "CATALOG_CONVERSION_FAILED",
            "目录派生失败。"
            f"intentId={details['intentId']}，"
            f"downloadId={details['downloadId'] or '<未知>'}，"
            f"实际文件={details['actualFile'] or '<未知>'}，"
            f"后缀={details['actualSuffix'] or '<无>'}，"
            f"签名={details['actualSignature'] or '<不可读>'}，"
            f"路径={details['actualPath']}，"
            f"原因={details['reason']}；"
            f"恢复建议：{details['recovery']}",
            details=details,
        )

    def _process_batch(
        self,
        store: TaskStore,
        task_id: str,
        batch: dict[str, Any],
        index: int,
        total: int,
        config: BatchTaskConfig,
        adapter: PkulawAdapter,
        controller: TaskControl,
        search_session: SearchSession,
    ) -> None:
        selected = list(batch["selected"])
        batch_year = str(
            selected[0].get("year")
            if selected
            else ""
        )
        batch_label = (
            f"{'中央政策' if batch['library'] == 'central' else '地方政策'}"
            f"_{batch_year or '全部年份'}"
            f"_{batch['page']}_{batch['rowStart']:03d}-{batch['rowEnd']:03d}"
        )
        library_directory = (
            "中央法规" if batch["library"] == "central" else "地方法规"
        )
        final_dir = (
            config.output_dir
            / "batch_files"
            / library_directory
            / safe_filename(batch_label)
        )
        catalog_contract = resolve_catalog_format(config.catalog_format)
        catalog_name = f"目录{catalog_contract.final_suffix}"
        zip_name = "全文.zip"
        report_name = "验收报告.json"
        final_catalog = final_dir / catalog_name
        final_zip = final_dir / zip_name
        final_report = final_dir / report_name
        if final_dir.exists():
            published = verify_published_directory(
                final_dir,
                batch_key=batch["batchKey"],
            )
            if published is None:
                raise AtomicPublishError(
                    "TARGET_EXISTS",
                    f"正式批次目录已存在且无法核验：{final_dir}",
                )
            report_payload = dict(
                (published.get("report") or {}).get("payload") or {}
            )
            expected_ids = [
                str(row.get("policyId") or "").lower()
                for row in selected
            ]
            report_ids = [
                str(value).lower()
                for value in report_payload.get("policyIds", [])
            ]
            if expected_ids != report_ids:
                raise AtomicPublishError(
                    "PUBLISH_IDENTITY_MISMATCH",
                    "已发布批次目录与当前批次政策 ID 不一致",
                )
            self._emit(
                "batch.publication-recovered",
                {
                    "taskId": task_id,
                    "batchKey": batch["batchKey"],
                    "directory": str(final_dir),
                },
            )
            self._register_completed_batch(
                store=store,
                task_id=task_id,
                batch=batch,
                selected=selected,
                index=index,
                total=total,
                batch_year=batch_year,
                controller=controller,
                final_catalog=final_catalog,
                final_zip=final_zip,
                final_report=final_report,
                report_payload=report_payload,
                search_session=search_session,
                recovered=True,
            )
            return
        batch_snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage="select",
            overall=batch_batch_overall(index, total, "select"),
        )
        self._update_task(
            store,
            task_id,
            status="running",
            progress=batch_snapshot.overall,
            current_stage=f"batch-{index}-{total}",
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentBatchKey": batch["batchKey"],
                "currentBatchIndex": index,
                "batchCount": total,
                "currentQuery": batch["query"],
                "currentLibrary": batch["library"],
                "currentYear": batch_year,
                "currentPage": batch["page"],
                "currentRowStart": batch["rowStart"],
                "currentRowEnd": batch["rowEnd"],
                "currentAction": "select-batch",
                "searchId": search_session.search_id,
                "searchIdentity": search_session.to_dict(),
                "progressSnapshot": batch_snapshot.to_dict(),
            },
        )
        self._safe_boundary(
            store, controller, task_id, current_action="select-batch"
        )
        store.upsert_task_batch(
            task_id,
            batch_key=batch["batchKey"],
            query=batch["query"],
            library=batch["library"],
            page=batch["page"],
            row_start=batch["rowStart"],
            row_end=batch["rowEnd"],
            status="running",
            progress=0.05,
            catalog_format=batch["catalogFormat"],
            full_format=batch["fullFormat"],
            selected=selected,
        )
        self._set_batch_items_status(store, task_id, selected, "running", "batch-running")
        self._with_manual_retry(
            store,
            controller,
            task_id,
            lambda: adapter.select_batch(selected),
            action_name="select-batch",
        )
        self._verify_batch_page(
            store,
            task_id,
            batch,
            selected,
            config,
            adapter,
            search_session,
        )
        self._safe_boundary(
            store, controller, task_id, current_action="after-select-batch"
        )
        search_session = self._verify_batch_page(
            store,
            task_id,
            batch,
            selected,
            config,
            adapter,
            search_session,
        )

        catalog_intent = self.browser_bridge.create_download_intent(
            task_id=task_id,
            kind="batch-catalog",
            source_url=adapter.search_url,
            expected_suffixes=catalog_contract.source_suffixes,
        )
        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadIntent": catalog_intent,
                "currentDownloadStatus": "pending",
                "currentAction": "catalog-download",
            },
        )
        catalog_cursor = self.browser_bridge.download_cursor()
        self._safe_boundary(
            store, controller, task_id, current_action="catalog-download"
        )
        self._with_manual_retry(
            store,
            controller,
            task_id,
            lambda: adapter.download_batch_file(
                kind="catalog",
                catalog_format="xls",
            ),
            action_name="catalog-download",
        )

        def record_catalog_progress(event: dict[str, Any]) -> None:
            outcome = str(event.get("outcome") or "")
            if outcome not in {"started", "completed", "failed", "mismatch"}:
                return
            checkpoint = dict(
                (store.get_task(task_id) or {}).get("checkpoint") or {}
            )
            checkpoint.update(
                {
                    "currentDownloadIntent": {
                        **catalog_intent,
                        "downloadId": event.get("downloadId"),
                        "path": str(event.get("path") or ""),
                        "status": outcome,
                        "matchedDownloads": 1,
                    },
                    "currentDownloadStatus": outcome,
                }
            )
            self._update_task(
                store,
                task_id,
                checkpoint=checkpoint,
            )

        catalog_event = self.browser_bridge.wait_for_download_intent(
            str(catalog_intent["intentId"]),
            catalog_cursor,
            timeout_seconds=60,
            on_event=record_catalog_progress,
        )
        if not catalog_event:
            raise CatalogFormatError(
                "CATALOG_DOWNLOAD_FAILED",
                "目录下载在限定时间内未完成。"
                f"intentId={catalog_intent['intentId']}；"
                "恢复建议：检查网站目录弹窗和下载事件后重试当前批次",
                details={
                    "intentId": catalog_intent["intentId"],
                    "expectedSuffixes": list(catalog_contract.source_suffixes),
                    "recovery": "检查网站目录弹窗和 Electron 下载事件后重试当前批次",
                },
            )
        if (
            catalog_event.get("event") == "mismatch"
            or catalog_event.get("errorCode")
            == "CATALOG_NATIVE_FORMAT_MISMATCH"
        ):
            catalog_error = self._catalog_event_error(
                catalog_event,
                catalog_intent,
            )
            self._set_catalog_validation_failure(
                store,
                task_id,
                catalog_intent,
                catalog_error,
            )
            raise catalog_error
        if catalog_event.get("event") != "completed":
            raise CatalogFormatError(
                "CATALOG_DOWNLOAD_FAILED",
                "目录下载未成功完成。"
                f"intentId={catalog_intent['intentId']}，"
                f"downloadId={catalog_event.get('downloadId') or '<未知>'}；"
                f"事件={catalog_event.get('event') or '<未知>'}；"
                "恢复建议：检查下载日志后重试当前批次",
                details={
                    "intentId": catalog_intent["intentId"],
                    "downloadId": catalog_event.get("downloadId"),
                    "event": catalog_event,
                    "recovery": "检查下载日志后重试当前批次",
                },
            )
        native_catalog_path = Path(str(catalog_event.get("path") or ""))
        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadIntent": {
                    **catalog_intent,
                    "downloadId": catalog_event.get("downloadId"),
                    "path": str(native_catalog_path),
                    "status": "completed",
                    "matchedDownloads": 1,
                },
                "currentDownloadStatus": "completed",
                "catalogNativePath": str(native_catalog_path),
                "catalogRequestedFormat": config.catalog_format,
                "catalogSourceContract": catalog_contract.to_dict(),
            },
        )
        prepared_destination = native_catalog_path.with_name(
            f"{safe_filename(str(batch['batchKey']))}-{catalog_name}"
        )
        try:
            prepared = prepare_catalog_output(
                native_catalog_path,
                requested_format=config.catalog_format,
                selected=selected,
                destination=prepared_destination,
            )
            catalog_result = validate_catalog(prepared.path, selected)
        except (CatalogFormatError, CatalogValidationError) as exc:
            catalog_error = self._catalog_preparation_error(
                exc,
                catalog_event,
                catalog_intent,
                config.catalog_format,
                native_catalog_path,
                prepared_destination,
            )
            self._update_task(
                store,
                task_id,
                checkpoint={
                    **(store.get_task(task_id).get("checkpoint") or {}),
                    "currentDownloadStatus": "validation_failed",
                    "downloadValidationDetail": str(catalog_error),
                    "downloadValidationDetails": catalog_error.details,
                    "catalogRequestedFormat": config.catalog_format,
                    "catalogSourceContract": catalog_contract.to_dict(),
                },
            )
            self.browser_bridge.record_download_intent_validation(
                str(catalog_intent["intentId"]),
                valid=False,
                actual_kind=native_catalog_path.suffix.lower(),
                detail=str(catalog_error),
            )
            raise catalog_error from exc
        if catalog_result["format"] != catalog_contract.final_format:
            self._update_task(
                store,
                task_id,
                checkpoint={
                    **(store.get_task(task_id).get("checkpoint") or {}),
                    "currentDownloadStatus": "validation_failed",
                    "downloadValidationDetail": "目录实际格式与配置不一致",
                },
            )
            self.browser_bridge.record_download_intent_validation(
                str(catalog_intent["intentId"]),
                valid=False,
                actual_kind=str(catalog_result["format"]),
                detail="目录实际格式与配置不一致",
            )
            raise CatalogFormatError(
                "CATALOG_CONVERSION_FAILED",
                f"派生目录实际格式 {catalog_result['format']} 与契约 "
                f"{catalog_contract.final_format} 不一致；"
                f"intentId={catalog_intent['intentId']}，"
                f"downloadId={catalog_event.get('downloadId')}",
                details={
                    "intentId": catalog_intent["intentId"],
                    "downloadId": catalog_event.get("downloadId"),
                    "actualFormat": catalog_result["format"],
                    "expectedFormat": catalog_contract.final_format,
                    "path": str(prepared.path),
                    "recovery": "保留网站原生 XLS，检查本地目录转换器后重试；未通过前不会启动 ZIP",
                },
            )
        self.browser_bridge.record_download_intent_validation(
            str(catalog_intent["intentId"]),
            valid=True,
            actual_kind="xls",
            detail=(
                f"网站原生 BIFF XLS 已验证；"
                f"最终输出={prepared.final_format}"
            ),
        )
        catalog_checkpoint = prepared.to_dict()
        catalog_checkpoint.update(
            {
                "requestedFormat": config.catalog_format,
                "nativePath": str(native_catalog_path),
                "finalPath": str(prepared.path),
                "native": prepared.contract.native,
                "sourceFormat": prepared.source_format,
                "finalFormat": prepared.final_format,
                "converted": prepared.converted,
            }
        )
        catalog_snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage="catalog",
            overall=batch_batch_overall(index, total, "catalog"),
        )
        self._update_task(
            store,
            task_id,
            progress=catalog_snapshot.overall,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadStatus": "validated",
                "catalogIntentId": catalog_intent["intentId"],
                "catalogDownloadId": catalog_event.get("downloadId"),
                "catalogPath": str(prepared.path),
                "catalogNativePath": str(native_catalog_path),
                "catalogRequestedFormat": config.catalog_format,
                "catalogActualNativeFormat": prepared.source_format,
                "catalogActualFinalFormat": prepared.final_format,
                "catalogNative": prepared.contract.native,
                "catalogConverted": prepared.converted,
                "catalogFormatContract": catalog_checkpoint,
                "currentAction": "after-catalog-validation",
                "progressSnapshot": catalog_snapshot.to_dict(),
            },
        )
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="after-catalog-validation",
        )
        search_session = self._verify_batch_page(
            store,
            task_id,
            batch,
            selected,
            config,
            adapter,
            search_session,
        )

        zip_intent = self.browser_bridge.create_download_intent(
            task_id=task_id,
            kind="batch-zip",
            source_url=adapter.search_url,
            expected_suffixes=(".zip",),
        )
        zip_snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage="zip",
            overall=batch_batch_overall(index, total, "zip"),
        )
        self._update_task(
            store,
            task_id,
            progress=zip_snapshot.overall,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadIntent": zip_intent,
                "currentDownloadStatus": "pending",
                "currentAction": "zip-download",
                "progressSnapshot": zip_snapshot.to_dict(),
            },
        )
        zip_cursor = self.browser_bridge.download_cursor()
        self._safe_boundary(
            store, controller, task_id, current_action="zip-download"
        )
        self._with_manual_retry(
            store,
            controller,
            task_id,
            lambda: adapter.download_batch_file(
                kind="zip",
                catalog_format=config.catalog_format,
                full_format=config.full_format,
            ),
            action_name="zip-download",
        )

        def record_zip_progress(event: dict[str, Any]) -> None:
            outcome = str(event.get("outcome") or "")
            if outcome not in {"started", "completed", "failed"}:
                return
            checkpoint = dict(
                (store.get_task(task_id) or {}).get("checkpoint") or {}
            )
            checkpoint.update(
                {
                    "currentDownloadIntent": {
                        **zip_intent,
                        "downloadId": event.get("downloadId"),
                        "path": str(event.get("path") or ""),
                        "status": outcome,
                        "matchedDownloads": 1,
                    },
                    "currentDownloadStatus": outcome,
                }
            )
            self._update_task(
                store,
                task_id,
                checkpoint=checkpoint,
            )

        zip_event = self.browser_bridge.wait_for_download_intent(
            str(zip_intent["intentId"]),
            zip_cursor,
            timeout_seconds=300,
            on_event=record_zip_progress,
        )
        if not zip_event or zip_event.get("event") != "completed":
            raise ArchiveValidationError(f"ZIP 下载未完成：{zip_event!r}")
        zip_path = Path(str(zip_event.get("path") or ""))
        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadIntent": {
                    **zip_intent,
                    "downloadId": zip_event.get("downloadId"),
                    "path": str(zip_path),
                    "status": "completed",
                    "matchedDownloads": 1,
                },
                "currentDownloadStatus": "completed",
            },
        )
        try:
            zip_result = validate_zip(
                zip_path,
                selected=selected,
                full_format=config.full_format,
            )
        except ArchiveValidationError as exc:
            self._update_task(
                store,
                task_id,
                checkpoint={
                    **(store.get_task(task_id).get("checkpoint") or {}),
                    "currentDownloadStatus": "validation_failed",
                    "downloadValidationDetail": str(exc),
                },
            )
            self.browser_bridge.record_download_intent_validation(
                str(zip_intent["intentId"]),
                valid=False,
                actual_kind="zip",
                detail=str(exc),
            )
            raise
        self.browser_bridge.record_download_intent_validation(
            str(zip_intent["intentId"]),
            valid=True,
            actual_kind="zip",
        )
        publish_snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage="publish",
            overall=batch_batch_overall(index, total, "zip"),
        )
        self._update_task(
            store,
            task_id,
            progress=publish_snapshot.overall,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadStatus": "validated",
                "zipIntentId": zip_intent["intentId"],
                "zipDownloadId": zip_event.get("downloadId"),
                "zipPath": str(zip_path),
                "currentAction": "after-zip-validation",
                "progressSnapshot": publish_snapshot.to_dict(),
            },
        )

        catalog_result = {
            **catalog_result,
            "path": str(final_catalog),
            "requestedFormat": config.catalog_format,
            "internalFormat": prepared.contract.requested,
            "native": prepared.contract.native,
            "sourceFormat": prepared.source_format,
            "finalFormat": prepared.final_format,
            "finalSuffix": prepared.contract.final_suffix,
            "converted": prepared.converted,
            "sourceSha256": prepared.source_sha256,
            "finalSha256": prepared.final_sha256,
        }
        report_payload = {
            **zip_result,
            "catalog": catalog_result,
            "taskId": task_id,
            "batchKey": batch["batchKey"],
            "query": batch["query"],
            "library": batch["library"],
            "page": batch["page"],
            "rowStart": batch["rowStart"],
            "rowEnd": batch["rowEnd"],
            "policyIds": [
                str(policy.get("policyId") or "")
                for policy in selected
            ],
            "catalogPath": str(final_catalog),
            "zipPath": str(final_zip),
            "catalogIntentId": catalog_intent["intentId"],
            "catalogDownloadId": catalog_event.get("downloadId"),
            "catalogRequestedFormat": config.catalog_format,
            "catalogNativeFormat": prepared.source_format,
            "catalogFinalFormat": prepared.final_format,
            "catalogNative": prepared.contract.native,
            "catalogConverted": prepared.converted,
            "catalogNativeSha256": prepared.source_sha256,
            "catalogFinalSha256": prepared.final_sha256,
            "zipIntentId": zip_intent["intentId"],
            "searchId": search_session.search_id,
            "searchIdentity": search_session.to_dict(),
            "verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        staging = prepare_publish_directory(
            staging_root=config.output_dir / "batch_files" / ".publishing",
            final_directory=final_dir,
            batch_key=batch["batchKey"],
            files={
                catalog_name: prepared.path,
                zip_name: zip_path,
            },
            report_name=report_name,
            report_payload=report_payload,
        )
        published = publish_directory(staging, final_dir)
        for downloaded_source in (
            native_catalog_path,
            prepared.path,
            zip_path,
        ):
            try:
                downloaded_source.unlink()
            except OSError:
                pass
        self._register_completed_batch(
            store=store,
            task_id=task_id,
            batch=batch,
            selected=selected,
            index=index,
            total=total,
            batch_year=batch_year,
            controller=controller,
            final_catalog=final_catalog,
            final_zip=final_zip,
            final_report=final_report,
            report_payload=report_payload,
            search_session=search_session,
            recovered=False,
            published=published,
        )

    @staticmethod
    def _completed_batch_integrity_error(
        batch: dict[str, Any],
    ) -> dict[str, Any] | None:
        catalog_path = Path(str(batch.get("catalogPath") or ""))
        zip_path = Path(str(batch.get("zipPath") or ""))
        report_path = Path(str(batch.get("reportPath") or ""))
        paths = (catalog_path, zip_path, report_path)
        if any(not path.is_file() for path in paths):
            return {
                "code": "BATCH_ARTIFACT_MISSING",
                "message": "已完成批次登记文件缺失，已失败关闭且不会自动重新下载",
            }
        try:
            directory = catalog_path.resolve(strict=False).parent
            if any(
                path.resolve(strict=False).parent != directory
                for path in paths
            ):
                raise ValueError
        except (OSError, ValueError):
            return {
                "code": "BATCH_ARTIFACT_PATH_MISMATCH",
                "message": "已完成批次登记文件不在同一正式目录，已失败关闭",
            }
        published = verify_published_directory(
            directory,
            batch_key=str(batch.get("batchKey") or ""),
        )
        if published is None:
            return {
                "code": "BATCH_ARTIFACT_INTEGRITY_MISMATCH",
                "message": "已完成批次的发布清单、文件大小或 SHA-256 不一致，已失败关闭",
            }
        manifest_names = {
            str(item.get("name") or "")
            for item in published.get("files", [])
            if isinstance(item, dict)
        }
        if not {
            catalog_path.name,
            zip_path.name,
            report_path.name,
        }.issubset(manifest_names):
            return {
                "code": "BATCH_ARTIFACT_DATABASE_MISMATCH",
                "message": "SQLite 登记路径与批次发布清单不一致，已失败关闭",
            }
        expected_ids = [
            str(policy.get("policyId") or "").lower()
            for policy in batch.get("selected", [])
        ]
        report_payload = dict(
            (published.get("report") or {}).get("payload") or {}
        )
        report_ids = [
            str(value).lower()
            for value in report_payload.get("policyIds", [])
        ]
        if expected_ids != report_ids:
            return {
                "code": "BATCH_ARTIFACT_IDENTITY_MISMATCH",
                "message": "已完成批次发布报告与 SQLite 政策身份不一致，已失败关闭",
            }
        return None

    def _register_completed_batch(
        self,
        *,
        store: TaskStore,
        task_id: str,
        batch: dict[str, Any],
        selected: list[dict[str, Any]],
        index: int,
        total: int,
        batch_year: str,
        controller: TaskControl,
        final_catalog: Path,
        final_zip: Path,
        final_report: Path,
        report_payload: dict[str, Any],
        search_session: SearchSession,
        recovered: bool,
        published: dict[str, Any] | None = None,
    ) -> None:
        published = published or verify_published_directory(
            final_catalog.parent,
            batch_key=batch["batchKey"],
        )
        if published is None:
            raise AtomicPublishError(
                "PUBLISH_VERIFY_FAILED",
                "批次正式目录未通过发布后核验",
            )
        catalog = dict(report_payload.get("catalog") or {})
        catalog_by_id = {
            str(row.get("policyId") or "").lower(): row
            for row in catalog.get("rows", [])
        }
        zip_by_id = {
            str(row.get("policyId") or "").lower(): row
            for row in report_payload.get("mapping", [])
        }
        retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        items = [
            {
                "policyKey": f"id:{str(policy.get('policyId') or '').lower()}",
                "title": str(policy.get("title") or ""),
                "sourceType": "北大法宝ZIP",
                "payload": {
                    "candidate": policy,
                    "batchKey": batch["batchKey"],
                    "catalogRow": catalog_by_id.get(
                        str(policy.get("policyId") or "").lower()
                    ),
                    "archive": zip_by_id.get(
                        str(policy.get("policyId") or "").lower()
                    ),
                    "catalogFile": str(final_catalog),
                    "zipFile": str(final_zip),
                    "reportFile": str(final_report),
                    "publishManifest": published["manifestPath"],
                    "retrievedAt": retrieved_at,
                },
            }
            for policy in selected
        ]
        store.complete_task_batch_atomic(
            task_id,
            batch_key=batch["batchKey"],
            query=batch["query"],
            library=batch["library"],
            page=batch["page"],
            row_start=batch["rowStart"],
            row_end=batch["rowEnd"],
            catalog_format=batch["catalogFormat"],
            full_format=batch["fullFormat"],
            selected=selected,
            items=items,
            catalog_path=str(final_catalog),
            zip_path=str(final_zip),
            report_path=str(final_report),
        )
        post_batch_status = (
            "stopping"
            if controller.stop_requested
            else "pausing"
            if controller.pause_requested
            else "running"
        )
        published_snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage="publish",
            overall=batch_batch_overall(index, total, "publish"),
        )
        self._update_task(
            store,
            task_id,
            status=post_batch_status,
            progress=published_snapshot.overall,
            current_stage=f"batch-completed-{index}-{total}",
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "lastCompletedBatch": batch["batchKey"],
                "currentBatchKey": batch["batchKey"],
                "currentBatchIndex": index,
                "batchCount": total,
                "currentQuery": batch["query"],
                "currentLibrary": batch["library"],
                "currentYear": batch_year,
                "currentPage": batch["page"],
                "currentRowStart": batch["rowStart"],
                "currentRowEnd": batch["rowEnd"],
                "currentCatalogFile": str(final_catalog),
                "currentZipFile": str(final_zip),
                "currentReportFile": str(final_report),
                "publishManifest": published["manifestPath"],
                "publishRecovered": recovered,
                "currentAction": "after-batch",
                "searchId": search_session.search_id,
                "searchIdentity": search_session.to_dict(),
                "progressSnapshot": published_snapshot.to_dict(),
            },
        )
        self._emit(
            "batch.batch-completed",
            {
                "taskId": task_id,
                "batchId": batch["id"],
                "batchKey": batch["batchKey"],
                "index": index,
                "total": total,
                "catalogFile": str(final_catalog),
                "zipFile": str(final_zip),
                "reportFile": str(final_report),
                "policyCount": len(selected),
                "recovered": recovered,
            },
        )
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="after-batch",
        )

    def _fail_batch(
        self,
        store: TaskStore,
        task_id: str,
        batch: dict[str, Any],
        error: dict[str, Any],
    ) -> None:
        store.upsert_task_batch(
            task_id,
            batch_key=batch["batchKey"],
            query=batch["query"],
            library=batch["library"],
            page=batch["page"],
            row_start=batch["rowStart"],
            row_end=batch["rowEnd"],
            status="failed",
            progress=0,
            catalog_format=batch["catalogFormat"],
            full_format=batch["fullFormat"],
            selected=batch["selected"],
            error=error,
        )
        self._set_batch_items_status(
            store,
            task_id,
            batch["selected"],
            "failed",
            "batch-failed",
            error,
        )
        self._emit(
            "batch.batch-failed",
            {
                "taskId": task_id,
                "batchId": batch["id"],
                "batchKey": batch["batchKey"],
                "error": error,
            },
        )

    def _set_batch_items_status(
        self,
        store: TaskStore,
        task_id: str,
        selected: list[dict[str, Any]],
        status: str,
        stage: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        for policy in selected:
            item = store.upsert_task_item(
                task_id,
                policy_key=f"id:{str(policy.get('policyId') or '').lower()}",
                title=str(policy.get("title") or ""),
                status=status,
                source_type="北大法宝ZIP",
                progress=1.0 if status == "completed" else 0.5 if status == "running" else 0.0,
                payload={"candidate": policy},
                error=error,
                current_stage=stage,
            )
            if item is None:
                raise RuntimeError("policy item update returned no item")

    @staticmethod
    def _adaptive_completed_policy_count(
        unique_identities: set[str],
    ) -> int:
        return len(unique_identities)

    @staticmethod
    def _adaptive_search_checkpoint(
        *,
        governor: SearchActionGovernor | None,
        adaptive_scope_states: dict[tuple[str, str], dict[str, Any]],
        config: BatchTaskConfig,
        boundary: dict[str, Any],
    ) -> dict[str, Any]:
        completed_years: set[str] = set()
        missing_years: set[str] = set()
        for (scope_query, scope_library), scope_state in (
            adaptive_scope_states.items()
        ):
            plan = plan_adaptive_years(
                config.year_selection,
                scope_state.get("entries") or [],
                scope_state.get("collected") or {},
                query=scope_query,
                library=scope_library,
            )
            completed_years.update(plan.completed_years)
            missing_years.update(plan.missing_years)
        snapshot = governor.snapshot() if governor is not None else {}
        scope_states = {
            f"{query}|{library}": {
                "entries": list(scope_state.get("entries") or []),
                "collected": dict(scope_state.get("collected") or {}),
            }
            for (query, library), scope_state in adaptive_scope_states.items()
        }
        return {
            "keywordSubmissions": int(
                snapshot.get("keyword_submissions") or 0
            ),
            "yearFilterSwitches": int(
                snapshot.get("year_filter_switches") or 0
            ),
            "captchaWaits": int(snapshot.get("captcha_waits") or 0),
            "completedYears": sorted(completed_years),
            "missingYears": sorted(missing_years),
            "fallbackMode": "A" if boundary else "C",
            "boundary": boundary,
            "scopeStates": scope_states,
        }

    def _adaptive_remaining_scopes(
        self,
        adaptive_scope_states: dict[tuple[str, str], dict[str, Any]],
        config: BatchTaskConfig,
    ) -> list[dict[str, Any]]:
        remaining: list[dict[str, Any]] = []
        queries = list(config.queries)
        for (query, library), scope_state in adaptive_scope_states.items():
            plan = plan_adaptive_years(
                config.year_selection,
                scope_state.get("entries") or [],
                scope_state.get("collected") or {},
                query=query,
                library=library,
            )
            query_index = (
                queries.index(query) if query in queries else 0
            )
            library_index = 0 if library == "central" else 1
            for year in plan.missing_years:
                remaining.append(
                    {
                        "query": query,
                        "library": library,
                        "year": year,
                        "province": config.province_for(library),
                        "queryIndex": query_index,
                        "libraryIndex": library_index,
                    }
                )
        return remaining

    def _append_capture_boundary_resume_combinations(
        self,
        *,
        config: BatchTaskConfig,
        initial_checkpoint: dict[str, Any],
        combinations: list[SearchCombination],
    ) -> list[SearchCombination]:
        adaptive = dict(initial_checkpoint.get("adaptiveSearch") or {})
        if adaptive.get("fallbackMode") != "A":
            return combinations
        boundary = dict(adaptive.get("boundary") or {})
        scopes = boundary.get("remainingScopes") or []
        if not scopes:
            return []
        queries = list(config.queries)
        result: list[SearchCombination] = []
        start_index = 0
        for offset, raw_scope in enumerate(scopes):
            if not isinstance(raw_scope, dict):
                continue
            query = str(raw_scope.get("query") or "").strip()
            library = str(raw_scope.get("library") or "").strip()
            year = str(raw_scope.get("year") or "").strip()
            if not query or not library or not year:
                continue
            query_index = int(raw_scope.get("queryIndex") or 0)
            if query not in queries:
                query_index = 0
            library_index = int(raw_scope.get("libraryIndex") or 0)
            result.append(
                SearchCombination(
                    query=query,
                    library=library,
                    year=year,
                    query_index=query_index,
                    library_index=library_index,
                    year_index=offset,
                    combination_index=start_index + offset + 1,
                    province=str(raw_scope.get("province") or ""),
                    partition_key=str(raw_scope.get("partitionKey") or ""),
                    action_kind="year-filter-switch",
                )
            )
        return result

    def _append_adaptive_missing_year_combinations(
        self,
        *,
        config: BatchTaskConfig,
        adapter: PkulawAdapter,
        combination: SearchCombination,
        scope_state: dict[str, Any],
        combinations: list[SearchCombination],
        adaptive_missing_years: list[str],
    ) -> None:
        plan = plan_adaptive_years(
            config.year_selection,
            adapter.available_filter_entries("year"),
            scope_state.get("collected") or {},
            query=combination.query,
            library=combination.library,
        )
        scope_state["plan"] = plan
        if not plan.missing_years:
            return
        missing_combinations = build_year_switch_combinations(
            plan,
            combination.query_index,
            combination.library_index,
            start_index=len(combinations),
        )
        combinations.extend(missing_combinations)
        adaptive_missing_years.extend(plan.missing_years)

    def _run_adaptive_result_action(
        self,
        *,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        config: BatchTaskConfig,
        adapter: PkulawAdapter,
        combination: SearchCombination,
        governor: SearchActionGovernor | None,
        completed_policy_count: int = 0,
    ) -> dict[str, Any]:
        if governor is None:
            raise RuntimeError("adaptive governor is not initialized")
        requested_kind: SearchActionKind = (
            "year-filter-switch"
            if combination.action_kind == "year-filter-switch"
            else "keyword-submission"
        )
        province = combination.province or config.province_for(combination.library)
        timeliness = combination.timeliness or config.timeliness
        last_error: Exception | None = None

        def can_reuse_session_for_year() -> bool:
            request = adapter.current_search_request
            session = adapter.current_search_session
            return bool(
                request is not None
                and session is not None
                and request.query == combination.query
                and request.library == combination.library
                and request.match_mode == config.match_mode
                and request.province == province
                and request.timeliness == timeliness
                and request.requested_page_size == config.page_size
            )

        for _attempt in range(3):
            self._safe_boundary(
                store,
                controller,
                task_id,
                current_action="search",
            )
            reuse_year_filter = (
                requested_kind == "year-filter-switch"
                and can_reuse_session_for_year()
            )
            effective_kind: SearchActionKind = (
                "year-filter-switch"
                if reuse_year_filter
                else "keyword-submission"
            )
            governor.before_result_action(effective_kind)
            try:
                if reuse_year_filter:
                    state = adapter.select_year(year=combination.year)
                else:
                    state = adapter.search(
                        keyword=combination.query,
                        match_mode=config.match_mode,
                        library=combination.library,
                        year=combination.year,
                        province=province,
                        timeliness=timeliness,
                        page_size=config.page_size,
                    )
                governor.after_result_action()
                return state
            except (ManualActionRequired, LoginRequired) as exc:
                last_error = exc
                captcha_detected = (
                    isinstance(exc, ManualActionRequired)
                    and (
                        "验证码" in str(exc)
                        or "滑块" in str(exc)
                        or "captcha" in str(exc).lower()
                    )
                )
                self._wait_for_manual(
                    store,
                    controller,
                    task_id,
                    str(exc),
                    "waiting_login"
                    if isinstance(exc, LoginRequired)
                    else "waiting_manual",
                )
                if captcha_detected:
                    released = governor.note_captcha_cleared()
                    if released:
                        raise CaptureBoundaryRequired(
                            CaptureBoundary(
                                reason="重复验证码，进入采集边界确认",
                                query=combination.query,
                                library=combination.library,
                                year=combination.year,
                                province=province,
                                completed_policy_count=(
                                    completed_policy_count
                                ),
                                remaining_scopes=(
                                    {
                                        "query": combination.query,
                                        "library": combination.library,
                                        "year": combination.year,
                                        "province": province,
                                    },
                                ),
                                estimated_result_operations=1,
                                detected_at=time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(),
                                ),
                            )
                        )
                request = adapter.current_search_request
                session = adapter.current_search_session
                if request is not None and session is not None:
                    try:
                        current = adapter.read_results()
                        if session.matches_request(
                            request,
                            expected_page=session.page,
                            require_refinements=True,
                        ):
                            governor.after_result_action()
                            return current
                    except Exception:
                        pass
                continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("adaptive result action exhausted retries")

    def _with_manual_retry(
        self,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        action: Callable[[], Any],
        attempts: int = 3,
        action_name: str = "action",
    ) -> Any:
        last: Exception | None = None
        for _ in range(attempts):
            self._safe_boundary(
                store,
                controller,
                task_id,
                current_action=action_name,
            )
            try:
                return action()
            except (ManualActionRequired, LoginRequired) as exc:
                last = exc
                self._wait_for_manual(
                    store,
                    controller,
                    task_id,
                    str(exc),
                    "waiting_login" if isinstance(exc, LoginRequired) else "waiting_manual",
                )
        raise last or RuntimeError("manual recovery exhausted")

    def _wait_for_page_limit(
        self,
        *,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        query: str,
        library: str,
        year: str,
        page: int,
        total_pages: int,
        max_pages: int,
    ) -> str:
        current = store.get_task(task_id) or {}
        checkpoint = dict(current.get("checkpoint") or {})
        checkpoint.update(
            {
                "pageMode": "limit",
                "pageSelection": {"mode": "limit", "maxPages": max_pages},
                "pageLimitGate": True,
                "pageLimitMaxPages": max_pages,
                "totalPages": total_pages,
                "currentPage": page,
                "currentYear": year,
                "currentQuery": query,
                "currentLibrary": library,
                "scannedPages": page,
                "unscannedPages": max(0, total_pages - page),
                "currentAction": "page-limit-decision",
            }
        )
        checkpoint = mark_current_year_status(checkpoint, "paused")
        snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage="waiting-page-limit",
            overall=float(
                (checkpoint.get("progressSnapshot") or {}).get("overall") or 0
            ),
        )
        checkpoint["progressSnapshot"] = snapshot.to_dict()
        self._update_task(
            store,
            task_id,
            status="waiting_manual",
            current_stage="waiting-page-limit",
            progress=snapshot.overall,
            error={
                "code": "PAGE_LIMIT_REACHED",
                "message": (
                    f"当前组合共 {total_pages} 页，超过上限 {max_pages} 页；"
                    "请选择继续到尾页或停止任务"
                ),
            },
            checkpoint=checkpoint,
        )
        self._emit(
            "batch.page-limit-reached",
            {
                "taskId": task_id,
                "page": page,
                "totalPages": total_pages,
                "maxPages": max_pages,
            },
        )
        controller.manual_wait()
        latest_checkpoint = dict(
            (store.get_task(task_id) or {}).get("checkpoint") or {}
        )
        page_mode = str(latest_checkpoint.get("pageMode") or "limit")
        if page_mode != "last":
            raise ManualActionRequired("页数上限决策未记录为继续到尾页")
        latest_checkpoint.update(
            {
                "pageLimitGate": False,
                "currentAction": "search",
            }
        )
        latest_checkpoint = mark_current_year_status(
            latest_checkpoint,
            "running",
        )
        self._update_task(
            store,
            task_id,
            status="running",
            current_stage="page-limit-continued",
            clear_error=True,
            checkpoint=latest_checkpoint,
        )
        self._emit(
            "batch.page-limit-continued",
            {"taskId": task_id, "pageMode": "last"},
        )
        return page_mode

    def _build_scale_estimate(
        self,
        *,
        config: BatchTaskConfig,
        total_pages: int,
        total_rows: int,
        total_batches: int,
        year_count: int,
        stage: str,
        basis: str,
        confidence: str,
        combination_count: int | None = None,
        page_mode: str | None = None,
    ) -> dict[str, Any]:
        library_count = 2 if config.library == "both" else 1
        combination_count = combination_count or (
            len(config.queries) * library_count * len(config.year_selection.years)
        )
        requires_confirmation = (
            total_pages >= SCALE_CONFIRM_PAGE_THRESHOLD
            or total_rows >= SCALE_CONFIRM_ROW_THRESHOLD
            or total_batches >= SCALE_CONFIRM_BATCH_THRESHOLD
        )
        return {
            "stage": stage,
            "combinationCount": combination_count,
            "estimatedPages": total_pages,
            "estimatedRows": total_rows,
            "estimatedBatches": total_batches,
            "yearCount": year_count,
            "libraryCount": library_count,
            "dualLibrary": config.library == "both",
            "pageMode": page_mode or config.page_selection.mode,
            "maxPages": config.page_selection.max_pages,
            "basis": basis,
            "confidence": confidence,
            "requiresConfirmation": requires_confirmation,
            "thresholds": {
                "pages": SCALE_CONFIRM_PAGE_THRESHOLD,
                "rows": SCALE_CONFIRM_ROW_THRESHOLD,
                "batches": SCALE_CONFIRM_BATCH_THRESHOLD,
            },
        }

    def _confirm_large_scale_if_needed(
        self,
        *,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        scale_estimate: dict[str, Any],
    ) -> None:
        checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
        checkpoint["scaleEstimate"] = scale_estimate
        if not scale_estimate.get("requiresConfirmation") or checkpoint.get(
            "scaleConfirmed"
        ):
            checkpoint["scaleConfirmationRequired"] = False
            self._update_task(store, task_id, checkpoint=checkpoint)
            return
        checkpoint.update(
            {
                "scaleConfirmationRequired": True,
                "scaleConfirmed": False,
                "currentAction": "scale-confirmation",
            }
        )
        snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage="waiting-scale-confirmation",
            overall=float(
                (checkpoint.get("progressSnapshot") or {}).get("overall") or 0
            ),
        )
        checkpoint["progressSnapshot"] = snapshot.to_dict()
        self._update_task(
            store,
            task_id,
            status="waiting_manual",
            current_stage="waiting-scale-confirmation",
            progress=snapshot.overall,
            error={
                "code": "BATCH_SCALE_CONFIRMATION_REQUIRED",
                "message": (
                    "预计批量规模较大，请确认扫描页数、政策行数和批次数后继续"
                ),
            },
            checkpoint=checkpoint,
        )
        self._emit(
            "batch.scale-confirmation-required",
            {"taskId": task_id, "scaleEstimate": scale_estimate},
        )
        controller.manual_wait()
        latest = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
        if not latest.get("scaleConfirmed"):
            raise ManualActionRequired("批量规模尚未确认")
        latest.update(
            {
                "scaleConfirmationRequired": False,
                "currentAction": "prepare-batches",
            }
        )
        self._update_task(
            store,
            task_id,
            status="running",
            current_stage="scale-confirmed",
            clear_error=True,
            checkpoint=latest,
        )
        self._emit(
            "batch.scale-confirmed",
            {"taskId": task_id, "scaleEstimate": scale_estimate},
        )

    def _wait_for_manual(
        self,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        message: str,
        status: str,
    ) -> None:
        checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
        checkpoint.update(
            {
                "pauseKind": "automatic",
                "pauseReason": message,
                "manualStatus": status,
                "manualAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        snapshot = self._progress_snapshot_for_batches(
            store,
            task_id,
            stage=status,
            overall=float(
                (checkpoint.get("progressSnapshot") or {}).get("overall") or 0
            ),
        )
        checkpoint["progressSnapshot"] = snapshot.to_dict()
        self._update_task(
            store,
            task_id,
            status=status,
            current_stage="waiting-manual",
            progress=snapshot.overall,
            error={"code": "MANUAL_ACTION_REQUIRED", "message": message},
            checkpoint=checkpoint,
        )
        self._emit(
            "batch.manual-required",
            {"taskId": task_id, "status": status, "message": message},
        )
        controller.manual_wait()
        resumed_checkpoint = checkpoint_after_manual_continue(
            checkpoint,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        resumed_checkpoint = mark_current_year_status(
            resumed_checkpoint,
            "running",
        )
        self._update_task(
            store,
            task_id,
            status="running",
            current_stage="manual-continued",
            clear_error=True,
            checkpoint=resumed_checkpoint,
        )

    def _safe_boundary(
        self,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        *,
        current_action: str,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        controller.check_stop()
        current = store.get_task(task_id)
        if current is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        merged = dict(current.get("checkpoint") or {})
        if checkpoint:
            merged.update(checkpoint)
        merged["currentAction"] = current_action
        store.update_task(task_id, checkpoint=merged)

        def mark_paused(reason: str) -> None:
            paused_checkpoint = dict(
                (store.get_task(task_id) or {}).get("checkpoint") or merged
            )
            paused_checkpoint.update(
                {
                    "pauseKind": "manual",
                    "pauseReason": reason,
                    "pausedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "pausedStage": str(
                        paused_checkpoint.get("pauseRequestedStage")
                        or current.get("currentStage")
                        or ""
                    ),
                    "currentAction": current_action,
                }
            )
            paused_checkpoint = mark_current_year_status(
                paused_checkpoint,
                "paused",
            )
            paused_snapshot = self._progress_snapshot_for_batches(
                store,
                task_id,
                stage="paused",
                overall=float(
                    (paused_checkpoint.get("progressSnapshot") or {}).get("overall")
                    or current.get("progress")
                    or 0
                ),
            )
            paused_checkpoint["progressSnapshot"] = paused_snapshot.to_dict()
            self._update_task(
                store,
                task_id,
                status="paused",
                current_stage="paused",
                progress=paused_snapshot.overall,
                checkpoint=paused_checkpoint,
            )
            self._emit(
                "batch.paused",
                {
                    "taskId": task_id,
                    "reason": reason,
                    "currentAction": current_action,
                },
            )

        controller.wait_at_boundary(on_pause=mark_paused)
        controller.check_stop()

    def _validate_resume_for_task(self) -> None:
        try:
            state = self.browser_bridge.state()
        except Exception as exc:
            raise ValidationError(
                "BROWSER_NOT_BOUND",
                f"继续前无法读取已绑定网页：{exc}",
            ) from exc
        if not state.get("targetBound"):
            raise ValidationError("BROWSER_NOT_BOUND", "继续前未检测到已绑定网页目标")
        if state.get("captchaVisible"):
            raise ValidationError(
                "MANUAL_VERIFICATION_REQUIRED",
                "继续前仍需完成滑块或人机验证",
            )
        if state.get("loginVisible") or not state.get("authenticated"):
            raise ValidationError("LOGIN_REQUIRED", "继续前需要重新完成机构登录")
        try:
            diagnostics = PkulawAdapter(
                self.browser_bridge,
                self.logger,
            ).diagnose_current_page()
        except Exception as exc:
            raise ValidationError(
                "PAGE_SELECTOR_MISMATCH",
                f"继续前选择器核验失败：{exc}",
            ) from exc
        if not diagnostics.get("detected") or not diagnostics.get("profileId"):
            raise ValidationError(
                "PAGE_SELECTOR_MISMATCH",
                "继续前当前页面未通过选择器识别",
            )

    @staticmethod
    def _progress_snapshot_for_batches(
        store: TaskStore,
        task_id: str,
        *,
        stage: str,
        overall: float,
    ):
        batches = store.list_task_batches(task_id)
        items = store.list_task_items(task_id)
        succeeded = sum(batch["status"] == "completed" for batch in batches)
        failed = sum(batch["status"] == "failed" for batch in batches)
        pending_review = sum(
            item["status"] == "pending_review" for item in items
        )
        pending_batches = max(0, len(batches) - succeeded - failed)
        return build_progress_snapshot(
            stage=stage,
            overall=overall,
            completed_units=succeeded + failed,
            total_units=len(batches) + pending_review,
            succeeded=succeeded,
            failed=failed,
            pending_review=pending_review + pending_batches,
        )

    def _update_task(
        self,
        store: TaskStore,
        task_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        task = store.update_task(task_id, **changes)
        self._emit(
            "batch.task-updated",
            {
                "taskId": task_id,
                "status": task["status"],
                "progress": task["progress"],
                "progressSnapshot": task.get("progressSnapshot"),
                "currentStage": task["currentStage"],
                "error": task["error"],
            },
        )
        return task

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        try:
            self.event_sink(event, data)
        except Exception as exc:
            self.logger.write(
                "warn",
                "batch.event_emit_failed",
                "Failed to emit batch task event",
                {"event": event, "error": repr(exc)},
            )
        self.logger.write("info", event, event, data)

    @staticmethod
    def _parse_config(task: dict[str, Any]) -> BatchTaskConfig:
        payload = dict(task.get("input") or {})
        raw_queries = payload.get("queries") or payload.get("keywords") or []
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        queries = tuple(str(item).strip() for item in raw_queries if str(item).strip())
        if not queries:
            raise ValidationError("QUERIES_REQUIRED", "请至少输入一个批量检索关键词")
        match_mode = str(payload.get("matchMode") or "keyword")
        resolve_search_mode(match_mode)
        if match_mode == "title":
            raise ValidationError(
                "BATCH_EXACT_TITLE_UNSUPPORTED",
                "精确完整标题只适用于逐篇任务；批量任务必须使用关键词模式",
            )
        library = str(payload.get("library") or "both")
        if library not in {"central", "local", "both"}:
            raise ValidationError("INVALID_LIBRARY", "library must be central, local or both")
        catalog_format = str(payload.get("catalogFormat") or "xlsx")
        if catalog_format not in {"xlsx", "xls", "html-xls"}:
            raise ValidationError("INVALID_CATALOG_FORMAT", "catalogFormat is unsupported")
        full_format = str(payload.get("fullFormat") or "docx")
        if full_format != "docx":
            raise ValidationError("INVALID_FULL_FORMAT", "本阶段仅支持 Word DOCX ZIP")
        try:
            page_size = int(payload.get("pageSize") or 50)
        except (TypeError, ValueError) as exc:
            raise ValidationError("INVALID_PAGING", "pageSize must be an integer") from exc
        if page_size not in {10, 20, 50, 100}:
            raise ValidationError("INVALID_PAGE_SIZE", "pageSize must be 10, 20, 50 or 100")
        year_selection = parse_year_selection(payload)
        page_selection = parse_page_selection(payload)
        page_selection = restore_page_selection(
            page_selection,
            dict(task.get("checkpoint") or {}),
        )
        try:
            search_strategy_version = int(
                payload.get("searchStrategyVersion") or 1
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "INVALID_SEARCH_STRATEGY",
                "searchStrategyVersion must be 1 or 2",
            ) from exc
        if search_strategy_version not in {1, 2}:
            raise ValidationError(
                "INVALID_SEARCH_STRATEGY",
                "searchStrategyVersion must be 1 or 2",
            )
        output_value = payload.get("outputDir") or task.get("outputDir") or ""
        if not str(output_value).strip():
            raise ValidationError("OUTPUT_DIR_REQUIRED", "批量任务必须指定输出目录")
        return BatchTaskConfig(
            queries=queries,
            match_mode=match_mode,
            library=library,
            year_selection=year_selection,
            province=str(payload.get("province") or "全部省份"),
            timeliness=str(payload.get("timeliness") or "全部（仅记录元数据，不筛选）"),
            page_size=page_size,
            page_selection=page_selection,
            output_dir=Path(str(output_value)).resolve(),
            catalog_format=catalog_format,
            full_format=full_format,
            search_strategy_version=search_strategy_version,
        )

    @staticmethod
    def _config_payload(config: BatchTaskConfig) -> dict[str, Any]:
        return {
            "queries": list(config.queries),
            "matchMode": config.match_mode,
            "library": config.library,
            **config.year_selection.to_payload(),
            "province": config.province,
            "timeliness": config.timeliness,
            "pageSize": config.page_size,
            **config.page_selection.to_payload(),
            "outputDir": str(config.output_dir),
            "catalogFormat": config.catalog_format,
            "fullFormat": config.full_format,
            "maxBatchSize": 50,
            "searchStrategyVersion": config.search_strategy_version,
        }
