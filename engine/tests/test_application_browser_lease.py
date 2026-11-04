from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pkulaw_engine.application import EngineApplication
from pkulaw_engine.batch_task import BatchTaskManager
from pkulaw_engine.collector_preview import CollectorPreviewService
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.browser_lease import BrowserLeaseManager
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.single_task import SingleTaskManager
from pkulaw_engine.storage import TaskStore, ValidationError


class ApplicationBrowserLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        logs = self.root / "logs"
        logs.mkdir()
        self.logger = JsonLineLogger(logs / "engine.jsonl")
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.output = io.StringIO()
        self.bridge = BrowserBridge(self.logger)
        self.application = EngineApplication(
            store=self.store,
            output=self.output,
            logger=self.logger,
            app_version="phase-c-test",
            browser_bridge=self.bridge,
        )

    def tearDown(self) -> None:
        self.application.browser_lease.reset("test_teardown")
        self.store.close()
        self.tempdir.cleanup()

    def _single_task(self, name: str) -> dict[str, object]:
        return self.store.create_task(
            name=name,
            mode="single",
            task_input={
                "queries": ["测试政策"],
                "matchMode": "title",
                "library": "central",
                "pageSize": 20,
                "outputDir": str(self.root / "output"),
            },
            output_dir=str(self.root / "output"),
        )

    def _batch_task(self, name: str) -> dict[str, object]:
        return self.store.create_task(
            name=name,
            mode="batch",
            task_input={
                "queries": ["测试政策"],
                "matchMode": "title",
                "library": "central",
                "pageSize": 50,
                "catalogFormat": "xlsx",
                "fullFormat": "docx",
                "outputDir": str(self.root / "batch-output"),
            },
            output_dir=str(self.root / "batch-output"),
        )

    def _data_source_task(self) -> tuple[dict[str, object], dict[str, object]]:
        task = self._single_task("浏览器型数据处理")
        item = self.store.upsert_task_item(
            str(task["id"]),
            policy_key="id:no-exact-match",
            title="原始标题",
            status="failed",
            error={"code": "NO_EXACT_MATCH", "message": "未找到精确标题"},
        )
        return task, item

    def _details(self, task: dict[str, object]) -> dict[str, object]:
        return {
            "task": self.store.get_task(str(task["id"])),
            "items": self.store.list_task_items(str(task["id"])),
        }

    def test_different_single_tasks_are_mutually_exclusive(self) -> None:
        first = self._single_task("单篇 A")
        second = self._single_task("单篇 B")
        with patch.object(
            self.application.single_manager,
            "start",
            side_effect=lambda task_id, **_: self._details(first),
        ):
            self.application._dispatch(
                "start-a",
                "single.start",
                {"taskId": first["id"]},
            )
            with self.assertRaises(ValidationError) as busy:
                self.application._dispatch(
                    "start-b",
                    "single.start",
                    {"taskId": second["id"]},
                )
        self.assertEqual(busy.exception.code, "BROWSER_LEASE_BUSY")

    def test_different_batch_tasks_are_mutually_exclusive(self) -> None:
        first = self._batch_task("批量 A")
        second = self._batch_task("批量 B")
        with patch.object(
            self.application.batch_manager,
            "start",
            side_effect=lambda task_id, **_: self._details(first),
        ):
            self.application._dispatch(
                "start-a",
                "batch.start",
                {"taskId": first["id"]},
            )
            with self.assertRaises(ValidationError) as busy:
                self.application._dispatch(
                    "start-b",
                    "batch.start",
                    {"taskId": second["id"]},
                )
        self.assertEqual(busy.exception.code, "BROWSER_LEASE_BUSY")

    def test_single_and_batch_share_the_same_lease(self) -> None:
        single = self._single_task("互斥单篇")
        batch = self._batch_task("互斥批量")
        with (
            patch.object(
                self.application.single_manager,
                "start",
                side_effect=lambda task_id, **_: self._details(single),
            ),
            patch.object(
                self.application.batch_manager,
                "start",
                side_effect=lambda task_id, **_: self._details(batch),
            ),
        ):
            self.application._dispatch(
                "start-single",
                "single.start",
                {"taskId": single["id"]},
            )
            with self.assertRaises(ValidationError) as busy:
                self.application._dispatch(
                    "start-batch",
                    "batch.start",
                    {"taskId": batch["id"]},
                )
        self.assertEqual(busy.exception.code, "BROWSER_LEASE_BUSY")

    def test_browser_data_processing_is_mutually_exclusive_with_collection(self) -> None:
        source, _ = self._data_source_task()
        collection = self._single_task("与数据处理互斥的采集")
        with patch.object(
            self.application.single_manager,
            "start",
            side_effect=lambda task_id, **_: self._details(collection),
        ):
            self.application._dispatch(
                "start-collection",
                "single.start",
                {"taskId": collection["id"]},
            )
            with self.assertRaises(ValidationError) as busy:
                self.application._dispatch(
                    "start-data",
                    "data.start",
                    {
                        "taskId": source["id"],
                        "options": {"governmentFallback": True},
                    },
                )
        self.assertEqual(busy.exception.code, "BROWSER_LEASE_BUSY")

        self.application.browser_lease.reset("test-switch-mode")
        with patch.object(
            self.application.data_manager,
            "start",
            return_value={"run": {"taskId": source["id"]}},
        ):
            self.application._dispatch(
                "start-data-first",
                "data.start",
                {
                    "taskId": source["id"],
                    "options": {"governmentFallback": True},
                },
            )
            with self.assertRaises(ValidationError) as busy:
                self.application._dispatch(
                    "start-collection-second",
                    "single.start",
                    {"taskId": collection["id"]},
                )
        self.assertEqual(busy.exception.code, "BROWSER_LEASE_BUSY")

    def test_local_data_processing_does_not_take_browser_lease(self) -> None:
        source, _ = self._data_source_task()
        with patch.object(
            self.application.data_manager,
            "start",
            return_value={"run": {"taskId": source["id"]}},
        ):
            self.application._dispatch(
                "local-data",
                "data.start",
                {
                    "taskId": source["id"],
                    "options": {"governmentFallback": False},
                },
            )
        self.assertTrue(self.application.browser_lease.state()["available"])

    def test_concurrent_different_tasks_have_exactly_one_winner(self) -> None:
        first = self._single_task("并发 A")
        second = self._single_task("并发 B")
        details_by_id = {
            str(first["id"]): self._details(first),
            str(second["id"]): self._details(second),
        }

        def start(task: dict[str, object]) -> tuple[str, str]:
            try:
                self.application._dispatch(
                    f"concurrent-{task['id']}",
                    "single.start",
                    {"taskId": task["id"]},
                )
                return "ok", str(task["id"])
            except ValidationError as exc:
                return exc.code, str(task["id"])

        with patch.object(
            self.application.single_manager,
            "start",
            side_effect=lambda task_id, **_: details_by_id[task_id],
        ), patch.object(
            self.application,
            "_require_task_mode",
            side_effect=lambda task_id, mode: details_by_id[task_id]["task"],
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(start, [first, second]))
        self.assertEqual(sum(result[0] == "ok" for result in results), 1)
        self.assertEqual(
            sum(result[0] == "BROWSER_LEASE_BUSY" for result in results),
            1,
        )

    def test_task_create_operation_id_is_idempotent(self) -> None:
        payload = {
            "name": "幂等创建",
            "mode": "single",
            "input": {"queries": ["同一标题"]},
            "outputDir": str(self.root / "idempotent"),
            "operationId": "create-operation-1",
        }
        first, _ = self.application._dispatch("create-1", "task.create", payload)
        second, second_events = self.application._dispatch(
            "create-2",
            "task.create",
            payload,
        )
        self.assertEqual(
            first["result"]["task"]["id"],
            second["result"]["task"]["id"],
        )
        self.assertTrue(second["result"]["idempotent"])
        self.assertEqual(second_events, [])
        self.assertEqual(len(self.store.list_tasks(20)), 1)

        with self.assertRaises(ValidationError) as reused:
            self.application._dispatch(
                "create-3",
                "task.create",
                {**payload, "name": "不同请求"},
            )
        self.assertEqual(reused.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_start_operation_id_returns_current_task_without_second_start(self) -> None:
        task = self._single_task("启动幂等")
        details = self._details(task)
        with patch.object(
            self.application.single_manager,
            "start",
            return_value=details,
        ) as start:
            first, _ = self.application._dispatch(
                "start-1",
                "single.start",
                {"taskId": task["id"], "operationId": "start-operation-1"},
            )
            second, _ = self.application._dispatch(
                "start-2",
                "single.start",
                {"taskId": task["id"], "operationId": "start-operation-1"},
            )
        self.assertEqual(
            {**first["result"], "timeline": []},
            {**second["result"], "timeline": []},
        )
        self.assertEqual(
            first["result"]["task"]["id"],
            second["result"]["task"]["id"],
        )
        start.assert_called_once()

    def test_repeated_start_after_completion_does_not_restart(self) -> None:
        task = self._single_task("完成后的重复启动")
        self.store.update_task(
            str(task["id"]),
            status="completed",
            current_stage="completed",
            progress=1.0,
        )
        with patch.object(self.application.single_manager, "start") as start:
            response, _ = self.application._dispatch(
                "completed-start",
                "single.start",
                {"taskId": task["id"]},
            )
        start.assert_not_called()
        self.assertEqual(response["result"]["task"]["status"], "completed")

    def test_preview_takes_and_releases_temporary_browser_lease(self) -> None:
        expected = {
            "mode": "single",
            "combinationCount": 1,
            "totalResults": 3,
            "totalPages": 1,
            "estimatedBatches50": 1,
            "downloadsStarted": 0,
            "downloadIntentsCreated": 0,
            "tasksCreated": 0,
        }
        with patch.object(
            self.application,
            "preview_service",
            create=True,
        ) as preview_service:
            preview_service.preview.return_value = expected
            response, events = self.application._dispatch(
                "preview-1",
                "task.preview",
                {
                    "mode": "single",
                    "input": {
                        "queries": ["碳中和"],
                        "matchMode": "keyword",
                        "library": "central",
                    },
                },
            )

        preview_service.preview.assert_called_once()
        self.assertEqual(response["result"], expected)
        self.assertEqual(events, [])
        self.assertTrue(self.application.browser_lease.state()["available"])

    def test_preview_is_rejected_while_browser_lease_is_busy(self) -> None:
        self.application.browser_lease.acquire("holder", "single")
        with patch.object(
            self.application,
            "preview_service",
            create=True,
        ) as preview_service:
            with self.assertRaises(ValidationError) as busy:
                self.application._dispatch(
                    "preview-busy",
                    "task.preview",
                    {
                        "mode": "single",
                        "input": {
                            "queries": ["碳中和"],
                            "matchMode": "keyword",
                            "library": "central",
                        },
                    },
                )

        preview_service.preview.assert_not_called()
        self.assertEqual(busy.exception.code, "BROWSER_LEASE_BUSY")

    def test_preview_failure_releases_lease_without_creating_task(self) -> None:
        task_count = len(self.store.list_tasks(20))
        with patch.object(
            self.application,
            "preview_service",
            create=True,
        ) as preview_service:
            preview_service.preview.side_effect = ValidationError(
                "PREVIEW_SEARCH_FAILED",
                "预览检索失败",
            )
            with self.assertRaises(ValidationError) as failed:
                self.application._dispatch(
                    "preview-failed",
                    "task.preview",
                    {
                        "mode": "batch",
                        "input": {
                            "queries": ["碳中和"],
                            "matchMode": "keyword",
                            "library": "both",
                        },
                    },
                )

        self.assertEqual(failed.exception.code, "PREVIEW_SEARCH_FAILED")
        self.assertTrue(self.application.browser_lease.state()["available"])
        self.assertEqual(len(self.store.list_tasks(20)), task_count)

    def test_task_create_rejects_exact_title_mode_for_batch(self) -> None:
        with self.assertRaises(ValidationError) as unsupported:
            self.application._dispatch(
                "create-batch-title",
                "task.create",
                {
                    "name": "错误批次",
                    "mode": "batch",
                    "input": {
                        "queries": ["中华人民共和国国防动员法(2026修订)"],
                        "matchMode": "title",
                        "library": "central",
                    },
                    "outputDir": str(self.root / "wrong-batch"),
                },
            )

        self.assertEqual(
            unsupported.exception.code,
            "BATCH_EXACT_TITLE_UNSUPPORTED",
        )


class ManagerLeaseLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        logs = self.root / "logs"
        logs.mkdir()
        self.logger = JsonLineLogger(logs / "engine.jsonl")
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.bridge = BrowserBridge(self.logger)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _wait_released(self, leases: BrowserLeaseManager) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if leases.state()["available"]:
                return
            time.sleep(0.01)
        self.fail("worker thread did not release the browser lease")

    def test_single_repeated_start_is_idempotent_and_releases_on_success(self) -> None:
        task = self.store.create_task(
            name="单篇线程租约",
            mode="single",
            task_input={
                "queries": ["测试政策"],
                "matchMode": "title",
                "library": "central",
                "outputDir": str(self.root / "single"),
            },
            output_dir=str(self.root / "single"),
        )
        leases = BrowserLeaseManager()
        manager = SingleTaskManager(
            self.store.database_path,
            self.bridge,
            self.logger,
            lambda _event, _data: None,
            leases,
        )
        lease = leases.acquire(str(task["id"]), "single")
        entered = threading.Event()
        finish = threading.Event()

        def fake_run(*_args: object) -> None:
            entered.set()
            finish.wait(timeout=5)

        with patch.object(manager, "_run", side_effect=fake_run):
            manager.start(str(task["id"]), lease_id=str(lease["leaseId"]))
            self.assertTrue(entered.wait(timeout=2))
            first = manager.details(str(task["id"]))
            repeated = manager.start(
                str(task["id"]),
                lease_id=str(lease["leaseId"]),
            )
            self.assertEqual(first["task"]["id"], repeated["task"]["id"])
            self.assertEqual(len(manager.controllers), 1)
            finish.set()
            manager.stop_all()
        self._wait_released(leases)

    def test_batch_worker_failure_still_releases_lease(self) -> None:
        task = self.store.create_task(
            name="批量线程租约",
            mode="batch",
            task_input={
                "queries": ["测试政策"],
                "matchMode": "title",
                "library": "central",
                "catalogFormat": "xlsx",
                "fullFormat": "docx",
                "outputDir": str(self.root / "batch"),
            },
            output_dir=str(self.root / "batch"),
        )
        leases = BrowserLeaseManager()
        manager = BatchTaskManager(
            self.store.database_path,
            self.bridge,
            self.logger,
            lambda _event, _data: None,
            leases,
        )
        lease = leases.acquire(str(task["id"]), "batch")

        with patch.object(manager, "_run", side_effect=RuntimeError("worker failed")):
            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                manager._run_with_lease(
                    str(task["id"]),
                    None,  # type: ignore[arg-type]
                    None,  # type: ignore[arg-type]
                    str(lease["leaseId"]),
                )
        self._wait_released(leases)

    def test_pause_and_resume_keep_the_same_lease(self) -> None:
        task = self.store.create_task(
            name="暂停保持租约",
            mode="single",
            task_input={
                "queries": ["测试政策"],
                "matchMode": "title",
                "library": "central",
                "outputDir": str(self.root / "pause"),
            },
            output_dir=str(self.root / "pause"),
        )
        leases = BrowserLeaseManager()
        manager = SingleTaskManager(
            self.store.database_path,
            self.bridge,
            self.logger,
            lambda _event, _data: None,
            leases,
        )
        lease = leases.acquire(str(task["id"]), "single")
        entered = threading.Event()
        finish = threading.Event()

        def fake_run(*_args: object) -> None:
            entered.set()
            finish.wait(timeout=5)

        with (
            patch.object(manager, "_run", side_effect=fake_run),
            patch.object(manager, "_validate_resume_for_task"),
        ):
            manager.start(str(task["id"]), lease_id=str(lease["leaseId"]))
            self.assertTrue(entered.wait(timeout=2))
            with manager.lock:
                controller = manager.controllers[str(task["id"])]
            controller.request_pause("测试暂停")
            self.store.update_task(
                str(task["id"]),
                status="pausing",
                current_stage="pausing",
                checkpoint={"pauseKind": "manual", "pauseReason": "测试暂停"},
            )
            paused_state = leases.state()
            self.assertFalse(paused_state["available"])
            self.assertEqual(paused_state["leaseId"], lease["leaseId"])

            manager.resume(
                str(task["id"]),
                lease_id=str(lease["leaseId"]),
            )
            resumed_state = leases.state()
            self.assertFalse(resumed_state["available"])
            self.assertEqual(resumed_state["leaseId"], lease["leaseId"])
            finish.set()
            manager.stop_all()
        self._wait_released(leases)


if __name__ == "__main__":
    unittest.main()
