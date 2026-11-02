from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.policy import group_results
from pkulaw_engine.single_task import SingleTaskManager
from pkulaw_engine.storage import TaskStore


class TaskIdentityTests(unittest.TestCase):
    def test_keyword_same_title_ids_are_both_prepared_without_choice(self) -> None:
        rows = [
            {
                "policyId": "policy-a",
                "title": "同名碳中和政策",
                "url": "https://example.test/pkulaw/detail/policy-a.html",
            },
            {
                "policyId": "policy-b",
                "title": "同名碳中和政策",
                "url": "https://example.test/pkulaw/detail/policy-b.html",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            try:
                task = store.create_task(
                    name="关键词身份",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(JsonLineLogger(root / "engine.jsonl")),
                    JsonLineLogger(root / "manager.jsonl"),
                    lambda _event, _data: None,
                )
                manager._prepare_items(  # noqa: SLF001
                    store,
                    str(task["id"]),
                    group_results(rows, identity_mode="keyword"),
                    match_mode="keyword",
                    queries=("碳中和",),
                )

                items = store.list_task_items(str(task["id"]))

                self.assertEqual(
                    {item["policyKey"] for item in items},
                    {"id:policy-a", "id:policy-b"},
                )
                self.assertTrue(all(item["status"] == "pending" for item in items))
                self.assertTrue(
                    all(
                        len(item["payload"]["candidates"]) == 1
                        for item in items
                    )
                )
            finally:
                store.close()

    def test_keyword_missing_id_is_pending_review(self) -> None:
        rows = [
            {
                "title": "缺少 ID",
                "url": "https://example.test/search/result-without-id",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            try:
                task = store.create_task(
                    name="关键词身份缺失",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(JsonLineLogger(root / "engine.jsonl")),
                    JsonLineLogger(root / "manager.jsonl"),
                    lambda _event, _data: None,
                )
                manager._prepare_items(  # noqa: SLF001
                    store,
                    str(task["id"]),
                    group_results(rows, identity_mode="keyword"),
                    match_mode="keyword",
                    queries=("碳中和",),
                )

                item = store.list_task_items(str(task["id"]))[0]

                self.assertEqual(item["status"], "pending_review")
                self.assertEqual(
                    item["error"]["code"],
                    "STABLE_POLICY_ID_MISSING",
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
