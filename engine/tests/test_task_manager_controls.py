from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkulaw_engine.batch_task import BatchTaskManager
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.single_task import SingleTaskManager
from pkulaw_engine.sources.pkulaw import ManualActionRequired
from pkulaw_engine.storage import TaskStore, ValidationError


class FakeThread:
    def __init__(
        self,
        *,
        alive: bool = True,
        fail_start: bool = False,
    ) -> None:
        self.alive = alive
        self.fail_start = fail_start
        self.started = False
        self.joined = False

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("thread start failed")
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.joined = True
        self.alive = False


class ManagerControlTestBase(unittest.TestCase):
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
        self.events: list[tuple[str, dict[str, object]]] = []
        self.manager = self.manager_type(
            self.store.database_path,
            self.bridge,
            self.logger,
            lambda event, data: self.events.append((event, data)),
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def create_task(self, **input_overrides: object) -> dict[str, object]:
        base_input: dict[str, object] = {
            "queries": ["测试政策"],
            "matchMode": "keyword" if self.mode == "batch" else "title",
            "library": "central",
            "pageSize": 20,
            "outputDir": str(self.root / "output"),
        }
        if self.mode == "batch":
            base_input.update(
                {
                    "catalogFormat": "xlsx",
                    "fullFormat": "docx",
                }
            )
        base_input.update(input_overrides)
        return self.store.create_task(
            name=f"{self.mode} control",
            mode=self.mode,
            task_input=base_input,
            output_dir=str(self.root / "output"),
        )

    def attach_controller(
        self,
        task_id: str,
        *,
        alive: bool = True,
    ) -> tuple[TaskControl, FakeThread]:
        controller = TaskControl(task_id)
        fake_thread = FakeThread(alive=alive)
        controller.thread = fake_thread  # type: ignore[assignment]
        self.manager.controllers[task_id] = controller
        return controller, fake_thread


class SingleManagerControlTests(ManagerControlTestBase):
    manager_type = SingleTaskManager

    def test_progress_snapshot_counts_completed_and_failed_items(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        self.store.upsert_task_item(
            task_id,
            policy_key="id:complete",
            title="已完成",
            status="completed",
        )
        self.store.upsert_task_item(
            task_id,
            policy_key="id:failed",
            title="失败",
            status="failed",
            error={"code": "TEST_FAILED", "message": "failed"},
        )

        snapshot = self.manager._progress_snapshot_for_items(
            self.store,
            task_id,
            stage="download",
            overall=0.60,
        )

        self.assertEqual(snapshot.completed_units, 2)
        self.assertEqual(snapshot.total_units, 2)
        self.assertEqual(snapshot.succeeded, 1)
        self.assertEqual(snapshot.failed, 1)
        self.assertEqual(snapshot.pending_review, 0)
        self.assertEqual(snapshot.overall, 0.60)

    def test_structured_item_error_preserves_body_quality_code(self) -> None:
        class QualityError(RuntimeError):
            code = "BODY_INCOMPLETE"
            details = {
                "status": "suspicious",
                "issues": ["MISSING_CLAUSE_NUMBERS"],
            }

        error = self.manager._structured_item_error(
            QualityError("正文缺条")
        )

        self.assertEqual(error["code"], "BODY_INCOMPLETE")
        self.assertEqual(error["message"], "正文缺条")
        self.assertEqual(
            error["details"]["issues"],
            ["MISSING_CLAUSE_NUMBERS"],
        )

    def test_start_success_and_start_failure(self) -> None:
        task = self.create_task()
        with patch(
            "pkulaw_engine.single_task.threading.Thread",
            side_effect=lambda **_kwargs: FakeThread(),
        ):
            details = self.manager.start(
                str(task["id"]),
                lease_id="",
            )
        self.assertEqual(details["task"]["status"], "pending")
        self.assertIn(str(task["id"]), self.manager.controllers)

        self.manager.controllers.pop(str(task["id"]), None)
        with patch(
            "pkulaw_engine.single_task.threading.Thread",
            side_effect=lambda **_kwargs: FakeThread(fail_start=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                self.manager.start(str(task["id"]))
        self.assertNotIn(str(task["id"]), self.manager.controllers)

    def test_pause_resume_stop_and_idempotent_states(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        self.attach_controller(task_id)
        self.store.update_task(
            task_id,
            status="running",
            current_stage="searching",
            checkpoint={"currentYear": "2024"},
        )

        paused = self.manager.pause(task_id)
        self.assertEqual(paused["task"]["status"], "pausing")
        self.assertEqual(
            paused["task"]["checkpoint"]["pauseKind"],
            "manual",
        )
        duplicate = self.manager.pause(task_id)
        self.assertEqual(duplicate["task"]["status"], "pausing")

        self.store.update_task(task_id, status="paused")
        with patch.object(self.manager, "_validate_resume_for_task"):
            resumed = self.manager.resume(task_id)
        self.assertEqual(resumed["task"]["status"], "running")

        stopped = self.manager.stop(task_id)
        self.assertEqual(stopped["task"]["status"], "stopping")
        self.assertEqual(
            self.store.get_task(task_id)["checkpoint"]["stopPreviousStatus"],
            "running",
        )
        self.store.update_task(task_id, status="stopped")
        self.assertEqual(self.manager.stop(task_id)["task"]["status"], "stopped")

    def test_pause_and_resume_reject_invalid_states(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        with self.assertRaises(ValidationError) as not_pausable:
            self.manager.pause(task_id)
        self.assertEqual(not_pausable.exception.code, "TASK_NOT_PAUSABLE")

        self.store.update_task(task_id, status="running")
        with self.assertRaises(ValidationError) as not_running:
            self.manager.pause(task_id)
        self.assertEqual(not_running.exception.code, "TASK_NOT_RUNNING")

        with self.assertRaises(ValidationError) as not_paused:
            self.manager.resume(task_id)
        self.assertEqual(not_paused.exception.code, "TASK_NOT_PAUSED")

        controller, _thread = self.attach_controller(task_id)
        self.store.update_task(task_id, status="running")
        unchanged = self.manager.resume(task_id)
        self.assertEqual(unchanged["task"]["status"], "running")
        self.assertFalse(controller.pause_requested)

    def test_stop_without_thread_marks_application_interrupted(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        self.store.update_task(task_id, status="running")

        details = self.manager.stop(task_id)

        self.assertEqual(details["task"]["status"], "stopped")
        self.assertEqual(
            details["task"]["error"]["code"],
            "APPLICATION_INTERRUPTED",
        )

    def test_page_continue_candidate_and_continue_active(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        controller, _thread = self.attach_controller(task_id)
        self.store.update_task(
            task_id,
            status="waiting_manual",
            current_stage="waiting-page-limit",
            error={
                "code": "PAGE_LIMIT_REACHED",
                "message": "limit",
            },
            checkpoint={"currentYear": "2024", "page": 2},
        )

        continued = self.manager.continue_to_last(task_id)
        self.assertEqual(
            continued["task"]["checkpoint"]["pageMode"],
            "last",
        )
        self.assertTrue(controller.snapshot()["continueRequested"])

        self.store.update_task(
            task_id,
            status="waiting_manual",
            current_stage="candidate-selection",
            error=None,
            clear_error=True,
            checkpoint={"currentAction": "candidate-selection"},
        )
        with self.assertRaises(ValidationError) as candidate:
            self.manager.continue_task(task_id)
        self.assertEqual(
            candidate.exception.code,
            "CANDIDATE_SELECTION_REQUIRED",
        )

        self.store.update_task(
            task_id,
            status="waiting_login",
            current_stage="waiting-login",
            error=None,
            clear_error=True,
            checkpoint={},
        )
        controller, _thread = self.attach_controller(task_id)
        self.manager.continue_active()
        self.assertTrue(controller.snapshot()["continueRequested"])

    def test_choose_candidate_and_reject_invalid_choice(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        controller, _thread = self.attach_controller(task_id)
        item = self.store.upsert_task_item(
            task_id,
            policy_key="title:测试",
            title="测试",
            status="waiting_manual",
            payload={
                "candidates": [
                    {"policyId": "v1", "title": "测试"},
                    {"policyId": "v2", "title": "测试"},
                ]
            },
        )

        details = self.manager.choose_candidate(task_id, item["id"], "v2")
        selected = details["items"][0]
        self.assertEqual(
            selected["payload"]["selectedCandidateKey"],
            "v2",
        )
        self.assertEqual(
            controller.wait_for_choice(item["id"]),
            "v2",
        )

        with self.assertRaises(ValidationError) as invalid:
            self.manager.choose_candidate(task_id, item["id"], "missing")
        self.assertEqual(invalid.exception.code, "CANDIDATE_NOT_FOUND")

    def test_stop_all_and_active_helpers(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        _, fake_thread = self.attach_controller(task_id)
        self.assertTrue(self.manager.has_active())
        self.assertTrue(self.manager.has_active_task(task_id))

        self.manager.stop_all()

        self.assertTrue(fake_thread.joined)
        self.assertFalse(self.manager.has_active())

    def test_parse_config_and_payload(self) -> None:
        task = {
            "input": {
                "keywords": "测试",
                "library": "both",
                "pageSize": 50,
                "yearMode": "single",
                "year": 2024,
                "pageMode": "limit",
                "maxPages": 3,
            },
            "outputDir": str(self.root / "output"),
            "checkpoint": {},
        }
        config = self.manager._parse_config(task)
        payload = self.manager._config_payload(config)
        self.assertEqual(config.queries, ("测试",))
        self.assertEqual(payload["pageSize"], 50)
        self.assertEqual(payload["pageMode"], "limit")
        fulltext_config = self.manager._parse_config(
            {
                **task,
                "input": {
                    **task["input"],
                    "matchMode": "fulltext",
                },
            }
        )
        self.assertEqual(fulltext_config.match_mode, "fulltext")

        invalid_inputs = [
            ({}, "QUERIES_REQUIRED"),
            ({"queries": ["x"], "matchMode": "bad"}, "INVALID_MATCH_MODE"),
            ({"queries": ["x"], "library": "bad"}, "INVALID_LIBRARY"),
            ({"queries": ["x"], "pageSize": "bad"}, "INVALID_PAGING"),
            ({"queries": ["x"], "pageSize": 11}, "INVALID_PAGE_SIZE"),
        ]
        for input_value, expected_code in invalid_inputs:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ValidationError) as context:
                    self.manager._parse_config(
                        {
                            "input": input_value,
                            "outputDir": str(self.root / "output"),
                            "checkpoint": {},
                        }
                    )
                self.assertEqual(context.exception.code, expected_code)

        with self.assertRaises(ValidationError) as missing_output:
            self.manager._parse_config(
                {
                    "input": {"queries": ["x"]},
                    "outputDir": "",
                    "checkpoint": {},
                }
            )
        self.assertEqual(
            missing_output.exception.code,
            "OUTPUT_DIR_REQUIRED",
        )

    def test_single_control_edge_branches(self) -> None:
        with self.assertRaises(ValidationError) as missing_start:
            self.manager.start("missing")
        self.assertEqual(missing_start.exception.code, "TASK_NOT_FOUND")

        batch_task = self.store.create_task(
            name="wrong mode",
            mode="batch",
            task_input={"queries": ["x"]},
            output_dir=str(self.root / "output"),
        )
        with self.assertRaises(ValidationError) as wrong_mode:
            self.manager.start(str(batch_task["id"]))
        self.assertEqual(wrong_mode.exception.code, "TASK_MODE_MISMATCH")

        for method in (
            lambda: self.manager.stop("missing"),
            lambda: self.manager.pause("missing"),
            lambda: self.manager.resume("missing"),
            lambda: self.manager.choose_candidate("missing", "item", "key"),
        ):
            with self.subTest(method=method):
                with self.assertRaises(ValidationError) as missing:
                    method()
                self.assertEqual(missing.exception.code, "TASK_NOT_FOUND")

        for method in (
            lambda: self.manager.continue_to_last("missing"),
            lambda: self.manager.continue_task("missing"),
        ):
            with self.subTest(method=method):
                with self.assertRaises(ValidationError) as no_controller:
                    method()
                self.assertEqual(
                    no_controller.exception.code,
                    "TASK_NOT_WAITING",
                )

        completed = self.create_task()
        self.store.update_task(str(completed["id"]), status="completed")
        self.assertEqual(
            self.manager.stop(str(completed["id"]))["task"]["status"],
            "completed",
        )

        stopping = self.create_task()
        stopping_id = str(stopping["id"])
        controller, _thread = self.attach_controller(stopping_id)
        controller.request_stop()
        self.store.update_task(stopping_id, status="running")
        self.assertEqual(
            self.manager.stop(stopping_id)["task"]["status"],
            "running",
        )

        paused = self.create_task()
        paused_id = str(paused["id"])
        paused_controller, _thread = self.attach_controller(paused_id)
        paused_controller.request_pause()
        self.store.update_task(paused_id, status="running")
        self.assertEqual(
            self.manager.pause(paused_id)["task"]["status"],
            "running",
        )

        invalid_resume = self.create_task()
        invalid_resume_id = str(invalid_resume["id"])
        self.attach_controller(invalid_resume_id)
        self.store.update_task(invalid_resume_id, status="waiting_manual")
        with self.assertRaises(ValidationError) as invalid_resume_error:
            self.manager.resume(invalid_resume_id)
        self.assertEqual(
            invalid_resume_error.exception.code,
            "TASK_NOT_PAUSED",
        )

        no_controller_resume = self.create_task()
        no_controller_resume_id = str(no_controller_resume["id"])
        self.store.update_task(no_controller_resume_id, status="paused")
        with (
            patch.object(self.manager, "_validate_resume_for_task"),
            patch.object(
                self.manager,
                "start",
                return_value={"task": {"id": no_controller_resume_id}},
            ) as start,
        ):
            self.manager.resume(no_controller_resume_id)
        start.assert_called_once()

        no_continue = self.create_task()
        no_continue_id = str(no_continue["id"])
        no_continue_controller, _thread = self.attach_controller(no_continue_id)
        no_continue_controller.request_continue()
        self.store.update_task(
            no_continue_id,
            status="waiting_login",
        )
        self.assertEqual(
            self.manager.continue_task(no_continue_id)["task"]["status"],
            "waiting_login",
        )

        self.store.update_task(
            no_continue_id,
            status="running",
        )
        self.assertEqual(
            self.manager.continue_task(no_continue_id)["task"]["status"],
            "running",
        )

        item = self.store.upsert_task_item(
            no_continue_id,
            policy_key="id:item",
            title="item",
            payload={"candidates": [{"policyId": "item", "title": "item"}]},
        )
        self.manager.controllers.pop(no_continue_id, None)
        with self.assertRaises(ValidationError) as no_worker:
            self.manager.choose_candidate(
                no_continue_id,
                item["id"],
                "item",
            )
        self.assertEqual(no_worker.exception.code, "TASK_NOT_WAITING")

        self.manager.controllers["no-thread"] = TaskControl("no-thread")
        self.manager.stop_all()
        self.assertFalse(self.manager.has_active())

    def test_single_remaining_control_branches(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        self.attach_controller(task_id)
        self.store.update_task(task_id, status="paused")
        with patch.object(self.manager, "_validate_resume_for_task"):
            result = self.manager.resume(task_id)
        self.assertEqual(result["task"]["status"], "paused")

        missing_controller = TaskControl("missing")
        missing_controller.thread = FakeThread()  # type: ignore[assignment]
        self.manager.controllers["missing"] = missing_controller
        with self.assertRaises(ValidationError) as missing_page:
            self.manager.continue_to_last("missing")
        self.assertEqual(missing_page.exception.code, "TASK_NOT_FOUND")
        with self.assertRaises(ValidationError) as missing_continue:
            self.manager.continue_task("missing")
        self.assertEqual(missing_continue.exception.code, "TASK_NOT_FOUND")

        self.store.update_task(task_id, status="running")
        with self.assertRaises(ValidationError) as invalid_page:
            self.manager.continue_to_last(task_id)
        self.assertEqual(
            invalid_page.exception.code,
            "PAGE_LIMIT_DECISION_NOT_AVAILABLE",
        )

        self.store.update_task(
            task_id,
            status="waiting_manual",
            error={"code": "PAGE_LIMIT_REACHED", "message": "limit"},
        )
        with self.assertRaises(ValidationError) as page_continue:
            self.manager.continue_task(task_id)
        self.assertEqual(page_continue.exception.code, "PAGE_LIMIT_REACHED")

        dead_controller = TaskControl("dead")
        dead_controller.thread = FakeThread(alive=False)  # type: ignore[assignment]
        self.manager.controllers["dead"] = dead_controller
        nonwaiting = self.create_task()
        nonwaiting_controller, _thread = self.attach_controller(
            str(nonwaiting["id"])
        )
        self.store.update_task(str(nonwaiting["id"]), status="running")
        page_limited = self.create_task()
        page_limited_controller, _thread = self.attach_controller(
            str(page_limited["id"])
        )
        self.store.update_task(
            str(page_limited["id"]),
            status="waiting_manual",
            error={"code": "PAGE_LIMIT_REACHED", "message": "limit"},
        )
        self.manager.continue_active()
        self.assertFalse(nonwaiting_controller.snapshot()["continueRequested"])
        self.assertFalse(page_limited_controller.snapshot()["continueRequested"])

        with self.assertRaises(ValidationError) as missing_item:
            self.manager.choose_candidate(task_id, "missing-item", "key")
        self.assertEqual(missing_item.exception.code, "TASK_ITEM_NOT_FOUND")


class BatchManagerControlTests(ManagerControlTestBase):
    manager_type = BatchTaskManager
    mode = "batch"

    def test_progress_snapshot_counts_completed_and_failed_batches(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        for key, status in (
            ("batch:complete", "completed"),
            ("batch:failed", "failed"),
        ):
            self.store.upsert_task_batch(
                task_id,
                batch_key=key,
                query="测试",
                library="central",
                page=1,
                row_start=1,
                row_end=1,
                status=status,
                catalog_format="xls",
                full_format="docx",
                selected=[],
            )

        snapshot = self.manager._progress_snapshot_for_batches(
            self.store,
            task_id,
            stage="publish",
            overall=0.80,
        )

        self.assertEqual(snapshot.completed_units, 2)
        self.assertEqual(snapshot.total_units, 2)
        self.assertEqual(snapshot.succeeded, 1)
        self.assertEqual(snapshot.failed, 1)
        self.assertEqual(snapshot.pending_review, 0)
        self.assertEqual(snapshot.overall, 0.80)

    def test_start_success_and_failure(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        with patch(
            "pkulaw_engine.batch_task.threading.Thread",
            side_effect=lambda **_kwargs: FakeThread(),
        ):
            details = self.manager.start(task_id)
        self.assertEqual(details["task"]["mode"], "batch")
        self.assertIn(task_id, self.manager.controllers)

        self.manager.controllers.pop(task_id, None)
        with patch(
            "pkulaw_engine.batch_task.threading.Thread",
            side_effect=lambda **_kwargs: FakeThread(fail_start=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                self.manager.start(task_id)

    def test_pause_resume_stop_and_interrupted_stop(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        self.attach_controller(task_id)
        self.store.update_task(
            task_id,
            status="running",
            current_stage="searching",
        )
        self.assertEqual(
            self.manager.pause(task_id)["task"]["status"],
            "pausing",
        )
        self.assertEqual(
            self.manager.pause(task_id)["task"]["status"],
            "pausing",
        )
        self.store.update_task(task_id, status="paused")
        with patch.object(self.manager, "_validate_resume_for_task"):
            self.assertEqual(
                self.manager.resume(task_id)["task"]["status"],
                "running",
            )
        self.assertEqual(
            self.manager.stop(task_id)["task"]["status"],
            "stopping",
        )
        self.assertEqual(
            self.manager.stop(task_id)["task"]["status"],
            "stopping",
        )

        other = self.create_task()
        self.store.update_task(
            str(other["id"]),
            status="running",
        )
        interrupted = self.manager.stop(str(other["id"]))
        self.assertEqual(
            interrupted["task"]["error"]["code"],
            "APPLICATION_INTERRUPTED",
        )

    def test_page_limit_scale_and_continue_gates(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        controller, _thread = self.attach_controller(task_id)

        self.store.update_task(
            task_id,
            status="waiting_manual",
            error={"code": "PAGE_LIMIT_REACHED", "message": "limit"},
            checkpoint={"page": 1},
        )
        page = self.manager.continue_to_last(task_id)
        self.assertEqual(page["task"]["checkpoint"]["pageMode"], "last")
        self.assertTrue(controller.snapshot()["continueRequested"])

        self.store.update_task(
            task_id,
            status="waiting_manual",
            error={
                "code": "BATCH_SCALE_CONFIRMATION_REQUIRED",
                "message": "scale",
            },
            checkpoint={},
        )
        scale = self.manager.confirm_scale(task_id)
        self.assertTrue(scale["task"]["checkpoint"]["scaleConfirmed"])
        self.assertTrue(controller.snapshot()["continueRequested"])

        self.store.update_task(
            task_id,
            status="waiting_manual",
            error={
                "code": "BATCH_SCALE_CONFIRMATION_REQUIRED",
                "message": "scale",
            },
        )
        with self.assertRaises(ValidationError):
            self.manager.continue_task(task_id)
        self.store.update_task(
            task_id,
            status="waiting_login",
            error=None,
            clear_error=True,
        )
        controller, _thread = self.attach_controller(task_id)
        self.manager.continue_active()
        self.assertTrue(controller.snapshot()["continueRequested"])

    def test_pause_resume_invalid_states(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        with self.assertRaises(ValidationError) as not_pausable:
            self.manager.pause(task_id)
        self.assertEqual(not_pausable.exception.code, "TASK_NOT_PAUSABLE")
        self.store.update_task(task_id, status="running")
        with self.assertRaises(ValidationError) as not_running:
            self.manager.pause(task_id)
        self.assertEqual(not_running.exception.code, "TASK_NOT_RUNNING")
        with self.assertRaises(ValidationError) as not_paused:
            self.manager.resume(task_id)
        self.assertEqual(not_paused.exception.code, "TASK_NOT_PAUSED")

    def test_parse_config_and_payload(self) -> None:
        task = {
            "input": {
                "queries": ["测试"],
                "matchMode": "keyword",
                "library": "both",
                "catalogFormat": "html-xls",
                "fullFormat": "docx",
                "pageSize": 100,
            },
            "outputDir": str(self.root / "output"),
            "checkpoint": {},
        }
        config = self.manager._parse_config(task)
        payload = self.manager._config_payload(config)
        self.assertEqual(config.catalog_format, "html-xls")
        self.assertEqual(payload["maxBatchSize"], 50)
        fulltext_config = self.manager._parse_config(
            {
                **task,
                "input": {
                    **task["input"],
                    "matchMode": "fulltext",
                },
            }
        )
        self.assertEqual(fulltext_config.match_mode, "fulltext")

        string_keywords = dict(task)
        string_keywords["input"] = {
            "keywords": "单个关键词",
            "matchMode": "keyword",
            "library": "central",
        }
        string_config = self.manager._parse_config(string_keywords)
        self.assertEqual(string_config.queries, ("单个关键词",))

        invalid_inputs = [
            ({}, "QUERIES_REQUIRED"),
            ({"queries": ["x"], "matchMode": "bad"}, "INVALID_MATCH_MODE"),
            (
                {"queries": ["x"], "matchMode": "title"},
                "BATCH_EXACT_TITLE_UNSUPPORTED",
            ),
            ({"queries": ["x"], "library": "bad"}, "INVALID_LIBRARY"),
            (
                {"queries": ["x"], "catalogFormat": "csv"},
                "INVALID_CATALOG_FORMAT",
            ),
            (
                {"queries": ["x"], "fullFormat": "pdf"},
                "INVALID_FULL_FORMAT",
            ),
            ({"queries": ["x"], "pageSize": "bad"}, "INVALID_PAGING"),
            ({"queries": ["x"], "pageSize": 11}, "INVALID_PAGE_SIZE"),
        ]
        for input_value, expected_code in invalid_inputs:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ValidationError) as context:
                    self.manager._parse_config(
                        {
                            "input": input_value,
                            "outputDir": str(self.root / "output"),
                            "checkpoint": {},
                        }
                    )
                self.assertEqual(context.exception.code, expected_code)

        with self.assertRaises(ValidationError) as missing_output:
            self.manager._parse_config(
                {
                    "input": {"queries": ["x"]},
                    "outputDir": "",
                    "checkpoint": {},
                }
            )
        self.assertEqual(
            missing_output.exception.code,
            "OUTPUT_DIR_REQUIRED",
        )

    def test_manual_retry_reports_exhaustion(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        controller = TaskControl(task_id)

        def fail_manually() -> None:
            raise ManualActionRequired("manual action required")

        with patch.object(self.manager, "_wait_for_manual"):
            with self.assertRaises(ManualActionRequired) as exhausted:
                self.manager._with_manual_retry(
                    self.store,
                    controller,
                    task_id,
                    fail_manually,
                    attempts=2,
                    action_name="search",
                )

        self.assertEqual(str(exhausted.exception), "manual action required")

    def test_stop_all_and_active_helpers(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        _, fake_thread = self.attach_controller(task_id)
        self.assertTrue(self.manager.has_active())
        self.assertTrue(self.manager.has_active_task(task_id))
        self.manager.stop_all()
        self.assertTrue(fake_thread.joined)
        self.assertFalse(self.manager.has_active())

    def test_batch_control_edge_branches(self) -> None:
        with self.assertRaises(ValidationError) as missing_start:
            self.manager.start("missing")
        self.assertEqual(missing_start.exception.code, "TASK_NOT_FOUND")

        single_task = self.store.create_task(
            name="wrong mode",
            mode="single",
            task_input={"queries": ["x"]},
            output_dir=str(self.root / "output"),
        )
        with self.assertRaises(ValidationError) as wrong_mode:
            self.manager.start(str(single_task["id"]))
        self.assertEqual(wrong_mode.exception.code, "TASK_MODE_MISMATCH")

        task = self.create_task()
        task_id = str(task["id"])
        self.attach_controller(task_id)
        with patch.object(self.manager, "details", return_value={"task": task}):
            self.assertEqual(
                self.manager.start(task_id)["task"]["id"],
                task["id"],
            )

        for method in (
            lambda: self.manager.stop("missing"),
            lambda: self.manager.pause("missing"),
            lambda: self.manager.resume("missing"),
            lambda: self.manager.details("missing"),
        ):
            with self.subTest(method=method):
                with self.assertRaises(ValidationError) as missing:
                    method()
                self.assertEqual(missing.exception.code, "TASK_NOT_FOUND")

        self.manager.controllers.pop(task_id, None)
        self.store.update_task(task_id, status="completed")
        self.assertEqual(
            self.manager.stop(task_id)["task"]["status"],
            "completed",
        )

        stopping = self.create_task()
        stopping_id = str(stopping["id"])
        controller, _thread = self.attach_controller(stopping_id)
        controller.request_stop()
        self.store.update_task(stopping_id, status="running")
        self.assertEqual(
            self.manager.stop(stopping_id)["task"]["status"],
            "running",
        )

        paused = self.create_task()
        paused_id = str(paused["id"])
        paused_controller, _thread = self.attach_controller(paused_id)
        paused_controller.request_pause()
        self.store.update_task(paused_id, status="running")
        self.assertEqual(
            self.manager.pause(paused_id)["task"]["status"],
            "running",
        )

        invalid_resume = self.create_task()
        invalid_resume_id = str(invalid_resume["id"])
        self.attach_controller(invalid_resume_id)
        self.store.update_task(invalid_resume_id, status="waiting_manual")
        with self.assertRaises(ValidationError) as invalid_resume_error:
            self.manager.resume(invalid_resume_id)
        self.assertEqual(
            invalid_resume_error.exception.code,
            "TASK_NOT_PAUSED",
        )

        no_controller_resume = self.create_task()
        no_controller_resume_id = str(no_controller_resume["id"])
        self.store.update_task(no_controller_resume_id, status="paused")
        with (
            patch.object(self.manager, "_validate_resume_for_task"),
            patch.object(
                self.manager,
                "start",
                return_value={"task": {"id": no_controller_resume_id}},
            ) as start,
        ):
            self.manager.resume(no_controller_resume_id)
        start.assert_called_once()

        gate = self.create_task()
        gate_id = str(gate["id"])
        self.store.update_task(gate_id, status="running")
        for method in (
            lambda: self.manager.continue_to_last(gate_id),
            lambda: self.manager.confirm_scale(gate_id),
            lambda: self.manager.continue_task(gate_id),
        ):
            with self.subTest(method=method):
                with self.assertRaises(ValidationError) as no_controller:
                    method()
                self.assertEqual(
                    no_controller.exception.code,
                    "TASK_NOT_WAITING",
                )

        gate_controller, _thread = self.attach_controller(gate_id)
        self.assertEqual(
            self.manager.continue_task(gate_id)["task"]["status"],
            "running",
        )
        gate_controller.request_continue()
        self.store.update_task(
            gate_id,
            status="waiting_login",
        )
        self.assertEqual(
            self.manager.continue_task(gate_id)["task"]["status"],
            "waiting_login",
        )

        self.manager.controllers["no-thread"] = TaskControl("no-thread")
        self.manager.stop_all()
        self.assertFalse(self.manager.has_active())

    def test_batch_remaining_control_branches(self) -> None:
        task = self.create_task()
        task_id = str(task["id"])
        self.attach_controller(task_id)
        self.store.update_task(task_id, status="paused")
        with patch.object(self.manager, "_validate_resume_for_task"):
            result = self.manager.resume(task_id)
        self.assertEqual(result["task"]["status"], "paused")

        missing_controller = TaskControl("missing")
        missing_controller.thread = FakeThread()  # type: ignore[assignment]
        self.manager.controllers["missing"] = missing_controller
        with self.assertRaises(ValidationError) as missing_gate:
            self.manager.continue_to_last("missing")
        self.assertEqual(missing_gate.exception.code, "TASK_NOT_FOUND")
        with self.assertRaises(ValidationError) as missing_continue:
            self.manager.continue_task("missing")
        self.assertEqual(missing_continue.exception.code, "TASK_NOT_FOUND")

        self.store.update_task(task_id, status="running")
        with self.assertRaises(ValidationError) as invalid_gate:
            self.manager.confirm_scale(task_id)
        self.assertEqual(
            invalid_gate.exception.code,
            "TASK_DECISION_NOT_AVAILABLE",
        )

        dead_controller = TaskControl("dead")
        dead_controller.thread = FakeThread(alive=False)  # type: ignore[assignment]
        self.manager.controllers["dead"] = dead_controller
        nonwaiting = self.create_task()
        nonwaiting_controller, _thread = self.attach_controller(
            str(nonwaiting["id"])
        )
        self.store.update_task(str(nonwaiting["id"]), status="running")
        decision_waiting = self.create_task()
        decision_controller, _thread = self.attach_controller(
            str(decision_waiting["id"])
        )
        self.store.update_task(
            str(decision_waiting["id"]),
            status="waiting_manual",
            error={
                "code": "PAGE_LIMIT_REACHED",
                "message": "limit",
            },
        )
        self.manager.continue_active()
        self.assertFalse(nonwaiting_controller.snapshot()["continueRequested"])
        self.assertFalse(decision_controller.snapshot()["continueRequested"])

    def test_resume_gate_rechecks_browser_login_and_selector_state(self) -> None:
        self.bridge.state.side_effect = RuntimeError("browser unavailable")
        with self.assertRaises(ValidationError) as browser_error:
            self.manager._validate_resume_for_task()
        self.assertEqual(browser_error.exception.code, "BROWSER_NOT_BOUND")

        gate_cases = [
            (
                {"targetBound": False},
                "BROWSER_NOT_BOUND",
            ),
            (
                {
                    "targetBound": True,
                    "captchaVisible": True,
                    "authenticated": True,
                },
                "MANUAL_VERIFICATION_REQUIRED",
            ),
            (
                {
                    "targetBound": True,
                    "captchaVisible": False,
                    "loginVisible": True,
                    "authenticated": True,
                },
                "LOGIN_REQUIRED",
            ),
            (
                {
                    "targetBound": True,
                    "captchaVisible": False,
                    "loginVisible": False,
                    "authenticated": False,
                },
                "LOGIN_REQUIRED",
            ),
        ]
        for state, expected_code in gate_cases:
            with self.subTest(expected_code=expected_code, state=state):
                self.bridge.state.side_effect = None
                self.bridge.state.return_value = state
                with self.assertRaises(ValidationError) as gate_error:
                    self.manager._validate_resume_for_task()
                self.assertEqual(gate_error.exception.code, expected_code)

        self.bridge.state.return_value = {
            "targetBound": True,
            "captchaVisible": False,
            "loginVisible": False,
            "authenticated": True,
        }
        with patch(
            "pkulaw_engine.batch_task.PkulawAdapter"
        ) as adapter_type:
            adapter_type.return_value.diagnose_current_page.side_effect = (
                RuntimeError("selector failed")
            )
            with self.assertRaises(ValidationError) as selector_error:
                self.manager._validate_resume_for_task()
            self.assertEqual(
                selector_error.exception.code,
                "PAGE_SELECTOR_MISMATCH",
            )

            adapter_type.return_value.diagnose_current_page.side_effect = None
            adapter_type.return_value.diagnose_current_page.return_value = {}
            with self.assertRaises(ValidationError) as undetected:
                self.manager._validate_resume_for_task()
            self.assertEqual(
                undetected.exception.code,
                "PAGE_SELECTOR_MISMATCH",
            )

            adapter_type.return_value.diagnose_current_page.return_value = {
                "detected": True,
                "profileId": "fixture",
            }
            self.manager._validate_resume_for_task()


if __name__ == "__main__":
    unittest.main()
