from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.application import EngineApplication
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.storage import TaskStore


class ApplicationRecoveryTests(unittest.TestCase):
    def test_engine_startup_marks_interrupted_active_task_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            logs = root / "logs"
            logs.mkdir()
            logger = JsonLineLogger(logs / "engine.jsonl")
            store = TaskStore(root / "pkulaw.sqlite3")
            task = store.create_task(
                name="异常中断恢复",
                mode="single",
                task_input={"queries": ["测试"]},
                output_dir=str(root / "output"),
            )
            store.update_task(
                task["id"],
                status="paused",
                current_stage="paused",
                checkpoint={
                    "query": "测试",
                    "library": "central",
                    "page": 1,
                    "currentAction": "after-word-download",
                },
            )

            application = EngineApplication(
                store=store,
                output=io.StringIO(),
                logger=logger,
                app_version="test",
                browser_bridge=BrowserBridge(logger),
            )

            recovered = store.get_task(task["id"])
            self.assertEqual(application.single_manager.details(task["id"])["task"]["status"], "stopped")
            self.assertEqual(recovered["status"], "stopped")
            self.assertEqual(recovered["error"]["code"], "APPLICATION_INTERRUPTED")
            self.assertEqual(recovered["checkpoint"]["query"], "测试")
            self.assertEqual(
                recovered["checkpoint"]["interruptedPreviousStatus"],
                "paused",
            )
            store.close()

    def test_engine_startup_marks_interrupted_processing_run_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            logs = root / "logs"
            logs.mkdir()
            logger = JsonLineLogger(logs / "engine.jsonl")
            store = TaskStore(root / "pkulaw.sqlite3")
            task = store.create_task(
                name="数据处理异常中断恢复",
                mode="single",
                task_input={"queries": ["测试"]},
                output_dir=str(root / "output"),
            )
            processing_id = "processing-interrupted"
            store.upsert_processing_run(
                processing_id,
                task["id"],
                status="paused",
                progress=0.4,
                current_stage="paused",
                options={"governmentFallback": False},
                summary={
                    "currentPolicyKey": "id:test-policy",
                    "completedDocuments": 2,
                    "currentAction": "after-policy",
                },
            )

            EngineApplication(
                store=store,
                output=io.StringIO(),
                logger=logger,
                app_version="test",
                browser_bridge=BrowserBridge(logger),
            )

            recovered = store.get_processing_run(processing_id)
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(recovered["status"], "stopped")
            self.assertEqual(
                recovered["error"]["code"],
                "APPLICATION_INTERRUPTED",
            )
            self.assertEqual(
                recovered["summary"]["interruptedPreviousStatus"],
                "paused",
            )
            self.assertEqual(
                recovered["summary"]["currentPolicyKey"],
                "id:test-policy",
            )
            self.assertEqual(
                recovered["summary"]["completedDocuments"],
                2,
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
