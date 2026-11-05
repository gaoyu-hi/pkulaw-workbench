from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 6
SEARCH_SEMANTICS_VERSION = 2
TASK_MODES = {"single", "batch"}
TASK_STATUSES = {
    "pending",
    "preflight",
    "waiting_login",
    "running",
    "waiting_manual",
    "verifying",
    "pausing",
    "paused",
    "stopping",
    "completed",
    "partial",
    "stopped",
    "failed",
}
ACTIVE_TASK_STATUSES = {
    "preflight",
    "waiting_login",
    "running",
    "waiting_manual",
    "verifying",
    "pausing",
    "paused",
    "stopping",
}
ACTIVE_PROCESSING_STATUSES = {
    "pending",
    "running",
    "waiting_manual",
    "waiting_login",
    "pausing",
    "paused",
    "stopping",
}
ITEM_STATUSES = {
    "pending",
    "running",
    "verifying",
    "waiting_manual",
    "pending_review",
    "completed",
    "failed",
    "skipped",
}


def normalize_task_input_metadata(
    task_input: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(task_input)
    normalized.setdefault(
        "searchSemanticsVersion",
        SEARCH_SEMANTICS_VERSION,
    )
    return normalized
BODY_COMPLETENESS_VALUES = {
    "unknown",
    "complete",
    "suspicious",
    "failed",
}
REVIEW_STATUS_VALUES = {
    "pending_review",
    "not_required",
    "verified",
    "rejected",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class ValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class TaskStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        current_version = int(
            self.connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if 0 < current_version < SCHEMA_VERSION:
            self._backup_before_migration(current_version)
        self._initialize()

    def _backup_before_migration(self, current_version: int) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.database_path.with_name(
            f"{self.database_path.name}.pre-v{SCHEMA_VERSION}-{stamp}.bak"
        )
        backup_connection = sqlite3.connect(backup_path)
        try:
            self.connection.backup(backup_connection)
        finally:
            backup_connection.close()
        return backup_path

    def _initialize(self) -> None:
        current_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {current_version} is newer than supported version {SCHEMA_VERSION}"
            )
        if current_version == 0:
            with self.connection:
                self.connection.executescript(
                    """
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK (mode IN ('single', 'batch')),
                        status TEXT NOT NULL,
                        input_json TEXT NOT NULL DEFAULT '{}',
                        output_dir TEXT NOT NULL DEFAULT '',
                        progress REAL NOT NULL DEFAULT 0,
                        current_stage TEXT NOT NULL DEFAULT '',
                        error_json TEXT,
                        checkpoint_json TEXT NOT NULL DEFAULT '{}',
                        archived_at TEXT,
                        archive_reason TEXT,
                        started_at TEXT,
                        finished_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX idx_tasks_status_updated
                    ON tasks(status, updated_at DESC);

                    CREATE TABLE task_items (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        policy_key TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        source_type TEXT NOT NULL DEFAULT '',
                        progress REAL NOT NULL DEFAULT 0,
                        current_stage TEXT NOT NULL DEFAULT 'pending',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        error_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(task_id, policy_key)
                    );

                    CREATE INDEX idx_task_items_task_status
                    ON task_items(task_id, status);

                    CREATE TABLE task_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX idx_task_events_task_created
                    ON task_events(task_id, created_at);

                    CREATE TABLE task_batches (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        batch_key TEXT NOT NULL,
                        query TEXT NOT NULL,
                        library TEXT NOT NULL,
                        page INTEGER NOT NULL,
                        row_start INTEGER NOT NULL,
                        row_end INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        progress REAL NOT NULL DEFAULT 0,
                        catalog_format TEXT NOT NULL,
                        full_format TEXT NOT NULL,
                        selected_json TEXT NOT NULL DEFAULT '[]',
                        catalog_path TEXT NOT NULL DEFAULT '',
                        zip_path TEXT NOT NULL DEFAULT '',
                        report_path TEXT NOT NULL DEFAULT '',
                        error_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(task_id, batch_key)
                    );

                    CREATE INDEX idx_task_batches_task_status
                    ON task_batches(task_id, status);

                    CREATE TABLE policy_documents (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        policy_key TEXT NOT NULL,
                        policy_id TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        document_number TEXT NOT NULL DEFAULT '',
                        issuing_authority TEXT NOT NULL DEFAULT '',
                        publication_date TEXT NOT NULL DEFAULT '',
                        effective_date TEXT NOT NULL DEFAULT '',
                        validity TEXT NOT NULL DEFAULT '',
                        effect_level TEXT NOT NULL DEFAULT '',
                        source_type TEXT NOT NULL DEFAULT '',
                        source_url TEXT NOT NULL DEFAULT '',
                        retrieved_at TEXT NOT NULL DEFAULT '',
                        retrieval_method TEXT NOT NULL DEFAULT '',
                        word_path TEXT NOT NULL DEFAULT '',
                        txt_path TEXT NOT NULL DEFAULT '',
                        evidence_path TEXT NOT NULL DEFAULT '',
                        sha256 TEXT NOT NULL DEFAULT '',
                        text_source_type TEXT NOT NULL DEFAULT '',
                        extraction_method TEXT NOT NULL DEFAULT '',
                        converter_name TEXT NOT NULL DEFAULT '',
                        converter_version TEXT NOT NULL DEFAULT '',
                        body_completeness TEXT NOT NULL DEFAULT 'unknown',
                        review_status TEXT NOT NULL DEFAULT 'pending_review',
                        status TEXT NOT NULL DEFAULT 'pending',
                        error_json TEXT,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(task_id, policy_key)
                    );

                    CREATE INDEX idx_policy_documents_task_status
                    ON policy_documents(task_id, status);

                    CREATE TABLE processing_runs (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        status TEXT NOT NULL,
                        progress REAL NOT NULL DEFAULT 0,
                        current_stage TEXT NOT NULL DEFAULT '',
                        options_json TEXT NOT NULL DEFAULT '{}',
                        summary_json TEXT NOT NULL DEFAULT '{}',
                        error_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX idx_processing_runs_task_created
                    ON processing_runs(task_id, created_at DESC);

                    CREATE TABLE task_artifacts (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        processing_id TEXT NOT NULL REFERENCES processing_runs(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        path TEXT NOT NULL,
                        sha256 TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX idx_task_artifacts_processing
                    ON task_artifacts(processing_id);
                    """
                )
                self.connection.execute("PRAGMA user_version = 6")
        else:
            if current_version == 1:
                with self.connection:
                    self.connection.execute(
                        "ALTER TABLE tasks ADD COLUMN checkpoint_json TEXT NOT NULL DEFAULT '{}'"
                    )
                    self.connection.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
                    self.connection.execute("ALTER TABLE tasks ADD COLUMN finished_at TEXT")
                    self.connection.execute(
                        "ALTER TABLE task_items ADD COLUMN current_stage TEXT NOT NULL DEFAULT 'pending'"
                    )
                    self.connection.execute("PRAGMA user_version = 2")
                current_version = 2

            if current_version == 2:
                with self.connection:
                    self.connection.executescript(
                        """
                        CREATE TABLE task_batches (
                            id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                            batch_key TEXT NOT NULL,
                            query TEXT NOT NULL,
                            library TEXT NOT NULL,
                            page INTEGER NOT NULL,
                            row_start INTEGER NOT NULL,
                            row_end INTEGER NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending',
                            progress REAL NOT NULL DEFAULT 0,
                            catalog_format TEXT NOT NULL,
                            full_format TEXT NOT NULL,
                            selected_json TEXT NOT NULL DEFAULT '[]',
                            catalog_path TEXT NOT NULL DEFAULT '',
                            zip_path TEXT NOT NULL DEFAULT '',
                            report_path TEXT NOT NULL DEFAULT '',
                            error_json TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(task_id, batch_key)
                        );

                        CREATE INDEX idx_task_batches_task_status
                        ON task_batches(task_id, status);
                        """
                    )
                    self.connection.execute("PRAGMA user_version = 3")
                current_version = 3

            if current_version == 3:
                with self.connection:
                    self.connection.executescript(
                        """
                        CREATE TABLE policy_documents (
                            id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                            policy_key TEXT NOT NULL,
                            policy_id TEXT NOT NULL DEFAULT '',
                            title TEXT NOT NULL DEFAULT '',
                            document_number TEXT NOT NULL DEFAULT '',
                            issuing_authority TEXT NOT NULL DEFAULT '',
                            publication_date TEXT NOT NULL DEFAULT '',
                            effective_date TEXT NOT NULL DEFAULT '',
                            validity TEXT NOT NULL DEFAULT '',
                            effect_level TEXT NOT NULL DEFAULT '',
                            source_type TEXT NOT NULL DEFAULT '',
                            source_url TEXT NOT NULL DEFAULT '',
                            retrieved_at TEXT NOT NULL DEFAULT '',
                            retrieval_method TEXT NOT NULL DEFAULT '',
                            word_path TEXT NOT NULL DEFAULT '',
                            txt_path TEXT NOT NULL DEFAULT '',
                            evidence_path TEXT NOT NULL DEFAULT '',
                            sha256 TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL DEFAULT 'pending',
                            error_json TEXT,
                            payload_json TEXT NOT NULL DEFAULT '{}',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(task_id, policy_key)
                        );

                        CREATE INDEX idx_policy_documents_task_status
                        ON policy_documents(task_id, status);

                        CREATE TABLE processing_runs (
                            id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                            status TEXT NOT NULL,
                            progress REAL NOT NULL DEFAULT 0,
                            current_stage TEXT NOT NULL DEFAULT '',
                            options_json TEXT NOT NULL DEFAULT '{}',
                            summary_json TEXT NOT NULL DEFAULT '{}',
                            error_json TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE INDEX idx_processing_runs_task_created
                        ON processing_runs(task_id, created_at DESC);

                        CREATE TABLE task_artifacts (
                            id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                            processing_id TEXT NOT NULL REFERENCES processing_runs(id) ON DELETE CASCADE,
                            kind TEXT NOT NULL,
                            path TEXT NOT NULL,
                            sha256 TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL
                        );

                        CREATE INDEX idx_task_artifacts_processing
                        ON task_artifacts(processing_id);
                        """
                    )
                    self.connection.execute("PRAGMA user_version = 4")
                current_version = 4

            if current_version == 4:
                with self.connection:
                    columns = {
                        row[1]
                        for row in self.connection.execute("PRAGMA table_info(tasks)")
                    }
                    if "archived_at" not in columns:
                        self.connection.execute(
                            "ALTER TABLE tasks ADD COLUMN archived_at TEXT"
                        )
                    if "archive_reason" not in columns:
                        self.connection.execute(
                            "ALTER TABLE tasks ADD COLUMN archive_reason TEXT"
                        )
                    self.connection.execute("PRAGMA user_version = 5")
                current_version = 5

            if current_version == 5:
                with self.connection:
                    document_columns = {
                        row[1]
                        for row in self.connection.execute(
                            "PRAGMA table_info(policy_documents)"
                        )
                    }
                    additions = {
                        "text_source_type": "TEXT NOT NULL DEFAULT ''",
                        "extraction_method": "TEXT NOT NULL DEFAULT ''",
                        "converter_name": "TEXT NOT NULL DEFAULT ''",
                        "converter_version": "TEXT NOT NULL DEFAULT ''",
                        "body_completeness": (
                            "TEXT NOT NULL DEFAULT 'unknown'"
                        ),
                        "review_status": (
                            "TEXT NOT NULL DEFAULT 'pending_review'"
                        ),
                    }
                    for name, definition in additions.items():
                        if name not in document_columns:
                            self.connection.execute(
                                f"ALTER TABLE policy_documents "
                                f"ADD COLUMN {name} {definition}"
                            )
                    self.connection.execute("PRAGMA user_version = 6")

    def create_task(
        self,
        name: str,
        mode: str,
        task_input: dict[str, Any],
        output_dir: str = "",
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValidationError("TASK_NAME_REQUIRED", "task name is required")
        if len(clean_name) > 120:
            raise ValidationError("TASK_NAME_TOO_LONG", "task name must not exceed 120 characters")
        if mode not in TASK_MODES:
            raise ValidationError("INVALID_TASK_MODE", "mode must be single or batch")
        if not isinstance(task_input, dict):
            raise ValidationError("INVALID_TASK_INPUT", "input must be an object")

        normalized_input = normalize_task_input_metadata(task_input)
        task_id = str(uuid.uuid4())
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tasks (
                    id, name, mode, status, input_json, output_dir,
                    progress, current_stage, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    clean_name,
                    mode,
                    "pending",
                    json.dumps(normalized_input, ensure_ascii=False),
                    str(output_dir or ""),
                    0.0,
                    "pending",
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, "task.created", json.dumps({"mode": mode}, ensure_ascii=False), now),
            )
        task = self.get_task(task_id)
        if task is None:
            raise RuntimeError("created task could not be read back")
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(
        self,
        limit: int = 50,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        where = "" if include_archived else "WHERE archived_at IS NULL"
        rows = self.connection.execute(
            f"SELECT * FROM tasks {where} ORDER BY updated_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_task_events(
        self,
        task_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if self.get_task(task_id) is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        safe_limit = max(1, min(int(limit), 500))
        rows = self.connection.execute(
            """
            SELECT *
            FROM (
                SELECT *
                FROM task_events
                WHERE task_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, id ASC
            """,
            (task_id, safe_limit),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "taskId": row["task_id"],
                "event": row["event_type"],
                "data": json.loads(row["payload_json"] or "{}"),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _task_event_identity(payload: dict[str, Any]) -> str:
        snapshot = payload.get("progressSnapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        error = payload.get("error")
        error = error if isinstance(error, dict) else {}
        identity = {
            "status": payload.get("status"),
            "currentStage": payload.get("currentStage"),
            "currentAction": payload.get("currentAction"),
            "errorCode": error.get("code"),
            "pauseKind": payload.get("pauseKind"),
            "progress": str(payload.get("progress") or ""),
            "overall": str(snapshot.get("overall") or ""),
            "completedUnits": str(snapshot.get("completedUnits") or 0),
            "totalUnits": str(snapshot.get("totalUnits") or 0),
            "succeeded": str(snapshot.get("succeeded") or 0),
            "failed": str(snapshot.get("failed") or 0),
            "pendingReview": str(snapshot.get("pendingReview") or 0),
        }
        return json.dumps(identity, ensure_ascii=False, sort_keys=True)

    def _insert_effective_task_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> bool:
        previous = self.connection.execute(
            """
            SELECT payload_json, created_at
            FROM task_events
            WHERE task_id = ? AND event_type = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (task_id, event_type),
        ).fetchone()
        if previous is not None:
            try:
                previous_payload = json.loads(previous["payload_json"] or "{}")
            except (TypeError, ValueError):
                previous_payload = {}
            previous_time = _parse_timestamp(str(previous["created_at"] or ""))
            current_time = _parse_timestamp(created_at)
            if (
                isinstance(previous_payload, dict)
                and previous_time is not None
                and current_time is not None
                and 0
                <= (current_time - previous_time).total_seconds()
                <= 2.0
                and self._task_event_identity(
                    previous_payload
                )
                == self._task_event_identity(payload)
            ):
                return False
        self.connection.execute(
            """
            INSERT INTO task_events (task_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                task_id,
                event_type,
                json.dumps(payload, ensure_ascii=False),
                created_at,
            ),
        )
        return True

    def archive_tasks(
        self,
        task_ids: list[str],
        *,
        reason: str = "",
    ) -> list[dict[str, Any]]:
        ids = self._normalize_task_ids(task_ids)
        tasks = self._load_management_tasks(ids)
        self._ensure_tasks_not_active(tasks, ids)
        now = utc_now()
        placeholders = ", ".join("?" for _ in ids)
        with self.connection:
            self.connection.execute(
                f"""
                UPDATE tasks
                SET archived_at = COALESCE(archived_at, ?),
                    archive_reason = COALESCE(NULLIF(archive_reason, ''), ?),
                    updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (now, str(reason or ""), now, *ids),
            )
            for task_id in ids:
                self.connection.execute(
                    """
                    INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        "task.archived",
                        json.dumps({"reason": str(reason or "")}, ensure_ascii=False),
                        now,
                    ),
                )
        return [self.get_task(task_id) for task_id in ids if self.get_task(task_id)]

    def restore_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        ids = self._normalize_task_ids(task_ids)
        self._load_management_tasks(ids)
        now = utc_now()
        placeholders = ", ".join("?" for _ in ids)
        with self.connection:
            self.connection.execute(
                f"""
                UPDATE tasks
                SET archived_at = NULL, archive_reason = NULL, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (now, *ids),
            )
            for task_id in ids:
                self.connection.execute(
                    """
                    INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, "task.restored", "{}", now),
                )
        return [self.get_task(task_id) for task_id in ids if self.get_task(task_id)]

    def cleanup_preview(self, task_ids: list[str]) -> dict[str, Any]:
        ids = self._normalize_task_ids(task_ids)
        selected_tasks = self._load_management_tasks(ids)
        self._ensure_tasks_not_active(selected_tasks, ids)
        all_tasks = self.connection.execute(
            "SELECT id, name, output_dir, archived_at FROM tasks"
        ).fetchall()
        refs_by_task: dict[str, dict[str, dict[str, Any]]] = {}
        for row in all_tasks:
            refs_by_task[str(row["id"])] = self._registered_file_refs(str(row["id"]))

        global_owners: dict[str, set[str]] = {}
        for owner_id, refs in refs_by_task.items():
            for normalized in refs:
                global_owners.setdefault(normalized, set()).add(owner_id)

        selected_set = set(ids)
        output_roots = [
            Path(str(task["outputDir"])).resolve(strict=False)
            for task in selected_tasks
            if str(task["outputDir"] or "").strip()
        ]
        home = Path.home().resolve(strict=False)
        database_parent = self.database_path.parent.resolve(strict=False)

        merged: dict[str, dict[str, Any]] = {}
        for task_id in ids:
            for normalized, ref in refs_by_task.get(task_id, {}).items():
                entry = merged.setdefault(
                    normalized,
                    {
                        "path": normalized,
                        "rawPath": ref["raw"],
                        "taskIds": set(),
                        "sources": set(),
                    },
                )
                entry["taskIds"].add(task_id)
                entry["sources"].update(ref["sources"])

        files: list[dict[str, Any]] = []
        protected: list[dict[str, Any]] = []
        for normalized, entry in merged.items():
            resolved = Path(normalized).resolve(strict=False)
            exists = resolved.exists()
            is_file = resolved.is_file() if exists else False
            owners = sorted(global_owners.get(normalized, set()))
            external_owners = sorted(set(owners) - selected_set)
            reason = ""
            if not Path(str(entry["rawPath"])).is_absolute():
                reason = "PATH_NOT_ABSOLUTE"
            elif self._contains_parent_segment(str(entry["rawPath"])):
                reason = "PATH_TRAVERSAL"
            elif resolved == home or resolved == Path(resolved.anchor):
                reason = "PROTECTED_ROOT"
            elif resolved == database_parent or resolved == self.database_path.resolve(strict=False):
                reason = "PROTECTED_DATA_DIRECTORY"
            elif output_roots and not any(self._path_within(resolved, root) for root in output_roots):
                reason = "OUTSIDE_TASK_OUTPUT"
            elif resolved in output_roots:
                reason = "TASK_OUTPUT_DIRECTORY"
            elif exists and not is_file:
                reason = "NOT_A_FILE"
            elif external_owners:
                reason = "SHARED_WITH_OTHER_TASK"
            item = {
                "path": normalized,
                "rawPath": entry["rawPath"],
                "taskIds": sorted(entry["taskIds"]),
                "sources": sorted(entry["sources"]),
                "exists": exists,
                "canTrash": not reason,
                "reason": reason,
            }
            files.append(item)
            if reason:
                protected.append(item)

        directories: dict[str, dict[str, Any]] = {}
        for root in output_roots:
            directories[str(root)] = {
                "path": str(root),
                "taskIds": sorted(ids),
                "reason": "TASK_OUTPUT_DIRECTORY",
                "canTrash": False,
                "shared": True,
            }
        for item in files:
            path = Path(str(item["path"]))
            if not path.exists() or not path.is_file():
                continue
            parent = path.parent
            external_owner = any(
                other_path != str(path)
                and Path(other_path).parent == parent
                for other_path, owners in global_owners.items()
                if set(owners) - selected_set
            )
            try:
                unregistered_present = any(
                    entry.is_file()
                    and str(entry.resolve(strict=False)) not in global_owners
                    for entry in parent.iterdir()
                )
            except OSError:
                unregistered_present = True
            shared = external_owner or unregistered_present
            previous = directories.get(str(parent))
            if previous is None:
                directories[str(parent)] = {
                    "path": str(parent),
                    "taskIds": sorted(item["taskIds"]),
                    "reason": "SHARED_DIRECTORY" if shared else "TASK_DIRECTORY",
                    "canTrash": False,
                    "shared": shared,
                }
            elif shared:
                previous["shared"] = True
                previous["reason"] = "SHARED_DIRECTORY"
        for root in output_roots:
            directories[str(root)]["shared"] = True
            directories[str(root)]["reason"] = "TASK_OUTPUT_DIRECTORY"

        eligible = [item for item in files if item["canTrash"]]
        return {
            "tasks": selected_tasks,
            "files": files,
            "eligibleFiles": eligible,
            "protectedFiles": protected,
            "directories": sorted(directories.values(), key=lambda item: item["path"]),
            "summary": {
                "taskCount": len(selected_tasks),
                "registeredFileCount": len(files),
                "eligibleFileCount": len(eligible),
                "existingEligibleFileCount": sum(
                    1 for item in eligible if item["exists"]
                ),
                "protectedFileCount": len(protected),
                "directoryCount": len(directories),
            },
        }

    def purge_tasks(
        self,
        task_ids: list[str],
        *,
        require_files_removed: bool = False,
    ) -> dict[str, Any]:
        ids = self._normalize_task_ids(task_ids)
        tasks = self._load_management_tasks(ids)
        self._ensure_tasks_not_active(tasks, ids)
        for task in tasks:
            if not task.get("archivedAt"):
                raise ValidationError(
                    "TASK_NOT_ARCHIVED",
                    "只能彻底删除回收站中的任务",
                )
        if require_files_removed:
            preview = self.cleanup_preview(ids)
            remaining = [
                item["path"]
                for item in preview["eligibleFiles"]
                if item["exists"]
            ]
            if remaining:
                raise ValidationError(
                    "CLEANUP_INCOMPLETE",
                    f"仍有 {len(remaining)} 个登记文件未进入回收站",
                )

        placeholders = ", ".join("?" for _ in ids)
        tables = (
            "task_items",
            "task_batches",
            "task_events",
            "policy_documents",
            "processing_runs",
            "task_artifacts",
        )
        counts: dict[str, int] = {}
        for table in tables:
            counts[table] = int(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE task_id IN ({placeholders})",
                    tuple(ids),
                ).fetchone()[0]
            )
        with self.connection:
            self.connection.execute(
                f"DELETE FROM tasks WHERE id IN ({placeholders})",
                tuple(ids),
            )
        return {
            "taskIds": ids,
            "deleted": counts,
        }

    def update_task_input(
        self,
        task_id: str,
        task_input: dict[str, Any],
        *,
        allowed_statuses: frozenset[str] = frozenset({"pending"}),
    ) -> dict[str, Any]:
        if not isinstance(task_input, dict):
            raise ValidationError("INVALID_TASK_INPUT", "task input must be an object")
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        if task["status"] not in allowed_statuses:
            raise ValidationError(
                "TASK_INPUT_NOT_EDITABLE",
                f"task input cannot be edited while status is {task['status']}",
            )
        normalized_input = normalize_task_input_metadata(task_input)
        now = utc_now()
        with self.connection:
            self.connection.execute(
                "UPDATE tasks SET input_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(normalized_input, ensure_ascii=False),
                    now,
                    task_id,
                ),
            )
        updated = self.get_task(task_id)
        if updated is None:
            raise RuntimeError("updated task could not be read back")
        return updated

    def apply_task_settings_revision(
        self,
        task_id: str,
        task_input: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        output_dir: str,
        allowed_statuses: frozenset[str],
    ) -> dict[str, Any]:
        if not isinstance(task_input, dict):
            raise ValidationError(
                "INVALID_TASK_INPUT",
                "task input must be an object",
            )
        if not isinstance(checkpoint, dict):
            raise ValidationError(
                "INVALID_TASK_CHECKPOINT",
                "task checkpoint must be an object",
            )
        clean_output = str(output_dir or "").strip()
        if not clean_output:
            raise ValidationError(
                "OUTPUT_DIR_REQUIRED",
                "任务设置必须指定输出目录",
            )
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError(
                "TASK_NOT_FOUND",
                f"task {task_id} was not found",
            )
        if task["status"] not in allowed_statuses:
            raise ValidationError(
                "TASK_SETTINGS_NOT_EDITABLE",
                f"任务状态 {task['status']} 不允许应用新设置",
            )
        normalized_input = normalize_task_input_metadata(task_input)
        now = utc_now()
        generation = int(checkpoint.get("configGeneration") or 0)
        fingerprint = str(checkpoint.get("configFingerprint") or "")
        superseded_items = [
            {
                "id": row["id"],
                "policyKey": row["policy_key"],
                "title": row["title"],
                "status": row["status"],
            }
            for row in self.connection.execute(
                """
                SELECT id, policy_key, title, status
                FROM task_items
                WHERE task_id = ? AND status <> 'completed'
                """,
                (task_id,),
            ).fetchall()
        ]
        superseded_batches = [
            {
                "id": row["id"],
                "batchKey": row["batch_key"],
                "status": row["status"],
            }
            for row in self.connection.execute(
                """
                SELECT id, batch_key, status
                FROM task_batches
                WHERE task_id = ? AND status <> 'completed'
                """,
                (task_id,),
            ).fetchall()
        ]
        with self.connection:
            self.connection.execute(
                "DELETE FROM task_batches WHERE task_id = ? AND status <> 'completed'",
                (task_id,),
            )
            self.connection.execute(
                "DELETE FROM task_items WHERE task_id = ? AND status <> 'completed'",
                (task_id,),
            )
            self.connection.execute(
                """
                UPDATE tasks
                SET input_json = ?, output_dir = ?, checkpoint_json = ?,
                    progress = 0, current_stage = 'settings-applied',
                    error_json = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(normalized_input, ensure_ascii=False),
                    clean_output,
                    json.dumps(checkpoint, ensure_ascii=False),
                    now,
                    task_id,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO task_events (
                    task_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    task_id,
                    "task.settings.applied",
                    json.dumps(
                        {
                            "generation": generation,
                            "configFingerprint": fingerprint,
                            "outputDir": clean_output,
                            "supersededItems": superseded_items,
                            "supersededBatches": superseded_batches,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        updated = self.get_task(task_id)
        if updated is None:
            raise RuntimeError("updated task could not be read back")
        return updated

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        current_stage: str | None = None,
        error: dict[str, Any] | None = None,
        clear_error: bool = False,
        checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        if status is not None and status not in TASK_STATUSES:
            raise ValidationError("INVALID_TASK_STATUS", f"unsupported task status: {status}")

        assignments: list[str] = []
        values: list[Any] = []
        now = utc_now()
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if progress is not None:
            assignments.append("progress = ?")
            values.append(max(0.0, min(float(progress), 1.0)))
        if current_stage is not None:
            assignments.append("current_stage = ?")
            values.append(str(current_stage))
        if error is not None:
            assignments.append("error_json = ?")
            values.append(json.dumps(error, ensure_ascii=False))
        elif clear_error:
            assignments.append("error_json = NULL")
        if checkpoint is not None:
            assignments.append("checkpoint_json = ?")
            values.append(json.dumps(checkpoint, ensure_ascii=False))

        if status in ACTIVE_TASK_STATUSES:
            assignments.append("started_at = COALESCE(started_at, ?)")
            values.append(now)
            assignments.append("finished_at = NULL")
        if status in {"completed", "partial", "stopped", "failed"}:
            assignments.append("finished_at = ?")
            values.append(now)
        assignments.append("updated_at = ?")
        values.append(now)
        values.append(task_id)

        with self.connection:
            self.connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            updated_row = self.connection.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if updated_row is None:
                raise RuntimeError("updated task could not be read back")
            event_task = self._task_from_row(updated_row)
            event_checkpoint = dict(event_task.get("checkpoint") or {})
            self._insert_effective_task_event(
                task_id,
                "task.updated",
                {
                    "status": event_task["status"],
                    "progress": event_task["progress"],
                    "progressSnapshot": event_task.get("progressSnapshot"),
                    "currentStage": event_task["currentStage"],
                    "error": event_task["error"],
                    "currentAction": event_checkpoint.get("currentAction"),
                    "pauseKind": event_checkpoint.get("pauseKind"),
                    "pauseReason": event_checkpoint.get("pauseReason"),
                    "searchId": event_checkpoint.get("searchId"),
                    "currentQuery": event_checkpoint.get("currentQuery"),
                    "currentLibrary": event_checkpoint.get("currentLibrary"),
                    "currentYear": event_checkpoint.get("currentYear"),
                    "currentPage": event_checkpoint.get("currentPage"),
                    "totalPages": event_checkpoint.get("totalPages"),
                    "groupingMode": event_checkpoint.get("groupingMode"),
                },
                now,
            )
        updated = self.get_task(task_id)
        if updated is None:
            raise RuntimeError("updated task could not be read back")
        return updated

    def recover_interrupted_tasks(self) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in ACTIVE_TASK_STATUSES)
        rows = self.connection.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders})",
            tuple(sorted(ACTIVE_TASK_STATUSES)),
        ).fetchall()
        recovered: list[dict[str, Any]] = []
        interrupted_at = utc_now()
        for row in rows:
            task = self._task_from_row(row)
            previous_status = str(task["status"])
            checkpoint = dict(task.get("checkpoint") or {})
            checkpoint.update(
                {
                    "interrupted": True,
                    "interruptedPreviousStatus": previous_status,
                    "interruptedAt": interrupted_at,
                }
            )
            recovered.append(
                self.update_task(
                    task["id"],
                    status="stopped",
                    current_stage="application-interrupted",
                    error={
                        "code": "APPLICATION_INTERRUPTED",
                        "message": "应用异常中断，任务已安全停止；请重新核验后重试",
                    },
                    checkpoint=checkpoint,
                )
            )
        return recovered

    def recover_interrupted_processing_runs(self) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in ACTIVE_PROCESSING_STATUSES)
        rows = self.connection.execute(
            f"SELECT * FROM processing_runs WHERE status IN ({placeholders})",
            tuple(sorted(ACTIVE_PROCESSING_STATUSES)),
        ).fetchall()
        recovered: list[dict[str, Any]] = []
        for row in rows:
            run = self._processing_run_from_row(row)
            summary = dict(run.get("summary") or {})
            summary.update(
                {
                    "interrupted": True,
                    "interruptedPreviousStatus": run["status"],
                    "interruptedAt": utc_now(),
                }
            )
            recovered.append(
                self.upsert_processing_run(
                    run["id"],
                    run["taskId"],
                    status="stopped",
                    progress=run["progress"],
                    current_stage="application-interrupted",
                    options=run["options"],
                    summary=summary,
                    error={
                        "code": "APPLICATION_INTERRUPTED",
                        "message": "应用异常中断，数据处理已安全停止",
                    },
                )
            )
        return recovered

    def upsert_task_item(
        self,
        task_id: str,
        *,
        policy_key: str,
        title: str,
        status: str = "pending",
        source_type: str = "",
        progress: float = 0.0,
        payload: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        current_stage: str = "pending",
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        clean_key = str(policy_key or "").strip()
        if not clean_key:
            raise ValidationError("POLICY_KEY_REQUIRED", "policyKey is required")
        if status not in ITEM_STATUSES:
            raise ValidationError("INVALID_ITEM_STATUS", f"unsupported item status: {status}")
        now = utc_now()
        item = self.connection.execute(
            "SELECT * FROM task_items WHERE task_id = ? AND policy_key = ?",
            (task_id, clean_key),
        ).fetchone()
        item_id = item["id"] if item else str(uuid.uuid4())
        created_at = item["created_at"] if item else now
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO task_items (
                    id, task_id, policy_key, title, status, source_type,
                    progress, current_stage, payload_json, error_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, policy_key) DO UPDATE SET
                    title = excluded.title,
                    status = excluded.status,
                    source_type = excluded.source_type,
                    progress = excluded.progress,
                    current_stage = excluded.current_stage,
                    payload_json = excluded.payload_json,
                    error_json = excluded.error_json,
                    updated_at = excluded.updated_at
                """,
                (
                    item_id,
                    task_id,
                    clean_key,
                    str(title or ""),
                    status,
                    str(source_type or ""),
                    max(0.0, min(float(progress), 1.0)),
                    str(current_stage or "pending"),
                    json.dumps(payload or {}, ensure_ascii=False),
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    created_at,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    task_id,
                    "task.item.updated",
                    json.dumps(
                        {
                            "itemId": item_id,
                            "policyKey": clean_key,
                            "status": status,
                            "currentStage": current_stage,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        stored = self.get_task_item(item_id)
        if stored is None:
            raise RuntimeError("updated task item could not be read back")
        return stored

    def get_task_item(self, item_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM task_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return self._item_from_row(row) if row else None

    def list_task_items(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM task_items WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def upsert_task_batch(
        self,
        task_id: str,
        *,
        batch_key: str,
        query: str,
        library: str,
        page: int,
        row_start: int,
        row_end: int,
        status: str = "pending",
        progress: float = 0.0,
        catalog_format: str,
        full_format: str,
        selected: list[dict[str, Any]],
        catalog_path: str = "",
        zip_path: str = "",
        report_path: str = "",
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        clean_key = str(batch_key or "").strip()
        if not clean_key:
            raise ValidationError("BATCH_KEY_REQUIRED", "batchKey is required")
        if status not in ITEM_STATUSES:
            raise ValidationError("INVALID_BATCH_STATUS", f"unsupported batch status: {status}")
        now = utc_now()
        existing = self.connection.execute(
            "SELECT * FROM task_batches WHERE task_id = ? AND batch_key = ?",
            (task_id, clean_key),
        ).fetchone()
        batch_id = existing["id"] if existing else str(uuid.uuid4())
        created_at = existing["created_at"] if existing else now
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO task_batches (
                    id, task_id, batch_key, query, library, page, row_start,
                    row_end, status, progress, catalog_format, full_format,
                    selected_json, catalog_path, zip_path, report_path,
                    error_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, batch_key) DO UPDATE SET
                    page = excluded.page,
                    row_start = excluded.row_start,
                    row_end = excluded.row_end,
                    status = excluded.status,
                    progress = excluded.progress,
                    catalog_format = excluded.catalog_format,
                    full_format = excluded.full_format,
                    selected_json = excluded.selected_json,
                    catalog_path = excluded.catalog_path,
                    zip_path = excluded.zip_path,
                    report_path = excluded.report_path,
                    error_json = excluded.error_json,
                    updated_at = excluded.updated_at
                """,
                (
                    batch_id,
                    task_id,
                    clean_key,
                    str(query or ""),
                    str(library or ""),
                    int(page),
                    int(row_start),
                    int(row_end),
                    status,
                    max(0.0, min(float(progress), 1.0)),
                    str(catalog_format),
                    str(full_format),
                    json.dumps(selected, ensure_ascii=False),
                    str(catalog_path or ""),
                    str(zip_path or ""),
                    str(report_path or ""),
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    created_at,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    task_id,
                    "task.batch.updated",
                    json.dumps(
                        {
                            "batchId": batch_id,
                            "batchKey": clean_key,
                            "status": status,
                            "progress": progress,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        stored = self.get_task_batch(batch_id)
        if stored is None:
            raise RuntimeError("updated task batch could not be read back")
        return stored

    def complete_task_batch_atomic(
        self,
        task_id: str,
        *,
        batch_key: str,
        query: str,
        library: str,
        page: int,
        row_start: int,
        row_end: int,
        catalog_format: str,
        full_format: str,
        selected: list[dict[str, Any]],
        items: list[dict[str, Any]],
        catalog_path: str,
        zip_path: str,
        report_path: str,
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        now = utc_now()
        with self.connection:
            for item in items:
                policy_key = str(item.get("policyKey") or "").strip()
                if not policy_key:
                    raise ValidationError("POLICY_KEY_REQUIRED", "policyKey is required")
                existing_item = self.connection.execute(
                    "SELECT id, created_at FROM task_items WHERE task_id = ? AND policy_key = ?",
                    (task_id, policy_key),
                ).fetchone()
                item_id = existing_item["id"] if existing_item else str(uuid.uuid4())
                item_created_at = existing_item["created_at"] if existing_item else now
                self.connection.execute(
                    """
                    INSERT INTO task_items (
                        id, task_id, policy_key, title, status, source_type,
                        progress, current_stage, payload_json, error_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'completed', ?, 1.0, 'completed', ?, NULL, ?, ?)
                    ON CONFLICT(task_id, policy_key) DO UPDATE SET
                        title = excluded.title,
                        status = 'completed',
                        source_type = excluded.source_type,
                        progress = 1.0,
                        current_stage = 'completed',
                        payload_json = excluded.payload_json,
                        error_json = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item_id,
                        task_id,
                        policy_key,
                        str(item.get("title") or ""),
                        str(item.get("sourceType") or "北大法宝ZIP"),
                        json.dumps(item.get("payload") or {}, ensure_ascii=False),
                        item_created_at,
                        now,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                    VALUES (?, 'task.item.completed', ?, ?)
                    """,
                    (
                        task_id,
                        json.dumps(
                            {
                                "itemId": item_id,
                                "policyKey": policy_key,
                                "status": "completed",
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )

            existing_batch = self.connection.execute(
                "SELECT id, created_at FROM task_batches WHERE task_id = ? AND batch_key = ?",
                (task_id, batch_key),
            ).fetchone()
            batch_id = existing_batch["id"] if existing_batch else str(uuid.uuid4())
            batch_created_at = existing_batch["created_at"] if existing_batch else now
            self.connection.execute(
                """
                INSERT INTO task_batches (
                    id, task_id, batch_key, query, library, page, row_start,
                    row_end, status, progress, catalog_format, full_format,
                    selected_json, catalog_path, zip_path, report_path,
                    error_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', 1.0, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(task_id, batch_key) DO UPDATE SET
                    page = excluded.page,
                    row_start = excluded.row_start,
                    row_end = excluded.row_end,
                    status = 'completed',
                    progress = 1.0,
                    catalog_format = excluded.catalog_format,
                    full_format = excluded.full_format,
                    selected_json = excluded.selected_json,
                    catalog_path = excluded.catalog_path,
                    zip_path = excluded.zip_path,
                    report_path = excluded.report_path,
                    error_json = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    batch_id,
                    task_id,
                    batch_key,
                    str(query or ""),
                    str(library or ""),
                    int(page),
                    int(row_start),
                    int(row_end),
                    str(catalog_format),
                    str(full_format),
                    json.dumps(selected, ensure_ascii=False),
                    str(catalog_path),
                    str(zip_path),
                    str(report_path),
                    batch_created_at,
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                VALUES (?, 'task.batch.completed', ?, ?)
                """,
                (
                    task_id,
                    json.dumps(
                        {
                            "batchId": batch_id,
                            "batchKey": batch_key,
                            "status": "completed",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        return next(
            batch
            for batch in self.list_task_batches(task_id)
            if batch["batchKey"] == batch_key
        )

    def get_task_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM task_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        return self._batch_from_row(row) if row else None

    def list_task_batches(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM task_batches
            WHERE task_id = ?
            ORDER BY page ASC, row_start ASC, created_at ASC
            """,
            (task_id,),
        ).fetchall()
        return [self._batch_from_row(row) for row in rows]

    def reset_task_batches_for_retry(self, task_id: str) -> int:
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE task_batches
                SET status = 'pending', progress = 0, catalog_path = '',
                    zip_path = '', report_path = '', error_json = NULL,
                    updated_at = ?
                WHERE task_id = ? AND status = 'failed'
                """,
                (now, task_id),
            )
            if cursor.rowcount:
                self.connection.execute(
                    """
                    INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        "task.batches.retry",
                        json.dumps({"count": cursor.rowcount}, ensure_ascii=False),
                        now,
                    ),
                )
        return cursor.rowcount

    def clear_policy_documents(self, task_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM policy_documents WHERE task_id = ?",
                (task_id,),
            )

    def upsert_policy_document(
        self,
        task_id: str,
        *,
        policy_key: str,
        policy_id: str = "",
        title: str = "",
        document_number: str = "",
        issuing_authority: str = "",
        publication_date: str = "",
        effective_date: str = "",
        validity: str = "",
        effect_level: str = "",
        source_type: str = "",
        source_url: str = "",
        retrieved_at: str = "",
        retrieval_method: str = "",
        word_path: str = "",
        txt_path: str = "",
        evidence_path: str = "",
        sha256: str = "",
        text_source_type: str = "",
        extraction_method: str = "",
        converter_name: str = "",
        converter_version: str = "",
        body_completeness: str = "unknown",
        review_status: str = "pending_review",
        status: str = "pending",
        error: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.get_task(task_id) is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        clean_key = str(policy_key or "").strip()
        if not clean_key:
            raise ValidationError("POLICY_KEY_REQUIRED", "policyKey is required")
        body_state = str(body_completeness or "unknown")
        if body_state not in BODY_COMPLETENESS_VALUES:
            raise ValidationError(
                "INVALID_BODY_COMPLETENESS",
                f"unsupported body completeness: {body_state}",
            )
        review_state = str(review_status or "pending_review")
        if review_state not in REVIEW_STATUS_VALUES:
            raise ValidationError(
                "INVALID_REVIEW_STATUS",
                f"unsupported review status: {review_state}",
            )
        now = utc_now()
        existing = self.connection.execute(
            "SELECT * FROM policy_documents WHERE task_id = ? AND policy_key = ?",
            (task_id, clean_key),
        ).fetchone()
        document_id = existing["id"] if existing else str(uuid.uuid4())
        created_at = existing["created_at"] if existing else now
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO policy_documents (
                    id, task_id, policy_key, policy_id, title, document_number,
                    issuing_authority, publication_date, effective_date, validity,
                    effect_level, source_type, source_url, retrieved_at,
                    retrieval_method, word_path, txt_path, evidence_path,
                    sha256, text_source_type, extraction_method, converter_name,
                    converter_version, body_completeness, review_status,
                    status, error_json, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, policy_key) DO UPDATE SET
                    policy_id = excluded.policy_id,
                    title = excluded.title,
                    document_number = excluded.document_number,
                    issuing_authority = excluded.issuing_authority,
                    publication_date = excluded.publication_date,
                    effective_date = excluded.effective_date,
                    validity = excluded.validity,
                    effect_level = excluded.effect_level,
                    source_type = excluded.source_type,
                    source_url = excluded.source_url,
                    retrieved_at = excluded.retrieved_at,
                    retrieval_method = excluded.retrieval_method,
                    word_path = excluded.word_path,
                    txt_path = excluded.txt_path,
                    evidence_path = excluded.evidence_path,
                    sha256 = excluded.sha256,
                    text_source_type = excluded.text_source_type,
                    extraction_method = excluded.extraction_method,
                    converter_name = excluded.converter_name,
                    converter_version = excluded.converter_version,
                    body_completeness = excluded.body_completeness,
                    review_status = excluded.review_status,
                    status = excluded.status,
                    error_json = excluded.error_json,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    document_id,
                    task_id,
                    clean_key,
                    str(policy_id or ""),
                    str(title or ""),
                    str(document_number or ""),
                    str(issuing_authority or ""),
                    str(publication_date or ""),
                    str(effective_date or ""),
                    str(validity or ""),
                    str(effect_level or ""),
                    str(source_type or ""),
                    str(source_url or ""),
                    str(retrieved_at or ""),
                    str(retrieval_method or ""),
                    str(word_path or ""),
                    str(txt_path or ""),
                    str(evidence_path or ""),
                    str(sha256 or ""),
                    str(text_source_type or ""),
                    str(extraction_method or ""),
                    str(converter_name or ""),
                    str(converter_version or ""),
                    body_state,
                    review_state,
                    str(status or "pending"),
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    json.dumps(payload or {}, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
        stored = self.get_policy_document(document_id)
        if stored is None:
            raise RuntimeError("policy document could not be read back")
        return stored

    def get_policy_document(self, document_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM policy_documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return self._policy_document_from_row(row) if row else None

    def list_policy_documents(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM policy_documents
            WHERE task_id = ?
            ORDER BY publication_date DESC, title ASC
            """,
            (task_id,),
        ).fetchall()
        return [self._policy_document_from_row(row) for row in rows]

    def upsert_processing_run(
        self,
        processing_id: str,
        task_id: str,
        *,
        status: str,
        progress: float,
        current_stage: str,
        options: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.get_task(task_id) is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        now = utc_now()
        existing = self.connection.execute(
            "SELECT * FROM processing_runs WHERE id = ?",
            (processing_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO processing_runs (
                    id, task_id, status, progress, current_stage, options_json,
                    summary_json, error_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    progress = excluded.progress,
                    current_stage = excluded.current_stage,
                    options_json = excluded.options_json,
                    summary_json = excluded.summary_json,
                    error_json = excluded.error_json,
                    updated_at = excluded.updated_at
                """,
                (
                    processing_id,
                    task_id,
                    status,
                    max(0.0, min(float(progress), 1.0)),
                    str(current_stage or ""),
                    json.dumps(options or {}, ensure_ascii=False),
                    json.dumps(summary or {}, ensure_ascii=False),
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    created_at,
                    now,
                ),
            )
            event_payload = {
                "status": status,
                "progress": max(0.0, min(float(progress), 1.0)),
                "progressSnapshot": (summary or {}).get("progressSnapshot"),
                "currentStage": str(current_stage or ""),
                "currentAction": (summary or {}).get("currentAction"),
                "error": error,
                "pauseKind": (summary or {}).get("pauseKind"),
                "pauseReason": (summary or {}).get("pauseReason"),
            }
            self._insert_effective_task_event(
                task_id,
                "data.run.updated",
                event_payload,
                now,
            )
        stored = self.get_processing_run(processing_id)
        if stored is None:
            raise RuntimeError("processing run could not be read back")
        return stored

    def get_processing_run(self, processing_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM processing_runs WHERE id = ?",
            (processing_id,),
        ).fetchone()
        return self._processing_run_from_row(row) if row else None

    def latest_processing_run(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM processing_runs
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return self._processing_run_from_row(row) if row else None

    def transition_processing_run(
        self,
        processing_id: str,
        task_id: str,
        *,
        status: str,
        current_stage: str,
        summary: dict[str, Any] | None = None,
        summary_updates: dict[str, Any] | None = None,
        clear_error: bool = False,
    ) -> dict[str, Any]:
        """Atomically transition a run without replacing newer worker progress."""
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM processing_runs WHERE id = ?",
                (processing_id,),
            ).fetchone()
            if row is None or row["task_id"] != task_id:
                raise ValidationError(
                    "PROCESSING_NOT_FOUND",
                    "数据处理运行不存在",
                )
            latest_summary = json.loads(row["summary_json"] or "{}")
            if not isinstance(latest_summary, dict):
                latest_summary = {}
            effective_summary = (
                dict(summary)
                if summary is not None
                else latest_summary
            )
            effective_summary.update(summary_updates or {})
            error_json = None if clear_error else row["error_json"]
            self.connection.execute(
                """
                UPDATE processing_runs
                SET status = ?, current_stage = ?, summary_json = ?,
                    error_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    str(current_stage or ""),
                    json.dumps(effective_summary, ensure_ascii=False),
                    error_json,
                    now,
                    processing_id,
                ),
            )
            self._insert_effective_task_event(
                task_id,
                "data.run.updated",
                {
                    "status": status,
                    "progress": row["progress"],
                    "progressSnapshot": effective_summary.get("progressSnapshot"),
                    "currentStage": str(current_stage or ""),
                    "currentAction": effective_summary.get("currentAction"),
                    "error": (
                        json.loads(error_json)
                        if error_json
                        else None
                    ),
                    "pauseKind": effective_summary.get("pauseKind"),
                    "pauseReason": effective_summary.get("pauseReason"),
                },
                now,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        stored = self.get_processing_run(processing_id)
        if stored is None:
            raise RuntimeError("processing run could not be read back")
        return stored

    def add_task_artifact(
        self,
        task_id: str,
        processing_id: str,
        *,
        kind: str,
        path: str,
        sha256: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        artifact_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO task_artifacts (
                    id, task_id, processing_id, kind, path, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, task_id, processing_id, kind, path, sha256, now),
            )
        return {
            "id": artifact_id,
            "taskId": task_id,
            "processingId": processing_id,
            "kind": kind,
            "path": path,
            "sha256": sha256,
            "createdAt": now,
        }

    def replace_task_artifacts_atomic(
        self,
        task_id: str,
        processing_id: str,
        artifacts: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        if self.get_task(task_id) is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        if self.get_processing_run(processing_id) is None:
            raise ValidationError(
                "PROCESSING_NOT_FOUND",
                "数据处理运行不存在",
            )
        now = utc_now()
        stored: list[dict[str, Any]] = []
        with self.connection:
            self.connection.execute(
                "DELETE FROM task_artifacts WHERE processing_id = ?",
                (processing_id,),
            )
            for artifact in artifacts:
                artifact_id = str(uuid.uuid4())
                self.connection.execute(
                    """
                    INSERT INTO task_artifacts (
                        id, task_id, processing_id, kind, path, sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        task_id,
                        processing_id,
                        str(artifact.get("kind") or ""),
                        str(artifact.get("path") or ""),
                        str(artifact.get("sha256") or ""),
                        now,
                    ),
                )
                stored.append(
                    {
                        "id": artifact_id,
                        "taskId": task_id,
                        "processingId": processing_id,
                        "kind": str(artifact.get("kind") or ""),
                        "path": str(artifact.get("path") or ""),
                        "sha256": str(artifact.get("sha256") or ""),
                        "createdAt": now,
                    }
                )
        return stored

    def list_task_artifacts(self, processing_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM task_artifacts
            WHERE processing_id = ?
            ORDER BY created_at ASC
            """,
            (processing_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "taskId": row["task_id"],
                "processingId": row["processing_id"],
                "kind": row["kind"],
                "path": row["path"],
                "sha256": row["sha256"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def resolve_managed_file(self, path_value: str) -> Path | None:
        requested = os.path.normcase(str(Path(path_value).resolve(strict=False)))
        document_rows = self.connection.execute(
            """
            SELECT word_path, txt_path, evidence_path
            FROM policy_documents
            """
        ).fetchall()
        for row in document_rows:
            for value in (row["word_path"], row["txt_path"], row["evidence_path"]):
                if not value:
                    continue
                candidate = Path(value).resolve(strict=False)
                if os.path.normcase(str(candidate)) == requested and candidate.is_file():
                    return candidate
        artifact_rows = self.connection.execute(
            "SELECT path FROM task_artifacts"
        ).fetchall()
        for row in artifact_rows:
            candidate = Path(row["path"]).resolve(strict=False)
            if os.path.normcase(str(candidate)) == requested and candidate.is_file():
                return candidate
        return None

    def resolve_task_output(self, task_id: str) -> Path | None:
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("TASK_NOT_FOUND", f"task {task_id} was not found")
        output_dir = Path(str(task.get("outputDir") or "")).resolve(strict=False)
        if not str(task.get("outputDir") or "") or not output_dir.is_dir():
            raise ValidationError("TASK_OUTPUT_MISSING", "任务输出目录不存在")
        return output_dir

    def reset_task_items_for_retry(self, task_id: str) -> int:
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE task_items
                SET status = 'pending', current_stage = 'pending', progress = 0,
                    error_json = NULL, updated_at = ?
                WHERE task_id = ? AND status = 'failed'
                """,
                (now, task_id),
            )
            if cursor.rowcount:
                self.connection.execute(
                    """
                    INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        "task.items.retry",
                        json.dumps({"count": cursor.rowcount}, ensure_ascii=False),
                        now,
                    ),
                )
        return cursor.rowcount

    @staticmethod
    def _normalize_task_ids(task_ids: list[str]) -> list[str]:
        if not isinstance(task_ids, list):
            raise ValidationError("INVALID_TASK_IDS", "taskIds must be a list")
        normalized: list[str] = []
        for value in task_ids:
            task_id = str(value or "").strip()
            if not task_id:
                raise ValidationError("TASK_ID_REQUIRED", "taskIds contains an empty id")
            if task_id not in normalized:
                normalized.append(task_id)
        if not normalized:
            raise ValidationError("TASK_IDS_REQUIRED", "至少选择一个任务")
        if len(normalized) > 200:
            raise ValidationError("TOO_MANY_TASKS", "单次最多处理 200 个任务")
        return normalized

    def _load_management_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in task_ids)
        rows = self.connection.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall()
        found = {str(row["id"]): self._task_from_row(row) for row in rows}
        missing = [task_id for task_id in task_ids if task_id not in found]
        if missing:
            raise ValidationError(
                "TASK_NOT_FOUND",
                f"任务不存在：{', '.join(missing)}",
            )
        return [found[task_id] for task_id in task_ids]

    def _ensure_tasks_not_active(
        self,
        tasks: list[dict[str, Any]],
        task_ids: list[str],
    ) -> None:
        active = [
            task["id"]
            for task in tasks
            if task.get("status") in ACTIVE_TASK_STATUSES
        ]
        if active:
            raise ValidationError(
                "TASK_ACTIVE",
                f"活动任务必须先停止：{', '.join(active)}",
            )
        placeholders = ", ".join("?" for _ in task_ids)
        processing_active = self.connection.execute(
            f"""
            SELECT DISTINCT task_id FROM processing_runs
            WHERE task_id IN ({placeholders})
              AND status IN ({", ".join("?" for _ in ACTIVE_PROCESSING_STATUSES)})
            """,
            (*task_ids, *sorted(ACTIVE_PROCESSING_STATUSES)),
        ).fetchall()
        if processing_active:
            ids = [str(row["task_id"]) for row in processing_active]
            raise ValidationError(
                "PROCESSING_ACTIVE",
                f"数据处理仍在运行，必须先停止：{', '.join(ids)}",
            )

    def _registered_file_refs(self, task_id: str) -> dict[str, dict[str, Any]]:
        refs: dict[str, dict[str, Any]] = {}

        def add(value: Any, source: str) -> None:
            raw = str(value or "").strip()
            if not raw:
                return
            try:
                normalized = str(Path(raw).resolve(strict=False))
            except (OSError, RuntimeError, ValueError):
                return
            entry = refs.setdefault(normalized, {"raw": raw, "sources": set()})
            entry["sources"].add(source)

        payload_keys = {
            "downloadedFile",
            "catalogFile",
            "zipFile",
            "reportFile",
            "officialHtml",
            "officialScreenshot",
        }

        def visit(value: Any, source: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    child_source = f"{source}.{key}"
                    if key in payload_keys:
                        add(child, f"payload:{source}.{key}")
                    visit(child, child_source)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{source}[{index}]")

        item_rows = self.connection.execute(
            "SELECT payload_json FROM task_items WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for row in item_rows:
            try:
                visit(json.loads(row["payload_json"] or "{}"), "taskItem")
            except (TypeError, ValueError):
                continue

        batch_rows = self.connection.execute(
            """
            SELECT catalog_path, zip_path, report_path
            FROM task_batches WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()
        for row in batch_rows:
            for key, value in (
                ("catalogPath", row["catalog_path"]),
                ("zipPath", row["zip_path"]),
                ("reportPath", row["report_path"]),
            ):
                add(value, f"batch:{key}")

        document_rows = self.connection.execute(
            """
            SELECT word_path, txt_path, evidence_path, payload_json
            FROM policy_documents WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()
        for row in document_rows:
            add(row["word_path"], "policy:wordPath")
            add(row["txt_path"], "policy:txtPath")
            add(row["evidence_path"], "policy:evidencePath")
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            for key in ("officialHtml", "officialScreenshot"):
                add(payload.get(key), f"policy:{key}")

        artifact_rows = self.connection.execute(
            "SELECT path FROM task_artifacts WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        for row in artifact_rows:
            add(row["path"], "artifact")
        return refs

    @staticmethod
    def _contains_parent_segment(path_value: str) -> bool:
        return any(part == ".." for part in Path(path_value).parts)

    @staticmethod
    def _path_within(path_value: Path, root: Path) -> bool:
        try:
            path_value.relative_to(root)
            return True
        except ValueError:
            try:
                return os.path.normcase(os.path.commonpath((str(path_value), str(root)))) == os.path.normcase(str(root))
            except ValueError:
                return False

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> dict[str, Any]:
        checkpoint = json.loads(row["checkpoint_json"] or "{}")
        progress_snapshot = (
            checkpoint.get("progressSnapshot")
            if isinstance(checkpoint, dict)
            else None
        )
        return {
            "id": row["id"],
            "name": row["name"],
            "mode": row["mode"],
            "status": row["status"],
            "input": json.loads(row["input_json"] or "{}"),
            "outputDir": row["output_dir"],
            "progress": row["progress"],
            "currentStage": row["current_stage"],
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "progressSnapshot": progress_snapshot,
            "checkpoint": checkpoint,
            "archivedAt": row["archived_at"],
            "archiveReason": row["archive_reason"] or "",
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "policyKey": row["policy_key"],
            "title": row["title"],
            "status": row["status"],
            "sourceType": row["source_type"],
            "progress": row["progress"],
            "currentStage": row["current_stage"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _batch_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "batchKey": row["batch_key"],
            "query": row["query"],
            "library": row["library"],
            "page": row["page"],
            "rowStart": row["row_start"],
            "rowEnd": row["row_end"],
            "status": row["status"],
            "progress": row["progress"],
            "catalogFormat": row["catalog_format"],
            "fullFormat": row["full_format"],
            "selected": json.loads(row["selected_json"] or "[]"),
            "catalogPath": row["catalog_path"],
            "zipPath": row["zip_path"],
            "reportPath": row["report_path"],
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _policy_document_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "policyKey": row["policy_key"],
            "policyId": row["policy_id"],
            "title": row["title"],
            "documentNumber": row["document_number"],
            "issuingAuthority": row["issuing_authority"],
            "publicationDate": row["publication_date"],
            "effectiveDate": row["effective_date"],
            "validity": row["validity"],
            "effectLevel": row["effect_level"],
            "sourceType": row["source_type"],
            "sourceUrl": row["source_url"],
            "retrievedAt": row["retrieved_at"],
            "retrievalMethod": row["retrieval_method"],
            "wordPath": row["word_path"],
            "txtPath": row["txt_path"],
            "evidencePath": row["evidence_path"],
            "sha256": row["sha256"],
            "textSourceType": row["text_source_type"],
            "extractionMethod": row["extraction_method"],
            "converterName": row["converter_name"],
            "converterVersion": row["converter_version"],
            "bodyCompleteness": row["body_completeness"],
            "reviewStatus": row["review_status"],
            "status": row["status"],
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "payload": json.loads(row["payload_json"] or "{}"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _processing_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
        summary = json.loads(row["summary_json"] or "{}")
        progress_snapshot = (
            summary.get("progressSnapshot")
            if isinstance(summary, dict)
            else None
        )
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "status": row["status"],
            "progress": row["progress"],
            "currentStage": row["current_stage"],
            "options": json.loads(row["options_json"] or "{}"),
            "summary": summary,
            "progressSnapshot": progress_snapshot,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
