from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pkulaw_engine.application import EngineApplication
from pkulaw_engine.batch_task import BatchTaskManager
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.single_task import SingleTaskManager
from pkulaw_engine.storage import TaskStore, ValidationError


def boundary_checkpoint() -> dict[str, object]:
    return {
        "adaptiveSearch": {
            "fallbackMode": "A",
            "boundary": {
                "reason": "测试边界",
                "query": "测试政策",
                "library": "central",
                "year": "2023",
                "province": "全部省份",
                "completedPolicyCount": 3,
                "remainingScopes": [
                    {"query": "测试政策", "library": "central", "year": "2024"}
                ],
                "estimatedResultOperations": 1,
                "detectedAt": "2026-11-06T00:00:00Z",
            },
        }
    }


class CaptureBoundaryManagerTestBase:
    manager_type: type[SingleTaskManager] | type[BatchTaskManager]
    mode = "single"

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.logger = JsonLineLogger(self.logs / "engine.jsonl")
        self.bridge = MagicMock()
        self.manager = self.manager_type(
            self.store.database_path,
            self.bridge,
            self.logger,
            lambda _event, _data: None,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def make_task(self) -> dict[str, object]:
        task_input: dict[str, object] = {
            "queries": ["测试政策"],
            "matchMode": "keyword" if self.mode == "batch" else "title",
            "library": "central",
            "pageSize": 20,
            "outputDir": str(self.root / "output"),
        }
        if self.mode == "batch":
            task_input.update({"catalogFormat": "xlsx", "fullFormat": "docx"})
        task = self.store.create_task(
            name=f"{self.mode} boundary",
            mode=self.mode,
            task_input=task_input,
            output_dir=str(self.root / "output"),
        )
        return self.store.update_task(
            str(task["id"]),
            status="waiting_manual",
            current_stage="capture-boundary",
            error={
                "code": "CAPTURE_BOUNDARY_REACHED",
                "message": "测试边界",
            },
            checkpoint=boundary_checkpoint(),
        )

    def test_continue_writes_fallback_confirmation_and_clears_error(self) -> None:
        task = self.make_task()
        checkpoint = dict(task["checkpoint"])
        checkpoint["searchRecovery"] = {
            "phase": "boundary",
            "action": {"actionId": "previous-captcha-action"},
        }
        self.store.update_task(
            str(task["id"]),
            checkpoint=checkpoint,
        )
        details = self.manager.continue_capture_boundary(str(task["id"]))
        updated = details["task"]
        adaptive = updated["checkpoint"]["adaptiveSearch"]
        self.assertEqual(adaptive["fallbackMode"], "A")
        self.assertTrue(adaptive["boundary"]["confirmedAt"])
        self.assertNotIn("searchRecovery", updated["checkpoint"])
        self.assertIsNone(updated["error"])
        self.assertEqual(updated["status"], "waiting_manual")

    def test_continue_is_idempotent(self) -> None:
        task = self.make_task()
        first = self.manager.continue_capture_boundary(str(task["id"]))
        second = self.manager.continue_capture_boundary(str(task["id"]))
        self.assertEqual(
            second["task"]["updatedAt"],
            first["task"]["updatedAt"],
        )

    def test_continue_rejects_non_boundary_wait(self) -> None:
        task = self.make_task()
        self.store.update_task(
            str(task["id"]),
            status="waiting_manual",
            error={"code": "PAGE_LIMIT_REACHED", "message": "page"},
        )
        with self.assertRaises(ValidationError):
            self.manager.continue_capture_boundary(str(task["id"]))


class SingleCaptureBoundaryControlsTest(
    CaptureBoundaryManagerTestBase,
    unittest.TestCase,
):
    manager_type = SingleTaskManager
    mode = "single"


class BatchCaptureBoundaryControlsTest(
    CaptureBoundaryManagerTestBase,
    unittest.TestCase,
):
    manager_type = BatchTaskManager
    mode = "batch"


class CaptureBoundaryApplicationRestartTest(unittest.TestCase):
    def test_absent_controller_starts_same_task_with_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "logs"
            logs.mkdir()
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(logs / "engine.jsonl")
            bridge = MagicMock()
            bridge.set_event_sink = MagicMock()
            lease = MagicMock()
            lease.state.return_value = {
                "available": True,
                "taskId": "",
                "mode": "",
            }
            lease.acquire.return_value = {
                "acquired": True,
                "leaseId": "lease-1",
                "taskId": "",
                "mode": "single",
            }
            app = EngineApplication(
                store,
                io.StringIO(),
                logger,
                "0.2.5-test",
                bridge,
                lease,
            )
            fake_manager = MagicMock()
            fake_manager.has_live_controller.return_value = False
            fake_manager.start.return_value = {
                "task": {},
                "items": [],
            }
            app.single_manager = fake_manager
            task = store.create_task(
                name="single boundary",
                mode="single",
                task_input={
                    "queries": ["测试政策"],
                    "matchMode": "title",
                    "library": "central",
                    "pageSize": 20,
                    "outputDir": str(root / "output"),
                },
                output_dir=str(root / "output"),
            )
            task = store.update_task(
                str(task["id"]),
                status="waiting_manual",
                current_stage="capture-boundary",
                error={
                    "code": "CAPTURE_BOUNDARY_REACHED",
                    "message": "测试边界",
                },
                checkpoint=boundary_checkpoint(),
            )
            task_id = str(task["id"])
            app._continue_capture_boundary(task_id, operation_id="op-1")
            app._continue_capture_boundary(task_id, operation_id="op-1")
            fake_manager.continue_capture_boundary.assert_called_once_with(task_id)
            fake_manager.start.assert_called_once()
            store.close()


if __name__ == "__main__":
    unittest.main()
