from __future__ import annotations

import unittest
from types import SimpleNamespace

from pkulaw_engine.scan_journal import scan_universe_fingerprint
from pkulaw_engine.search_governor import SearchActionGovernor
from pkulaw_engine.search_recovery import (
    CheckpointedActionResultRunner,
    SearchRecoveryBoundaryRequired,
    build_pending_search_action,
)
from pkulaw_engine.sources.pkulaw import ManualActionRequired
from pkulaw_engine.sources.search_transaction import SearchRequestIdentity
from pkulaw_engine.task_capabilities import derive_task_capabilities
from pkulaw_engine.task_fresh_start import build_fresh_task_seed


class FakeStore:
    def __init__(self) -> None:
        self.task = {"id": "task-1", "checkpoint": {}}

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


class RecoveryLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore()
        self.request = SearchRequestIdentity(
            query="人工智能",
            match_mode="keyword",
            library="central",
            year="全部年份",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=20,
        )
        self.action = build_pending_search_action(
            task_id="task-1",
            kind="keyword-submission",
            request=self.request,
            expected_page=1,
            previous_result_signature="",
            target={"year": "全部年份"},
        )
        self.governor = SearchActionGovernor(
            clock=lambda: 100.0,
            sleep=lambda _seconds: None,
        )

    def _runner(self) -> CheckpointedActionResultRunner:
        return CheckpointedActionResultRunner(
            self.store,
            "task-1",
            self.governor,
        )

    def test_captcha_reuses_original_result_without_resubmit(self) -> None:
        execute_calls = 0

        def execute() -> dict[str, object]:
            nonlocal execute_calls
            execute_calls += 1
            raise ManualActionRequired("captcha")

        state = self._runner().run(
            self.action,
            execute=execute,
            observe=lambda: {
                "page": 1,
                "searchSession": {"query": "人工智能"},
            },
            wait_for_manual=lambda *_args: True,
        )

        self.assertEqual(execute_calls, 1)
        self.assertEqual(state["page"], 1)
        recovery = self.store.task["checkpoint"]["searchRecovery"]
        self.assertTrue(recovery["reusedResult"])

    def test_unapplied_action_retries_once_then_boundary(self) -> None:
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

    def test_page_mode_is_outside_scan_universe_identity(self) -> None:
        base = {
            "queries": ["人工智能"],
            "pageSize": 20,
        }
        last = {
            **base,
            "pageMode": "last",
            "pageSelection": {"mode": "last"},
        }
        limit = {
            **base,
            "pageMode": "limit",
            "pageSelection": {"mode": "limit", "maxPages": 1},
            "maxPages": 1,
        }

        self.assertEqual(
            scan_universe_fingerprint(last),
            scan_universe_fingerprint(limit),
        )

    def test_terminal_task_seed_preserves_old_task_and_strips_identity(
        self,
    ) -> None:
        task = {
            "id": "old-task",
            "mode": "single",
            "status": "stopped",
            "error": {"code": "TASK_STOPPED", "message": "stopped"},
            "checkpoint": {"currentPage": 2},
            "archivedAt": None,
            "outputDir": "D:/output",
            "input": {
                "queries": ["残疾人体育"],
                "matchMode": "keyword",
                "library": "local",
                "pageMode": "last",
                "searchId": "old-search",
                "previewFingerprint": "old-preview",
            },
        }
        capabilities = derive_task_capabilities(
            task,
            controller_alive=False,
            lease={"taskId": "", "leaseId": ""},
        )
        seed = build_fresh_task_seed(
            task,
            may_duplicate_downloads=True,
        )

        self.assertTrue(capabilities.can_create_fresh_task)
        self.assertTrue(seed.old_task_preserved)
        self.assertEqual(seed.old_task_id, "old-task")
        self.assertNotIn("searchId", seed.input)
        self.assertNotIn("previewFingerprint", seed.input)
        self.assertTrue(seed.may_duplicate_downloads)


if __name__ == "__main__":
    unittest.main()
