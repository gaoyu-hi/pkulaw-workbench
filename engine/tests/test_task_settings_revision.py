from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pkulaw_engine.application import EngineApplication
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.storage import TaskStore, ValidationError
from pkulaw_engine.task_settings_revision import build_task_revision


class TaskSettingsRevisionTests(unittest.TestCase):
    def test_unchanged_config_ignores_preview_snapshot(self) -> None:
        task = {
            "id": "task-1",
            "input": {
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "searchSemanticsVersion": 2,
                "outputDir": "D:/old-output",
                "previewSnapshot": {"previewFingerprint": "preview-1"},
            },
            "outputDir": "D:/old-output",
            "checkpoint": {
                "configGeneration": 4,
                "scanJournal": {"completedPages": [1, 2]},
                "completedFiles": ["D:/old-output/已完成.docx"],
            },
        }

        plan = build_task_revision(
            task,
            {
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "outputDir": "D:/old-output",
            },
            "D:/old-output",
        )

        self.assertFalse(plan.changed)
        self.assertEqual(plan.generation, 4)
        self.assertEqual(task["checkpoint"], plan.checkpoint)
        self.assertNotIn("previewSnapshot", plan.task_input)

    def test_changed_config_starts_a_new_generation_without_deleting_history(
        self,
    ) -> None:
        task = {
            "id": "task-1",
            "input": {
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "outputDir": "D:/old-output",
            },
            "outputDir": "D:/old-output",
            "checkpoint": {
                "configGeneration": 2,
                "configFingerprint": "old-fingerprint",
                "scanJournal": {"completedPages": [1, 2]},
                "scanJournalSealed": True,
                "scanCompleted": True,
                "currentPage": 2,
                "previewSnapshot": {"totalPages": 3},
                "resultWindow": {"reportedTotalPages": 3},
                "yearPartition": {"status": "complete"},
                "yearStats": [{"year": "2025", "resultCount": 10}],
                "yearWindows": [{"query": "人工智能", "library": "central"}],
                "originalYearSelection": {
                    "mode": "range",
                    "startYear": 1949,
                    "endYear": 2025,
                },
                "effectiveYearSelection": {
                    "mode": "range",
                    "startYear": 2022,
                    "endYear": 2025,
                },
                "yearWindowAdjustment": {"adjusted": True},
                "configHistory": [{"generation": 1}],
                "completedFiles": ["D:/old-output/已完成.docx"],
            },
        }

        plan = build_task_revision(
            task,
            {
                "queries": ["碳中和"],
                "matchMode": "keyword",
                "searchSemanticsVersion": 2,
                "outputDir": "D:/new-output",
            },
            "D:/new-output",
        )

        self.assertTrue(plan.changed)
        self.assertEqual(plan.generation, 3)
        self.assertEqual(plan.task_input["queries"], ["碳中和"])
        self.assertEqual(plan.task_input["outputDir"], "D:/new-output")
        for key in (
            "scanJournal",
            "scanJournalSealed",
            "scanCompleted",
            "currentPage",
            "previewSnapshot",
            "resultWindow",
            "yearPartition",
            "yearStats",
            "yearWindows",
            "originalYearSelection",
            "effectiveYearSelection",
            "yearWindowAdjustment",
        ):
            self.assertNotIn(key, plan.checkpoint)
        self.assertEqual(
            plan.checkpoint["completedFiles"],
            ["D:/old-output/已完成.docx"],
        )
        self.assertEqual(plan.checkpoint["configGeneration"], 3)
        self.assertEqual(len(plan.checkpoint["configHistory"]), 2)
        self.assertEqual(
            plan.checkpoint["configHistory"][-1]["scanJournal"],
            {"completedPages": [1, 2]},
        )

    def test_legacy_search_meaning_forces_a_new_revision(self) -> None:
        task = {
            "id": "legacy-task",
            "input": {
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "outputDir": "D:/legacy-output",
            },
            "outputDir": "D:/legacy-output",
            "checkpoint": {
                "configGeneration": 1,
                "scanJournal": {"completedPages": [1]},
                "completedFiles": ["D:/legacy-output/已完成.docx"],
            },
        }

        plan = build_task_revision(
            task,
            {
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "outputDir": "D:/legacy-output",
            },
            "D:/legacy-output",
        )

        self.assertTrue(plan.changed)
        self.assertEqual(plan.generation, 2)
        self.assertEqual(
            plan.task_input["searchSemanticsVersion"],
            2,
        )
        self.assertNotIn("scanJournal", plan.checkpoint)
        self.assertEqual(
            plan.checkpoint["completedFiles"],
            ["D:/legacy-output/已完成.docx"],
        )


class TaskSettingsApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        logs = self.root / "logs"
        logs.mkdir()
        self.logger = JsonLineLogger(logs / "engine.jsonl")
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.application = EngineApplication(
            store=self.store,
            output=io.StringIO(),
            logger=self.logger,
            app_version="test",
            browser_bridge=BrowserBridge(self.logger),
        )
        self.task = self.store.create_task(
            name="设置更新任务",
            mode="single",
            task_input={
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "outputDir": str(self.root / "output"),
            },
            output_dir=str(self.root / "output"),
        )
        self.store.update_task(
            str(self.task["id"]),
            status="running",
            current_stage="searching",
            checkpoint={
                "configGeneration": 1,
                "scanJournal": {"completedPages": [1]},
                "currentPage": 1,
                "scanCompleted": False,
                "previewSnapshot": {"totalPages": 2},
                "completedFiles": ["D:/output/已完成.docx"],
            },
        )
        self.store.upsert_task_item(
            str(self.task["id"]),
            policy_key="id:completed",
            title="已完成政策",
            status="completed",
            payload={"downloadedFile": "D:/output/已完成.docx"},
        )

    def tearDown(self) -> None:
        self.application.browser_lease.reset("test_teardown")
        self.store.close()
        self.tempdir.cleanup()

    def _params(
        self,
        *,
        queries: list[str],
        operation_id: str = "settings-1",
        output_dir: str | None = None,
    ) -> dict[str, object]:
        target_output = output_dir or str(self.root / "new-output")
        return {
            "taskId": self.task["id"],
            "operationId": operation_id,
            "outputDir": target_output,
            "input": {
                "queries": queries,
                "matchMode": "keyword",
                "outputDir": target_output,
            },
        }

    def _running_details(self, task_id: str) -> dict[str, object]:
        task = self.store.update_task(
            task_id,
            status="running",
            current_stage="searching",
        )
        return {
            "task": task,
            "items": self.store.list_task_items(task_id),
        }

    def test_no_change_does_not_stop_or_restart(self) -> None:
        with (
            patch.object(self.application.single_manager, "stop") as stop,
            patch.object(self.application.single_manager, "start") as start,
        ):
            response, _ = self.application._dispatch(
                "settings-unchanged",
                "task.apply-settings",
                self._params(
                    queries=["人工智能"],
                    output_dir=str(self.root / "output"),
                ),
            )

        self.assertTrue(response["ok"])
        self.assertFalse(response["result"]["changed"])
        stop.assert_not_called()
        start.assert_not_called()

    def test_running_task_stops_then_restarts_same_id_and_preserves_files(
        self,
    ) -> None:
        task_id = str(self.task["id"])

        def stop(current_task_id: str) -> dict[str, object]:
            current = self.store.get_task(current_task_id)
            self.assertEqual(current["input"]["queries"], ["人工智能"])
            task = self.store.update_task(current_task_id, status="stopped")
            return {"task": task, "items": self.store.list_task_items(current_task_id)}

        with (
            patch.object(
                self.application.single_manager,
                "stop",
                side_effect=stop,
            ) as manager_stop,
            patch.object(
                self.application.single_manager,
                "start",
                side_effect=lambda current_task_id, **_: self._running_details(
                    current_task_id
                ),
            ) as manager_start,
        ):
            response, _ = self.application._dispatch(
                "settings-changed",
                "task.apply-settings",
                self._params(queries=["碳中和"]),
            )

        self.assertTrue(response["ok"])
        self.assertTrue(response["result"]["changed"])
        self.assertEqual(response["result"]["generation"], 2)
        manager_stop.assert_called_once_with(task_id)
        manager_start.assert_called_once()
        self.assertEqual(len(self.store.list_tasks()), 1)
        current = self.store.get_task(task_id)
        assert current is not None
        self.assertEqual(current["input"]["queries"], ["碳中和"])
        self.assertNotIn("scanJournal", current["checkpoint"])
        self.assertNotIn("previewSnapshot", current["checkpoint"])
        self.assertEqual(
            current["checkpoint"]["completedFiles"],
            ["D:/output/已完成.docx"],
        )
        items = self.store.list_task_items(task_id)
        self.assertEqual(items[0]["status"], "completed")
        self.assertEqual(
            items[0]["payload"]["downloadedFile"],
            "D:/output/已完成.docx",
        )

    def test_stop_timeout_does_not_write_new_settings(self) -> None:
        task_id = str(self.task["id"])
        self.application._settings_stop_timeout_seconds = 0.01

        def stop(current_task_id: str) -> dict[str, object]:
            task = self.store.update_task(current_task_id, status="stopping")
            return {"task": task, "items": []}

        with (
            patch.object(
                self.application.single_manager,
                "stop",
                side_effect=stop,
            ),
            patch.object(self.application.single_manager, "start") as start,
        ):
            with self.assertRaises(ValidationError) as context:
                self.application._dispatch(
                    "settings-timeout",
                    "task.apply-settings",
                    self._params(queries=["碳中和"]),
                )

        self.assertEqual(context.exception.code, "TASK_PAUSE_TIMEOUT")
        start.assert_not_called()
        current = self.store.get_task(task_id)
        assert current is not None
        self.assertEqual(current["input"]["queries"], ["人工智能"])

    def test_same_operation_id_starts_the_revision_only_once(self) -> None:
        def stop(current_task_id: str) -> dict[str, object]:
            task = self.store.update_task(current_task_id, status="stopped")
            return {
                "task": task,
                "items": self.store.list_task_items(current_task_id),
            }

        with (
            patch.object(
                self.application.single_manager,
                "stop",
                side_effect=stop,
            ) as manager_stop,
            patch.object(
                self.application.single_manager,
                "start",
                side_effect=lambda current_task_id, **_: self._running_details(
                    current_task_id
                ),
            ) as manager_start,
        ):
            first, _ = self.application._dispatch(
                "settings-once-a",
                "task.apply-settings",
                self._params(
                    queries=["碳中和"],
                    operation_id="settings-once",
                ),
            )
            second, _ = self.application._dispatch(
                "settings-once-b",
                "task.apply-settings",
                self._params(
                    queries=["碳中和"],
                    operation_id="settings-once",
                ),
            )

        self.assertEqual(
            first["result"]["task"]["id"],
            second["result"]["task"]["id"],
        )
        manager_stop.assert_called_once()
        manager_start.assert_called_once()

    def test_completed_task_can_be_revised_without_resetting_completed_files(
        self,
    ) -> None:
        task_id = str(self.task["id"])
        self.store.update_task(task_id, status="completed")
        with (
            patch.object(self.application.single_manager, "stop") as stop,
            patch.object(
                self.application.single_manager,
                "start",
                side_effect=lambda current_task_id, **_: self._running_details(
                    current_task_id
                ),
            ) as start,
        ):
            response, _ = self.application._dispatch(
                "settings-completed",
                "task.apply-settings",
                self._params(
                    queries=["碳中和"],
                    operation_id="settings-completed",
                ),
            )

        self.assertTrue(response["ok"])
        self.assertTrue(response["result"]["changed"])
        stop.assert_not_called()
        start.assert_called_once()
        current = self.store.get_task(task_id)
        assert current is not None
        self.assertEqual(
            current["checkpoint"]["completedFiles"],
            ["D:/output/已完成.docx"],
        )


if __name__ == "__main__":
    unittest.main()
