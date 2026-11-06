from __future__ import annotations

import unittest
from types import SimpleNamespace

from pkulaw_engine.search_recovery import (
    CheckpointedActionResultRunner,
    PendingSearchAction,
    SearchRecoveryBoundaryRequired,
    build_pending_search_action,
    pending_action_for_request,
    pending_action_from_checkpoint,
    pending_action_to_checkpoint,
)
from pkulaw_engine.search_governor import SearchActionGovernor
from pkulaw_engine.sources.pkulaw import ManualActionRequired
from pkulaw_engine.sources.search_transaction import SearchRequestIdentity


class PendingSearchActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = PendingSearchAction(
            action_id="action-1",
            kind="year-filter-switch",
            request=SearchRequestIdentity(
                query="人工智能",
                match_mode="keyword",
                library="central",
                year="2024",
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                requested_page_size=20,
            ),
            expected_page=1,
            previous_result_signature="previous-signature",
            target={"year": "2024"},
            attempt_count=1,
            created_at="2026-11-06T00:00:00Z",
        )

    def test_pending_action_round_trip_preserves_original_identity(self) -> None:
        restored = pending_action_from_checkpoint(
            pending_action_to_checkpoint(self.action)
        )

        self.assertEqual(restored, self.action)

    def test_malformed_checkpoint_returns_none(self) -> None:
        for payload in (
            None,
            {},
            {"kind": "next-page"},
            {"actionId": "", "kind": "next-page"},
            {
                "actionId": "action-1",
                "kind": "not-an-action",
                "request": self.action.request.to_dict(),
                "expectedPage": 1,
            },
            {
                "actionId": "action-1",
                "kind": "next-page",
                "request": {"query": "incomplete"},
                "expectedPage": 1,
            },
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(
                    pending_action_from_checkpoint(payload)
                )

    def test_checkpoint_uses_protocol_camel_case(self) -> None:
        payload = pending_action_to_checkpoint(self.action)

        self.assertEqual(payload["actionId"], "action-1")
        self.assertEqual(payload["expectedPage"], 1)
        self.assertEqual(payload["attemptCount"], 1)
        self.assertEqual(payload["request"]["query"], "人工智能")
        self.assertNotIn("action_id", payload)

    def test_checkpoint_supplies_pending_action_for_same_result_scope(
        self,
    ) -> None:
        target = {"year": "2024"}
        request = self.action.request
        action = build_pending_search_action(
            task_id="task-1",
            kind="year-filter-switch",
            request=request,
            expected_page=1,
            previous_result_signature="previous",
            target=target,
        )

        restored = pending_action_for_request(
            {
                "searchRecovery": {
                    "action": pending_action_to_checkpoint(action),
                }
            },
            request=request,
            expected_page=1,
            target=target,
        )

        self.assertEqual(restored, action)
        self.assertEqual(restored.kind, "year-filter-switch")

    def test_checkpoint_does_not_supply_action_for_other_scope(
        self,
    ) -> None:
        request = self.action.request
        action = build_pending_search_action(
            task_id="task-1",
            kind="year-filter-switch",
            request=request,
            expected_page=1,
            previous_result_signature="previous",
            target={"year": "2024"},
        )

        restored = pending_action_for_request(
            {
                "searchRecovery": {
                    "action": pending_action_to_checkpoint(action),
                }
            },
            request=request,
            expected_page=1,
            target={"year": "2022"},
        )

        self.assertIsNone(restored)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeRecoveryStore:
    def __init__(self, task_id: str) -> None:
        self.task = {
            "id": task_id,
            "checkpoint": {},
        }

    def get_task(self, task_id: str) -> dict[str, object] | None:
        return self.task if task_id == self.task["id"] else None

    def update_task(
        self,
        task_id: str,
        *,
        checkpoint: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> dict[str, object]:
        if task_id != self.task["id"]:
            raise AssertionError("unexpected task")
        if checkpoint is not None:
            self.task["checkpoint"] = checkpoint
        return self.task


class CheckpointedActionResultRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeRecoveryStore("task-1")
        self.action = build_pending_search_action(
            task_id="task-1",
            kind="keyword-submission",
            request=SearchRequestIdentity(
                query="人工智能",
                match_mode="keyword",
                library="central",
                year="全部年份",
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                requested_page_size=20,
            ),
            expected_page=1,
            previous_result_signature="",
            target={"year": "全部年份"},
        )
        self.governor = SearchActionGovernor(
            clock=FakeClock(),
            sleep=lambda _: None,
        )

    def _runner(
        self,
        governor: SearchActionGovernor | None = None,
    ) -> CheckpointedActionResultRunner:
        return CheckpointedActionResultRunner(
            self.store,
            "task-1",
            governor or self.governor,
        )

    def test_runner_reuses_after_manual_without_second_execute(self) -> None:
        execute_calls = 0
        observed_states = [
            {"page": 1, "searchSession": {"query": "人工智能"}},
        ]

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            raise ManualActionRequired("captcha")

        def observe() -> dict[str, object] | None:
            return observed_states.pop(0)

        result = self._runner().run(
            self.action,
            execute=execute,
            observe=observe,
            wait_for_manual=lambda *_args: True,
        )

        self.assertEqual(execute_calls, 1)
        self.assertEqual(result["searchSession"]["query"], "人工智能")
        recovery = self.store.task["checkpoint"]["searchRecovery"]
        self.assertTrue(recovery["reusedResult"])
        self.assertEqual(recovery["phase"], "reused")

    def test_runner_retries_only_the_original_action_once(self) -> None:
        execute_calls = 0

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            if execute_calls == 1:
                raise ManualActionRequired("captcha")
            return {"page": 1, "searchSession": {"query": "人工智能"}}

        result = self._runner().run(
            self.action,
            execute=execute,
            observe=lambda: None,
            wait_for_manual=lambda *_args: True,
        )

        self.assertEqual(execute_calls, 2)
        self.assertEqual(result["page"], 1)
        recovery = self.store.task["checkpoint"]["searchRecovery"]
        self.assertEqual(recovery["attemptCount"], 1)
        self.assertEqual(recovery["phase"], "completed")

    def test_runner_second_captcha_on_same_action_reaches_boundary(
        self,
    ) -> None:
        execute_calls = 0

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            raise ManualActionRequired("captcha")

        with self.assertRaises(SearchRecoveryBoundaryRequired):
            self._runner().run(
                self.action,
                execute=execute,
                observe=lambda: None,
                wait_for_manual=lambda *_args: True,
            )

        self.assertEqual(execute_calls, 2)
        recovery = self.store.task["checkpoint"]["searchRecovery"]
        self.assertEqual(recovery["phase"], "boundary")
        self.assertEqual(
            recovery["breakerReason"],
            "same-action-captcha",
        )

    def test_captcha_reappearing_during_reuse_observation_reaches_boundary(
        self,
    ) -> None:
        execute_calls = 0

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            raise ManualActionRequired("captcha")

        with self.assertRaises(SearchRecoveryBoundaryRequired):
            self._runner().run(
                self.action,
                execute=execute,
                observe=lambda: (_ for _ in ()).throw(
                    ManualActionRequired("captcha reappeared")
                ),
                wait_for_manual=lambda *_args: True,
            )

        self.assertEqual(execute_calls, 1)
        recovery = self.store.task["checkpoint"]["searchRecovery"]
        self.assertEqual(recovery["phase"], "boundary")
        self.assertEqual(
            recovery["breakerReason"],
            "same-action-captcha",
        )

    def test_runner_restore_observes_pending_action_before_execute(
        self,
    ) -> None:
        first_calls = 0

        def first_execute() -> dict[str, object]:
            nonlocal first_calls
            first_calls += 1
            raise ManualActionRequired("captcha")

        with self.assertRaises(ManualActionRequired):
            self._runner().run(
                self.action,
                execute=first_execute,
                observe=lambda: None,
                wait_for_manual=lambda *_args: (_ for _ in ()).throw(
                    ManualActionRequired("captcha still visible")
                ),
            )

        snapshot = self.store.task["checkpoint"]["searchRecovery"]
        restored_governor = SearchActionGovernor.restore(
            snapshot["governor"],
            clock=FakeClock(111.0),
            sleep=lambda _: None,
        )
        execute_calls = 0

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            return {"page": 1}

        result = self._runner(restored_governor).run(
            self.action,
            execute=execute,
            observe=lambda: {
                "page": 1,
                "searchSession": {"query": "人工智能"},
            },
            wait_for_manual=lambda *_args: True,
        )

        self.assertEqual(first_calls, 1)
        self.assertEqual(execute_calls, 0)
        self.assertEqual(result["page"], 1)

    def test_restored_pending_action_reuses_result_after_rebuild(
        self,
    ) -> None:
        first_calls = 0

        def first_execute() -> dict[str, object]:
            nonlocal first_calls
            first_calls += 1
            raise ManualActionRequired("captcha")

        with self.assertRaises(ManualActionRequired):
            self._runner().run(
                self.action,
                execute=first_execute,
                observe=lambda: None,
                wait_for_manual=lambda *_args: (_ for _ in ()).throw(
                    ManualActionRequired("captcha still visible")
                ),
            )

        rebuilt = PendingSearchAction(
            action_id=self.action.action_id,
            kind=self.action.kind,
            request=self.action.request,
            expected_page=self.action.expected_page,
            previous_result_signature=self.action.previous_result_signature,
            target=self.action.target,
            attempt_count=self.action.attempt_count,
            created_at="2026-11-07T00:00:00Z",
        )
        execute_calls = 0

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            return {"page": 1}

        result = self._runner().run(
            rebuilt,
            execute=execute,
            observe=lambda: {
                "page": 1,
                "searchSession": {"query": "人工智能"},
            },
            wait_for_manual=lambda *_args: True,
        )

        self.assertEqual(first_calls, 1)
        self.assertEqual(execute_calls, 0)
        self.assertEqual(result["page"], 1)

    def test_interrupted_retry_does_not_execute_a_third_time(
        self,
    ) -> None:
        retry_action = PendingSearchAction(
            action_id=self.action.action_id,
            kind=self.action.kind,
            request=self.action.request,
            expected_page=self.action.expected_page,
            previous_result_signature=self.action.previous_result_signature,
            target=self.action.target,
            attempt_count=1,
            created_at=self.action.created_at,
        )
        self.store.task["checkpoint"] = {
            "searchRecovery": {
                "phase": "retrying",
                "eventId": "recovery-1",
                "action": pending_action_to_checkpoint(retry_action),
                "governor": self.governor.snapshot(),
            }
        }
        execute_calls = 0

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            return {"page": 1}

        with self.assertRaises(SearchRecoveryBoundaryRequired):
            self._runner().run(
                self.action,
                execute=execute,
                observe=lambda: None,
                wait_for_manual=lambda *_args: True,
            )

        self.assertEqual(execute_calls, 0)
        recovery = self.store.task["checkpoint"]["searchRecovery"]
        self.assertEqual(recovery["phase"], "boundary")
        self.assertEqual(
            recovery["breakerReason"],
            "automatic-retry-already-attempted",
        )


if __name__ == "__main__":
    unittest.main()
