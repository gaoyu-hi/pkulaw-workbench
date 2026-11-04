from __future__ import annotations

import unittest

from pkulaw_engine.batch_execution import (
    BatchExecutionContext,
    BatchExecutionContextError,
)
from pkulaw_engine.sources.search_transaction import SearchSession


def _session(
    *,
    query: str = "批量测试",
    library: str = "central",
    year: str = "全部年份",
    search_id: str = "search-1",
    page: int = 1,
    signature: str = "sig-1",
) -> SearchSession:
    return SearchSession(
        search_id=search_id,
        query=query,
        match_mode="keyword",
        library=library,
        year=year,
        province="全部省份",
        timeliness="全部（仅记录元数据，不筛选）",
        requested_page_size=50,
        actual_page_size=50,
        page=page,
        total_pages=2,
        result_count=94,
        row_count=50,
        grouping_mode="ungrouped",
        result_signature=signature,
        observed_at="2026-11-01T00:00:00Z",
        url=f"https://example.test/search?page={page}",
        actual_year=year,
        actual_province="全部省份",
        actual_timeliness="全部（仅记录元数据，不筛选）",
    )


def _contexts() -> list[dict[str, object]]:
    return [
        {
            "query": "批量测试",
            "library": "central",
            "year": "全部年份",
            "searchId": "search-1",
            "pages": [
                {"page": 1, "resultSignature": "sig-1"},
                {"page": 2, "resultSignature": "sig-2"},
            ],
            "batchKeys": ["batch-1", "batch-2"],
            "lastPage": 2,
        },
        {
            "query": "另一查询",
            "library": "central",
            "year": "全部年份",
            "searchId": "search-2",
            "pages": [{"page": 1, "resultSignature": "other-sig"}],
            "batchKeys": ["batch-3"],
            "lastPage": 1,
        },
    ]


class BatchExecutionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = ("批量测试", "central", "全部年份")
        self.execution = BatchExecutionContext(_contexts())

    def test_exact_current_page_is_reused(self) -> None:
        self.execution.bind_current(self.key, _session())
        decision = self.execution.decide(self.key, 1)
        self.assertEqual(decision.action, "reuse")
        self.assertEqual(self.execution.decide(self.key, 1).action, "reuse")

    def test_current_page_before_target_moves_forward(self) -> None:
        self.execution.bind_current(self.key, _session())
        decision = self.execution.decide(self.key, 2)
        self.assertEqual(decision.action, "forward")
        self.execution.accept_forward_page(
            self.key,
            _session(page=2, signature="sig-2"),
        )
        self.assertEqual(self.execution.decide(self.key, 2).action, "reuse")

    def test_current_page_after_target_requires_research(self) -> None:
        self.execution.bind_current(
            self.key,
            _session(page=2, signature="sig-2"),
        )
        self.assertEqual(
            self.execution.decide(self.key, 1).action,
            "research",
        )

    def test_different_combination_requires_research(self) -> None:
        self.execution.bind_current(self.key, _session())
        self.assertEqual(
            self.execution.decide(("另一查询", "central", "全部年份"), 1).action,
            "research",
        )

    def test_stale_search_id_requires_research(self) -> None:
        self.execution.bind_current(self.key, _session())
        self.execution.current_session = _session(search_id="stale")
        self.assertEqual(self.execution.decide(self.key, 1).action, "research")

    def test_signature_change_requires_research(self) -> None:
        self.execution.bind_current(self.key, _session())
        self.execution.current_session = _session(signature="changed")
        self.assertEqual(self.execution.decide(self.key, 1).action, "research")

    def test_bind_rejects_wrong_request_identity(self) -> None:
        with self.assertRaises(BatchExecutionContextError) as context:
            self.execution.bind_current(
                self.key,
                _session(query="错误查询"),
            )
        self.assertEqual(context.exception.code, "RESULTS_STALE")

    def test_forward_rejects_unexpected_signature(self) -> None:
        self.execution.bind_current(self.key, _session())
        with self.assertRaises(BatchExecutionContextError) as context:
            self.execution.accept_forward_page(
                self.key,
                _session(page=2, signature="unexpected"),
            )
        self.assertEqual(context.exception.code, "BATCH_RESULTS_CHANGED")


if __name__ == "__main__":
    unittest.main()
