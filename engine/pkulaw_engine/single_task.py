from __future__ import annotations

import hashlib
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from .browser_lease import BrowserLeaseManager
from .browser_bridge import BrowserBridge
from .collector_preview import checkpoint_preview_snapshot
from .controls.manual_resume import checkpoint_after_manual_continue
from .controls.task_control import TaskControl, TaskStopped
from .download_intent import DownloadIntentExhausted
from .logging_utils import JsonLineLogger
from .policy import (
    group_results,
    keyword_result_identity,
    normalize_title,
    policy_identity,
    policy_filename,
    safe_filename,
    validate_downloaded_word,
)
from .progress import (
    build_progress_snapshot,
    single_download_overall,
    single_scan_overall,
)
from .scan_journal import ScanJournal, ScanJournalError, config_fingerprint
from .search_window import (
    YearPartitionRequired,
    assess_result_window,
)
from .selections.years import (
    SearchCombination,
    YearSelection,
    expand_search_combinations,
    finalize_year_stats,
    mark_current_year_status,
    parse_year_selection,
)
from .selections.pagination import (
    PageSelection,
    page_signature,
    pagination_transition_error,
    parse_page_selection,
    restore_page_selection,
    should_pause_for_page_limit,
)
from .sources.pkulaw import (
    DownloadUnclear,
    FilterNotApplied,
    InstitutionQuotaReached,
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
from .storage import TaskStore, ValidationError


@dataclass(frozen=True)
class SingleTaskConfig:
    queries: tuple[str, ...]
    match_mode: str
    library: str
    year_selection: YearSelection
    province: str
    timeliness: str
    page_size: int
    page_selection: PageSelection
    output_dir: Path


class SingleTaskManager:
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

    @staticmethod
    def _structured_item_error(exc: Exception) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": str(
                getattr(exc, "code", "")
                or "ITEM_PROCESSING_FAILED"
            ),
            "message": str(exc),
        }
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            error["details"] = dict(details)
        return error

    def start(
        self,
        task_id: str,
        retry_failed: bool = False,
        lease_id: str = "",
    ) -> dict[str, Any]:
        with self.lock:
            existing = self.controllers.get(task_id)
            if existing and existing.thread and existing.thread.is_alive():
                return self._details(task_id)
            if self.browser_lease is not None:
                self.browser_lease.validate(lease_id, task_id, "single")

            store = TaskStore(self.database_path)
            try:
                task = store.get_task(task_id)
                if task is None:
                    raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
                if task["mode"] != "single":
                    raise ValidationError("TASK_MODE_MISMATCH", "该任务不是逐篇任务")
                if retry_failed:
                    store.reset_task_items_for_retry(task_id)
                config = self._parse_config(task)
            finally:
                store.close()

            controller = TaskControl(task_id)
            thread = threading.Thread(
                target=self._run_with_lease,
                args=(task_id, config, controller, lease_id),
                name=f"single-task-{task_id[:8]}",
                daemon=True,
            )
            controller.thread = thread
            self.controllers[task_id] = controller
            try:
                thread.start()
            except Exception:
                self.controllers.pop(task_id, None)
                raise
            return self._details(task_id)

    def stop(self, task_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            with self.lock:
                controller = self.controllers.get(task_id)
            if task["status"] in {"stopping", "stopped"}:
                return {"task": task, "items": store.list_task_items(task_id)}
            if not controller or not controller.thread or not controller.thread.is_alive():
                if task["status"] in {
                    "preflight",
                    "running",
                    "pausing",
                    "paused",
                    "waiting_login",
                    "waiting_manual",
                    "verifying",
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
                return self._details(task_id)
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
                    "single.stopping",
                    {"taskId": task_id, "previousStatus": task["status"]},
                )
        finally:
            store.close()
        return self._details(task_id)

    def pause(self, task_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if task["status"] in {"pausing", "paused"}:
                return {"task": task, "items": store.list_task_items(task_id)}
            if task["status"] not in {"preflight", "running", "verifying"}:
                raise ValidationError("TASK_NOT_PAUSABLE", "当前任务状态不能暂停")
            with self.lock:
                controller = self.controllers.get(task_id)
            if not controller or not controller.thread or not controller.thread.is_alive():
                raise ValidationError("TASK_NOT_RUNNING", "该逐篇任务没有正在运行的工作线程")
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
                    "single.pause-requested",
                    {"taskId": task_id, "reason": "用户手动暂停"},
                )
        finally:
            store.close()
        return self._details(task_id)

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
                raise ValidationError("TASK_NOT_PAUSED", "该逐篇任务当前不处于暂停状态")
            if task["status"] in {"preflight", "running"} and not controller.paused:
                return {"task": task, "items": store.list_task_items(task_id)}
            if task["status"] not in {"pausing", "paused"}:
                raise ValidationError("TASK_NOT_PAUSED", "该逐篇任务当前不处于暂停状态")
            self._validate_resume_for_task()
            resume_requested = False
            with controller.condition:
                if controller.request_resume():
                    checkpoint = {
                        key: value
                        for key, value in dict(task.get("checkpoint") or {}).items()
                        if key
                        not in {
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
                self._emit("single.resumed", {"taskId": task_id})
        finally:
            store.close()
        return self._details(task_id)

    def continue_to_last(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(task_id)
        if not controller or not controller.thread or not controller.thread.is_alive():
            raise ValidationError("TASK_NOT_WAITING", "该逐篇任务当前没有等待分页决策")
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if (
                task["status"] != "waiting_manual"
                or (task.get("error") or {}).get("code") != "PAGE_LIMIT_REACHED"
            ):
                raise ValidationError(
                    "PAGE_LIMIT_DECISION_NOT_AVAILABLE",
                    "当前没有待处理的页数上限决策",
                )
            with controller.condition:
                checkpoint = dict(task.get("checkpoint") or {})
                checkpoint.update(
                    {
                        "pageMode": "last",
                        "pageSelection": {"mode": "last"},
                        "pageLimitGate": False,
                        "pageLimitDecision": "continue_to_last",
                        "pageModeUpdatedAt": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                        ),
                    }
                )
                self._update_task(
                    store,
                    task_id,
                    checkpoint=checkpoint,
                )
                controller.request_continue()
            self._emit(
                "single.page-limit-continue",
                {"taskId": task_id, "decision": "continue_to_last"},
            )
        finally:
            store.close()
        return self._details(task_id)

    def continue_task(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(task_id)
        if not controller or not controller.thread or not controller.thread.is_alive():
            raise ValidationError("TASK_NOT_WAITING", "该逐篇任务当前没有等待人工继续")
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if task["status"] not in {"waiting_login", "waiting_manual"}:
                return {"task": task, "items": store.list_task_items(task_id)}
            if (
                task["currentStage"] == "waiting-candidate"
                or (task.get("checkpoint") or {}).get("currentAction")
                == "candidate-selection"
            ):
                raise ValidationError(
                    "CANDIDATE_SELECTION_REQUIRED",
                    "当前需要先选择同名政策版本，不能使用普通继续",
                )
            if (task.get("error") or {}).get("code") == "PAGE_LIMIT_REACHED":
                raise ValidationError(
                    "PAGE_LIMIT_REACHED",
                    "请选择“继续到尾页”或停止任务",
                )
            if controller.request_continue():
                self._emit(
                    "single.manual-continue-requested",
                    {"taskId": task_id, "status": task["status"]},
                )
        finally:
            store.close()
        return self._details(task_id)

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
                if (task.get("error") or {}).get("code") == "PAGE_LIMIT_REACHED":
                    continue
                controller.request_continue()
        finally:
            store.close()

    def choose_candidate(
        self,
        task_id: str,
        item_id: str,
        candidate_key: str,
    ) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            item = store.get_task_item(item_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if item is None or item["taskId"] != task_id:
                raise ValidationError("TASK_ITEM_NOT_FOUND", "任务项不存在")
            payload = dict(item.get("payload") or {})
            candidates = list(payload.get("candidates") or [])
            chosen = next(
                (
                    candidate
                    for candidate in candidates
                    if str(candidate.get("policyId") or candidate.get("url") or "") == candidate_key
                ),
                None,
            )
            if chosen is None:
                raise ValidationError("CANDIDATE_NOT_FOUND", "所选同名版本不在候选列表中")
            payload["selectedCandidateKey"] = candidate_key
            payload["selectedCandidate"] = chosen
            store.upsert_task_item(
                task_id,
                policy_key=item["policyKey"],
                title=item["title"],
                status="running",
                source_type=item.get("sourceType", ""),
                progress=item.get("progress", 0.0),
                payload=payload,
                current_stage="candidate-selected",
            )
        finally:
            store.close()

        with self.lock:
            controller = self.controllers.get(task_id)
        if not controller or not controller.thread or not controller.thread.is_alive():
            raise ValidationError("TASK_NOT_WAITING", "该逐篇任务当前没有等待同名版本选择")
        controller.request_choice(item_id, candidate_key)
        return self.details(task_id)

    def details(self, task_id: str) -> dict[str, Any]:
        return self._details(task_id)

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
        config: SingleTaskConfig,
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
        config: SingleTaskConfig,
        controller: TaskControl,
    ) -> None:
        store = TaskStore(self.database_path)
        adapter = PkulawAdapter(self.browser_bridge, self.logger)
        try:
            task_input = dict(
                ((store.get_task(task_id) or {}).get("input") or {})
            )
            preview_snapshot = checkpoint_preview_snapshot(task_input)
            preflight_snapshot = build_progress_snapshot(
                stage="preflight",
                overall=0.05,
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
                    "progressSnapshot": preflight_snapshot.to_dict(),
                    **(
                        {"previewSnapshot": preview_snapshot}
                        if preview_snapshot
                        else {}
                    ),
                },
            )
            self._safe_boundary(
                store,
                controller,
                task_id,
                current_action="preflight",
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
                store,
                controller,
                task_id,
                current_action="preflight-complete",
            )
            self._update_task(
                store,
                task_id,
                status="running",
                current_stage="collecting",
                clear_error=True,
            )

            try:
                all_rows, scans = self._collect(
                    task_id,
                    config,
                    adapter,
                    store,
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
                    "single.year-partition-required",
                    {
                        "taskId": task_id,
                        **partition.to_dict(),
                        "downloadsStarted": 0,
                    },
                )
                all_rows, scans = self._collect(
                    task_id,
                    config,
                    adapter,
                    store,
                    controller,
                )
            self._ensure_processing_selector_profile(adapter)
            groups = group_results(
                all_rows,
                identity_mode=config.match_mode,
            )
            self._prepare_items(
                store,
                task_id,
                groups,
                match_mode=config.match_mode,
                queries=config.queries,
            )
            self._seal_scan_journal(store, task_id, config)
            items = store.list_task_items(task_id)
            self._emit(
                "single.items-prepared",
                {
                    "taskId": task_id,
                    "itemCount": len(items),
                    "scanCount": scans,
                    "checkpoint": store.get_task(task_id).get("checkpoint", {}),
                },
            )

            for index, item in enumerate(items, start=1):
                self._safe_boundary(
                    store,
                    controller,
                    task_id,
                    current_action="next-item",
                )
                current = store.get_task_item(item["id"])
                if current is None:
                    continue
                if current["status"] in {"completed", "pending_review"}:
                    continue
                try:
                    self._process_item(
                        store,
                        task_id,
                        current,
                        index,
                        len(items),
                        config,
                        adapter,
                        controller,
                    )
                except (
                    InstitutionQuotaReached,
                    LoginRequired,
                    ManualActionRequired,
                    DownloadUnclear,
                    DownloadIntentExhausted,
                    ResultWindowTruncated,
                    ResultsStale,
                    SelectorProfileMismatch,
                    SelectorUnstable,
                    TaskStopped,
                ):
                    raise
                except Exception as exc:
                    failed_item = store.get_task_item(current["id"]) or current
                    error = self._structured_item_error(exc)
                    store.upsert_task_item(
                        task_id,
                        policy_key=failed_item["policyKey"],
                        title=failed_item["title"],
                        status="failed",
                        source_type=failed_item.get("sourceType", ""),
                        progress=failed_item.get("progress", 0.0),
                        payload=dict(failed_item.get("payload") or {}),
                        error=error,
                        current_stage="failed",
                    )
                    self.logger.write(
                        "warn",
                        "single.item_failed",
                        "Single policy item failed",
                        {
                            "taskId": task_id,
                            "itemId": current["id"],
                            "title": current["title"],
                            "error": repr(exc),
                        },
                    )
                    self._emit(
                        "single.item-failed",
                        {
                            "taskId": task_id,
                            "itemId": current["id"],
                            "title": current["title"],
                            "error": error,
                        },
                    )
                    try:
                        adapter.return_to_results()
                    except Exception:
                        pass

            items = store.list_task_items(task_id)
            controller.check_stop()
            completed = sum(item["status"] == "completed" for item in items)
            failed = sum(item["status"] == "failed" for item in items)
            pending_review = sum(
                item["status"] == "pending_review" for item in items
            )
            final_snapshot = build_progress_snapshot(
                stage="completed" if completed == len(items) else "partial",
                overall=1.0,
                completed_units=len(items),
                total_units=len(items),
                succeeded=completed,
                failed=failed,
                pending_review=pending_review,
            )
            if completed == len(items):
                status = "completed"
                stage = "completed"
                message = f"逐篇采集完成，共处理 {completed} 条政策"
            else:
                status = "partial"
                stage = "partial"
                message = f"逐篇采集部分完成：{completed}/{len(items)}"
            self._update_task(
                store,
                task_id,
                status=status,
                current_stage=stage,
                progress=final_snapshot.overall,
                error=None,
                clear_error=True,
                checkpoint=finalize_year_stats({
                    **store.get_task(task_id).get("checkpoint", {}),
                    "completedItems": completed,
                    "totalItems": len(items),
                    "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "progressSnapshot": final_snapshot.to_dict(),
                }),
            )
            self._emit(
                "single.completed",
                {
                    "taskId": task_id,
                    "status": status,
                    "message": message,
                    "completedItems": completed,
                    "totalItems": len(items),
                },
            )
        except TaskStopped:
            checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
            checkpoint["stoppedAt"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            stopped_snapshot = self._progress_snapshot_for_items(
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
                error={"code": "TASK_STOPPED", "message": "任务已停止，逐条断点已保留"},
                checkpoint=checkpoint,
            )
            self._emit("single.stopped", {"taskId": task_id})
        except (
            InstitutionQuotaReached,
            LoginRequired,
            ManualActionRequired,
            DownloadUnclear,
            YearFilterUnavailable,
            ResultsStale,
            DownloadIntentExhausted,
            ResultWindowTruncated,
            SelectorProfileMismatch,
            SelectorUnstable,
            FilterNotApplied,
            PageSizeNotApplied,
            ResultGroupingUnavailable,
            ScanJournalError,
        ) as exc:
            status = "waiting_login" if isinstance(exc, LoginRequired) else "stopped"
            checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
            safety_snapshot = self._progress_snapshot_for_items(
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
                current_stage=getattr(exc, "code", "manual-action"),
                error={"code": getattr(exc, "code", "SOURCE_ACCESS_ERROR"), "message": str(exc)},
                progress=safety_snapshot.overall,
                checkpoint=checkpoint,
            )
            self._emit(
                "single.safety-stopped",
                {
                    "taskId": task_id,
                    "code": getattr(exc, "code", "SOURCE_ACCESS_ERROR"),
                    "message": str(exc),
                },
            )
        except Exception as exc:
            self.logger.write(
                "error",
                "single.task_failed",
                "Single task failed",
                {"taskId": task_id, "error": repr(exc)},
            )
            checkpoint = dict((store.get_task(task_id) or {}).get("checkpoint") or {})
            failed_snapshot = self._progress_snapshot_for_items(
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
                    "code": getattr(exc, "code", "SINGLE_TASK_FAILED"),
                    "message": str(exc),
                },
                progress=failed_snapshot.overall,
                checkpoint=checkpoint,
            )
            self._emit(
                "single.failed",
                {"taskId": task_id, "message": str(exc)},
            )
        finally:
            store.close()
            with self.lock:
                self.controllers.pop(task_id, None)

    def _collect(
        self,
        task_id: str,
        config: SingleTaskConfig,
        adapter: PkulawAdapter,
        store: TaskStore,
        controller: TaskControl,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        libraries = ["central", "local"] if config.library == "both" else [config.library]
        combinations: list[SearchCombination]
        rows_by_identity: dict[str, dict[str, Any]] = {}
        scans: list[dict[str, Any]] = []
        scan_index = 0
        year_stats: dict[str, dict[str, Any]] = {}
        combination_totals: list[dict[str, Any]] = []
        page_mode = config.page_selection.mode
        initial_checkpoint = dict(
            (store.get_task(task_id) or {}).get("checkpoint") or {}
        )
        year_partition = dict(
            initial_checkpoint.get("yearPartition") or {}
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
            )
        else:
            combinations = expand_search_combinations(
                config.queries,
                libraries,
                config.year_selection,
            )
        total_planned = max(1, len(combinations))
        journal = self._scan_journal(task_id, config)
        journal_records = journal.read()
        records_by_combination: dict[
            tuple[str, str, str],
            dict[int, dict[str, Any]],
        ] = {}
        for record in journal_records:
            records_by_combination.setdefault(
                (
                    str(record["query"]),
                    str(record["library"]),
                    str(record["effectiveYear"]),
                ),
                {},
            )[int(record["page"])] = record
        search_recovery_required = checkpoint_requires_controlled_research(
            initial_checkpoint,
            has_existing_items=bool(store.list_task_items(task_id)),
        )

        for combination in combinations:
            query = combination.query
            library = combination.library
            year = combination.year
            stats_key = f"{combination.query_index}:{library}:{year}"
            stats = year_stats.setdefault(
                stats_key,
                {
                    "query": query,
                    "library": library,
                    "year": year,
                    "status": "pending",
                    "resultCount": 0,
                    "rowsRead": 0,
                    "pagesScanned": 0,
                },
            )
            existing_pages = records_by_combination.get(
                (query, library, year),
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
                        "扫描日志页不连续，拒绝从旧 DOM 或旧断点继续",
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
                    for row in record.get("rows") or []:
                        identity = (
                            keyword_result_identity(row)
                            if config.match_mode == "keyword"
                            else policy_identity(row)
                        )
                        rows_by_identity.setdefault(identity, row)
                    scan = self._scan_from_journal_record(record)
                    scans.append(scan)
                    scan_index += 1
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
                                store.get_task(task_id).get("checkpoint")
                                or {}
                            ),
                            **combination.checkpoint(),
                            "yearStats": list(year_stats.values()),
                            "scanJournal": journal.checkpoint_summary(),
                            "currentAction": "scan-journal-combination-reused",
                        },
                    )
                    continue
            stats["status"] = "running"
            checkpoint = {
                **store.get_task(task_id).get("checkpoint", {}),
                **combination.checkpoint(),
                "yearMode": config.year_selection.mode,
                "yearStart": config.year_selection.start_year,
                "yearEnd": config.year_selection.end_year,
                "combinationCount": len(combinations),
                "pageMode": page_mode,
                "yearStats": list(year_stats.values()),
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
            }
            scan_snapshot = build_progress_snapshot(
                stage="scan",
                overall=0.05
                + 0.25
                * (combination.combination_index - 1)
                / total_planned,
                completed_units=scan_index,
                total_units=scan_index,
            )
            checkpoint["progressSnapshot"] = scan_snapshot.to_dict()
            self._update_task(
                store,
                task_id,
                status="running",
                current_stage="searching",
                progress=scan_snapshot.overall,
                checkpoint=checkpoint,
            )
            self._safe_boundary(
                store,
                controller,
                task_id,
                current_action="search",
            )
            try:
                state = self._with_manual_retry(
                    store,
                    controller,
                    task_id,
                    lambda query=query, library=library, year=year: adapter.search(
                        keyword=query,
                        match_mode=config.match_mode,
                        library=library,
                        year=year,
                        province=config.province,
                        timeliness=config.timeliness,
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
                    "single.combination-skipped",
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
                expected_page=page,
            )
            resume_record: dict[str, Any] | None = None
            if existing_ordered:
                first_record = existing_ordered[0]
                if (
                    int(first_record.get("page") or 0) != 1
                    or search_session.result_signature
                    != str(first_record.get("resultSignature") or "")
                ):
                    raise ScanJournalError(
                        "SCAN_JOURNAL_IDENTITY_MISMATCH",
                        "重新检索后的第 1 页结果签名与扫描日志不一致",
                    )
                journal_total_pages = int(
                    existing_ordered[-1].get("totalPages") or 0
                )
                if (
                    journal_total_pages
                    and total_pages != journal_total_pages
                ):
                    raise ScanJournalError(
                        "SCAN_JOURNAL_IDENTITY_MISMATCH",
                        "重新检索后的有效总页数与扫描日志不一致",
                    )
                resume_record = existing_ordered[-1]
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
                    expected_page=page,
                )
            combination_total = {
                "query": query,
                "library": library,
                "year": year,
                "combinationIndex": combination.combination_index,
                "page": page,
                "totalPages": total_pages,
            }
            combination_totals.append(combination_total)
            self._update_task(
                store,
                task_id,
                checkpoint={
                    **store.get_task(task_id).get("checkpoint", {}),
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
                    expected_page=page,
                )
                if config.match_mode == "title":
                    query_norm = normalize_title(query)
                    rows = [
                        row for row in rows
                        if normalize_title(row.get("title", "")) == query_norm
                    ]
                for row in rows:
                    row["library"] = library
                    row["query"] = query
                    row["year"] = year
                    identity = str(row.get("policyId") or row.get("url") or "")
                    rows_by_identity.setdefault(identity, row)
                stats["resultCount"] = max(
                    int(stats.get("resultCount") or 0),
                    int(state.get("resultCount") or 0),
                )
                stats["pagesScanned"] = int(stats.get("pagesScanned") or 0) + 1
                stats["rowsRead"] = (
                    int(stats.get("rowsRead") or 0) + len(rows)
                )
                scan = {
                    "query": query,
                    "library": library,
                    "year": year,
                    "yearIndex": combination.year_index,
                    "combinationIndex": combination.combination_index,
                    "page": page,
                    "totalPages": total_pages,
                    "resultCount": state.get("resultCount"),
                    "rowCount": len(rows),
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
                                    existing_ordered[0].get("page") or 1
                                ),
                                "resultSignature": str(
                                    existing_ordered[0].get(
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
                scan_index += 1
                scan_snapshot = build_progress_snapshot(
                    stage="scan",
                    overall=single_scan_overall(
                        combination_index=combination.combination_index,
                        combination_count=total_planned,
                        page=page,
                        total_pages=total_pages,
                    ),
                    completed_units=scan_index,
                    total_units=max(
                        scan_index,
                        total_pages * total_planned,
                    ),
                )
                self._emit(
                    "single.search-page",
                    {
                        "taskId": task_id,
                        **scan,
                        "collectedRows": len(rows_by_identity),
                        "yearStats": list(year_stats.values()),
                    },
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
                if page >= total_pages:
                    stats["status"] = (
                        "no_result"
                        if int(stats.get("resultCount") or 0) == 0
                        else "completed"
                    )
                    break
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
                expected = page + 1
                previous_signature = page_signature(list(state.get("rows") or []))
                previous_page = page
                previous_total_pages = total_pages
                self._safe_boundary(
                    store,
                    controller,
                    task_id,
                    current_action="next-page",
                )
                def load_next_page() -> dict[str, Any]:
                    next_state = adapter.next_page(expected)
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
                search_session = self._require_search_state(
                    state,
                    config=config,
                    query=query,
                    library=library,
                    year=year,
                    expected_page=expected,
                )
                self._safe_boundary(
                    store,
                    controller,
                    task_id,
                    current_action="after-next-page",
                )
                page = int(state.get("page") or expected)
                total_pages = int(state.get("totalPages") or total_pages)

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
            if (
                actual_results != expected_results
                or actual_rows != expected_results
            ):
                raise ResultWindowTruncated(
                    "按年份拆分后结果数不一致："
                    f"期望 {expected_results}，"
                    f"结果数合计 {actual_results}，"
                    f"实际读取 {actual_rows}"
                )
            year_partition.update(
                {
                    "status": "complete",
                    "actualResultCount": actual_results,
                    "actualRowsRead": actual_rows,
                    "completedCombinations": len(partition_stats),
                }
            )

        current_task = store.get_task(task_id)
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="results-complete",
        )
        self._update_task(
            store,
            task_id,
            status="running",
            progress=0.30,
            current_stage="results-collected",
            checkpoint={
                **((current_task or {}).get("checkpoint") or {}),
                "scannedPages": len(scans),
                "collectedRows": len(rows_by_identity),
                "lastScan": scans[-1] if scans else None,
                "yearStats": list(year_stats.values()),
                "currentAction": "results-complete",
                "scanJournal": journal.checkpoint_summary(),
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
                                    current_task
                                    or {}
                                ).get("resultWindowProbe")
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
                "progressSnapshot": build_progress_snapshot(
                    stage="download",
                    overall=0.30,
                    completed_units=len(scans),
                    total_units=len(scans),
                ).to_dict(),
            },
        )
        return list(rows_by_identity.values()), scans

    def _require_search_state(
        self,
        state: dict[str, Any],
        *,
        config: SingleTaskConfig,
        query: str,
        library: str,
        year: str,
        expected_page: int,
    ) -> SearchSession:
        request = SearchRequestIdentity(
            query=query,
            match_mode=config.match_mode,
            library=library,
            year=year,
            province=config.province,
            timeliness=config.timeliness,
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

    def _scan_journal(
        self,
        task_id: str,
        config: SingleTaskConfig,
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

    @staticmethod
    def _year_partition_combinations(
        config: SingleTaskConfig,
        libraries: list[str],
        years: list[str],
        *,
        scope_query: str,
        scope_library: str,
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
                    combinations.append(
                        SearchCombination(
                            query=query,
                            library=library,
                            year=year,
                            query_index=query_index,
                            library_index=library_index,
                            year_index=year_index,
                            combination_index=len(combinations) + 1,
                        )
                    )
        return combinations

    def _seal_scan_journal(
        self,
        store: TaskStore,
        task_id: str,
        config: SingleTaskConfig,
    ) -> dict[str, Any]:
        summary = self._scan_journal(task_id, config).seal()
        task = store.get_task(task_id)
        if task is None:
            raise ValidationError(
                "TASK_NOT_FOUND",
                f"task {task_id} was not found",
            )
        self._update_task(
            store,
            task_id,
            checkpoint={
                **dict(task.get("checkpoint") or {}),
                "scanJournal": summary,
                "scanJournalSealed": True,
                "scanCompleted": True,
            },
        )
        return summary

    @staticmethod
    def _scan_from_journal_record(
        record: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "query": record["query"],
            "library": record["library"],
            "year": record["effectiveYear"],
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

    def _prepare_items(
        self,
        store: TaskStore,
        task_id: str,
        groups: list[dict[str, Any]],
        *,
        match_mode: str,
        queries: tuple[str, ...],
    ) -> None:
        existing = {item["policyKey"]: item for item in store.list_task_items(task_id)}
        for group in groups:
            previous = existing.get(group["policyKey"])
            previous_payload = dict(previous.get("payload") or {}) if previous else {}
            group_status = str(group.get("identityStatus") or "verified")
            status = (
                previous.get("status", "pending")
                if previous
                else (
                    "pending_review"
                    if group_status == "pending_review"
                    else "pending"
                )
            )
            payload = {
                **previous_payload,
                "candidates": group["candidates"],
                "identityStatus": group_status,
            }
            if group.get("sourceRow"):
                payload["sourceRow"] = group["sourceRow"]
            if group.get("identityError"):
                payload["identityError"] = group["identityError"]
            store.upsert_task_item(
                task_id,
                policy_key=group["policyKey"],
                title=group["title"],
                status=status,
                source_type=previous.get("sourceType", "") if previous else "",
                progress=previous.get("progress", 0.0) if previous else 0.0,
                payload=payload,
                error=(
                    previous.get("error")
                    if previous
                    else group.get("identityError")
                ),
                current_stage=(
                    previous.get("currentStage", "pending")
                    if previous
                    else (
                        "pending-review"
                        if group_status == "pending_review"
                        else "pending"
                    )
                ),
            )
        if match_mode == "title":
            found_titles = {
                normalize_title(str(group.get("title") or ""))
                for group in groups
            }
            for query in queries:
                normalized = normalize_title(query)
                if not normalized or normalized in found_titles:
                    continue
                policy_key = f"title:{normalized}"
                if policy_key in existing:
                    continue
                store.upsert_task_item(
                    task_id,
                    policy_key=policy_key,
                    title=query,
                    status="failed",
                    source_type="待核验",
                    payload={"candidates": [], "missingExactTitle": True},
                    error={
                        "code": "NO_EXACT_MATCH",
                        "message": "北大法宝未找到标题完全一致的政策",
                    },
                    current_stage="exact-title-missing",
                )

    def _process_item(
        self,
        store: TaskStore,
        task_id: str,
        item: dict[str, Any],
        index: int,
        total: int,
        config: SingleTaskConfig,
        adapter: PkulawAdapter,
        controller: TaskControl,
    ) -> None:
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="prepare-item",
        )
        payload = dict(item.get("payload") or {})
        if item.get("status") == "pending_review":
            return
        if (
            payload.get("missingExactTitle")
            and (item.get("error") or {}).get("code") == "NO_EXACT_MATCH"
        ):
            return
        candidates = list(payload.get("candidates") or [])
        if not candidates:
            store.upsert_task_item(
                task_id,
                policy_key=item["policyKey"],
                title=item["title"],
                status="failed",
                payload=payload,
                error={"code": "NO_CANDIDATES", "message": "没有可处理的政策候选"},
                current_stage="failed",
            )
            return

        selected_key = str(payload.get("selectedCandidateKey") or "")
        if not selected_key and len(candidates) > 1:
            policy_id = str(candidates[0].get("policyId") or "") if candidates else ""
            candidate_snapshot = self._progress_snapshot_for_items(
                store,
                task_id,
                stage="waiting-candidate",
                overall=single_download_overall(index - 1, total),
            )
            store.update_task(
                task_id,
                status="waiting_manual",
                progress=candidate_snapshot.overall,
                current_stage="waiting-candidate",
                checkpoint={
                    **store.get_task(task_id).get("checkpoint", {}),
                    "waitingItemId": item["id"],
                    "waitingItemTitle": item["title"],
                    "currentPolicyId": policy_id,
                    "currentAction": "candidate-selection",
                    "progressSnapshot": candidate_snapshot.to_dict(),
                },
            )
            store.upsert_task_item(
                task_id,
                policy_key=item["policyKey"],
                title=item["title"],
                status="waiting_manual",
                source_type=item.get("sourceType", ""),
                progress=0.0,
                payload=payload,
                current_stage="candidate-selection",
            )
            self._emit(
                "single.candidate-selection-required",
                {
                    "taskId": task_id,
                    "itemId": item["id"],
                    "title": item["title"],
                    "candidates": candidates,
                },
            )
            selected_key = controller.wait_for_choice(item["id"])
            refreshed = store.get_task_item(item["id"])
            if refreshed:
                item = refreshed
                payload = dict(item.get("payload") or {})
                candidates = list(payload.get("candidates") or candidates)
                selected_key = str(payload.get("selectedCandidateKey") or selected_key)

        candidate = next(
            (
                value
                for value in candidates
                if str(value.get("policyId") or value.get("url") or "") == selected_key
            ),
            candidates[0] if len(candidates) == 1 else None,
        )
        if candidate is None:
            raise ValidationError("CANDIDATE_NOT_FOUND", f"同名版本选择失败：{item['title']}")

        download_snapshot = self._progress_snapshot_for_items(
            store,
            task_id,
            stage="download",
            overall=single_download_overall(index - 1, total),
        )
        store.update_task(
            task_id,
            status="running",
            progress=download_snapshot.overall,
            current_stage=f"detail-{index}-{total}",
            checkpoint={
                **store.get_task(task_id).get("checkpoint", {}),
                "currentPolicyId": str(
                    candidate.get("policyId") or candidate.get("url") or ""
                ),
                "currentTitle": str(candidate.get("title") or item["title"]),
                "currentAction": "open-detail",
                "progressSnapshot": download_snapshot.to_dict(),
            },
        )
        store.upsert_task_item(
            task_id,
            policy_key=item["policyKey"],
            title=item["title"],
            status="running",
            source_type="北大法宝原件",
            progress=0.25,
            payload=payload,
            current_stage="detail",
        )

        detail = self._with_manual_retry(
            store,
            controller,
            task_id,
            lambda: adapter.open_detail(candidate),
            action_name="open-detail",
        )
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="after-detail",
        )
        if normalize_title(detail.get("title", "")) != normalize_title(candidate.get("title", "")):
            raise ValidationError(
                "DETAIL_TITLE_MISMATCH",
                f"详情标题与候选不一致：{detail.get('title')} != {candidate.get('title')}",
            )

        download_intent = self.browser_bridge.create_download_intent(
            task_id=task_id,
            kind="word",
            source_url=str(candidate.get("url") or ""),
            expected_suffixes=(".doc", ".docx"),
        )
        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadIntent": download_intent,
                "currentDownloadStatus": "pending",
                "currentAction": "word-download",
            },
        )
        cursor = self.browser_bridge.download_cursor()
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="word-download",
        )
        clicked = self._with_manual_retry(
            store,
            controller,
            task_id,
            adapter.click_word_download,
            action_name="word-download",
        )
        self._emit(
            "single.download-requested",
            {
                "taskId": task_id,
                "itemId": item["id"],
                "clicked": clicked,
                "downloadIntent": download_intent,
            },
        )

        def record_download_progress(event: dict[str, Any]) -> None:
            outcome = str(event.get("outcome") or "")
            if outcome not in {"started", "completed", "failed"}:
                return
            checkpoint = dict(
                (store.get_task(task_id) or {}).get("checkpoint") or {}
            )
            checkpoint.update(
                {
                    "currentDownloadIntent": {
                        **download_intent,
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

        download = self.browser_bridge.wait_for_download_intent(
            str(download_intent["intentId"]),
            cursor,
            timeout_seconds=75,
            on_event=record_download_progress,
        )
        if not download:
            raise DownloadUnclear("点击 Word 后 75 秒内未收到 Electron 下载完成事件")
        if download.get("event") != "completed":
            raise DownloadUnclear(f"Electron 下载失败：{download!r}")

        source_path = Path(str(download.get("path") or ""))
        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadIntent": {
                    **download_intent,
                    "downloadId": download.get("downloadId"),
                    "path": str(source_path),
                    "status": "completed",
                    "matchedDownloads": 1,
                },
                "currentDownloadStatus": "completed",
            },
        )
        valid, reason = validate_downloaded_word(source_path)
        self.browser_bridge.record_download_intent_validation(
            str(download_intent["intentId"]),
            valid=valid,
            actual_kind=source_path.suffix.lower(),
            detail=reason,
        )
        if not valid:
            self._update_task(
                store,
                task_id,
                checkpoint={
                    **(store.get_task(task_id).get("checkpoint") or {}),
                    "currentDownloadStatus": "validation_failed",
                    "downloadValidationDetail": reason,
                },
            )
            file_size = source_path.stat().st_size if source_path.is_file() else 0
            digest = (
                hashlib.sha256(source_path.read_bytes()).hexdigest()
                if source_path.is_file()
                else ""
            )
            quarantine_path: Path | None = None
            if source_path.is_file():
                quarantine_dir = config.output_dir / "99_失败样本" / "Word校验失败"
                try:
                    quarantine_dir.mkdir(parents=True, exist_ok=True)
                    quarantine_name = (
                        f"{safe_filename(source_path.stem, 150)}{source_path.suffix.lower()}"
                    )
                    quarantine_path = quarantine_dir / quarantine_name
                    counter = 2
                    while quarantine_path.exists():
                        quarantine_path = (
                            quarantine_dir
                            / f"{safe_filename(source_path.stem, 150)}_{counter}"
                            f"{source_path.suffix.lower()}"
                        )
                        counter += 1
                    shutil.move(str(source_path), str(quarantine_path))
                except OSError:
                    quarantine_path = None
            failed_payload = {
                **payload,
                "downloadValidation": {
                    "status": (
                        "quarantined"
                        if quarantine_path is not None
                        else "staging"
                        if source_path.is_file()
                        else "missing"
                    ),
                    "originalPath": str(source_path),
                    "filePath": str(quarantine_path or source_path),
                    "extension": source_path.suffix.lower(),
                    "size": file_size,
                    "sha256": digest,
                    "reason": reason,
                },
            }
            store.upsert_task_item(
                task_id,
                policy_key=item["policyKey"],
                title=item["title"],
                status="failed",
                source_type="北大法宝原件",
                progress=0.25,
                payload=failed_payload,
                error={"code": "INVALID_WORD_FILE", "message": reason},
                current_stage="failed",
            )
            raise ValidationError("INVALID_WORD_FILE", reason)

        self._update_task(
            store,
            task_id,
            checkpoint={
                **(store.get_task(task_id).get("checkpoint") or {}),
                "currentDownloadStatus": "validated",
                "downloadValidationDetail": reason,
            },
        )

        publication_date = str(
            detail.get("publicationDate") or candidate.get("publicationDate") or ""
        )
        filename = policy_filename(
            library=str(candidate.get("library") or item.get("payload", {}).get("library") or "central"),
            title=str(detail.get("title") or candidate.get("title") or item["title"]),
            publication_date=publication_date,
            source_url=str(candidate.get("url") or ""),
            suffix=source_path.suffix or ".docx",
        )
        target_dir = config.output_dir / "01_来源Word" / "北大法宝"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename
        counter = 2
        while target_path.exists():
            target_path = target_dir / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
            counter += 1
        shutil.move(str(source_path), str(target_path))
        digest = hashlib.sha256(target_path.read_bytes()).hexdigest()

        completed_payload = {
            **payload,
            "selectedCandidateKey": selected_key,
            "selectedCandidate": candidate,
            "detail": detail,
            "sourceUrl": candidate.get("url"),
            "downloadedFile": str(target_path),
            "sha256": digest,
            "retrievedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "downloadIntent": download_intent,
            "downloadEvent": {
                "downloadId": download.get("downloadId"),
                "path": str(target_path),
                "intentId": download.get("intentId"),
            },
        }
        store.upsert_task_item(
            task_id,
            policy_key=item["policyKey"],
            title=str(detail.get("title") or candidate.get("title") or item["title"]),
            status="completed",
            source_type="北大法宝原件",
            progress=1.0,
            payload=completed_payload,
            current_stage="completed",
        )
        post_download_status = (
            "stopping"
            if controller.stop_requested
            else "pausing"
            if controller.pause_requested
            else "running"
        )
        downloaded_snapshot = self._progress_snapshot_for_items(
            store,
            task_id,
            stage="download",
            overall=single_download_overall(index, total),
        )
        self._update_task(
            store,
            task_id,
            status=post_download_status,
            progress=downloaded_snapshot.overall,
            current_stage=f"downloaded-{index}-{total}",
            checkpoint={
                **store.get_task(task_id).get("checkpoint", {}),
                "lastCompletedPolicyId": candidate.get("policyId"),
                "lastCompletedTitle": candidate.get("title"),
                "currentAction": "after-word-download",
                "progressSnapshot": downloaded_snapshot.to_dict(),
            },
        )
        self._emit(
            "single.item-completed",
            {
                "taskId": task_id,
                "itemId": item["id"],
                "title": detail.get("title"),
                "file": str(target_path),
                "sha256": digest,
            },
        )
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="after-word-download",
        )
        self._safe_boundary(
            store,
            controller,
            task_id,
            current_action="return-to-results",
        )
        self._with_manual_retry(
            store,
            controller,
            task_id,
            adapter.return_to_results,
            action_name="return-to-results",
        )

    def _with_manual_retry(
        self,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        action: Callable[[], Any],
        attempts: int = 3,
        action_name: str = "action",
    ) -> Any:
        last_error: Exception | None = None
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
                last_error = exc
                status = "waiting_login" if isinstance(exc, LoginRequired) else "waiting_manual"
                self._wait_for_manual(store, controller, task_id, str(exc), status)
        raise last_error or RuntimeError("manual recovery exhausted")

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
        snapshot = self._progress_snapshot_for_items(
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
            "single.page-limit-reached",
            {
                "taskId": task_id,
                "page": page,
                "totalPages": total_pages,
                "maxPages": max_pages,
            },
        )
        controller.manual_wait()
        latest = store.get_task(task_id) or {}
        latest_checkpoint = dict(latest.get("checkpoint") or {})
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
            "single.page-limit-continued",
            {"taskId": task_id, "pageMode": "last"},
        )
        return page_mode

    def _wait_for_manual(
        self,
        store: TaskStore,
        controller: TaskControl,
        task_id: str,
        message: str,
        status: str,
    ) -> None:
        current = store.get_task(task_id) or {}
        checkpoint = dict(current.get("checkpoint") or {})
        checkpoint.update(
            {
                "pauseKind": "automatic",
                "pauseReason": message,
                "manualStatus": status,
                "manualAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        snapshot = self._progress_snapshot_for_items(
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
            progress=snapshot.overall,
            current_stage=(
                "waiting-login" if status == "waiting_login" else "waiting-manual"
            ),
            error={"code": "MANUAL_ACTION_REQUIRED", "message": message},
            checkpoint=checkpoint,
        )
        self._emit(
            "single.manual-required",
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
    ) -> None:
        controller.check_stop()
        current = store.get_task(task_id)
        if current is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        checkpoint = dict(current.get("checkpoint") or {})
        checkpoint["currentAction"] = current_action
        store.update_task(task_id, checkpoint=checkpoint)

        def mark_paused(reason: str) -> None:
            paused_checkpoint = dict(
                (store.get_task(task_id) or {}).get("checkpoint") or checkpoint
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
            paused_snapshot = self._progress_snapshot_for_items(
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
                "single.paused",
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
            raise ValidationError(
                "BROWSER_NOT_BOUND",
                "继续前未检测到 Python 已绑定的网页目标",
            )
        if state.get("captchaVisible"):
            raise ValidationError(
                "MANUAL_VERIFICATION_REQUIRED",
                "继续前仍需完成滑块或人机验证",
            )
        if state.get("loginVisible") or not state.get("authenticated"):
            raise ValidationError(
                "LOGIN_REQUIRED",
                "继续前需要重新完成机构登录",
            )
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

    def _details(self, task_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            return {
                "task": task,
                "items": store.list_task_items(task_id),
                "timeline": store.list_task_events(task_id),
            }
        finally:
            store.close()

    @staticmethod
    def _progress_snapshot_for_items(
        store: TaskStore,
        task_id: str,
        *,
        stage: str,
        overall: float,
    ):
        items = store.list_task_items(task_id)
        succeeded = sum(item["status"] == "completed" for item in items)
        failed = sum(item["status"] == "failed" for item in items)
        pending_review = sum(
            item["status"] == "pending_review" for item in items
        )
        return build_progress_snapshot(
            stage=stage,
            overall=overall,
            completed_units=succeeded + failed,
            total_units=len(items),
            succeeded=succeeded,
            failed=failed,
            pending_review=pending_review,
        )

    def _update_task(
        self,
        store: TaskStore,
        task_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        task = store.update_task(task_id, **changes)
        self._emit(
            "single.task-updated",
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
                "single.event_emit_failed",
                "Failed to emit single task event",
                {"event": event, "error": repr(exc)},
            )
        self.logger.write("info", event, event, data)

    @staticmethod
    def _parse_config(task: dict[str, Any]) -> SingleTaskConfig:
        payload = dict(task.get("input") or {})
        raw_queries = payload.get("queries") or payload.get("keywords") or []
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        queries = tuple(str(item).strip() for item in raw_queries if str(item).strip())
        if not queries:
            raise ValidationError("QUERIES_REQUIRED", "请至少输入一个关键词或完整标题")
        match_mode = str(payload.get("matchMode") or "keyword")
        if match_mode not in {"keyword", "title"}:
            raise ValidationError("INVALID_MATCH_MODE", "matchMode must be keyword or title")
        library = str(payload.get("library") or "both")
        if library not in {"central", "local", "both"}:
            raise ValidationError("INVALID_LIBRARY", "library must be central, local or both")
        try:
            page_size = int(payload.get("pageSize") or 20)
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
        output_value = payload.get("outputDir") or task.get("outputDir") or ""
        if not str(output_value).strip():
            raise ValidationError("OUTPUT_DIR_REQUIRED", "逐篇任务必须指定输出目录")
        return SingleTaskConfig(
            queries=queries,
            match_mode=match_mode,
            library=library,
            year_selection=year_selection,
            province=str(payload.get("province") or "全部省份"),
            timeliness=str(payload.get("timeliness") or "全部（仅记录元数据，不筛选）"),
            page_size=page_size,
            page_selection=page_selection,
            output_dir=Path(str(output_value)).resolve(),
        )

    @staticmethod
    def _config_payload(config: SingleTaskConfig) -> dict[str, Any]:
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
        }
