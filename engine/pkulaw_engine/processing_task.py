from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, Callable
from urllib.parse import urlparse

from openpyxl import load_workbook

from .atomic_publish import atomic_replace_many
from .body_quality import assess_body_completeness
from .browser_lease import BrowserLeaseManager
from .browser_bridge import BrowserBridge
from .controls.task_control import TaskControl, TaskStopped as ProcessingStopped
from .download_intent import DownloadIntentExhausted
from .logging_utils import JsonLineLogger
from .policy import (
    extract_policy_id,
    normalize_title,
    safe_filename,
    validate_word_attachment,
)
from .progress import (
    build_progress_snapshot,
    processing_document_overall,
)
from .processors.documents import (
    DocumentExtractionError,
    DocumentExtractionResult,
    create_docx_from_text,
    extract_document,
    normalize_text,
    write_text,
)
from .processors.reporting import (
    file_sha256,
    stage_master_catalog,
    write_task_report,
)
from .sources.government import GovernmentProvider, GovernmentSourceUnavailable
from .sources.pkulaw import (
    InstitutionQuotaReached,
    LoginRequired,
    ManualActionRequired,
    PkulawAdapter,
)
from .storage import TaskStore, ValidationError


def can_use_government_fallback(
    task: dict[str, Any],
    item: dict[str, Any],
    options: dict[str, Any],
) -> bool:
    if not options.get("governmentFallback"):
        return False
    if task.get("mode") != "single":
        return False
    task_input = dict(task.get("input") or {})
    if task_input.get("matchMode") != "title":
        return False
    if item.get("status") not in {"failed", "pending"}:
        return False
    return (item.get("error") or {}).get("code") == "NO_EXACT_MATCH"


