from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pkulaw_engine.application import EngineApplication
from pkulaw_engine.batch_task import BatchTaskManager
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.manual_recovery import decide_manual_recovery
from pkulaw_engine.single_task import SingleTaskManager
from pkulaw_engine.storage import TaskStore, ValidationError


class ManualRecoveryDecisionTests(unittest.TestCase):
    def test_only_access_waits_can_continue_or_restart(self) -> None:
        base = {
            "status": "waiting_manual",
            "error": {"code": "MANUAL_ACTION_REQUIRED", "message": "captcha"},
        }
        self.assertEqual(
            decide_manual_recovery(base, controller_alive=True).action,
            "continue",
        )
        self.assertEqual(
            decide_manual_recovery(base, controller_alive=False).action,
            "restart",
        )
        for code in (
            "PAGE_LIMIT_REACHED",
            "BATCH_SCALE_CONFIRMATION_REQUIRED",
            "CANDIDATE_SELECTION_REQUIRED",
            "INSTITUTION_QUOTA_REACHED",
        ):
            task = {
                "status": "waiting_manual",
                "error": {"code": code, "message": code},
            }
            self.assertEqual(
                decide_manual_recovery(task, controller_alive=True).action,
                "none",
            )


class ManualRecoveryApplicationTests(unittest.TestCase):
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
            name="验证码恢复",
            mode="single",
            task_input={"queries": ["人工智能"]},
            output_dir=str(self.root / "output"),
        )
        self.store.update_task(
            str(self.task["id"]),
            status="waiting_manual",
            current_stage="waiting-captcha",
            error={
                "code": "MANUAL_ACTION_REQUIRED",
                "message": "请人工完成验证码",
            },
        )

    def tearDown(self) -> None:
        self.application.browser_lease.reset("test_teardown")
        self.store.close()
        self.tempdir.cleanup()

    def _details(self) -> dict[str, object]:
        task = self.store.get_task(str(self.task["id"]))
        return {
            "task": task,
            "items": self.store.list_task_items(str(self.task["id"])),
        }

    def test_live_controller_continues_after_browser_recheck(self) -> None:
        with (
            patch.object(
                self.application.browser_bridge,
                "user_continue",
                return_value={"captchaVisible": False},
            ) as browser_continue,
            patch.object(
                self.application.single_manager,
                "has_live_controller",
                return_value=True,
            ),
            patch.object(
                self.application.single_manager,
                "continue_task",
                side_effect=lambda task_id: self._details(),
            ) as continue_task,
        ):
            response, events = self.application._dispatch(
                "manual-continue",
                "task.resume-after-manual",
                {"taskId": self.task["id"], "operationId": "manual-1"},
            )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["action"], "continue")
        browser_continue.assert_called_once()
        continue_task.assert_called_once_with(str(self.task["id"]))
        self.assertEqual(
            [event["event"] for event in events],
            ["task.manual-recovered"],
        )

    def test_dead_controller_restarts_same_task(self) -> None:
        with (
            patch.object(
                self.application.browser_bridge,
                "user_continue",
                return_value={"captchaVisible": False},
            ),
            patch.object(
                self.application.single_manager,
                "has_live_controller",
                return_value=False,
            ),
            patch.object(
                self.application.single_manager,
                "restart_after_access_wait",
                side_effect=lambda task_id, **_: self._details(),
            ) as restart,
        ):
            response, _ = self.application._dispatch(
                "manual-restart",
                "task.resume-after-manual",
                {"taskId": self.task["id"], "operationId": "manual-2"},
            )

        self.assertEqual(response["result"]["action"], "restart")
        restart.assert_called_once()

    def test_non_access_decision_never_calls_browser_continue(self) -> None:
        self.store.update_task(
            str(self.task["id"]),
            error={"code": "PAGE_LIMIT_REACHED", "message": "page limit"},
        )
        with (
            patch.object(
                self.application.browser_bridge,
                "user_continue",
            ) as browser_continue,
            patch.object(
                self.application.single_manager,
                "has_live_controller",
                return_value=True,
            ),
        ):
            response, _ = self.application._dispatch(
                "manual-none",
                "task.resume-after-manual",
                {"taskId": self.task["id"], "operationId": "manual-3"},
            )

        self.assertEqual(response["result"]["action"], "none")
        browser_continue.assert_not_called()

    def test_same_operation_rechecks_browser_only_once(self) -> None:
        with (
            patch.object(
                self.application.browser_bridge,
                "user_continue",
                return_value={"captchaVisible": False},
            ) as browser_continue,
            patch.object(
                self.application.single_manager,
                "has_live_controller",
                return_value=True,
            ),
            patch.object(
                self.application.single_manager,
                "continue_task",
                side_effect=lambda task_id: self._details(),
            ),
        ):
            first, _ = self.application._dispatch(
                "manual-once-a",
                "task.resume-after-manual",
                {"taskId": self.task["id"], "operationId": "manual-once"},
            )
            second, _ = self.application._dispatch(
                "manual-once-b",
                "task.resume-after-manual",
                {"taskId": self.task["id"], "operationId": "manual-once"},
            )

        self.assertEqual(first["result"]["action"], "continue")
        self.assertEqual(second["result"]["action"], "continue")
        browser_continue.assert_called_once()

    def test_stale_recovery_token_cannot_wake_current_wait(self) -> None:
        self.store.update_task(
            str(self.task["id"]),
            checkpoint={
                "searchRecovery": {
                    "version": 1,
                    "eventId": "recovery-current",
                    "phase": "waiting",
                    "action": {
                        "actionId": "action-current",
                    },
                }
            },
        )
        with patch.object(
            self.application.browser_bridge,
            "user_continue",
        ) as browser_continue:
            with self.assertRaises(ValidationError) as context:
                self.application._resume_after_manual(
                    str(self.task["id"]),
                    operation_id="old-request",
                    recovery_token="recovery-already-consumed",
                )

        self.assertEqual(
            context.exception.code,
            "MANUAL_RECOVERY_STALE",
        )
        browser_continue.assert_not_called()

    def test_current_recovery_token_is_accepted_before_continue(self) -> None:
        task_id = str(self.task["id"])
        self.store.update_task(
            task_id,
            checkpoint={
                "searchRecovery": {
                    "version": 1,
                    "eventId": "recovery-current",
                    "phase": "waiting",
                    "action": {
                        "actionId": "action-current",
                    },
                }
            },
        )
        accepted_events: list[str] = []

        def continue_task(_task_id: str) -> dict[str, object]:
            current = self.store.get_task(task_id)
            accepted_events.append(
                str(
                    dict(
                        (current or {}).get("checkpoint") or {}
                    )
                    .get("manualRecovery", {})
                    .get("acceptedEventId")
                    or ""
                )
            )
            return self._details()

        with (
            patch.object(
                self.application.browser_bridge,
                "user_continue",
                return_value={"captchaVisible": False},
            ),
            patch.object(
                self.application.single_manager,
                "has_live_controller",
                return_value=True,
            ),
            patch.object(
                self.application.single_manager,
                "continue_task",
                side_effect=continue_task,
            ),
        ):
            result = self.application._resume_after_manual(
                task_id,
                operation_id="current-request",
                recovery_token="recovery-current",
            )

        self.assertEqual(result["action"], "continue")
        self.assertEqual(accepted_events, ["recovery-current"])


class ManualRecoveryRestartValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.logger = JsonLineLogger(self.root / "engine.jsonl")
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.browser_bridge = BrowserBridge(self.logger)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _waiting_task(self, mode: str) -> dict[str, object]:
        task = self.store.create_task(
            name=f"{mode}验证码恢复",
            mode=mode,
            task_input={"queries": ["人工智能"], "matchMode": "keyword"},
            output_dir=str(self.root / "output"),
        )
        task = self.store.update_task(
            str(task["id"]),
            status="waiting_manual",
            current_stage="waiting-captcha",
            error={
                "code": "MANUAL_ACTION_REQUIRED",
                "message": "请人工完成验证码",
            },
        )
        assert task is not None
        return task

    def test_single_restart_preserves_failed_items_and_requires_access_wait(
        self,
    ) -> None:
        task = self._waiting_task("single")
        task_id = str(task["id"])
        self.store.upsert_task_item(
            task_id,
            policy_key="id:failed-policy",
            title="失败政策",
            status="failed",
            error={"code": "ITEM_FAILED", "message": "failed"},
        )
        manager = SingleTaskManager(
            self.store.database_path,
            self.browser_bridge,
            self.logger,
            lambda _event, _data: None,
        )
        with patch.object(manager, "_run") as run:
            manager.restart_after_access_wait(task_id)
            controller = manager.controllers[task_id]
            assert controller.thread is not None
            controller.thread.join(1)

        run.assert_called_once()
        failed = next(
            item
            for item in self.store.list_task_items(task_id)
            if item["policyKey"] == "id:failed-policy"
        )
        self.assertEqual(failed["status"], "failed")

        self.store.update_task(
            task_id,
            error={"code": "PAGE_LIMIT_REACHED", "message": "page limit"},
        )
        with self.assertRaises(ValidationError) as context:
            manager.restart_after_access_wait(task_id)
        self.assertEqual(context.exception.code, "MANUAL_RECOVERY_NOT_ALLOWED")

    def test_batch_restart_rejects_scale_decisions(self) -> None:
        task = self._waiting_task("batch")
        task_id = str(task["id"])
        self.store.update_task(
            task_id,
            error={
                "code": "BATCH_SCALE_CONFIRMATION_REQUIRED",
                "message": "confirm scale",
            },
        )
        manager = BatchTaskManager(
            self.store.database_path,
            self.browser_bridge,
            self.logger,
            lambda _event, _data: None,
        )

        with self.assertRaises(ValidationError) as context:
            manager.restart_after_access_wait(task_id)
        self.assertEqual(context.exception.code, "MANUAL_RECOVERY_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
