from __future__ import annotations

import json
import sys
import time
import uuid
from collections import OrderedDict
from threading import Event, RLock, Thread
from typing import Any, TextIO

from . import __version__
from .batch_task import BatchTaskManager
from .browser_lease import BrowserLeaseBusy, BrowserLeaseManager
from .browser_bridge import BrowserBridge
from .collector_preview import (
    CollectorPreviewCancelled,
    CollectorPreviewService,
    checkpoint_preview_snapshot,
)
from .diagnostics import collect_diagnostics
from .logging_utils import JsonLineLogger
from .manual_recovery import decide_manual_recovery
from .processing_task import DataProcessingManager, can_use_government_fallback
from .protocol import (
    ENGINE_NAME,
    PROTOCOL_VERSION,
    ProtocolValidationError,
    event_message,
    response_message,
    validate_request,
)
from .single_task import SingleTaskManager
from .sources.pkulaw import PkulawAdapter
from .sources.search_mode import resolve_search_mode
from .storage import ACTIVE_TASK_STATUSES, TaskStore, ValidationError
from .task_settings_revision import build_task_revision


class EngineApplication:
    def __init__(
        self,
        store: TaskStore,
        output: TextIO,
        logger: JsonLineLogger,
        app_version: str,
        browser_bridge: BrowserBridge,
        browser_lease: BrowserLeaseManager | None = None,
    ) -> None:
        self.store = store
        self.output = output
        self.logger = logger
        self.app_version = app_version
        self.browser_bridge = browser_bridge
        self.browser_bridge.set_event_sink(
            lambda event, data: self.emit(event_message(event, data))
        )
        self._response_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_cached_responses = 1000
        self._emit_lock = RLock()
        self._operation_lock = RLock()
        self._operation_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_cached_operations = 1000
        self._preflight_threads: dict[str, Thread] = {}
        self._preflight_cancelled: dict[str, Event] = {}
        self._preflight_lock = RLock()
        self._settings_stop_timeout_seconds = 10.0
        self.shutdown_requested = False
        self.browser_lease = browser_lease or BrowserLeaseManager(
            event_sink=self._emit_lease_event
        )
        if browser_lease is not None:
            self.browser_lease.set_event_sink(self._emit_lease_event)
        recovered = self.store.recover_interrupted_tasks()
        recovered_runs = self.store.recover_interrupted_processing_runs()
        if recovered:
            self.logger.write(
                "warn",
                "task.interrupted_recovered",
                "Recovered interrupted active tasks",
                {
                    "count": len(recovered),
                    "taskIds": [task["id"] for task in recovered],
                },
            )
        if recovered_runs:
            self.logger.write(
                "warn",
                "processing.interrupted_recovered",
                "Recovered interrupted data processing runs",
                {
                    "count": len(recovered_runs),
                    "processingIds": [run["id"] for run in recovered_runs],
                },
            )
        self.single_manager = SingleTaskManager(
            store.database_path,
            browser_bridge,
            logger,
            self._emit_single_event,
            self.browser_lease,
        )
        self.batch_manager = BatchTaskManager(
            store.database_path,
            browser_bridge,
            logger,
            self._emit_batch_event,
            self.browser_lease,
        )
        self.preview_service = CollectorPreviewService(
            browser_bridge,
            logger,
        )
        self.data_manager = DataProcessingManager(
            store.database_path,
            browser_bridge,
            logger,
            self._emit_data_event,
            self.browser_lease,
        )

    def emit(self, message: dict[str, Any]) -> None:
        with self._emit_lock:
            self.output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output.flush()

    def _emit_single_event(self, event: str, data: dict[str, Any]) -> None:
        self.emit(event_message(event, data))

    def _emit_batch_event(self, event: str, data: dict[str, Any]) -> None:
        self.emit(event_message(event, data))

    def _emit_data_event(self, event: str, data: dict[str, Any]) -> None:
        self.emit(event_message(event, data))

    def _emit_lease_event(self, event: str, lease_state: dict[str, Any]) -> None:
        self.emit(event_message(event, {"lease": lease_state}))

    def announce_ready(self) -> None:
        self.emit(
            event_message(
                "engine.ready",
                {
                    "engine": ENGINE_NAME,
                    "engineVersion": __version__,
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": [
                        "system.ping",
                        "tasks.v1",
                        "tasks.lifecycle.v1",
                        "single.v1",
                        "single.controls.v1",
                        "batch.controls.v1",
                        "tasks.pagination.v1",
                        "data.controls.v1",
                        "batch.v1",
                        "data.v1",
                        "system.diagnostics",
                        "system.resolve-managed-file",
                        "system.resolve-task-output",
                        "browser.selector-diagnostics",
                        "browser.lease.v1",
                        "task.preview.v1",
                        "collector.start.v1",
                        "manual.recovery.v1",
                    ],
                },
            )
        )

    def handle_line(self, line: str) -> None:
        if not line.strip():
            return

        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            self.emit(
                response_message(
                    "unknown",
                    error={
                        "code": "INVALID_JSON",
                        "message": f"request is not valid JSON: {exc.msg}",
                    },
                )
            )
            return

        if isinstance(decoded, dict) and decoded.get("type") == "notification":
            self._handle_notification(decoded)
            return

        request_id = decoded.get("requestId") if isinstance(decoded, dict) else None
        if isinstance(request_id, str) and request_id in self._response_cache:
            self.emit(self._response_cache[request_id])
            return

        try:
            request = validate_request(decoded)
            response, follow_up = self._dispatch(
                request["requestId"],
                request["method"],
                request["params"],
            )
        except ProtocolValidationError as exc:
            response = response_message(
                request_id if isinstance(request_id, str) and request_id else "unknown",
                error={"code": exc.code, "message": str(exc)},
            )
            follow_up = []
        except ValidationError as exc:
            error = {
                "code": exc.code,
                "message": str(exc),
            }
            if exc.details:
                error["details"] = dict(exc.details)
            response = response_message(
                request_id,
                error=error,
            )
            follow_up = []
        except Exception as exc:
            self.logger.write(
                "error",
                "request.failed",
                "unhandled request error",
                {"error": repr(exc), "requestId": request_id},
            )
            response = response_message(
                request_id if isinstance(request_id, str) and request_id else "unknown",
                error={"code": "INTERNAL_ERROR", "message": "internal engine error"},
            )
            follow_up = []

        self.emit(response)
        self._cache_response(response["requestId"], response)
        for message in follow_up:
            self.emit(message)

    def _dispatch(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if method == "system.handshake":
            requested_version = str(params.get("protocolVersion") or "")
            if requested_version != PROTOCOL_VERSION:
                return (
                    response_message(
                        request_id,
                        error={
                            "code": "PROTOCOL_VERSION_MISMATCH",
                            "message": (
                                f"host requested protocol {requested_version or '<missing>'}; "
                                f"engine supports {PROTOCOL_VERSION}"
                            ),
                        },
                    ),
                    [],
                )
            client = params.get("client")
            if not isinstance(client, dict):
                raise ValidationError("INVALID_CLIENT", "handshake client must be an object")
            self.logger.write(
                "info",
                "protocol.handshake",
                "protocol handshake completed",
                {"client": client, "protocolVersion": PROTOCOL_VERSION},
            )
            return (
                response_message(
                    request_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "engine": ENGINE_NAME,
                        "engineVersion": __version__,
                        "clientVersion": self.app_version,
                        "capabilities": [
                            "system.ping",
                            "tasks.v1",
                            "tasks.lifecycle.v1",
                            "single.v1",
                            "single.controls.v1",
                            "batch.controls.v1",
                            "tasks.pagination.v1",
                            "data.controls.v1",
                            "batch.v1",
                            "data.v1",
                            "system.diagnostics",
                            "system.resolve-managed-file",
                            "system.resolve-task-output",
                            "browser.selector-diagnostics",
                            "browser.lease.v1",
                        ],
                    },
                ),
                [],
            )

        if method == "system.ping":
            return (
                response_message(
                    request_id,
                    {
                        "status": "ok",
                        "engineVersion": __version__,
                        "protocolVersion": PROTOCOL_VERSION,
                    },
                ),
                [],
            )

        if method == "system.diagnostics":
            return (
                response_message(
                    request_id,
                    collect_diagnostics(
                        self.store,
                        self.store.database_path.parent,
                        self.app_version,
                    ),
                ),
                [],
            )

        if method == "system.resolve-managed-file":
            requested_path = str(params.get("path") or "").strip()
            if not requested_path:
                raise ValidationError("PATH_REQUIRED", "path is required")
            resolved = self.store.resolve_managed_file(requested_path)
            if resolved is None:
                raise ValidationError(
                    "MANAGED_FILE_NOT_FOUND",
                    "该文件不是当前数据库登记的任务产物",
                )
            return response_message(request_id, {"path": str(resolved)}), []

        if method == "system.resolve-task-output":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            output_path = self.store.resolve_task_output(task_id)
            return response_message(request_id, {"path": str(output_path)}), []

        if method == "system.shutdown":
            self.single_manager.stop_all()
            self.batch_manager.stop_all()
            self.data_manager.stop_all()
            self.browser_lease.reset("application_shutdown")
            self.shutdown_requested = True
            self.browser_bridge.disconnect()
            return response_message(request_id, {"status": "stopping"}), []

        if method == "browser.connect":
            browser = self.browser_bridge.connect(params)
            return response_message(
                request_id,
                {"browser": {**browser, "lease": self.browser_lease.state()}},
            ), []

        if method == "browser.state":
            return response_message(
                request_id,
                {
                    "browser": {
                        **self.browser_bridge.state(),
                        "lease": self.browser_lease.state(),
                    }
                },
            ), []

        if method == "browser.lease":
            return response_message(
                request_id,
                {"lease": self.browser_lease.state()},
            ), []

        if method == "browser.selector-diagnostics":
            adapter = PkulawAdapter(self.browser_bridge, self.logger)
            return (
                response_message(
                    request_id,
                    adapter.diagnose_current_page_stable(),
                ),
                [],
            )

        if method == "browser.page-audit":
            adapter = PkulawAdapter(self.browser_bridge, self.logger)
            return response_message(request_id, adapter.audit_current_page()), []

        if method == "browser.user-continue":
            state = self.browser_bridge.user_continue()
            self.single_manager.continue_active()
            self.batch_manager.continue_active()
            self.data_manager.continue_active()
            return (
                response_message(request_id, {"browser": state}),
                [],
            )

        if method == "browser.fixture-login":
            return (
                response_message(request_id, {"browser": self.browser_bridge.fixture_login()}),
                [],
            )

        if method == "browser.fixture-captcha":
            return (
                response_message(
                    request_id,
                    {"browser": self.browser_bridge.fixture_show_captcha()},
                ),
                [],
            )

        if method == "browser.fixture-download":
            return response_message(request_id, self.browser_bridge.fixture_download()), []

        if method == "browser.fixture-child":
            return (
                response_message(request_id, {"browser": self.browser_bridge.fixture_open_child()}),
                [],
            )

        if method == "task.preview":
            mode = str(params.get("mode") or "").strip()
            if mode not in {"single", "batch"}:
                raise ValidationError(
                    "INVALID_TASK_MODE",
                    "preview mode must be single or batch",
                )
            task_input = params.get("input")
            if not isinstance(task_input, dict):
                raise ValidationError(
                    "INVALID_TASK_INPUT",
                    "preview input must be an object",
                )
            preview_task_id = f"preview:{uuid.uuid4()}"
            lease = self._acquire_browser_lease(preview_task_id, mode)
            lease_id = str(lease.get("leaseId") or "")
            try:
                result = self.preview_service.preview(mode, dict(task_input))
            finally:
                if lease_id:
                    self.browser_lease.release(lease_id)
            self.logger.write(
                "info",
                "task.preview",
                "collector preview completed",
                {
                    "mode": mode,
                    "combinationCount": result["combinationCount"],
                    "totalResults": result["totalResults"],
                    "downloadsStarted": 0,
                },
            )
            return response_message(request_id, result), []

        if method == "task.create":
            task, idempotent = self._create_task_record(
                name=str(params.get("name") or ""),
                mode=str(params.get("mode") or ""),
                task_input=params.get("input") or {},
                output_dir=str(params.get("outputDir") or ""),
                operation_id=str(params.get("operationId") or "").strip(),
                operation_prefix="task.create",
            )
            if idempotent:
                return (
                    response_message(
                        request_id,
                        {"task": task, "idempotent": True},
                    ),
                    [],
                )
            self.logger.write(
                "info",
                "task.created",
                "task created",
                {"taskId": task["id"], "mode": task["mode"]},
            )
            return (
                response_message(request_id, {"task": task, "idempotent": False}),
                [event_message("task.created", {"task": task})],
            )

        if method == "collector.start":
            result = self._start_collector_task(
                name=str(params.get("name") or ""),
                mode=str(params.get("mode") or ""),
                task_input=params.get("input") or {},
                output_dir=str(params.get("outputDir") or ""),
                operation_id=str(params.get("operationId") or "").strip(),
            )
            events: list[dict[str, Any]] = []
            if not result["idempotent"]:
                events.append(
                    event_message("task.created", {"task": result["task"]})
                )
                if result["started"]:
                    events.append(
                        event_message(
                            "collector.started",
                            {
                                "task": result["task"],
                                "mode": result["task"]["mode"],
                            },
                        )
                    )
                elif result.get("preparing"):
                    events.append(
                        event_message(
                            "collector.preparing",
                            {
                                "task": result["task"],
                                "mode": result["task"]["mode"],
                            },
                        )
                    )
                else:
                    events.append(
                        event_message(
                            "collector.preflight.failed",
                            {"task": result["task"], "error": result["error"]},
                        )
                    )
            return response_message(request_id, result), events

        if method == "task.get":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            task = self.store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            return response_message(request_id, {"task": task}), []

        if method == "task.list":
            try:
                limit = int(params.get("limit") or 50)
            except (TypeError, ValueError) as exc:
                raise ValidationError("INVALID_LIMIT", "limit must be an integer") from exc
            tasks = self.store.list_tasks(
                limit,
                include_archived=bool(params.get("includeArchived")),
            )
            return response_message(request_id, {"tasks": tasks}), []

        if method in {"task.archive", "task.restore", "task.cleanup-preview", "task.purge"}:
            task_ids = params.get("taskIds")
            if not isinstance(task_ids, list):
                raise ValidationError("INVALID_TASK_IDS", "taskIds must be a list")
            ids = [str(value or "").strip() for value in task_ids]
            if method == "task.archive":
                tasks = self.store.archive_tasks(
                    ids,
                    reason=str(params.get("reason") or ""),
                )
                self.logger.write(
                    "info",
                    "task.archived",
                    "tasks archived",
                    {"taskIds": [task["id"] for task in tasks]},
                )
                return response_message(request_id, {"tasks": tasks}), [
                    event_message("task.archived", {"taskIds": ids})
                ]
            if method == "task.restore":
                tasks = self.store.restore_tasks(ids)
                self.logger.write(
                    "info",
                    "task.restored",
                    "tasks restored",
                    {"taskIds": [task["id"] for task in tasks]},
                )
                return response_message(request_id, {"tasks": tasks}), [
                    event_message("task.restored", {"taskIds": ids})
                ]
            if method == "task.cleanup-preview":
                preview = self.store.cleanup_preview(ids)
                self.logger.write(
                    "info",
                    "task.cleanup-preview",
                    "task cleanup preview generated",
                    {
                        "taskIds": ids,
                        "summary": preview["summary"],
                    },
                )
                return response_message(request_id, preview), []
            file_mode = str(params.get("fileMode") or "")
            if file_mode not in {"keep", "cleaned"}:
                raise ValidationError(
                    "INVALID_FILE_MODE",
                    "fileMode must be keep or cleaned",
                )
            result = self.store.purge_tasks(
                ids,
                require_files_removed=file_mode == "cleaned",
            )
            self.logger.write(
                "info",
                "task.purged",
                "task records permanently deleted",
                {
                    "taskIds": result["taskIds"],
                    "fileMode": file_mode,
                    "deleted": result["deleted"],
                },
            )
            return response_message(
                request_id,
                {**result, "fileMode": file_mode},
            ), [event_message("task.purged", {"taskIds": result["taskIds"]})]

        if method in {"single.start", "single.retry"}:
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            details = self._start_single_task(
                task_id,
                retry_failed=method == "single.retry",
                operation_id=str(params.get("operationId") or "").strip(),
            )
            return response_message(request_id, details), []

        if method == "single.pause":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(request_id, self.single_manager.pause(task_id)), []

        if method == "single.resume":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self._resume_single_task(
                    task_id,
                    operation_id=str(params.get("operationId") or "").strip(),
                ),
            ), []

        if method == "single.stop":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            details = self.single_manager.stop(task_id)
            self._cancel_collector_preflight(task_id)
            return response_message(request_id, details), []

        if method == "single.continue":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self.single_manager.continue_task(task_id),
            ), []

        if method == "single.continue-to-last":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self.single_manager.continue_to_last(task_id),
            ), []

        if method in {
            "single.continue-capture-boundary",
            "batch.continue-capture-boundary",
        }:
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self._continue_capture_boundary(
                    task_id,
                    operation_id=str(params.get("operationId") or "").strip(),
                ),
            ), []

        if method == "single.choose-candidate":
            task_id = str(params.get("taskId") or "").strip()
            item_id = str(params.get("itemId") or "").strip()
            candidate_key = str(params.get("candidateKey") or "").strip()
            if not task_id or not item_id or not candidate_key:
                raise ValidationError(
                    "CANDIDATE_CHOICE_INCOMPLETE",
                    "taskId, itemId and candidateKey are required",
                )
            return response_message(
                request_id,
                self.single_manager.choose_candidate(task_id, item_id, candidate_key),
            ), []

        if method == "single.details":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(request_id, self.single_manager.details(task_id)), []

        if method in {"batch.start", "batch.retry"}:
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            details = self._start_batch_task(
                task_id,
                retry_failed=method == "batch.retry",
                operation_id=str(params.get("operationId") or "").strip(),
            )
            return response_message(request_id, details), []

        if method == "batch.pause":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(request_id, self.batch_manager.pause(task_id)), []

        if method == "batch.resume":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self._resume_batch_task(
                    task_id,
                    operation_id=str(params.get("operationId") or "").strip(),
                ),
            ), []

        if method == "batch.stop":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            details = self.batch_manager.stop(task_id)
            self._cancel_collector_preflight(task_id)
            return response_message(request_id, details), []

        if method == "batch.continue":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self.batch_manager.continue_task(task_id),
            ), []

        if method == "batch.continue-to-last":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self.batch_manager.continue_to_last(task_id),
            ), []

        if method == "batch.confirm-scale":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self.batch_manager.confirm_scale(task_id),
            ), []

        if method == "batch.details":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(request_id, self.batch_manager.details(task_id)), []

        if method == "task.continue-capture-boundary":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            return response_message(
                request_id,
                self._continue_capture_boundary(
                    task_id,
                    operation_id=str(params.get("operationId") or "").strip(),
                ),
            ), []

        if method == "task.details":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            task = self.store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            details = (
                self.single_manager.details(task_id)
                if task["mode"] == "single"
                else self.batch_manager.details(task_id)
            )
            return response_message(request_id, details), []

        if method == "task.apply-settings":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError(
                    "TASK_ID_REQUIRED",
                    "taskId is required",
                )
            task_input = params.get("input")
            if not isinstance(task_input, dict):
                raise ValidationError(
                    "INVALID_TASK_INPUT",
                    "input must be an object",
                )
            return response_message(
                request_id,
                self._apply_task_settings(
                    task_id,
                    task_input=task_input,
                    output_dir=str(params.get("outputDir") or "").strip(),
                    operation_id=str(
                        params.get("operationId") or ""
                    ).strip(),
                ),
            ), []

        if method == "task.resume-after-manual":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            operation_id = str(params.get("operationId") or "").strip()
            result = self._resume_after_manual(
                task_id,
                operation_id=operation_id,
            )
            follow_up = (
                [
                    event_message(
                        "task.manual-recovered",
                        {
                            "taskId": task_id,
                            "operationId": operation_id,
                            "action": result["action"],
                        },
                    )
                ]
                if result["action"] != "none"
                else []
            )
            return response_message(request_id, result), follow_up

        if method == "data.start":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            options = params.get("options")
            if not isinstance(options, dict):
                options = {}
            return response_message(
                request_id,
                self._start_data_processing(
                    task_id,
                    options,
                    operation_id=str(params.get("operationId") or "").strip(),
                ),
            ), []

        if method == "data.stop":
            processing_id = str(params.get("processingId") or "").strip()
            if not processing_id:
                raise ValidationError("PROCESSING_ID_REQUIRED", "processingId is required")
            return response_message(request_id, self.data_manager.stop(processing_id)), []

        if method == "data.pause":
            processing_id = str(params.get("processingId") or "").strip()
            if not processing_id:
                raise ValidationError("PROCESSING_ID_REQUIRED", "processingId is required")
            return response_message(request_id, self.data_manager.pause(processing_id)), []

        if method == "data.resume":
            processing_id = str(params.get("processingId") or "").strip()
            if not processing_id:
                raise ValidationError("PROCESSING_ID_REQUIRED", "processingId is required")
            return response_message(request_id, self.data_manager.resume(processing_id)), []

        if method == "data.continue":
            processing_id = str(params.get("processingId") or "").strip()
            if not processing_id:
                raise ValidationError("PROCESSING_ID_REQUIRED", "processingId is required")
            return response_message(
                request_id,
                self.data_manager.continue_run(processing_id),
            ), []

        if method == "data.details":
            processing_id = str(params.get("processingId") or "").strip()
            if not processing_id:
                raise ValidationError("PROCESSING_ID_REQUIRED", "processingId is required")
            return response_message(request_id, self.data_manager.details(processing_id)), []

        if method == "data.latest":
            task_id = str(params.get("taskId") or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskId is required")
            details = self.data_manager.latest(task_id)
            if details is None:
                raise ValidationError("PROCESSING_NOT_FOUND", "该任务还没有数据处理记录")
            return response_message(request_id, details), []

        return (
            response_message(
                request_id,
                error={
                    "code": "METHOD_NOT_FOUND",
                    "message": f"unsupported method: {method}",
                },
            ),
            [],
        )

    def _create_task_record(
        self,
        *,
        name: str,
        mode: str,
        task_input: Any,
        output_dir: str,
        operation_id: str,
        operation_prefix: str,
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(task_input, dict):
            raise ValidationError(
                "INVALID_TASK_INPUT",
                "task input must be an object",
            )
        match_mode = str(task_input.get("matchMode") or "keyword")
        resolve_search_mode(match_mode)
        if mode == "batch" and match_mode == "title":
            raise ValidationError(
                "BATCH_EXACT_TITLE_UNSUPPORTED",
                "精确完整标题只适用于逐篇任务；批量任务必须使用关键词模式",
            )
        if len(operation_id) > 200:
            raise ValidationError(
                "INVALID_OPERATION_ID",
                "operationId must not exceed 200 characters",
            )
        task_payload = {
            "name": name,
            "mode": mode,
            "input": task_input,
            "outputDir": output_dir,
        }
        fingerprint = json.dumps(
            task_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"{operation_prefix}:{operation_id}" if operation_id else ""
        with self._operation_lock:
            cached = self._operation_lookup(cache_key, fingerprint)
            if cached is not None:
                task = self.store.get_task(str(cached["taskId"]))
                if task is None:
                    raise ValidationError(
                        "TASK_NOT_FOUND",
                        "幂等请求对应的任务记录已不存在",
                    )
                return task, True
            task = self.store.create_task(
                name=name,
                mode=mode,
                task_input=task_input,
                output_dir=output_dir,
            )
            self._remember_operation(cache_key, fingerprint, task["id"])
            return task, False

    def _task_details(self, task: dict[str, Any]) -> dict[str, Any]:
        return (
            self.single_manager.details(str(task["id"]))
            if task["mode"] == "single"
            else self.batch_manager.details(str(task["id"]))
        )

    def _start_collector_task(
        self,
        *,
        name: str,
        mode: str,
        task_input: Any,
        output_dir: str,
        operation_id: str,
    ) -> dict[str, Any]:
        task, idempotent = self._create_task_record(
            name=name,
            mode=mode,
            task_input=task_input,
            output_dir=output_dir,
            operation_id=operation_id,
            operation_prefix="collector.start",
        )
        task_id = str(task["id"])
        if idempotent:
            current = self.store.get_task(task_id)
            if current is None:
                raise ValidationError("TASK_NOT_FOUND", "任务记录已不存在")
            raw_preview = (current.get("input") or {}).get("previewSnapshot")
            preview = raw_preview if isinstance(raw_preview, dict) else None
            return {
                "task": current,
                "preview": preview,
                "details": self._task_details(current),
                "started": current["status"]
                not in {"pending", "waiting_manual", "waiting_login", "failed", "stopped"},
                "preparing": current["status"] == "preflight",
                "error": current.get("error"),
                "idempotent": True,
            }

        lease = self._acquire_browser_lease(task_id, mode)
        checkpoint = dict(task.get("checkpoint") or {})
        checkpoint["collectorPreflight"] = {
            "status": "running",
            "phase": "starting",
            "completed": 0,
            "total": None,
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        current = self.store.update_task(
            task_id,
            status="preflight",
            current_stage="collector-preflight",
            checkpoint=checkpoint,
        )
        worker = Thread(
            target=self._run_collector_preflight,
            args=(
                task_id,
                mode,
                dict(task_input),
                lease,
            ),
            name=f"collector-preflight-{task_id[:8]}",
            daemon=True,
        )
        cancel_event = Event()
        with self._preflight_lock:
            self._preflight_threads[task_id] = worker
            self._preflight_cancelled[task_id] = cancel_event
        try:
            worker.start()
        except Exception:
            with self._preflight_lock:
                self._preflight_threads.pop(task_id, None)
                self._preflight_cancelled.pop(task_id, None)
            if lease.get("acquired"):
                self.browser_lease.release(lease["leaseId"])
            raise
        return {
            "task": current,
            "preview": None,
            "details": self._task_details(current),
            "started": False,
            "preparing": True,
            "error": None,
            "idempotent": False,
        }

    def _run_collector_preflight(
        self,
        task_id: str,
        mode: str,
        task_input: dict[str, Any],
        lease: dict[str, Any],
    ) -> None:
        worker_store = TaskStore(self.store.database_path)
        try:
            with self._preflight_lock:
                cancel_event = self._preflight_cancelled.get(task_id)
            preview = self.preview_service.preview(
                mode,
                dict(task_input),
                progress=lambda progress: self._record_preflight_progress(
                    worker_store,
                    task_id,
                    progress,
                ),
                cancel_check=(
                    cancel_event.is_set
                    if cancel_event is not None
                    else None
                ),
            )
            if cancel_event is not None and cancel_event.is_set():
                raise CollectorPreviewCancelled()
            current = worker_store.get_task(task_id)
            if current is None or current["status"] != "preflight":
                raise CollectorPreviewCancelled()
            updated_input = dict(task_input)
            updated_input["previewSnapshot"] = preview
            current = worker_store.update_task_input(
                task_id,
                updated_input,
                allowed_statuses=frozenset({"preflight"}),
            )
            checkpoint = dict(current.get("checkpoint") or {})
            checkpoint["collectorPreflight"] = {
                "status": "complete",
                "previewFingerprint": preview.get("previewFingerprint"),
                "generatedAt": preview.get("generatedAt"),
            }
            snapshot = checkpoint_preview_snapshot(updated_input)
            if snapshot:
                checkpoint["previewSnapshot"] = snapshot
            worker_store.update_task(
                task_id,
                current_stage="collector-preflight",
                checkpoint=checkpoint,
            )
            if mode == "single":
                details = self.single_manager.start(
                    task_id,
                    retry_failed=False,
                    lease_id=str(lease["leaseId"]),
                )
            else:
                details = self.batch_manager.start(
                    task_id,
                    retry_failed=False,
                    lease_id=str(lease["leaseId"]),
                )
            current = worker_store.get_task(task_id)
            if current is None:
                raise ValidationError("TASK_NOT_FOUND", "任务记录已不存在")
            self.emit(
                event_message(
                    "task.preflight.completed",
                    {
                        "taskId": task_id,
                        "task": current,
                        "details": details,
                        "preview": preview,
                    },
                )
            )
        except CollectorPreviewCancelled:
            if lease.get("acquired"):
                self.browser_lease.release(lease["leaseId"])
            current = worker_store.get_task(task_id)
            if current is not None:
                self.emit(
                    event_message(
                        "task.preflight.cancelled",
                        {
                            "taskId": task_id,
                            "task": current,
                            "details": self._task_details(current),
                        },
                    )
                )
        except ValidationError as exc:
            current = worker_store.get_task(task_id)
            if current is None or current["status"] != "preflight":
                if lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                return
            if lease.get("acquired"):
                self.browser_lease.release(lease["leaseId"])
            error = {
                "code": exc.code,
                "message": str(exc),
            }
            if exc.details:
                error["details"] = dict(exc.details)
            current = worker_store.get_task(task_id)
            checkpoint = dict((current or {}).get("checkpoint") or {})
            checkpoint["collectorPreflight"] = {
                "status": "failed",
                "error": error,
            }
            checkpoint["accessRecovery"] = {
                "taskId": task_id,
                "leaseId": str(lease.get("leaseId") or ""),
                "browserLeaseTaskId": task_id,
                "waitCode": exc.code,
                "detectedAt": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(),
                ),
            }
            current = worker_store.update_task(
                task_id,
                status="waiting_manual",
                current_stage="collector-preflight",
                error=error,
                checkpoint=checkpoint,
            )
            self.emit(
                event_message(
                    "task.preflight.failed",
                    {
                        "taskId": task_id,
                        "task": current,
                        "details": self._task_details(current),
                        "error": error,
                    },
                )
            )
        except Exception as exc:
            if lease.get("acquired"):
                self.browser_lease.release(lease["leaseId"])
            error = {
                "code": "PREFLIGHT_FAILED",
                "message": "启动预检失败，请检查浏览器页面后重试",
                "details": {"reason": str(exc)},
            }
            current = worker_store.get_task(task_id)
            checkpoint = dict((current or {}).get("checkpoint") or {})
            checkpoint["collectorPreflight"] = {
                "status": "failed",
                "error": error,
            }
            current = worker_store.update_task(
                task_id,
                status="failed",
                current_stage="collector-preflight",
                error=error,
                checkpoint=checkpoint,
            )
            self.logger.write(
                "error",
                "collector.preflight_failed",
                "Collector preflight failed",
                {
                    "taskId": task_id,
                    "error": repr(exc),
                },
            )
            self.emit(
                event_message(
                    "task.preflight.failed",
                    {
                        "taskId": task_id,
                        "task": current,
                        "details": self._task_details(current),
                        "error": error,
                    },
                )
            )
        finally:
            worker_store.close()
            with self._preflight_lock:
                self._preflight_threads.pop(task_id, None)
                self._preflight_cancelled.pop(task_id, None)

    def _record_preflight_progress(
        self,
        store: TaskStore,
        task_id: str,
        progress: dict[str, Any],
    ) -> None:
        current = store.get_task(task_id)
        with self._preflight_lock:
            cancel_event = self._preflight_cancelled.get(task_id)
        if (
            current is None
            or current["status"] != "preflight"
            or (cancel_event is not None and cancel_event.is_set())
        ):
            return
        checkpoint = dict(current.get("checkpoint") or {})
        checkpoint["collectorPreflight"] = {
            "status": "running",
            **dict(progress),
        }
        current = store.update_task(
            task_id,
            status="preflight",
            current_stage="collector-preflight",
            checkpoint=checkpoint,
        )
        self.emit(
            event_message(
                "task.preflight.progress",
                {
                    "taskId": task_id,
                    "task": current,
                    **dict(progress),
                },
            )
        )

    def _cancel_collector_preflight(self, task_id: str) -> None:
        with self._preflight_lock:
            cancel_event = self._preflight_cancelled.get(task_id)
        if cancel_event is not None:
            cancel_event.set()

    def _resume_after_manual(
        self,
        task_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        fingerprint = json.dumps(
            {"taskId": task_id, "action": "resume-after-manual"},
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"manual.resume:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValidationError("TASK_NOT_FOUND", "任务记录已不存在")
                checkpoint = dict(task.get("checkpoint") or {})
                recovery = checkpoint.get("manualRecovery") or {}
                action = str(recovery.get("action") or "none")
                return {
                    "action": action,
                    "task": task,
                    "details": self._task_details(task),
                }

            task = self.store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            manager = (
                self.single_manager
                if task["mode"] == "single"
                else self.batch_manager
            )
            decision = decide_manual_recovery(
                task,
                controller_alive=manager.has_live_controller(task_id),
            )
            if decision.action == "none":
                self._remember_operation(
                    cache_key if operation_id else "",
                    fingerprint,
                    task_id,
                )
                return {
                    "action": "none",
                    "task": task,
                    "details": self._task_details(task),
                }

            self.browser_bridge.user_continue()
            if decision.action == "continue":
                details = manager.continue_task(task_id)
            else:
                lease = self._acquire_browser_lease(task_id, task["mode"])
                try:
                    details = manager.restart_after_access_wait(
                        task_id,
                        lease_id=str(lease["leaseId"]),
                    )
                except Exception:
                    if lease.get("acquired"):
                        self.browser_lease.release(lease["leaseId"])
                    raise

            current = self.store.get_task(task_id)
            if current is None:
                raise ValidationError("TASK_NOT_FOUND", "任务记录已不存在")
            checkpoint = dict(current.get("checkpoint") or {})
            checkpoint.pop("accessRecovery", None)
            checkpoint["manualRecovery"] = {
                "action": decision.action,
                "reason": decision.reason,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self.store.update_task(task_id, checkpoint=checkpoint)
            current = self.store.get_task(task_id)
            assert current is not None
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return {
                "action": decision.action,
                "task": current,
                "details": details,
            }

    def _apply_task_settings(
        self,
        task_id: str,
        *,
        task_input: dict[str, Any],
        output_dir: str,
        operation_id: str,
    ) -> dict[str, Any]:
        if not output_dir:
            raise ValidationError(
                "OUTPUT_DIR_REQUIRED",
                "任务设置必须指定输出目录",
            )
        fingerprint = json.dumps(
            {
                "taskId": task_id,
                "input": task_input,
                "outputDir": output_dir,
                "action": "apply-settings",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"task.settings:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValidationError(
                        "TASK_NOT_FOUND",
                        "任务记录已不存在",
                    )
                checkpoint = dict(task.get("checkpoint") or {})
                return {
                    "changed": True,
                    "generation": int(
                        checkpoint.get("configGeneration") or 0
                    ),
                    "task": task,
                    "details": self._task_details(task),
                }

            task = self.store.get_task(task_id)
            if task is None:
                raise ValidationError(
                    "TASK_NOT_FOUND",
                    f"task {task_id} was not found",
                )
            plan = build_task_revision(task, task_input, output_dir)
            manager = (
                self.single_manager
                if task["mode"] == "single"
                else self.batch_manager
            )
            if not plan.changed:
                return {
                    "changed": False,
                    "generation": plan.generation,
                    "task": task,
                    "details": self._task_details(task),
                }

            if task["status"] in ACTIVE_TASK_STATUSES:
                manager.stop(task_id)
                if not self._wait_for_settings_stop(task_id):
                    raise ValidationError(
                        "TASK_PAUSE_TIMEOUT",
                        "任务未能在安全边界停止，原设置保持不变",
                    )
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValidationError(
                        "TASK_NOT_FOUND",
                        "任务记录已不存在",
                    )
            if task["status"] not in {
                "stopped",
                "failed",
                "partial",
                "completed",
            }:
                raise ValidationError(
                    "TASK_SETTINGS_NOT_EDITABLE",
                    f"任务状态 {task['status']} 不允许应用新设置",
                )

            checkpoint = dict(plan.checkpoint)
            checkpoint["lastSettingsRevision"] = {
                "operationId": operation_id,
                "generation": plan.generation,
                "changed": True,
            }
            self.store.apply_task_settings_revision(
                task_id,
                plan.task_input,
                checkpoint,
                output_dir=output_dir,
                allowed_statuses=frozenset(
                    {"stopped", "failed", "partial", "completed"}
                ),
            )
            lease = self._acquire_browser_lease(task_id, task["mode"])
            try:
                details = manager.start(
                    task_id,
                    retry_failed=True,
                    lease_id=str(lease["leaseId"]),
                )
            except Exception:
                if lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                raise
            current = self.store.get_task(task_id)
            if current is None:
                raise ValidationError(
                    "TASK_NOT_FOUND",
                    "任务记录已不存在",
                )
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return {
                "changed": True,
                "generation": int(
                    (current.get("checkpoint") or {}).get(
                        "configGeneration"
                    )
                    or 0
                ),
                "task": current,
                "details": details,
            }

    def _wait_for_settings_stop(self, task_id: str) -> bool:
        deadline = time.monotonic() + self._settings_stop_timeout_seconds
        while True:
            task = self.store.get_task(task_id)
            if task is None:
                return False
            if task["status"] == "stopped":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def _start_single_task(
        self,
        task_id: str,
        *,
        retry_failed: bool,
        operation_id: str,
    ) -> dict[str, Any]:
        fingerprint = json.dumps(
            {"taskId": task_id, "retryFailed": retry_failed},
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"single.{'retry' if retry_failed else 'start'}:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                return self.single_manager.details(task_id)

            task = self._require_task_mode(task_id, "single")
            allowed_status = (
                {"failed", "partial", "stopped"}
                if retry_failed
                else {"pending"}
            )
            if task["status"] not in allowed_status:
                details = self.single_manager.details(task_id)
                self._remember_operation(
                    cache_key if operation_id else "",
                    fingerprint,
                    task_id,
                )
                return details
            current = self.browser_lease.state()
            if (
                not current["available"]
                and current["taskId"] == task_id
                and current["mode"] == "single"
                and self.single_manager.has_active_task(task_id)
            ):
                details = self.single_manager.details(task_id)
                self._remember_operation(
                    cache_key if operation_id else "",
                    fingerprint,
                    task_id,
                )
                return details

            lease = self._acquire_browser_lease(task_id, "single")
            try:
                details = self.single_manager.start(
                    task_id,
                    retry_failed=retry_failed,
                    lease_id=lease["leaseId"],
                )
            except Exception:
                if lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                raise
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return details

    def _resume_single_task(
        self,
        task_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        fingerprint = json.dumps(
            {"taskId": task_id, "action": "resume"},
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"single.resume:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                return self.single_manager.details(task_id)
            self._require_task_mode(task_id, "single")
            lease = self._acquire_browser_lease(task_id, "single")
            try:
                details = self.single_manager.resume(
                    task_id,
                    lease_id=lease["leaseId"],
                )
            except Exception:
                if lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                raise
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return details

    def _start_batch_task(
        self,
        task_id: str,
        *,
        retry_failed: bool,
        operation_id: str,
    ) -> dict[str, Any]:
        fingerprint = json.dumps(
            {"taskId": task_id, "retryFailed": retry_failed},
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"batch.{'retry' if retry_failed else 'start'}:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                return self.batch_manager.details(task_id)

            task = self._require_task_mode(task_id, "batch")
            allowed_status = (
                {"failed", "partial", "stopped"}
                if retry_failed
                else {"pending"}
            )
            if task["status"] not in allowed_status:
                details = self.batch_manager.details(task_id)
                self._remember_operation(
                    cache_key if operation_id else "",
                    fingerprint,
                    task_id,
                )
                return details
            current = self.browser_lease.state()
            if (
                not current["available"]
                and current["taskId"] == task_id
                and current["mode"] == "batch"
                and self.batch_manager.has_active_task(task_id)
            ):
                details = self.batch_manager.details(task_id)
                self._remember_operation(
                    cache_key if operation_id else "",
                    fingerprint,
                    task_id,
                )
                return details

            lease = self._acquire_browser_lease(task_id, "batch")
            try:
                details = self.batch_manager.start(
                    task_id,
                    retry_failed=retry_failed,
                    lease_id=lease["leaseId"],
                )
            except Exception:
                if lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                raise
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return details

    def _resume_batch_task(
        self,
        task_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        fingerprint = json.dumps(
            {"taskId": task_id, "action": "resume"},
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"batch.resume:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                return self.batch_manager.details(task_id)
            self._require_task_mode(task_id, "batch")
            lease = self._acquire_browser_lease(task_id, "batch")
            try:
                details = self.batch_manager.resume(
                    task_id,
                    lease_id=lease["leaseId"],
                )
            except Exception:
                if lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                raise
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return details

    def _continue_capture_boundary(
        self,
        task_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        fingerprint = json.dumps(
            {"taskId": task_id, "action": "continue-capture-boundary"},
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"capture-boundary.continue:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValidationError(
                        "TASK_NOT_FOUND",
                        "任务记录已不存在",
                    )
                return self._task_details(task)

            task = self.store.get_task(task_id)
            if task is None:
                raise ValidationError(
                    "TASK_NOT_FOUND",
                    f"task {task_id} was not found",
                )
            if (
                task["status"] != "waiting_manual"
                or (task.get("error") or {}).get("code")
                != "CAPTURE_BOUNDARY_REACHED"
            ):
                raise ValidationError(
                    "CAPTURE_BOUNDARY_NOT_AVAILABLE",
                    "当前没有待确认的完整采集边界",
                )
            manager = (
                self.single_manager
                if task["mode"] == "single"
                else self.batch_manager
            )
            lease = self._acquire_browser_lease(task_id, task["mode"])
            try:
                manager.continue_capture_boundary(task_id)
                if not manager.has_live_controller(task_id):
                    details = manager.start(
                        task_id,
                        retry_failed=False,
                        lease_id=lease["leaseId"],
                    )
                else:
                    details = manager.details(task_id)
            except Exception:
                if lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                raise
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return details

    def _start_data_processing(
        self,
        task_id: str,
        options: dict[str, Any],
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        fingerprint = json.dumps(
            {"taskId": task_id, "options": options},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = f"data.start:{operation_id}"
        with self._operation_lock:
            cached = self._operation_lookup(
                cache_key if operation_id else "",
                fingerprint,
            )
            if cached is not None:
                active = self.data_manager.latest(task_id)
                if active is not None:
                    return active

            task = self.store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            if self.data_manager.has_active_task(task_id):
                active = self.data_manager.latest(task_id)
                if active is None:
                    raise ValidationError(
                        "PROCESSING_NOT_FOUND",
                        "当前数据处理线程存在，但找不到运行记录",
                    )
                return active

            requires_browser = any(
                can_use_government_fallback(task, item, options)
                for item in self.store.list_task_items(task_id)
            )
            lease: dict[str, Any] | None = None
            if requires_browser:
                lease = self._acquire_browser_lease(task_id, "data")
            try:
                details = self.data_manager.start(
                    task_id,
                    options,
                    lease_id=str(lease["leaseId"]) if lease else "",
                )
            except Exception:
                if lease and lease.get("acquired"):
                    self.browser_lease.release(lease["leaseId"])
                raise
            self._remember_operation(
                cache_key if operation_id else "",
                fingerprint,
                task_id,
            )
            return details

    def _require_task_mode(self, task_id: str, expected_mode: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        if task["mode"] != expected_mode:
            raise ValidationError(
                "TASK_MODE_MISMATCH",
                f"task {task_id} is not a {expected_mode} task",
            )
        return task

    def _acquire_browser_lease(
        self,
        task_id: str,
        mode: str,
    ) -> dict[str, Any]:
        current = self.browser_lease.state()
        if (
            not current["available"]
            and current["taskId"] == task_id
            and current["mode"] == mode
        ):
            return {
                **current,
                "acquired": False,
            }
        try:
            return self.browser_lease.acquire(task_id, mode)
        except BrowserLeaseBusy as exc:
            raise ValidationError(exc.code, str(exc)) from exc

    def _operation_lookup(
        self,
        cache_key: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        if not cache_key:
            return None
        cached = self._operation_cache.get(cache_key)
        if cached is None:
            return None
        if cached["fingerprint"] != fingerprint:
            raise ValidationError(
                "IDEMPOTENCY_KEY_REUSED",
                "同一 operationId 不能用于不同的启动请求",
            )
        self._operation_cache.move_to_end(cache_key)
        return dict(cached)

    def _remember_operation(
        self,
        cache_key: str,
        fingerprint: str,
        task_id: str,
    ) -> None:
        if not cache_key:
            return
        self._operation_cache[cache_key] = {
            "fingerprint": fingerprint,
            "taskId": task_id,
        }
        self._operation_cache.move_to_end(cache_key)
        while len(self._operation_cache) > self._max_cached_operations:
            self._operation_cache.popitem(last=False)

    def _handle_notification(self, message: dict[str, Any]) -> None:
        event = str(message.get("event") or "").strip()
        data = message.get("data") or {}
        if not event or not isinstance(data, dict):
            self.logger.write(
                "warn",
                "protocol.invalid_notification",
                "Ignoring malformed notification",
            )
            return
        if event.startswith("browser.download."):
            self.browser_bridge.note_download_event(event, data)
            return
        if event in {"browser.child-opened", "browser.page-crashed"}:
            self.browser_bridge.note_browser_event(event, data)
            return
        self.logger.write(
            "warn",
            "protocol.unknown_notification",
            "Ignoring unsupported notification",
            {"event": event},
        )

    def _cache_response(self, request_id: str, response: dict[str, Any]) -> None:
        self._response_cache[request_id] = response
        self._response_cache.move_to_end(request_id)
        while len(self._response_cache) > self._max_cached_responses:
            self._response_cache.popitem(last=False)

    def run(self, input_stream: TextIO = sys.stdin) -> int:
        self.announce_ready()
        self.logger.write("info", "engine.started", "engine started")
        for line in input_stream:
            self.handle_line(line)
            if self.shutdown_requested:
                break
        self.logger.write("info", "engine.stopped", "engine stopped")
        return 0
