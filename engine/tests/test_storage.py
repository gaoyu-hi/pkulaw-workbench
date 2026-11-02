from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from pkulaw_engine.storage import TaskStore, ValidationError


class TaskStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tempdir.name) / "pkulaw.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_create_get_and_list_task(self) -> None:
        task = self.store.create_task(
            name="测试逐篇任务",
            mode="single",
            task_input={"keywords": ["绿色矿山"]},
            output_dir="D:/example-output",
        )

        self.assertEqual(task["name"], "测试逐篇任务")
        self.assertEqual(task["mode"], "single")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["input"]["keywords"], ["绿色矿山"])
        self.assertEqual(self.store.get_task(task["id"]), task)
        self.assertEqual([item["id"] for item in self.store.list_tasks()], [task["id"]])

    def test_reject_invalid_mode_and_input(self) -> None:
        with self.assertRaises(ValidationError) as mode_context:
            self.store.create_task("坏任务", "unknown", {})
        self.assertEqual(mode_context.exception.code, "INVALID_TASK_MODE")

        with self.assertRaises(ValidationError) as input_context:
            self.store.create_task("坏任务", "single", [])
        self.assertEqual(input_context.exception.code, "INVALID_TASK_INPUT")

    def test_schema_version_is_initialized(self) -> None:
        version = self.store.connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 6)
        columns = {
            row[1]
            for row in self.store.connection.execute(
                "PRAGMA table_info(policy_documents)"
            )
        }
        self.assertTrue(
            {
                "text_source_type",
                "extraction_method",
                "converter_name",
                "converter_version",
                "body_completeness",
                "review_status",
            }.issubset(columns)
        )
        task = self.store.create_task(
            name="v6 默认值",
            mode="single",
            task_input={},
            output_dir="D:/v6-default",
        )
        document = self.store.upsert_policy_document(
            task["id"],
            policy_key="id:v6-default",
            title="默认来源状态",
        )
        self.assertEqual(document["bodyCompleteness"], "unknown")
        self.assertEqual(document["reviewStatus"], "pending_review")

    def test_task_items_and_checkpoint_update(self) -> None:
        task = self.store.create_task(
            name="逐条状态测试",
            mode="single",
            task_input={"queries": ["测试政策"]},
            output_dir="D:/single-test",
        )
        updated = self.store.update_task(
            task["id"],
            status="waiting_manual",
            progress=0.4,
            current_stage="candidate-selection",
            checkpoint={"page": 2, "item": 3},
        )
        self.assertEqual(updated["status"], "waiting_manual")
        self.assertEqual(updated["checkpoint"], {"page": 2, "item": 3})
        self.assertIsNotNone(updated["startedAt"])

        item = self.store.upsert_task_item(
            task["id"],
            policy_key="id:test-001",
            title="测试政策",
            status="waiting_manual",
            source_type="北大法宝原件",
            progress=0.25,
            payload={"candidates": [{"policyId": "test-001", "title": "测试政策"}]},
            current_stage="candidate-selection",
        )
        self.assertEqual(item["policyKey"], "id:test-001")
        self.assertEqual(item["payload"]["candidates"][0]["policyId"], "test-001")
        self.assertEqual(self.store.list_task_items(task["id"])[0]["id"], item["id"])

        completed = self.store.upsert_task_item(
            task["id"],
            policy_key="id:test-001",
            title="测试政策",
            status="completed",
            source_type="北大法宝原件",
            progress=1.0,
            payload={"downloadedFile": "D:/single-test/file.docx"},
            current_stage="completed",
        )
        self.assertEqual(completed["id"], item["id"])
        self.assertEqual(completed["status"], "completed")

    def test_task_timeline_persists_effective_state(self) -> None:
        task = self.store.create_task(
            name="时间线任务",
            mode="single",
            task_input={"queries": ["测试"]},
            output_dir="D:/timeline-test",
        )
        self.store.update_task(
            task["id"],
            status="waiting_login",
            current_stage="waiting-login",
            checkpoint={
                "currentAction": "preflight",
                "pauseKind": "automatic",
                "pauseReason": "请完成登录",
            },
        )
        self.store.update_task(
            task["id"],
            status="running",
            current_stage="searching",
            checkpoint={
                "currentAction": "search",
                "searchId": "search-1",
                "currentQuery": "测试",
                "currentLibrary": "central",
                "currentYear": "全部年份",
                "currentPage": 1,
                "totalPages": 2,
                "groupingMode": "ungrouped",
            },
        )

        timeline = self.store.list_task_events(task["id"])

        self.assertEqual(timeline[0]["event"], "task.created")
        effective = [
            item for item in timeline if item["event"] == "task.updated"
        ]
        self.assertEqual(len(effective), 2)
        self.assertEqual(effective[0]["data"]["status"], "waiting_login")
        self.assertEqual(
            effective[0]["data"]["currentAction"],
            "preflight",
        )
        self.assertEqual(effective[1]["data"]["status"], "running")
        self.assertEqual(effective[1]["data"]["searchId"], "search-1")
        self.assertEqual(effective[1]["data"]["currentPage"], 1)
        self.assertEqual(effective[1]["data"]["totalPages"], 2)
        self.assertEqual(
            self.store.list_task_events(task["id"], limit=1)[-1]["id"],
            timeline[-1]["id"],
        )

    def test_progress_snapshot_round_trips_through_task_checkpoint(self) -> None:
        task = self.store.create_task(
            name="进度快照",
            mode="single",
            task_input={"queries": ["测试"]},
            output_dir="D:/progress-test",
        )
        snapshot = {
            "overall": 0.55,
            "stage": "download",
            "completedUnits": 1,
            "totalUnits": 3,
            "succeeded": 1,
            "failed": 0,
            "pendingReview": 0,
        }

        updated = self.store.update_task(
            task["id"],
            status="running",
            progress=0.55,
            current_stage="download-1-3",
            checkpoint={
                "currentAction": "word-download",
                "progressSnapshot": snapshot,
            },
        )

        self.assertEqual(updated["progressSnapshot"], snapshot)
        self.assertEqual(
            self.store.get_task(task["id"])["progressSnapshot"],
            snapshot,
        )

    def test_identical_task_progress_events_are_deduplicated(self) -> None:
        task = self.store.create_task(
            name="事件去重",
            mode="batch",
            task_input={"queries": ["测试"]},
            output_dir="D:/event-test",
        )
        snapshot = {
            "overall": 0.35,
            "stage": "catalog",
            "completedUnits": 1,
            "totalUnits": 4,
            "succeeded": 1,
            "failed": 0,
            "pendingReview": 0,
        }
        first = self.store.update_task(
            task["id"],
            status="running",
            progress=0.35,
            current_stage="batch-1-4",
            checkpoint={
                "currentAction": "after-catalog-validation",
                "progressSnapshot": snapshot,
            },
        )
        self.store.update_task(
            task["id"],
            status="running",
            progress=0.35,
            current_stage="batch-1-4",
            checkpoint={
                "currentAction": "after-catalog-validation",
                "progressSnapshot": snapshot,
            },
        )

        identical = [
            event
            for event in self.store.list_task_events(task["id"])
            if event["event"] == "task.updated"
        ]
        self.assertEqual(len(identical), 1)
        self.assertEqual(identical[0]["data"]["progressSnapshot"], snapshot)

        changed = {
            **snapshot,
            "overall": 0.5,
            "stage": "zip",
            "completedUnits": 2,
            "succeeded": 2,
        }
        self.store.update_task(
            task["id"],
            status="running",
            progress=0.5,
            current_stage="batch-1-4",
            checkpoint={
                "currentAction": "after-zip-validation",
                "progressSnapshot": changed,
            },
        )
        events = [
            event
            for event in self.store.list_task_events(task["id"])
            if event["event"] == "task.updated"
        ]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["data"]["progressSnapshot"], changed)
        self.assertEqual(events[-1]["data"]["currentAction"], "after-zip-validation")

        stage_only = {
            **changed,
            "stage": "publish",
        }
        self.store.update_task(
            task["id"],
            status="running",
            progress=0.5,
            current_stage="batch-1-4",
            checkpoint={
                "currentAction": "after-batch",
                "progressSnapshot": stage_only,
            },
        )
        stage_events = [
            event
            for event in self.store.list_task_events(task["id"])
            if event["event"] == "task.updated"
        ]
        self.assertEqual(len(stage_events), 3)
        self.assertEqual(
            stage_events[-1]["data"]["progressSnapshot"]["stage"],
            "publish",
        )

    def test_processing_run_snapshot_is_protocol_visible_and_events_dedupe(self) -> None:
        task = self.store.create_task(
            name="数据处理时间线",
            mode="single",
            task_input={"queries": ["测试"]},
            output_dir="D:/run-progress-test",
        )
        summary = {
            "completedDocuments": 1,
            "totalDocuments": 3,
            "currentAction": "process-policy",
            "progressSnapshot": {
                "overall": 0.35,
                "stage": "documents",
                "completedUnits": 1,
                "totalUnits": 3,
                "succeeded": 0,
                "failed": 0,
                "pendingReview": 1,
            },
        }
        first = self.store.upsert_processing_run(
            "run-progress",
            task["id"],
            status="running",
            progress=0.35,
            current_stage="document-1-3",
            summary=summary,
        )
        self.store.upsert_processing_run(
            "run-progress",
            task["id"],
            status="running",
            progress=0.35,
            current_stage="document-1-3",
            summary=summary,
        )

        self.assertEqual(
            first["progressSnapshot"],
            summary["progressSnapshot"],
        )
        events = [
            event
            for event in self.store.list_task_events(task["id"])
            if event["event"] == "data.run.updated"
        ]
        self.assertEqual(len(events), 1)

    def test_processing_status_transition_preserves_latest_progress_and_summary(
        self,
    ) -> None:
        task = self.store.create_task(
            name="处理状态迁移",
            mode="single",
            task_input={"queries": ["测试"]},
            output_dir="D:/transition-test",
        )
        snapshot = {
            "overall": 0.65,
            "stage": "documents",
            "completedUnits": 8,
            "totalUnits": 12,
            "succeeded": 8,
            "failed": 0,
            "pendingReview": 0,
        }
        self.store.upsert_processing_run(
            "run-transition",
            task["id"],
            status="running",
            progress=0.65,
            current_stage="document-8-12",
            summary={
                "completedDocuments": 8,
                "totalDocuments": 12,
                "currentAction": "after-policy",
                "progressSnapshot": snapshot,
            },
        )

        transitioned = self.store.transition_processing_run(
            "run-transition",
            task["id"],
            status="stopping",
            current_stage="stopping",
            summary_updates={
                "stopRequestedAt": "2026-11-02T08:00:00Z",
                "stopPreviousStatus": "running",
            },
        )

        self.assertEqual(transitioned["progress"], 0.65)
        self.assertEqual(transitioned["progressSnapshot"], snapshot)
        self.assertEqual(
            transitioned["summary"]["completedDocuments"],
            8,
        )
        self.assertEqual(
            transitioned["summary"]["stopPreviousStatus"],
            "running",
        )
    def test_version_one_database_is_migrated(self) -> None:
        self.store.close()
        database = Path(self.tempdir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL DEFAULT '{}',
                output_dir TEXT NOT NULL DEFAULT '',
                progress REAL NOT NULL DEFAULT 0,
                current_stage TEXT NOT NULL DEFAULT '',
                error_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE task_items (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                policy_key TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                source_type TEXT NOT NULL DEFAULT '',
                progress REAL NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL DEFAULT '{}',
                error_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, policy_key)
            );
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        connection.commit()
        connection.close()

        migrated = TaskStore(database)
        try:
            self.assertEqual(
                migrated.connection.execute("PRAGMA user_version").fetchone()[0],
                6,
            )
            task_columns = {
                row[1] for row in migrated.connection.execute("PRAGMA table_info(tasks)")
            }
            item_columns = {
                row[1] for row in migrated.connection.execute("PRAGMA table_info(task_items)")
            }
            self.assertIn("checkpoint_json", task_columns)
            self.assertIn("started_at", task_columns)
            self.assertIn("finished_at", task_columns)
            self.assertIn("archived_at", task_columns)
            self.assertIn("archive_reason", task_columns)
            self.assertIn("current_stage", item_columns)
            tables = {
                row[0]
                for row in migrated.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("task_batches", tables)
            self.assertIn("policy_documents", tables)
            self.assertIn("processing_runs", tables)
            self.assertIn("task_artifacts", tables)
            document_columns = {
                row[1]
                for row in migrated.connection.execute(
                    "PRAGMA table_info(policy_documents)"
                )
            }
            self.assertIn("body_completeness", document_columns)
            self.assertIn("review_status", document_columns)
        finally:
            migrated.close()
            self.store = TaskStore(Path(self.tempdir.name) / "pkulaw.sqlite3")

    def test_version_four_database_is_migrated_with_archive_columns(self) -> None:
        task = self.store.create_task(
            name="v4 迁移任务",
            mode="single",
            task_input={"queries": ["迁移"]},
            output_dir="D:/migration-v4",
        )
        self.store.close()
        connection = sqlite3.connect(Path(self.tempdir.name) / "pkulaw.sqlite3")
        try:
            connection.execute("ALTER TABLE tasks DROP COLUMN archive_reason")
            connection.execute("ALTER TABLE tasks DROP COLUMN archived_at")
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
        finally:
            connection.close()

        migrated = TaskStore(Path(self.tempdir.name) / "pkulaw.sqlite3")
        try:
            self.assertEqual(
                migrated.connection.execute("PRAGMA user_version").fetchone()[0],
                6,
            )
            columns = {
                row[1] for row in migrated.connection.execute("PRAGMA table_info(tasks)")
            }
            self.assertIn("archived_at", columns)
            self.assertIn("archive_reason", columns)
            loaded = migrated.get_task(task["id"])
            self.assertIsNotNone(loaded)
            self.assertIsNone(loaded["archivedAt"])
            self.assertEqual(loaded["archiveReason"], "")
            backups = list(
                (Path(self.tempdir.name)).glob("pkulaw.sqlite3.pre-v6-*.bak")
            )
            self.assertEqual(len(backups), 1)
            backup = sqlite3.connect(backups[0])
            try:
                self.assertEqual(
                    backup.execute("PRAGMA user_version").fetchone()[0],
                    4,
                )
            finally:
                backup.close()
        finally:
            migrated.close()
            self.store = TaskStore(Path(self.tempdir.name) / "pkulaw.sqlite3")

    def test_version_five_database_migrates_source_provenance_without_data_loss(
        self,
    ) -> None:
        task = self.store.create_task(
            name="v5 来源迁移",
            mode="batch",
            task_input={"queries": ["迁移"]},
            output_dir="D:/migration-v5",
        )
        self.store.upsert_task_batch(
            task["id"],
            batch_key="batch:v5",
            query="迁移",
            library="central",
            page=1,
            row_start=1,
            row_end=1,
            status="completed",
            catalog_format="xls",
            full_format="docx",
            selected=[{"policyId": "p1", "title": "迁移政策"}],
        )
        self.store.upsert_processing_run(
            "run-v5",
            task["id"],
            status="completed",
            progress=1.0,
            current_stage="completed",
        )
        document = self.store.upsert_policy_document(
            task["id"],
            policy_key="id:p1",
            policy_id="p1",
            title="迁移政策",
            source_type="北大法宝ZIP",
            retrieval_method="网站原生 Word ZIP",
            status="processed",
        )
        artifact = self.store.add_task_artifact(
            task["id"],
            "run-v5",
            kind="总目录CSV",
            path="D:/migration-v5/06_总目录.csv",
            sha256="a" * 64,
        )
        self.store.close()
        database = Path(self.tempdir.name) / "pkulaw.sqlite3"
        connection = sqlite3.connect(database)
        try:
            for column in (
                "text_source_type",
                "extraction_method",
                "converter_name",
                "converter_version",
                "body_completeness",
                "review_status",
            ):
                connection.execute(
                    f"ALTER TABLE policy_documents DROP COLUMN {column}"
                )
            connection.execute("PRAGMA user_version = 5")
            connection.commit()
        finally:
            connection.close()

        migrated = TaskStore(database)
        try:
            self.assertEqual(
                migrated.connection.execute("PRAGMA user_version").fetchone()[0],
                6,
            )
            self.assertEqual(
                migrated.connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0],
                "ok",
            )
            loaded_task = migrated.get_task(task["id"])
            loaded_batches = migrated.list_task_batches(task["id"])
            loaded_run = migrated.get_processing_run("run-v5")
            loaded_document = migrated.get_policy_document(document["id"])
            loaded_artifacts = migrated.list_task_artifacts("run-v5")
            self.assertEqual(loaded_task["outputDir"], "D:/migration-v5")
            self.assertEqual(loaded_batches[0]["batchKey"], "batch:v5")
            self.assertEqual(loaded_run["status"], "completed")
            self.assertEqual(loaded_document["sourceType"], "北大法宝ZIP")
            self.assertEqual(loaded_document["textSourceType"], "")
            self.assertEqual(loaded_document["extractionMethod"], "")
            self.assertEqual(loaded_document["converterName"], "")
            self.assertEqual(loaded_document["converterVersion"], "")
            self.assertEqual(loaded_document["bodyCompleteness"], "unknown")
            self.assertEqual(loaded_document["reviewStatus"], "pending_review")
            self.assertEqual(loaded_artifacts[0]["id"], artifact["id"])
            backups = list(
                Path(self.tempdir.name).glob("pkulaw.sqlite3.pre-v6-*.bak")
            )
            self.assertEqual(len(backups), 1)
            backup = sqlite3.connect(backups[0])
            try:
                self.assertEqual(
                    backup.execute("PRAGMA user_version").fetchone()[0],
                    5,
                )
            finally:
                backup.close()
        finally:
            migrated.close()
            self.store = TaskStore(database)

    def test_archive_list_restore_and_active_guard(self) -> None:
        first = self.store.create_task(
            name="归档任务一",
            mode="single",
            task_input={},
            output_dir="D:/archive-one",
        )
        second = self.store.create_task(
            name="归档任务二",
            mode="batch",
            task_input={},
            output_dir="D:/archive-two",
        )
        archived = self.store.archive_tasks(
            [first["id"], second["id"]],
            reason="清理常规列表",
        )
        self.assertEqual({task["id"] for task in archived}, {first["id"], second["id"]})
        self.assertTrue(all(task["archivedAt"] for task in archived))
        self.assertEqual([task["id"] for task in self.store.list_tasks()], [])
        self.assertEqual(
            {task["id"] for task in self.store.list_tasks(include_archived=True)},
            {first["id"], second["id"]},
        )
        restored = self.store.restore_tasks([first["id"]])
        self.assertIsNone(restored[0]["archivedAt"])
        self.assertEqual(restored[0]["archiveReason"], "")

        active = self.store.create_task(
            name="活动任务",
            mode="single",
            task_input={},
            output_dir="D:/active",
        )
        self.store.update_task(active["id"], status="running", current_stage="running")
        with self.assertRaises(ValidationError) as context:
            self.store.archive_tasks([active["id"]])
        self.assertEqual(context.exception.code, "TASK_ACTIVE")

    def test_archive_rejects_active_processing_run(self) -> None:
        task = self.store.create_task(
            name="处理中禁止归档",
            mode="single",
            task_input={},
            output_dir="D:/processing-active",
        )
        self.store.upsert_processing_run(
            "processing-active",
            task["id"],
            status="running",
            progress=0.2,
            current_stage="extract",
        )
        with self.assertRaises(ValidationError) as context:
            self.store.archive_tasks([task["id"]])
        self.assertEqual(context.exception.code, "PROCESSING_ACTIVE")

    def test_cleanup_preview_only_returns_registered_safe_files(self) -> None:
        root = Path(self.tempdir.name) / "任务 输出"
        root.mkdir()
        word = root / "来源 Word.docx"
        txt = root / "正文.txt"
        evidence = root / "证据.json"
        official_html = root / "official.html"
        official_png = root / "official.png"
        artifact = root / "总目录.csv"
        shared = root / "共享文件.txt"
        unmanaged = root / "未登记.txt"
        for file in (
            word,
            txt,
            evidence,
            official_html,
            official_png,
            artifact,
            shared,
            unmanaged,
        ):
            file.write_text("test", encoding="utf-8")

        task = self.store.create_task(
            name="清理预览任务",
            mode="single",
            task_input={},
            output_dir=str(root),
        )
        other = self.store.create_task(
            name="共享任务",
            mode="single",
            task_input={},
            output_dir=str(root),
        )
        self.store.upsert_task_item(
            task["id"],
            policy_key="id:p1",
            title="政策一",
            status="completed",
            payload={"downloadedFile": str(word)},
        )
        self.store.upsert_policy_document(
            task["id"],
            policy_key="id:p1",
            title="政策一",
            word_path=str(word),
            txt_path=str(txt),
            evidence_path=str(evidence),
            payload={
                "officialHtml": str(official_html),
                "officialScreenshot": str(official_png),
            },
            status="processed",
        )
        self.store.upsert_processing_run(
            "preview-run",
            task["id"],
            status="completed",
            progress=1,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "preview-run",
            kind="总目录CSV",
            path=str(artifact),
        )
        self.store.add_task_artifact(
            task["id"],
            "preview-run",
            kind="共享文件",
            path=str(shared),
        )
        self.store.upsert_processing_run(
            "other-run",
            other["id"],
            status="completed",
            progress=1,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            other["id"],
            "other-run",
            kind="共享文件",
            path=str(shared),
        )
        traversal = root / ".." / "traversal.txt"
        self.store.add_task_artifact(
            task["id"],
            "preview-run",
            kind="路径穿越",
            path=str(traversal),
        )

        preview = self.store.cleanup_preview([task["id"]])
        eligible_paths = {item["path"] for item in preview["eligibleFiles"]}
        self.assertIn(str(word.resolve()), eligible_paths)
        self.assertIn(str(txt.resolve()), eligible_paths)
        self.assertIn(str(artifact.resolve()), eligible_paths)
        self.assertEqual(
            sum(item["path"] == str(word.resolve()) for item in preview["files"]),
            1,
        )
        protected_reasons = {
            item["reason"] for item in preview["protectedFiles"]
        }
        self.assertIn("SHARED_WITH_OTHER_TASK", protected_reasons)
        self.assertIn("PATH_TRAVERSAL", protected_reasons)
        self.assertTrue(
            any(
                item["path"] == str(root.resolve())
                and item["canTrash"] is False
                for item in preview["directories"]
            )
        )
        self.assertTrue(
            any(
                item["path"] == str(root.resolve()) and item["shared"] is True
                for item in preview["directories"]
            )
        )
        self.assertNotIn(str(unmanaged.resolve()), eligible_paths)

    def test_purge_requires_archive_and_rechecks_file_removal(self) -> None:
        root = Path(self.tempdir.name) / "purge 输出"
        root.mkdir()
        artifact = root / "report.txt"
        artifact.write_text("keep", encoding="utf-8")
        task = self.store.create_task(
            name="彻底删除任务",
            mode="single",
            task_input={},
            output_dir=str(root),
        )
        self.store.upsert_processing_run(
            "purge-run",
            task["id"],
            status="completed",
            progress=1,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "purge-run",
            kind="report",
            path=str(artifact),
        )
        with self.assertRaises(ValidationError) as not_archived:
            self.store.purge_tasks([task["id"]], require_files_removed=True)
        self.assertEqual(not_archived.exception.code, "TASK_NOT_ARCHIVED")

        self.store.archive_tasks([task["id"]])
        with self.assertRaises(ValidationError) as incomplete:
            self.store.purge_tasks([task["id"]], require_files_removed=True)
        self.assertEqual(incomplete.exception.code, "CLEANUP_INCOMPLETE")
        self.assertIsNotNone(self.store.get_task(task["id"]))

        artifact.unlink()
        result = self.store.purge_tasks([task["id"]], require_files_removed=True)
        self.assertEqual(result["taskIds"], [task["id"]])
        self.assertIsNone(self.store.get_task(task["id"]))
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM task_artifacts WHERE task_id = ?",
                (task["id"],),
            ).fetchone()[0],
            0,
        )

    def test_purge_can_delete_records_and_keep_output_files(self) -> None:
        root = Path(self.tempdir.name) / "keep 输出"
        root.mkdir()
        artifact = root / "保留.txt"
        artifact.write_text("keep", encoding="utf-8")
        task = self.store.create_task(
            name="保留文件删除记录",
            mode="single",
            task_input={},
            output_dir=str(root),
        )
        self.store.upsert_processing_run(
            "keep-run",
            task["id"],
            status="completed",
            progress=1,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "keep-run",
            kind="report",
            path=str(artifact),
        )
        self.store.archive_tasks([task["id"]])

        self.store.purge_tasks([task["id"]], require_files_removed=False)

        self.assertTrue(artifact.is_file())
        self.assertIsNone(self.store.get_task(task["id"]))

    def test_batch_state_round_trip_and_retry_reset(self) -> None:
        task = self.store.create_task(
            name="批量状态测试",
            mode="batch",
            task_input={"queries": ["批量测试"]},
            output_dir="D:/batch-test",
        )
        batch = self.store.upsert_task_batch(
            task["id"],
            batch_key="batch:test",
            query="批量测试",
            library="central",
            page=1,
            row_start=1,
            row_end=3,
            status="failed",
            progress=0,
            catalog_format="xlsx",
            full_format="docx",
            selected=[
                {"number": 1, "policyId": "p1", "title": "政策1"},
                {"number": 2, "policyId": "p2", "title": "政策2"},
                {"number": 3, "policyId": "p3", "title": "政策3"},
            ],
            catalog_path="D:/batch-test/catalog.xlsx",
            zip_path="D:/batch-test/word.zip",
            error={"code": "BAD_ZIP", "message": "invalid zip"},
        )
        self.assertEqual(batch["selected"][1]["policyId"], "p2")
        self.assertEqual(self.store.list_task_batches(task["id"])[0]["id"], batch["id"])
        self.assertEqual(self.store.reset_task_batches_for_retry(task["id"]), 1)
        reset = self.store.list_task_batches(task["id"])[0]
        self.assertEqual(reset["status"], "pending")
        self.assertEqual(reset["catalogPath"], "")

    def test_recover_interrupted_active_tasks(self) -> None:
        active = self.store.create_task(
            name="中断任务",
            mode="single",
            task_input={"queries": ["测试"]},
            output_dir="D:/interrupted",
        )
        self.store.update_task(
            active["id"],
            status="paused",
            current_stage="paused",
            progress=0.4,
            checkpoint={"query": "测试", "page": 1},
        )
        pending = self.store.create_task(
            name="待启动任务",
            mode="single",
            task_input={"queries": ["保留"]},
            output_dir="D:/pending",
        )

        recovered = self.store.recover_interrupted_tasks()

        self.assertEqual([task["id"] for task in recovered], [active["id"]])
        task = self.store.get_task(active["id"])
        self.assertEqual(task["status"], "stopped")
        self.assertEqual(task["error"]["code"], "APPLICATION_INTERRUPTED")
        self.assertEqual(
            task["checkpoint"]["interruptedPreviousStatus"],
            "paused",
        )
        self.assertEqual(self.store.get_task(pending["id"])["status"], "pending")

    def test_recover_interrupted_processing_run(self) -> None:
        task = self.store.create_task(
            name="处理中断任务",
            mode="single",
            task_input={"queries": ["处理"]},
            output_dir="D:/processing-interrupted",
        )
        self.store.upsert_processing_run(
            "run-interrupted",
            task["id"],
            status="paused",
            progress=0.35,
            current_stage="paused",
            summary={"currentPolicyKey": "id:p1", "completedDocuments": 1},
        )

        recovered = self.store.recover_interrupted_processing_runs()

        self.assertEqual([run["id"] for run in recovered], ["run-interrupted"])
        run = self.store.get_processing_run("run-interrupted")
        self.assertEqual(run["status"], "stopped")
        self.assertEqual(run["error"]["code"], "APPLICATION_INTERRUPTED")
        self.assertEqual(
            run["summary"]["interruptedPreviousStatus"],
            "paused",
        )

    def test_processing_run_and_policy_document_round_trip(self) -> None:
        task = self.store.create_task(
            name="数据处理测试",
            mode="single",
            task_input={"matchMode": "title"},
            output_dir="D:/processing-test",
        )
        run = self.store.upsert_processing_run(
            "run-001",
            task["id"],
            status="running",
            progress=0.5,
            current_stage="extract",
            options={"governmentFallback": False},
        )
        self.assertEqual(run["progress"], 0.5)
        self.assertEqual(self.store.latest_processing_run(task["id"])["id"], "run-001")

        document = self.store.upsert_policy_document(
            task["id"],
            policy_key="id:p1",
            policy_id="p1",
            title="政策一",
            source_type="北大法宝原件",
            source_url="https://example.test/p1",
            word_path="D:/processing-test/01_来源Word/p1.docx",
            txt_path="D:/processing-test/02_正文TXT/p1.txt",
            evidence_path="D:/processing-test/04_来源证据/p1.json",
            sha256="abc",
            text_source_type="word-text",
            extraction_method="python-docx",
            converter_name="python-docx",
            converter_version="1.2.0",
            body_completeness="complete",
            review_status="not_required",
            status="processed",
        )
        self.assertEqual(document["sourceType"], "北大法宝原件")
        self.assertEqual(document["textSourceType"], "word-text")
        self.assertEqual(document["extractionMethod"], "python-docx")
        self.assertEqual(document["converterName"], "python-docx")
        self.assertEqual(document["converterVersion"], "1.2.0")
        self.assertEqual(document["bodyCompleteness"], "complete")
        self.assertEqual(document["reviewStatus"], "not_required")
        self.assertEqual(self.store.list_policy_documents(task["id"])[0]["id"], document["id"])

        with self.assertRaises(ValidationError) as body_error:
            self.store.upsert_policy_document(
                task["id"],
                policy_key="id:invalid-body",
                body_completeness="guessed",
            )
        self.assertEqual(
            body_error.exception.code,
            "INVALID_BODY_COMPLETENESS",
        )
        with self.assertRaises(ValidationError) as review_error:
            self.store.upsert_policy_document(
                task["id"],
                policy_key="id:invalid-review",
                review_status="assumed_verified",
            )
        self.assertEqual(
            review_error.exception.code,
            "INVALID_REVIEW_STATUS",
        )

        artifact = self.store.add_task_artifact(
            task["id"],
            "run-001",
            kind="总目录CSV",
            path="D:/processing-test/06_总目录.csv",
            sha256="def",
        )
        self.assertEqual(self.store.list_task_artifacts("run-001")[0]["id"], artifact["id"])
        replaced = self.store.replace_task_artifacts_atomic(
            task["id"],
            "run-001",
            [
                {
                    "kind": "总目录CSV",
                    "path": "D:/processing-test/06_总目录.csv",
                    "sha256": "new-csv-hash",
                },
                {
                    "kind": "任务报告",
                    "path": "D:/processing-test/07_任务报告.html",
                    "sha256": "new-report-hash",
                },
            ],
        )
        self.assertEqual(len(replaced), 2)
        self.assertEqual(
            len(self.store.list_task_artifacts("run-001")),
            2,
        )

    def test_only_registered_artifacts_can_be_resolved(self) -> None:
        output = Path(self.tempdir.name) / "任务 输出"
        output.mkdir()
        managed = output / "总目录.csv"
        managed.write_text("title\n测试政策\n", encoding="utf-8")
        unmanaged = output / "任意文件.txt"
        unmanaged.write_text("not registered", encoding="utf-8")
        task = self.store.create_task(
            name="受控文件打开",
            mode="single",
            task_input={},
            output_dir=str(output),
        )
        self.store.upsert_processing_run(
            "run-managed",
            task["id"],
            status="completed",
            progress=1,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "run-managed",
            kind="总目录CSV",
            path=str(managed),
        )

        self.assertEqual(self.store.resolve_managed_file(str(managed)), managed)
        self.assertIsNone(self.store.resolve_managed_file(str(unmanaged)))
        self.assertEqual(self.store.resolve_task_output(task["id"]), output)

    def test_completed_batch_registration_is_atomic(self) -> None:
        task = self.store.create_task(
            name="批次原子登记",
            mode="batch",
            task_input={},
            output_dir=str(Path(self.tempdir.name) / "batch-output"),
        )
        selected = [
            {
                "number": 1,
                "policyId": "policy-1",
                "title": "批量政策",
            }
        ]
        self.store.upsert_task_batch(
            task["id"],
            batch_key="batch:atomic",
            query="批量",
            library="central",
            page=1,
            row_start=1,
            row_end=1,
            status="pending",
            catalog_format="xlsx",
            full_format="docx",
            selected=selected,
        )
        self.store.upsert_task_item(
            task["id"],
            policy_key="id:policy-1",
            title="批量政策",
            status="running",
        )

        self.store.complete_task_batch_atomic(
            task["id"],
            batch_key="batch:atomic",
            query="批量",
            library="central",
            page=1,
            row_start=1,
            row_end=1,
            catalog_format="xlsx",
            full_format="docx",
            selected=selected,
            items=[
                {
                    "policyKey": "id:policy-1",
                    "title": "批量政策",
                    "sourceType": "北大法宝ZIP",
                    "payload": {"zipFile": "D:/batch/全文.zip"},
                }
            ],
            catalog_path="D:/batch/目录.xlsx",
            zip_path="D:/batch/全文.zip",
            report_path="D:/batch/验收报告.json",
        )

        batch = self.store.list_task_batches(task["id"])[0]
        item = self.store.list_task_items(task["id"])[0]
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(item["status"], "completed")
        self.assertEqual(batch["zipPath"], "D:/batch/全文.zip")


if __name__ == "__main__":
    unittest.main()
