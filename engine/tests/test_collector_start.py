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


PREVIEW = {
    "formFingerprint": "form-1",
    "previewFingerprint": "preview-1",
    "generatedAt": "2026-11-03T10:00:00Z",
    "mode": "single",
    "queryCount": 1,
    "libraryCount": 1,
    "yearCount": 1,
    "combinationCount": 1,
    "totalResults": 67,
    "totalPages": 4,
    "estimatedBatches50": 2,
    "windowStatus": "complete",
    "downloadsStarted": 0,
    "downloadIntentsCreated": 0,
    "tasksCreated": 0,
    "combinations": [],
}


class CollectorStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        logs = self.root / "logs"
        logs.mkdir()
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.application = EngineApplication(
            store=self.store,
            output=io.StringIO(),
            logger=JsonLineLogger(logs / "engine.jsonl"),
            app_version="test",
            browser_bridge=BrowserBridge(
                JsonLineLogger(logs / "engine.jsonl")
            ),
        )

    def tearDown(self) -> None:
        self.application.browser_lease.reset("test_teardown")
        self.store.close()
        self.tempdir.cleanup()

    def _params(self) -> dict[str, object]:
        return {
            "name": "人工智能逐篇",
            "mode": "single",
            "outputDir": str(self.root / "output"),
            "operationId": "start-1",
            "input": {
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "library": "central",
                "yearSelection": {"mode": "single", "year": 2026},
                "province": "全部省份",
                "timeliness": "全部（仅记录元数据，不筛选）",
                "pageSize": 20,
            },
        }

    def _running_details(self, task_id: str) -> dict[str, object]:
        task = self.store.update_task(
            task_id,
            status="running",
            current_stage="running",
        )
        return {
            "task": task,
            "items": self.store.list_task_items(task_id),
        }

    def test_start_collector_creates_one_task_and_starts_after_preview(self) -> None:
        with (
            patch.object(
                self.application.preview_service,
                "preview",
                return_value=PREVIEW,
            ) as preview,
            patch.object(
                self.application.single_manager,
                "start",
                side_effect=lambda task_id, **_: self._running_details(task_id),
            ) as start,
        ):
            response, events = self.application._dispatch(
                "collector-start",
                "collector.start",
                self._params(),
            )

        result = response["result"]
        tasks = self.store.list_tasks()
        self.assertTrue(response["ok"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(result["task"]["id"], tasks[0]["id"])
        self.assertEqual(result["preview"]["previewFingerprint"], "preview-1")
        self.assertTrue(result["started"])
        self.assertEqual(
            tasks[0]["input"]["previewSnapshot"]["previewFingerprint"],
            "preview-1",
        )
        self.assertEqual(
            self.application.browser_lease.state()["taskId"],
            tasks[0]["id"],
        )
        preview.assert_called_once()
        start.assert_called_once()
        self.assertEqual(
            [item["event"] for item in events],
            ["task.created", "collector.started"],
        )

    def test_start_collector_preflight_failure_has_no_worker_or_download(self) -> None:
        with (
            patch.object(
                self.application.preview_service,
                "preview",
                side_effect=ValidationError(
                    "MANUAL_ACTION_REQUIRED",
                    "请人工完成验证码",
                ),
            ),
            patch.object(self.application.single_manager, "start") as start,
        ):
            response, _ = self.application._dispatch(
                "collector-failed",
                "collector.start",
                self._params(),
            )

        result = response["result"]
        tasks = self.store.list_tasks()
        self.assertTrue(response["ok"])
        self.assertEqual(len(tasks), 1)
        self.assertFalse(result["started"])
        self.assertIsNone(result["preview"])
        self.assertEqual(result["error"]["code"], "MANUAL_ACTION_REQUIRED")
        self.assertEqual(tasks[0]["status"], "waiting_manual")
        self.assertEqual(
            tasks[0]["checkpoint"]["accessRecovery"]["taskId"],
            tasks[0]["id"],
        )
        self.assertEqual(
            tasks[0]["checkpoint"]["accessRecovery"]["waitCode"],
            "MANUAL_ACTION_REQUIRED",
        )
        self.assertTrue(self.application.browser_lease.state()["available"])
        start.assert_not_called()

    def test_start_collector_operation_id_is_idempotent(self) -> None:
        with (
            patch.object(
                self.application.preview_service,
                "preview",
                return_value=PREVIEW,
            ),
            patch.object(
                self.application.single_manager,
                "start",
                side_effect=lambda task_id, **_: self._running_details(task_id),
            ) as start,
        ):
            first, _ = self.application._dispatch(
                "collector-first",
                "collector.start",
                self._params(),
            )
            second, _ = self.application._dispatch(
                "collector-second",
                "collector.start",
                self._params(),
            )

        self.assertEqual(
            first["result"]["task"]["id"],
            second["result"]["task"]["id"],
        )
        self.assertEqual(len(self.store.list_tasks()), 1)
        start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