def materialize_zip_member(
    archive_path: Path,
    member: str,
    destination: Path,
) -> None:
    """Atomically copy one already-validated ZIP member to a final Word path."""
    normalized_member = PurePosixPath(member.replace("\\", "/"))
    if normalized_member.is_absolute() or ".." in normalized_member.parts:
        raise ValueError(f"unsafe ZIP member path: {member}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(destination) + ".tmp")
    try:
        with zipfile.ZipFile(archive_path) as archive_file:
            with archive_file.open(member) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _document_review_metadata(
    *,
    body: str,
    source_type: str,
    extraction: DocumentExtractionResult,
) -> tuple[str, str, dict[str, Any]]:
    assessment = assess_body_completeness(body)
    status = "processed" if assessment.status == "complete" else "pending_review"
    review_status = (
        "pending_review"
        if assessment.status != "complete"
        or source_type == "政府官网正文整理"
        else "not_required"
    )
    metadata = {
        "status": status,
        "textSourceType": extraction.text_source_type,
        "extractionMethod": extraction.artifact_method,
        "converterName": extraction.converter_name,
        "converterVersion": extraction.converter_version,
        "bodyCompleteness": assessment.status,
        "reviewStatus": review_status,
        "bodyQuality": assessment.to_dict(),
    }
    return status, review_status, metadata


def _status_progress_snapshot(
    existing: dict[str, Any],
    *,
    stage: str,
):
    summary = dict(existing.get("summary") or {})
    current = dict(summary.get("progressSnapshot") or {})
    return build_progress_snapshot(
        stage=stage,
        overall=float(current.get("overall") or existing.get("progress") or 0),
        completed_units=int(current.get("completedUnits") or 0),
        total_units=int(current.get("totalUnits") or 0),
        succeeded=int(current.get("succeeded") or 0),
        failed=int(current.get("failed") or 0),
        pending_review=int(current.get("pendingReview") or 0),
    )


class DataProcessingManager:
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
        options: dict[str, Any],
        lease_id: str = "",
    ) -> dict[str, Any]:
        with self.lock:
            active = next(
                (
                    controller
                    for controller in self.controllers.values()
                    if controller.thread and controller.thread.is_alive()
                ),
                None,
            )
            if active:
                active_details = self.details(active.task_id)
                if active_details["run"]["taskId"] == task_id:
                    return active_details
                raise ValidationError("PROCESSING_ALREADY_RUNNING", "已有数据处理任务正在运行")
            if self.browser_lease is not None and lease_id:
                self.browser_lease.validate(lease_id, task_id, "data")
            processing_id = str(uuid.uuid4())
            store = TaskStore(self.database_path)
            try:
                task = store.get_task(task_id)
                if task is None:
                    raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
                if not task.get("outputDir"):
                    raise ValidationError("OUTPUT_DIR_REQUIRED", "源任务没有输出目录")
                store.upsert_processing_run(
                    processing_id,
                    task_id,
                    status="pending",
                    progress=0,
                    current_stage="pending",
                    options=options,
                )
            finally:
                store.close()
            controller = TaskControl(processing_id)
            thread = threading.Thread(
                target=self._run_with_lease,
                args=(processing_id, task_id, options, controller, lease_id),
                name=f"data-processing-{processing_id[:8]}",
                daemon=True,
            )
            controller.thread = thread
            self.controllers[processing_id] = controller
            try:
                thread.start()
            except Exception:
                self.controllers.pop(processing_id, None)
                raise
            return self.details(processing_id)

    def stop(self, processing_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            run = store.get_processing_run(processing_id)
            if run is None:
                raise ValidationError("PROCESSING_NOT_FOUND", "数据处理运行不存在")
            with self.lock:
                controller = self.controllers.get(processing_id)
            if run["status"] in {"stopping", "stopped"}:
                return {
                    "run": run,
                    "documents": store.list_policy_documents(run["taskId"]),
                    "artifacts": store.list_task_artifacts(processing_id),
                }
            if not controller or not controller.thread or not controller.thread.is_alive():
                self._update_run(
                    store,
                    processing_id,
                    run["taskId"],
                    status="stopped",
                    progress=run["progress"],
                    current_stage="stopped",
                    options=run["options"],
                    summary={
                        **run["summary"],
                        "interruptedPreviousStatus": run["status"],
                    },
                    error={
                        "code": "APPLICATION_INTERRUPTED",
                        "message": "数据处理线程已不存在，已按异常中断安全停止",
                    },
                )
                return self.details(processing_id)
            stop_requested = False
            with controller.condition:
                if controller.request_stop():
                    transitioned = store.transition_processing_run(
                        processing_id,
                        run["taskId"],
                        status="stopping",
                        current_stage="stopping",
                        summary_updates={
                            "stopRequestedAt": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                            "stopPreviousStatus": run["status"],
                        },
                        clear_error=True,
                    )
                    self._emit_run_progress(transitioned)
                    stop_requested = True
            if stop_requested:
                self._emit(
                    "data.stopping",
                    {"processingId": processing_id, "taskId": run["taskId"]},
                )
        finally:
            store.close()
        return self.details(processing_id)

    def pause(self, processing_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            run = store.get_processing_run(processing_id)
            if run is None:
                raise ValidationError("PROCESSING_NOT_FOUND", "数据处理运行不存在")
            if run["status"] in {"pausing", "paused"}:
                return {
                    "run": run,
                    "documents": store.list_policy_documents(run["taskId"]),
                    "artifacts": store.list_task_artifacts(processing_id),
                }
            if run["status"] != "running":
                raise ValidationError("PROCESSING_NOT_PAUSABLE", "当前数据处理状态不能暂停")
            with self.lock:
                controller = self.controllers.get(processing_id)
            if not controller or not controller.thread or not controller.thread.is_alive():
                raise ValidationError("PROCESSING_NOT_RUNNING", "数据处理没有运行中的工作线程")
            pause_requested = False
            with controller.condition:
                if controller.request_pause("用户手动暂停"):
                    transitioned = store.transition_processing_run(
                        processing_id,
                        run["taskId"],
                        status="pausing",
                        current_stage="pausing",
                        summary_updates={
                            "pauseKind": "manual",
                            "pauseReason": "用户手动暂停",
                            "pauseRequestedStage": run["currentStage"],
                            "pauseRequestedAt": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        },
                        clear_error=True,
                    )
                    self._emit_run_progress(transitioned)
                    pause_requested = True
            if pause_requested:
                self._emit(
                    "data.pause-requested",
                    {"processingId": processing_id, "taskId": run["taskId"]},
                )
        finally:
            store.close()
        return self.details(processing_id)

    def resume(self, processing_id: str) -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(processing_id)
        store = TaskStore(self.database_path)
        try:
            run = store.get_processing_run(processing_id)
            if run is None:
                raise ValidationError("PROCESSING_NOT_FOUND", "数据处理运行不存在")
            if not controller or not controller.thread or not controller.thread.is_alive():
                raise ValidationError("PROCESSING_NOT_PAUSED", "数据处理当前不处于可继续状态")
            if run["status"] in {"running"} and not controller.paused:
                return {
                    "run": run,
                    "documents": store.list_policy_documents(run["taskId"]),
                    "artifacts": store.list_task_artifacts(processing_id),
                }
            if run["status"] not in {"pausing", "paused"}:
                raise ValidationError("PROCESSING_NOT_PAUSED", "数据处理当前不处于暂停状态")
            self._validate_resume_for_processing()
            resume_requested = False
            with controller.condition:
                if controller.request_resume():
                    latest = store.get_processing_run(processing_id) or run
                    summary = {
                        key: value
                        for key, value in dict(latest.get("summary") or {}).items()
                        if key not in {
                            "pauseKind",
                            "pauseReason",
                            "pauseRequestedStage",
                            "pauseRequestedAt",
                            "pausedAt",
                            "pausedStage",
                        }
                    }
                    summary["resumedAt"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    )
                    resume_snapshot = _status_progress_snapshot(
                        latest,
                        stage="documents",
                    )
                    summary["progressSnapshot"] = resume_snapshot.to_dict()
                    transitioned = store.transition_processing_run(
                        processing_id,
                        run["taskId"],
                        status="running",
                        current_stage="resumed",
                        summary=summary,
                        clear_error=True,
                    )
                    self._emit_run_progress(transitioned)
                    resume_requested = True
            if resume_requested:
                self._emit(
                    "data.resumed",
                    {"processingId": processing_id, "taskId": run["taskId"]},
                )
        finally:
            store.close()
        return self.details(processing_id)

    def continue_run(self, processing_id: str) -> dict[str, Any]:
        with self.lock:
            controller = self.controllers.get(processing_id)
        if not controller or not controller.thread or not controller.thread.is_alive():
            raise ValidationError("PROCESSING_NOT_WAITING", "数据处理当前不在人工等待")
        store = TaskStore(self.database_path)
        try:
            run = store.get_processing_run(processing_id)
            if run is None:
                raise ValidationError("PROCESSING_NOT_FOUND", "数据处理运行不存在")
            if run["status"] not in {"waiting_login", "waiting_manual"}:
                return {
                    "run": run,
                    "documents": store.list_policy_documents(run["taskId"]),
                    "artifacts": store.list_task_artifacts(processing_id),
                }
            if controller.request_continue():
                self._emit(
                    "data.manual-continue-requested",
                    {"processingId": processing_id, "taskId": run["taskId"]},
                )
        finally:
            store.close()
        return self.details(processing_id)

    def continue_active(self) -> None:
        with self.lock:
            controllers = list(self.controllers.values())
        for controller in controllers:
            if controller.thread and controller.thread.is_alive():
                controller.request_continue()

    def details(self, processing_id: str) -> dict[str, Any]:
        store = TaskStore(self.database_path)
        try:
            run = store.get_processing_run(processing_id)
            if run is None:
                raise ValidationError("PROCESSING_NOT_FOUND", "数据处理运行不存在")
            return {
                "run": run,
                "documents": store.list_policy_documents(run["taskId"]),
                "artifacts": store.list_task_artifacts(processing_id),
                "timeline": store.list_task_events(run["taskId"]),
            }
        finally:
            store.close()

    def latest(self, task_id: str) -> dict[str, Any] | None:
        store = TaskStore(self.database_path)
        try:
            run = store.latest_processing_run(task_id)
            if run is None:
                return None
            return {
                "run": run,
                "documents": store.list_policy_documents(task_id),
                "artifacts": store.list_task_artifacts(run["id"]),
                "timeline": store.list_task_events(task_id),
            }
        finally:
            store.close()

    def has_active(self) -> bool:
        with self.lock:
            return any(
                controller.thread and controller.thread.is_alive()
                for controller in self.controllers.values()
            )

    def has_active_task(self, task_id: str) -> bool:
        with self.lock:
            controllers = [
                controller
                for controller in self.controllers.values()
                if controller.thread and controller.thread.is_alive()
            ]
        store = TaskStore(self.database_path)
        try:
            for controller in controllers:
                run = store.get_processing_run(controller.task_id)
                if run is not None and run["taskId"] == task_id:
                    return True
            return False
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

    def _run_with_lease(
        self,
        processing_id: str,
        task_id: str,
        options: dict[str, Any],
        controller: TaskControl,
        lease_id: str,
    ) -> None:
        try:
            self._run(processing_id, task_id, options, controller)
        finally:
            if self.browser_lease is not None and lease_id:
                self.browser_lease.release(lease_id)

    def _run(
        self,
        processing_id: str,
        task_id: str,
        options: dict[str, Any],
        controller: TaskControl,
    ) -> None:
        store = TaskStore(self.database_path)
        government = GovernmentProvider(self.browser_bridge, self.logger)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
            output_root = Path(task["outputDir"]).resolve() / "processed"
            source_root = output_root / "01_来源Word"
            txt_root = output_root / "02_正文TXT"
            government_generated_root = output_root / "03_政府官网正文整理"
            evidence_root = output_root / "04_来源证据"
            pending_root = output_root / "05_待核验"
            for directory in (
                source_root / "北大法宝",
                source_root / "政府官网",
                txt_root,
                government_generated_root,
                evidence_root,
                pending_root,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            store.clear_policy_documents(task_id)
            initial_snapshot = build_progress_snapshot(
                stage="documents",
                overall=0.05,
                completed_units=0,
                total_units=0,
                succeeded=0,
                failed=0,
                pending_review=0,
            )
            self._update_run(
                store,
                processing_id,
                task_id,
                status="running",
                progress=initial_snapshot.overall,
                current_stage="started",
                options=options,
                summary={
                    "progressSnapshot": initial_snapshot.to_dict(),
                    "completedDocuments": 0,
                    "totalDocuments": 0,
                    "currentAction": "started",
                },
            )
            self._safe_boundary(
                store,
                processing_id,
                task_id,
                controller,
                current_action="started",
                completed_documents=0,
            )

            items = store.list_task_items(task_id)
            total = max(1, len(items))
            for index, item in enumerate(items, start=1):
                self._safe_boundary(
                    store,
                    processing_id,
                    task_id,
                    controller,
                    current_action="process-policy",
                    current_policy_key=str(item["policyKey"]),
                    completed_documents=index - 1,
                )
                try:
                    document = self._process_item(
                        store=store,
                        task=task,
                        item=item,
                        options=options,
                        output_root=output_root,
                        source_root=source_root,
                        txt_root=txt_root,
                        government_generated_root=government_generated_root,
                        evidence_root=evidence_root,
                        pending_root=pending_root,
                        government=government,
                        controller=controller,
                        processing_id=processing_id,
                    )
                except (
                    InstitutionQuotaReached,
                    LoginRequired,
                    ManualActionRequired,
                    DownloadIntentExhausted,
                ):
                    raise
                except Exception as exc:
                    document = self._pending_document(
                        store,
                        task_id,
                        item,
                        {
                            "code": getattr(exc, "code", "DATA_PROCESSING_ITEM_FAILED"),
                            "message": str(exc),
                        },
                        pending_root,
                    )
                documents_so_far = store.list_policy_documents(task_id)
                succeeded = sum(
                    document["status"] == "processed"
                    for document in documents_so_far
                )
                failed = sum(
                    document["status"] == "failed"
                    for document in documents_so_far
                )
                pending_review = sum(
                    document["status"] == "pending_review"
                    for document in documents_so_far
                )
                progress_snapshot = build_progress_snapshot(
                    stage="documents",
                    overall=processing_document_overall(index, len(items)),
                    completed_units=index,
                    total_units=len(items),
                    succeeded=succeeded,
                    failed=failed,
                    pending_review=pending_review,
                )
                post_processing_status = (
                    "stopping"
                    if controller.stop_requested
                    else "pausing"
                    if controller.pause_requested
                    else "running"
                )
                self._update_run(
                    store,
                    processing_id,
                    task_id,
                    status=post_processing_status,
                    progress=progress_snapshot.overall,
                    current_stage=f"document-{index}-{total}",
                    options=options,
                    summary={
                        **(store.get_processing_run(processing_id) or {}).get(
                            "summary", {}
                        ),
                        "currentPolicyKey": str(item["policyKey"]),
                        "completedDocuments": index,
                        "totalDocuments": len(items),
                        "currentAction": "after-policy",
                        "progressSnapshot": progress_snapshot.to_dict(),
                    },
                )
                self._emit(
                    "data.document-processed",
                    {
                        "taskId": task_id,
                        "processingId": processing_id,
                        "itemId": item["id"],
                        "status": document["status"],
                        "title": document["title"],
                    },
                )
                self._safe_boundary(
                    store,
                    processing_id,
                    task_id,
                    controller,
                    current_action="after-policy",
                    current_policy_key=str(item["policyKey"]),
                    completed_documents=index,
                )

            self._safe_boundary(
                store,
                processing_id,
                task_id,
                controller,
                current_action="finalize-artifacts",
                completed_documents=len(items),
            )
            documents = store.list_policy_documents(task_id)
            final_csv = output_root / "06_总目录.csv"
            final_xlsx = output_root / "06_总目录.xlsx"
            final_report = output_root / "07_任务报告.html"
            artifact_staging = (
                output_root
                / ".processing-publishing"
                / f"{processing_id}-{uuid.uuid4()}"
            )
            staged_csv, staged_xlsx = stage_master_catalog(
                documents,
                output_root,
                artifact_staging,
            )
            artifacts = {
                "总目录CSV": str(staged_csv),
                "总目录XLSX": str(staged_xlsx),
            }
            report_path = write_task_report(
                output_root=output_root,
                task=task,
                processing_run=store.get_processing_run(processing_id) or {},
                documents=documents,
                artifacts=artifacts,
                report_path=artifact_staging / "07_任务报告.html",
                display_artifacts={
                    "总目录CSV": str(final_csv),
                    "总目录XLSX": str(final_xlsx),
                },
            )
            self._verify_outputs(
                output_root,
                documents,
                staged_csv,
                staged_xlsx,
            )
            artifact_records = [
                {
                    "kind": "总目录CSV",
                    "path": str(final_csv),
                    "sha256": file_sha256(staged_csv),
                },
                {
                    "kind": "总目录XLSX",
                    "path": str(final_xlsx),
                    "sha256": file_sha256(staged_xlsx),
                },
                {
                    "kind": "任务报告",
                    "path": str(final_report),
                    "sha256": file_sha256(report_path),
                },
            ]
            artifacts["任务报告"] = str(report_path)

            def verify_published_artifacts() -> None:
                self._verify_outputs(
                    output_root,
                    documents,
                    final_csv,
                    final_xlsx,
                )
                for artifact in artifact_records:
                    artifact_path = Path(str(artifact["path"]))
                    if not artifact_path.is_file():
                        raise ValidationError(
                            "OUTPUT_ARTIFACT_MISSING",
                            f"发布产物不存在：{artifact_path}",
                        )
                    if file_sha256(artifact_path) != artifact["sha256"]:
                        raise ValidationError(
                            "OUTPUT_ARTIFACT_HASH_MISMATCH",
                            f"发布产物哈希不一致：{artifact['kind']}",
                        )

            atomic_replace_many(
                [
                    (staged_csv, final_csv),
                    (staged_xlsx, final_xlsx),
                    (report_path, final_report),
                ],
                post_publish=verify_published_artifacts,
            )
            try:
                artifact_staging.rmdir()
            except OSError:
                pass
            artifacts = {
                "总目录CSV": str(final_csv),
                "总目录XLSX": str(final_xlsx),
                "任务报告": str(final_report),
            }
            store.replace_task_artifacts_atomic(
                task_id,
                processing_id,
                artifact_records,
            )
            self._verify_artifact_records(store, processing_id)
            self._safe_boundary(
                store,
                processing_id,
                task_id,
                controller,
                current_action="after-artifacts",
                completed_documents=len(documents),
            )
            processed = sum(document["status"] == "processed" for document in documents)
            pending = len(documents) - processed
            status = "completed" if pending == 0 else "partial"
            failed = sum(document["status"] == "failed" for document in documents)
            pending_review = sum(
                document["status"] == "pending_review"
                for document in documents
            )
            progress_snapshot = build_progress_snapshot(
                stage=status,
                overall=1.0,
                completed_units=len(documents),
                total_units=len(documents),
                succeeded=processed,
                failed=failed,
                pending_review=pending_review,
            )
            summary = {
                "outputRoot": str(output_root),
                "total": len(documents),
                "processed": processed,
                "pending": pending,
                "artifacts": artifacts,
                "currentAction": "after-artifacts",
                "completedDocuments": len(documents),
                "totalDocuments": len(documents),
                "progressSnapshot": progress_snapshot.to_dict(),
            }
            self._update_run(
                store,
                processing_id,
                task_id,
                status=status,
                progress=progress_snapshot.overall,
                current_stage="completed" if status == "completed" else "partial",
                options=options,
                summary=summary,
                error=None,
            )
            self._emit(
                "data.completed",
                {
                    "taskId": task_id,
                    "processingId": processing_id,
                    "status": status,
                    "summary": summary,
                },
            )
        except ProcessingStopped:
            existing = store.get_processing_run(processing_id) or {}
            summary = dict(existing.get("summary") or {})
            summary["stoppedAt"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            stopped_snapshot = _status_progress_snapshot(
                existing,
                stage="stopped",
            )
            summary["progressSnapshot"] = stopped_snapshot.to_dict()
            self._update_run(
                store,
                processing_id,
                task_id,
                status="stopped",
                progress=stopped_snapshot.overall,
                current_stage="stopped",
                options=options,
                summary=summary,
                error={"code": "PROCESSING_STOPPED", "message": "数据处理已停止"},
            )
            self._emit("data.stopped", {"processingId": processing_id, "taskId": task_id})
        except (
            InstitutionQuotaReached,
            LoginRequired,
            ManualActionRequired,
            DownloadIntentExhausted,
        ) as exc:
            status = "waiting_login" if isinstance(exc, LoginRequired) else "stopped"
            existing = store.get_processing_run(processing_id) or {}
            safety_snapshot = _status_progress_snapshot(
                existing,
                stage=status,
            )
            summary = {
                **dict(existing.get("summary") or {}),
                "safetyStoppedAt": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "progressSnapshot": safety_snapshot.to_dict(),
            }
            self._update_run(
                store,
                processing_id,
                task_id,
                status=status,
                progress=safety_snapshot.overall,
                current_stage=getattr(exc, "code", "MANUAL_ACTION_REQUIRED"),
                options=options,
                summary=summary,
                error={"code": getattr(exc, "code", "SOURCE_ACCESS_ERROR"), "message": str(exc)},
            )
            self._emit(
                "data.safety-stopped",
                {
                    "processingId": processing_id,
                    "taskId": task_id,
                    "message": str(exc),
                },
            )
        except Exception as exc:
            existing = store.get_processing_run(processing_id) or {}
            failed_snapshot = _status_progress_snapshot(
                existing,
                stage="failed",
            )
            self.logger.write(
                "error",
                "data.processing_failed",
                "Data processing failed",
                {"processingId": processing_id, "error": repr(exc)},
            )
            self._update_run(
                store,
                processing_id,
                task_id,
                status="failed",
                progress=failed_snapshot.overall,
                current_stage="failed",
                options=options,
                summary={
                    **dict(existing.get("summary") or {}),
                    "progressSnapshot": failed_snapshot.to_dict(),
                },
                error={"code": "DATA_PROCESSING_FAILED", "message": str(exc)},
            )
            self._emit(
                "data.failed",
                {"processingId": processing_id, "taskId": task_id, "message": str(exc)},
            )
        finally:
            store.close()
            with self.lock:
                self.controllers.pop(processing_id, None)

    def _process_item(
        self,
        *,
        store: TaskStore,
        task: dict[str, Any],
        item: dict[str, Any],
        options: dict[str, Any],
        output_root: Path,
        source_root: Path,
        txt_root: Path,
        government_generated_root: Path,
        evidence_root: Path,
        pending_root: Path,
        government: GovernmentProvider,
        controller: TaskControl,
        processing_id: str,
    ) -> dict[str, Any]:
        if can_use_government_fallback(task, item, options):
            return self._process_government(
                store=store,
                task=task,
                item=item,
                options=options,
                source_root=source_root,
                txt_root=txt_root,
                government_generated_root=government_generated_root,
                evidence_root=evidence_root,
                pending_root=pending_root,
                government=government,
                controller=controller,
                processing_id=processing_id,
            )
        if item["status"] != "completed":
            return self._pending_document(
                store,
                task["id"],
                item,
                {
                    "code": "SOURCE_NOT_COMPLETED",
                    "message": "源政策未成功取得可处理文件",
                },
                pending_root,
            )
        if task["mode"] == "single":
            return self._process_single_item(
                store,
                task,
                item,
                source_root,
                txt_root,
                evidence_root,
                pending_root,
            )
        return self._process_batch_item(
            store,
            task,
            item,
            source_root,
            txt_root,
            evidence_root,
            pending_root,
        )

    def _process_single_item(
        self,
        store: TaskStore,
        task: dict[str, Any],
        item: dict[str, Any],
        source_root: Path,
        txt_root: Path,
        evidence_root: Path,
        pending_root: Path,
    ) -> dict[str, Any]:
        payload = dict(item.get("payload") or {})
        detail = dict(payload.get("detail") or {})
        candidate = dict(payload.get("selectedCandidate") or {})
        word_source_value = str(payload.get("downloadedFile") or "")
        if not word_source_value or not Path(word_source_value).is_file():
            return self._pending_document(
                store,
                task["id"],
                item,
                {"code": "SOURCE_WORD_MISSING", "message": "来源 Word 文件不存在"},
                pending_root,
            )
        source_word = Path(word_source_value)
        policy_id = str(
            payload.get("selectedCandidateKey")
            or candidate.get("policyId")
            or item.get("policyKey", "").removeprefix("id:")
        )
        title = str(detail.get("title") or candidate.get("title") or item["title"])
        library = str(candidate.get("library") or task.get("input", {}).get("library") or "central")
        library_dir = "中央法规" if library == "central" else "地方法规"
        destination = (
            source_root
            / "北大法宝"
            / library_dir
            / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}{source_word.suffix.lower()}"
        )
        self._copy_atomic(source_word, destination)
        body = ""
        extraction: DocumentExtractionResult | None = None
        extraction_error: DocumentExtractionError | None = None
        try:
            extraction = extract_document(destination)
            body = extraction.text
        except DocumentExtractionError as exc:
            extraction_error = exc
            page_body = normalize_text(str(detail.get("body") or ""))
            if len(page_body) >= 20:
                body = page_body
                extraction = DocumentExtractionResult(
                    text=page_body,
                    artifact_method="detail-page-fallback",
                    text_source_type="detail-page-text",
                    converter_name="pkulaw-detail-dom",
                    converter_version="2026-11-02",
                )
        if not body or extraction is None:
            code = (
                extraction_error.code
                if extraction_error is not None
                else "DOC_TEXT_EXTRACTION_FAILED"
            )
            message = (
                str(extraction_error)
                if extraction_error is not None
                else "Word 正文无法提取"
            )
            return self._pending_document(
                store,
                task["id"],
                item,
                {
                    "code": code,
                    "message": message,
                    "details": (
                        extraction_error.details
                        if extraction_error is not None
                        else {}
                    ),
                },
                pending_root,
            )
        document_status, review_status, review_metadata = (
            _document_review_metadata(
                body=body,
                source_type="北大法宝原件",
                extraction=extraction,
            )
        )
        txt_path = txt_root / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}.txt"
        write_text(txt_path, body)
        word_sha = file_sha256(destination)
        txt_sha = file_sha256(txt_path)
        evidence_path = evidence_root / f"{safe_filename(policy_id, 40)}_source.json"
        self._write_json(
            evidence_path,
            {
                "sourceType": "北大法宝原件",
                "policyId": policy_id,
                "title": title,
                "sourceUrl": candidate.get("url") or payload.get("sourceUrl"),
                "wordPath": str(destination),
                "wordSha256": word_sha,
                "txtPath": str(txt_path),
                "txtSha256": txt_sha,
                "textMethod": extraction.artifact_method,
                "extraction": extraction.to_dict(),
                "bodyQuality": review_metadata["bodyQuality"],
                "detail": detail,
            },
        )
        return store.upsert_policy_document(
            task["id"],
            policy_key=item["policyKey"],
            policy_id=policy_id,
            title=title,
            document_number=str(detail.get("documentNumber") or candidate.get("documentNumber") or ""),
            issuing_authority=str(detail.get("issuingAuthority") or candidate.get("issuingAuthority") or ""),
            publication_date=str(detail.get("publicationDate") or candidate.get("publicationDate") or ""),
            effective_date=str(detail.get("effectiveDate") or candidate.get("effectiveDate") or ""),
            validity=str(detail.get("validity") or candidate.get("validity") or ""),
            effect_level=str(detail.get("effectLevel") or ""),
            source_type="北大法宝原件",
            source_url=str(candidate.get("url") or ""),
            retrieved_at=str(payload.get("retrievedAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            retrieval_method="单篇 Word 下载",
            word_path=str(destination),
            txt_path=str(txt_path),
            evidence_path=str(evidence_path),
            sha256=word_sha,
            text_source_type=extraction.text_source_type,
            extraction_method=extraction.artifact_method,
            converter_name=extraction.converter_name,
            converter_version=extraction.converter_version,
            body_completeness=str(review_metadata["bodyCompleteness"]),
            review_status=review_status,
            status=document_status,
            payload={
                "sourceItemId": item["id"],
                "textMethod": extraction.artifact_method,
                "txtSha256": txt_sha,
                "extraction": extraction.to_dict(),
                "bodyQuality": review_metadata["bodyQuality"],
            },
        )

    def _process_batch_item(
        self,
        store: TaskStore,
        task: dict[str, Any],
        item: dict[str, Any],
        source_root: Path,
        txt_root: Path,
        evidence_root: Path,
        pending_root: Path,
    ) -> dict[str, Any]:
        payload = dict(item.get("payload") or {})
        candidate = dict(payload.get("candidate") or payload.get("candidates", [{}])[0] or {})
        catalog_row = dict(payload.get("catalogRow") or {})
        archive = dict(payload.get("archive") or {})
        zip_path = Path(str(payload.get("zipFile") or ""))
        member = str(archive.get("archiveMember") or "")
        if not zip_path.is_file() or not member:
            return self._pending_document(
                store,
                task["id"],
                item,
                {"code": "BATCH_SOURCE_MISSING", "message": "批量 ZIP 或成员路径不存在"},
                pending_root,
            )
        policy_id = str(catalog_row.get("policyId") or candidate.get("policyId") or item["policyKey"].removeprefix("id:"))
        title = str(catalog_row.get("title") or candidate.get("title") or item["title"])
        library = str(candidate.get("library") or "central")
        library_dir = "中央法规" if library == "central" else "地方法规"
        extension = Path(member.replace("\\", "/")).suffix.lower() or ".docx"
        destination = (
            source_root
            / "北大法宝"
            / library_dir
            / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}{extension}"
        )
        materialize_zip_member(zip_path, member, destination)
        try:
            extraction = extract_document(destination)
            body = extraction.text
        except DocumentExtractionError as exc:
            return self._pending_document(
                store,
                task["id"],
                item,
                {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
                pending_root,
            )
        source_type = "北大法宝ZIP"
        document_status, review_status, review_metadata = (
            _document_review_metadata(
                body=body,
                source_type=source_type,
                extraction=extraction,
            )
        )
        txt_path = txt_root / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}.txt"
        write_text(txt_path, body)
        word_sha = file_sha256(destination)
        txt_sha = file_sha256(txt_path)
        evidence_path = evidence_root / f"{safe_filename(policy_id, 40)}_source.json"
        self._write_json(
            evidence_path,
            {
                "sourceType": source_type,
                "policyId": policy_id,
                "title": title,
                "sourceUrl": catalog_row.get("url") or candidate.get("url"),
                "catalogRow": catalog_row,
                "zipPath": str(zip_path),
                "archiveMember": member,
                "archiveSha256": archive.get("sha256"),
                "wordSha256": word_sha,
                "txtPath": str(txt_path),
                "txtSha256": txt_sha,
                "textMethod": extraction.artifact_method,
                "extraction": extraction.to_dict(),
                "bodyQuality": review_metadata["bodyQuality"],
            },
        )
        return store.upsert_policy_document(
            task["id"],
            policy_key=item["policyKey"],
            policy_id=policy_id,
            title=title,
            document_number=str(candidate.get("documentNumber") or ""),
            issuing_authority=str(candidate.get("issuingAuthority") or ""),
            publication_date=str(candidate.get("publicationDate") or ""),
            effective_date=str(candidate.get("effectiveDate") or ""),
            validity=str(candidate.get("validity") or ""),
            effect_level=str(candidate.get("effectLevel") or ""),
            source_type=source_type,
            source_url=str(catalog_row.get("url") or candidate.get("url") or ""),
            retrieved_at=str(
                payload.get("retrievedAt")
                or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ),
            retrieval_method="网站原生 Word ZIP",
            word_path=str(destination),
            txt_path=str(txt_path),
            evidence_path=str(evidence_path),
            sha256=word_sha,
            text_source_type=extraction.text_source_type,
            extraction_method=extraction.artifact_method,
            converter_name=extraction.converter_name,
            converter_version=extraction.converter_version,
            body_completeness=str(review_metadata["bodyCompleteness"]),
            review_status=review_status,
            status=document_status,
            payload={
                "sourceItemId": item["id"],
                "textMethod": extraction.artifact_method,
                "txtSha256": txt_sha,
                "extraction": extraction.to_dict(),
                "bodyQuality": review_metadata["bodyQuality"],
            },
        )

    def _process_government(
        self,
        *,
        store: TaskStore,
        task: dict[str, Any],
        item: dict[str, Any],
        options: dict[str, Any],
        source_root: Path,
        txt_root: Path,
        government_generated_root: Path,
        evidence_root: Path,
        pending_root: Path,
        government: GovernmentProvider,
        controller: TaskControl,
        processing_id: str,
    ) -> dict[str, Any]:
        title = str(item.get("title") or "")
        search_template = str(options.get("governmentSearchTemplate") or "")
        self._safe_boundary(
            store,
            processing_id,
            task["id"],
            controller,
            current_action="government-search",
            current_policy_key=str(item["policyKey"]),
        )
        try:
            page = self._with_manual_retry(
                store,
                processing_id,
                task["id"],
                controller,
                lambda: government.resolve(
                    title=title,
                    search_template=search_template,
                    evidence_directory=evidence_root / safe_filename(title, 80),
                ),
                action_name="government-search",
            )
        except GovernmentSourceUnavailable as exc:
            return self._pending_document(
                store,
                task["id"],
                item,
                {"code": "GOVERNMENT_SOURCE_UNAVAILABLE", "message": str(exc)},
                pending_root,
            )

        fields = dict(page.get("fields") or {})
        attachments = [
            attachment
            for attachment in (page.get("attachments") or [])
            if Path(urlparse(str(attachment.get("url") or "")).path).suffix.lower()
            in {".docx", ".doc"}
        ]
        policy_id = "GOV-" + hashlib.sha256(
            str(page.get("sourceUrl") or title).encode("utf-8")
        ).hexdigest()[:12].upper()
        if attachments:
            self._safe_boundary(
                store,
                processing_id,
                task["id"],
                controller,
                current_action="government-attachment-download",
                current_policy_key=str(item["policyKey"]),
            )
            attachment_url = str(attachments[0].get("url") or "")
            attachment_intent = self.browser_bridge.create_download_intent(
                task_id=task["id"],
                kind="government-word",
                source_url=attachment_url,
                expected_suffixes=(".doc", ".docx"),
            )
            self._update_run(
                store,
                processing_id,
                task["id"],
                summary={
                    **(store.get_processing_run(processing_id) or {}).get(
                        "summary",
                        {},
                    ),
                    "currentDownloadIntent": attachment_intent,
                    "currentDownloadStatus": "pending",
                    "currentAction": "government-attachment-download",
                },
            )
            cursor = self.browser_bridge.download_cursor()
            self._with_manual_retry(
                store,
                processing_id,
                task["id"],
                controller,
                lambda: government.trigger_attachment(attachment_url),
                action_name="government-attachment-download",
            )

            def record_attachment_progress(event: dict[str, Any]) -> None:
                outcome = str(event.get("outcome") or "")
                if outcome not in {"started", "completed", "failed"}:
                    return
                current = store.get_processing_run(processing_id) or {}
                self._update_run(
                    store,
                    processing_id,
                    task["id"],
                    summary={
                        **(current.get("summary") or {}),
                        "currentDownloadIntent": {
                            **attachment_intent,
                            "downloadId": event.get("downloadId"),
                            "path": str(event.get("path") or ""),
                            "status": outcome,
                            "matchedDownloads": 1,
                        },
                        "currentDownloadStatus": outcome,
                    },
                )

            event = self.browser_bridge.wait_for_download_intent(
                str(attachment_intent["intentId"]),
                cursor,
                timeout_seconds=75,
                on_event=record_attachment_progress,
            )
            if not event or event.get("event") != "completed":
                raise GovernmentSourceUnavailable("政府官网附件下载未完成")
            downloaded = Path(str(event.get("path") or ""))
            self._update_run(
                store,
                processing_id,
                task["id"],
                summary={
                    **(store.get_processing_run(processing_id) or {}).get(
                        "summary",
                        {},
                    ),
                    "currentDownloadIntent": {
                        **attachment_intent,
                        "downloadId": event.get("downloadId"),
                        "path": str(downloaded),
                        "status": "completed",
                        "matchedDownloads": 1,
                    },
                    "currentDownloadStatus": "completed",
                },
            )
            extension = (
                Path(urlparse(attachment_url).path).suffix.lower()
                or downloaded.suffix.lower()
            )
            valid, reason = validate_word_attachment(downloaded, extension)
            self.browser_bridge.record_download_intent_validation(
                str(attachment_intent["intentId"]),
                valid=valid,
                actual_kind=extension,
                detail=reason,
            )
            if not valid:
                self._update_run(
                    store,
                    processing_id,
                    task["id"],
                    summary={
                        **(store.get_processing_run(processing_id) or {}).get(
                            "summary",
                            {},
                        ),
                        "currentDownloadStatus": "validation_failed",
                        "downloadValidationDetail": reason,
                    },
                )
                return self._pending_document(
                    store,
                    task["id"],
                    item,
                    {"code": "GOVERNMENT_ATTACHMENT_INVALID", "message": reason},
                    pending_root,
                )
            self._update_run(
                store,
                processing_id,
                task["id"],
                summary={
                    **(store.get_processing_run(processing_id) or {}).get(
                        "summary",
                        {},
                    ),
                    "currentDownloadStatus": "validated",
                    "downloadValidationDetail": reason,
                },
            )
            destination = (
                source_root
                / "政府官网"
                / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}{extension}"
            )
            self._copy_atomic(downloaded, destination)
            source_type = "政府官网原生附件"
            retrieval_method = "政府官网附件下载"
        else:
            page_body = normalize_text(str(page.get("body") or ""))
            if len(page_body) < 20:
                return self._pending_document(
                    store,
                    task["id"],
                    item,
                    {"code": "GOVERNMENT_BODY_TOO_SHORT", "message": "政府官网正文不足"},
                    pending_root,
                )
            destination = (
                government_generated_root
                / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}_政府官网正文整理.docx"
            )
            create_docx_from_text(title, page_body, destination)
            source_type = "政府官网正文整理"
            retrieval_method = "政府官网正文提取并生成DOCX"

        try:
            extraction = extract_document(destination)
        except DocumentExtractionError as exc:
            return self._pending_document(
                store,
                task["id"],
                item,
                {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
                pending_root,
                source_type=source_type,
                retrieval_method=retrieval_method,
            )
        body = extraction.text
        document_status, review_status, review_metadata = (
            _document_review_metadata(
                body=body,
                source_type=source_type,
                extraction=extraction,
            )
        )
        txt_path = txt_root / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}.txt"
        write_text(txt_path, body)
        word_sha = file_sha256(destination)
        txt_sha = file_sha256(txt_path)
        evidence_path = evidence_root / f"{safe_filename(policy_id, 40)}_official_evidence.json"
        self._write_json(
            evidence_path,
            {
                "sourceType": source_type,
                "policyId": policy_id,
                "title": title,
                "sourceUrl": page.get("sourceUrl"),
                "searchUrl": page.get("searchUrl"),
                "htmlPath": page.get("htmlPath"),
                "htmlSha256": page.get("htmlSha256"),
                "screenshotPath": page.get("screenshotPath"),
                "screenshotSha256": page.get("screenshotSha256"),
                "fields": fields,
                "wordPath": str(destination),
                "wordSha256": word_sha,
                "txtPath": str(txt_path),
                "txtSha256": txt_sha,
                "textMethod": extraction.artifact_method,
                "extraction": extraction.to_dict(),
                "bodyQuality": review_metadata["bodyQuality"],
            },
        )
        document = store.upsert_policy_document(
            task["id"],
            policy_key=item["policyKey"],
            policy_id=policy_id,
            title=title,
            document_number=str(fields.get("documentNumber") or ""),
            issuing_authority=str(fields.get("issuingAuthority") or ""),
            publication_date=str(fields.get("publicationDate") or ""),
            effective_date=str(fields.get("effectiveDate") or ""),
            validity=str(fields.get("validity") or ""),
            effect_level=str(fields.get("effectLevel") or ""),
            source_type=source_type,
            source_url=str(page.get("sourceUrl") or ""),
            retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            retrieval_method=retrieval_method,
            word_path=str(destination),
            txt_path=str(txt_path),
            evidence_path=str(evidence_path),
            sha256=word_sha,
            text_source_type=extraction.text_source_type,
            extraction_method=extraction.artifact_method,
            converter_name=extraction.converter_name,
            converter_version=extraction.converter_version,
            body_completeness=str(review_metadata["bodyCompleteness"]),
            review_status=review_status,
            status=document_status,
            payload={
                "sourceItemId": item["id"],
                "officialHtml": page.get("htmlPath"),
                "officialHtmlSha256": page.get("htmlSha256"),
                "officialScreenshot": page.get("screenshotPath"),
                "officialScreenshotSha256": page.get("screenshotSha256"),
                "txtSha256": txt_sha,
                "textMethod": extraction.artifact_method,
                "extraction": extraction.to_dict(),
                "bodyQuality": review_metadata["bodyQuality"],
            },
        )
        store.upsert_task_item(
            task["id"],
            policy_key=item["policyKey"],
            title=title,
            status="completed",
            source_type=source_type,
            progress=1.0,
            payload={
                **(item.get("payload") or {}),
                "governmentDocumentId": document["id"],
                "sourceUrl": page.get("sourceUrl"),
            },
            current_stage="government-completed",
        )
        return document

    def _pending_document(
        self,
        store: TaskStore,
        task_id: str,
        item: dict[str, Any],
        error: dict[str, Any],
        pending_root: Path,
        *,
        source_type: str = "",
        retrieval_method: str = "",
    ) -> dict[str, Any]:
        payload = dict(item.get("payload") or {})
        candidate = dict(payload.get("selectedCandidate") or payload.get("candidate") or {})
        title = str(item.get("title") or candidate.get("title") or "")
        policy_id = str(
            candidate.get("policyId")
            or item.get("policyKey", "").removeprefix("id:")
            or hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
        )
        resolved_source_type = str(
            source_type
            or item.get("sourceType")
            or candidate.get("sourceType")
            or "待核验"
        )
        default_retrieval_method = {
            "北大法宝原件": "单篇 Word 下载",
            "北大法宝ZIP": "网站原生 Word ZIP",
            "政府官网原生附件": "政府官网附件下载",
            "政府官网正文整理": "政府官网正文提取并生成DOCX",
        }.get(resolved_source_type, "未取得可处理的正式来源")
        pending_root.mkdir(parents=True, exist_ok=True)
        pending_path = pending_root / f"{safe_filename(policy_id, 40)}_{safe_filename(title, 100)}.json"
        self._write_json(
            pending_path,
            {
                "policyKey": item["policyKey"],
                "title": title,
                "status": "pending_review",
                "error": error,
                "sourceItem": item,
            },
        )
        return store.upsert_policy_document(
            task_id,
            policy_key=item["policyKey"],
            policy_id=policy_id,
            title=title,
            document_number=str(candidate.get("documentNumber") or ""),
            issuing_authority=str(candidate.get("issuingAuthority") or ""),
            publication_date=str(candidate.get("publicationDate") or ""),
            effective_date=str(candidate.get("effectiveDate") or ""),
            validity=str(candidate.get("validity") or ""),
            effect_level=str(candidate.get("effectLevel") or ""),
            source_type=resolved_source_type,
            source_url=str(candidate.get("url") or ""),
            retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            retrieval_method=retrieval_method or default_retrieval_method,
            evidence_path=str(pending_path),
            text_source_type="",
            extraction_method="",
            converter_name="",
            converter_version="",
            body_completeness="failed",
            review_status="pending_review",
            status="pending_review",
            error=error,
            payload={
                "sourceItemId": item["id"],
                "extractionError": error,
            },
        )

    def _with_manual_retry(
        self,
        store: TaskStore,
        processing_id: str,
        task_id: str,
        controller: TaskControl,
        action: Callable[[], Any],
        attempts: int = 3,
        action_name: str = "action",
    ) -> Any:
        last: Exception | None = None
        for _ in range(attempts):
            self._safe_boundary(
                store,
                processing_id,
                task_id,
                controller,
                current_action=action_name,
            )
            try:
                return action()
            except (ManualActionRequired, LoginRequired) as exc:
                last = exc
                status = "waiting_login" if isinstance(exc, LoginRequired) else "waiting_manual"
                self._wait_for_manual(
                    store,
                    processing_id,
                    task_id,
                    controller,
                    str(exc),
                    status,
                )
        raise last or RuntimeError("manual recovery exhausted")

    def _wait_for_manual(
        self,
        store: TaskStore,
        processing_id: str,
        task_id: str,
        controller: TaskControl,
        message: str,
        status: str,
    ) -> None:
        run = store.get_processing_run(processing_id) or {}
        summary = dict(run.get("summary") or {})
        summary.update(
            {
                "pauseKind": "automatic",
                "pauseReason": message,
                "manualStatus": status,
                "manualAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        self._update_run(
            store,
            processing_id,
            task_id,
            status=status,
            progress=run.get("progress", 0),
            current_stage=(
                "waiting-login" if status == "waiting_login" else "waiting-manual"
            ),
            options=run.get("options") or {},
            summary=summary,
            error={
                "code": (
                    "LOGIN_REQUIRED"
                    if status == "waiting_login"
                    else "MANUAL_ACTION_REQUIRED"
                ),
                "message": message,
            },
        )
        self._emit(
            "data.manual-required",
            {
                "processingId": processing_id,
                "taskId": task_id,
                "status": status,
                "message": message,
            },
        )
        controller.manual_wait()
        self._update_run(
            store,
            processing_id,
            task_id,
            status="running",
            progress=run.get("progress", 0),
            current_stage="manual-continued",
            options=run.get("options") or {},
            summary={
                **summary,
                "resumedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "resumeKind": "automatic",
            },
        )

    def _safe_boundary(
        self,
        store: TaskStore,
        processing_id: str,
        task_id: str,
        controller: TaskControl,
        *,
        current_action: str,
        current_policy_key: str | None = None,
        completed_documents: int | None = None,
    ) -> None:
        controller.check_stop()
        run = store.get_processing_run(processing_id)
        if run is None:
            raise ValidationError("PROCESSING_NOT_FOUND", "数据处理运行不存在")
        summary = dict(run.get("summary") or {})
        summary["currentAction"] = current_action
        if current_policy_key is not None:
            summary["currentPolicyKey"] = current_policy_key
        if completed_documents is not None:
            summary["completedDocuments"] = completed_documents
        store.upsert_processing_run(
            processing_id,
            task_id,
            status=run["status"],
            progress=run["progress"],
            current_stage=run["currentStage"],
            options=run["options"],
            summary=summary,
            error=run.get("error"),
        )

        def mark_paused(reason: str) -> None:
            latest = store.get_processing_run(processing_id) or run
            paused_summary = dict(latest.get("summary") or summary)
            paused_summary.update(
                {
                    "pauseKind": "manual",
                    "pauseReason": reason,
                    "pausedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "pausedStage": str(
                        paused_summary.get("pauseRequestedStage")
                        or latest.get("currentStage")
                        or ""
                    ),
                    "currentAction": current_action,
                }
            )
            paused_snapshot = _status_progress_snapshot(
                latest,
                stage="paused",
            )
            paused_summary["progressSnapshot"] = paused_snapshot.to_dict()
            self._update_run(
                store,
                processing_id,
                task_id,
                status="paused",
                progress=paused_snapshot.overall,
                current_stage="paused",
                options=latest.get("options") or {},
                summary=paused_summary,
                error=None,
            )
            self._emit(
                "data.paused",
                {
                    "processingId": processing_id,
                    "taskId": task_id,
                    "reason": reason,
                    "currentAction": current_action,
                },
            )

        controller.wait_at_boundary(on_pause=mark_paused)
        controller.check_stop()

    def _validate_resume_for_processing(self) -> None:
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

    def _verify_outputs(
        self,
        output_root: Path,
        documents: list[dict[str, Any]],
        csv_path: Path,
        xlsx_path: Path,
    ) -> None:
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
        try:
            xlsx_rows = list(workbook.active.iter_rows(values_only=True))
        finally:
            workbook.close()
        if len(csv_rows) != len(documents) or len(xlsx_rows) - 1 != len(documents):
            raise ValidationError(
                "OUTPUT_ROW_MISMATCH",
                f"SQLite={len(documents)} CSV={len(csv_rows)} XLSX={max(0, len(xlsx_rows)-1)}",
            )
        for document in documents:
            if document["status"] != "processed":
                continue
            for key in ("wordPath", "txtPath", "evidencePath"):
                path = Path(str(document.get(key) or ""))
                if not path.is_file():
                    raise ValidationError(
                        "OUTPUT_FILE_MISSING",
                        f"{document['title']} 的 {key} 不存在：{path}",
                    )
            if file_sha256(Path(document["wordPath"])) != document["sha256"]:
                raise ValidationError("OUTPUT_HASH_MISMATCH", f"{document['title']} 的 Word 哈希不一致")
            expected_txt_sha = str((document.get("payload") or {}).get("txtSha256") or "")
            if expected_txt_sha and file_sha256(Path(document["txtPath"])) != expected_txt_sha:
                raise ValidationError("OUTPUT_HASH_MISMATCH", f"{document['title']} 的 TXT 哈希不一致")
            try:
                evidence = json.loads(
                    Path(document["evidencePath"]).read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                raise ValidationError(
                    "OUTPUT_EVIDENCE_INVALID",
                    f"{document['title']} 的来源证据无法读取：{exc}",
                ) from exc
            if evidence.get("sourceType") != document["sourceType"]:
                raise ValidationError(
                    "OUTPUT_EVIDENCE_MISMATCH",
                    f"{document['title']} 的证据来源类型不一致",
                )
            if evidence.get("wordSha256") != document["sha256"]:
                raise ValidationError(
                    "OUTPUT_EVIDENCE_MISMATCH",
                    f"{document['title']} 的证据 Word 哈希不一致",
                )
            if expected_txt_sha and evidence.get("txtSha256") != expected_txt_sha:
                raise ValidationError(
                    "OUTPUT_EVIDENCE_MISMATCH",
                    f"{document['title']} 的证据 TXT 哈希不一致",
                )
            if document["sourceType"].startswith("政府官网"):
                for key in ("htmlPath", "screenshotPath"):
                    evidence_file = Path(str(evidence.get(key) or ""))
                    if not evidence_file.is_file() or evidence_file.stat().st_size == 0:
                        raise ValidationError(
                            "OUTPUT_EVIDENCE_MISSING",
                            f"{document['title']} 的官方证据 {key} 不存在或为空",
                        )
                if file_sha256(Path(evidence["htmlPath"])) != evidence.get(
                    "htmlSha256"
                ):
                    raise ValidationError(
                        "OUTPUT_HASH_MISMATCH",
                        f"{document['title']} 的官方 HTML 哈希不一致",
                    )
                if file_sha256(Path(evidence["screenshotPath"])) != evidence.get(
                    "screenshotSha256"
                ):
                    raise ValidationError(
                        "OUTPUT_HASH_MISMATCH",
                        f"{document['title']} 的官方截图哈希不一致",
                    )

    def _verify_artifact_records(
        self,
        store: TaskStore,
        processing_id: str,
    ) -> None:
        records = store.list_task_artifacts(processing_id)
        if len(records) != 3:
            raise ValidationError(
                "OUTPUT_ARTIFACT_COUNT_MISMATCH",
                f"产物登记数量为 {len(records)}，预期 3",
            )
        for artifact in records:
            path = Path(str(artifact.get("path") or ""))
            if not path.is_file():
                raise ValidationError(
                    "OUTPUT_ARTIFACT_MISSING",
                    f"登记产物不存在：{path}",
                )
            actual = file_sha256(path)
            if actual != artifact.get("sha256"):
                raise ValidationError(
                    "OUTPUT_ARTIFACT_HASH_MISMATCH",
                    f"登记产物哈希不一致：{artifact.get('kind')}",
                )

    def _update_run(
        self,
        store: TaskStore,
        processing_id: str,
        task_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        current_stage: str | None = None,
        options: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = store.get_processing_run(processing_id)
        run = store.upsert_processing_run(
            processing_id,
            task_id,
            status=status or (existing["status"] if existing else "running"),
            progress=progress if progress is not None else (existing["progress"] if existing else 0),
            current_stage=current_stage or (existing["currentStage"] if existing else ""),
            options=options if options is not None else (existing["options"] if existing else {}),
            summary=summary if summary is not None else (existing["summary"] if existing else {}),
            error=error,
        )
        self._emit_run_progress(run)
        return run

    def _emit_run_progress(self, run: dict[str, Any]) -> None:
        self._emit(
            "data.progress",
            {
                "processingId": run["id"],
                "taskId": run["taskId"],
                "status": run["status"],
                "progress": run["progress"],
                "progressSnapshot": run.get("progressSnapshot"),
                "currentStage": run["currentStage"],
            },
        )

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        try:
            self.event_sink(event, data)
        except Exception as exc:
            self.logger.write(
                "warn",
                "data.event_emit_failed",
                "Failed to emit data processing event",
                {"event": event, "error": repr(exc)},
            )
        self.logger.write("info", event, event, data)

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(str(destination) + ".tmp")
        shutil.copy2(source, temporary)
        temporary.replace(destination)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(str(path) + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
