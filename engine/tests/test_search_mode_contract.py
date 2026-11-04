from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from pkulaw_engine.batch_task import BatchTaskManager
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.single_task import SingleTaskManager
from pkulaw_engine.storage import TaskStore

from pkulaw_engine.sources.search_mode import (
    SEARCH_SEMANTICS_VERSION,
    ensure_current_search_semantics,
    resolve_search_mode,
)
from pkulaw_engine.sources.search_transaction import SearchRequestIdentity
from pkulaw_engine.storage import ValidationError


class SearchModeContractTests(unittest.TestCase):
    def test_search_modes_map_to_exact_pkulaw_fields(self) -> None:
        keyword = resolve_search_mode("keyword")
        fulltext = resolve_search_mode("fulltext")
        exact_title = resolve_search_mode("title")

        self.assertEqual(keyword.search_keyword_type, "Title")
        self.assertEqual(fulltext.search_keyword_type, "FullText")
        self.assertEqual(exact_title.search_keyword_type, "Title")
        self.assertEqual(keyword.match_type, "Exact")
        self.assertEqual(fulltext.match_type, "Exact")
        self.assertEqual(exact_title.match_type, "Exact")
        self.assertEqual(keyword.identity_mode, "keyword")
        self.assertEqual(fulltext.identity_mode, "keyword")
        self.assertEqual(exact_title.identity_mode, "title")

    def test_unknown_mode_fails_closed(self) -> None:
        with self.assertRaises(ValidationError) as context:
            resolve_search_mode("fuzzy")
        self.assertEqual(context.exception.code, "INVALID_MATCH_MODE")

    def test_search_semantics_version_is_two(self) -> None:
        self.assertEqual(SEARCH_SEMANTICS_VERSION, 2)

    def test_legacy_task_input_requires_explicit_upgrade(self) -> None:
        with self.assertRaises(ValidationError) as context:
            ensure_current_search_semantics(
                {"matchMode": "keyword", "queries": ["人工智能"]}
            )
        self.assertEqual(
            context.exception.code,
            "SEARCH_SEMANTICS_UPGRADE_REQUIRED",
        )

    def test_current_task_input_is_accepted(self) -> None:
        ensure_current_search_semantics(
            {
                "matchMode": "keyword",
                "queries": ["人工智能"],
                "searchSemanticsVersion": SEARCH_SEMANTICS_VERSION,
            }
        )

    def test_request_identity_only_accepts_the_resolved_field(self) -> None:
        def request(match_mode: str) -> SearchRequestIdentity:
            return SearchRequestIdentity(
                query="人工智能",
                match_mode=match_mode,
                library="central",
                year="全部年份",
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                requested_page_size=20,
            )

        self.assertEqual(request("keyword").expected_search_types, ("Title",))
        self.assertEqual(request("fulltext").expected_search_types, ("FullText",))
        self.assertEqual(request("title").expected_search_types, ("Title",))


class LegacySearchTaskGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.logger = JsonLineLogger(self.root / "engine.jsonl")
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.browser_bridge = BrowserBridge(self.logger)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _legacy_task(self, mode: str) -> dict[str, object]:
        task = self.store.create_task(
            name=f"{mode}-legacy-search",
            mode=mode,
            task_input={
                "queries": ["人工智能"],
                "matchMode": "keyword",
                "outputDir": str(self.root / "output"),
            },
            output_dir=str(self.root / "output"),
        )
        legacy_input = dict(task["input"])
        legacy_input.pop("searchSemanticsVersion", None)
        self.store.connection.execute(
            "UPDATE tasks SET input_json = ? WHERE id = ?",
            (json.dumps(legacy_input, ensure_ascii=False), task["id"]),
        )
        self.store.connection.commit()
        current = self.store.get_task(str(task["id"]))
        assert current is not None
        return current

    def test_single_start_rejects_legacy_meaning_before_retry_reset(self) -> None:
        task = self._legacy_task("single")
        task_id = str(task["id"])
        self.store.upsert_task_item(
            task_id,
            policy_key="id:legacy-failed",
            title="旧失败项",
            status="failed",
        )
        manager = SingleTaskManager(
            self.store.database_path,
            self.browser_bridge,
            self.logger,
            lambda _event, _data: None,
        )

        with self.assertRaises(ValidationError) as context:
            manager.start(task_id, retry_failed=True)

        self.assertEqual(
            context.exception.code,
            "SEARCH_SEMANTICS_UPGRADE_REQUIRED",
        )
        item = self.store.list_task_items(task_id)[0]
        self.assertEqual(item["status"], "failed")

    def test_batch_start_rejects_legacy_meaning(self) -> None:
        task = self._legacy_task("batch")
        manager = BatchTaskManager(
            self.store.database_path,
            self.browser_bridge,
            self.logger,
            lambda _event, _data: None,
        )

        with self.assertRaises(ValidationError) as context:
            manager.start(str(task["id"]))

        self.assertEqual(
            context.exception.code,
            "SEARCH_SEMANTICS_UPGRADE_REQUIRED",
        )


if __name__ == "__main__":
    unittest.main()
