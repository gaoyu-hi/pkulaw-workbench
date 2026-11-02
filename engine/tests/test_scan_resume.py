from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.scan_journal import ScanJournal, config_fingerprint
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import YearSelection
from pkulaw_engine.single_task import SingleTaskConfig, SingleTaskManager
from pkulaw_engine.sources.search_transaction import (
    SearchSession,
    result_signature,
)
from pkulaw_engine.sources.pkulaw import (
    ResultWindowTruncated,
    SelectorProfileMismatch,
)
from pkulaw_engine.search_window import YearPartitionRequired
from pkulaw_engine.storage import TaskStore


def _rows(page: int) -> list[dict[str, object]]:
    count = 100 if page < 5 else 50
    return [
        {
            "number": (page - 1) * 100 + index + 1,
            "policyId": f"policy-{page}-{index}",
            "title": f"碳中和政策第 {page}-{index} 条",
            "url": (
                "https://example.test/pkulaw/detail/"
                f"policy-{page}-{index}.html"
            ),
        }
        for index in range(count)
    ]


def _state(page: int, search_id: str) -> dict[str, object]:
    rows = _rows(page)
    signature = result_signature(rows)
    session = SearchSession(
        search_id=search_id,
        query="碳中和",
        match_mode="keyword",
        library="central",
        year="2024",
        province="全部省份",
        timeliness="全部（仅记录元数据，不筛选）",
        requested_page_size=100,
        actual_page_size=100,
        page=page,
        total_pages=5,
        result_count=450,
        row_count=len(rows),
        grouping_mode="ungrouped",
        result_signature=signature,
        observed_at="2026-11-02T00:00:00Z",
        url=f"https://example.test/search?page={page}",
        actual_year="2024",
        actual_province="全部省份",
        actual_timeliness="全部（仅记录元数据，不筛选）",
        reported_total_pages=5,
    )
    return {
        "page": page,
        "totalPages": 5,
        "reportedTotalPages": 5,
        "resultCount": 450,
        "actualPageSize": 100,
        "rows": rows,
        "resultSignature": signature,
        "searchSession": session.to_dict(),
        "url": f"https://example.test/search?page={page}",
    }


class _ResumeAdapter:
    def __init__(
        self,
        *,
        fail_page: int | None = None,
        resumed_search_id: str = "search-1",
    ) -> None:
        self.fail_page = fail_page
        self.resumed_search_id = resumed_search_id
        self.search_calls = 0
        self.next_pages: list[int] = []
        self.go_targets: list[int] = []

    def search(self, **_kwargs: object) -> dict[str, object]:
        self.search_calls += 1
        return _state(1, self.resumed_search_id)

    def next_page(self, expected_page: int) -> dict[str, object]:
        self.next_pages.append(expected_page)
        if self.fail_page == expected_page:
            raise RuntimeError("page 5 interrupted")
        return _state(expected_page, self.resumed_search_id)

    def go_to_page(self, target_page: int) -> dict[str, object]:
        self.go_targets.append(target_page)
        return _state(target_page, self.resumed_search_id)


def _partition_state(
    *,
    year: str,
    search_id: str,
) -> dict[str, object]:
    rows = [
        {
            "number": index + 1,
            "policyId": f"{year}-policy-{index}",
            "title": f"{year} 碳中和政策 {index}",
            "url": (
                "https://example.test/pkulaw/detail/"
                f"{year}-policy-{index}.html"
            ),
        }
        for index in range(2)
    ]
    signature = result_signature(rows)
    session = SearchSession(
        search_id=search_id,
        query="碳中和",
        match_mode="keyword",
        library="central",
        year=year,
        province="全部省份",
        timeliness="全部（仅记录元数据，不筛选）",
        requested_page_size=100,
        actual_page_size=100,
        page=1,
        total_pages=1,
        result_count=2,
        row_count=2,
        grouping_mode="ungrouped",
        result_signature=signature,
        observed_at="2026-11-02T00:00:00Z",
        url=f"https://example.test/search?year={year}",
        actual_year=year,
        actual_province="全部省份",
        actual_timeliness="全部（仅记录元数据，不筛选）",
        reported_total_pages=1,
    )
    return {
        "page": 1,
        "totalPages": 1,
        "reportedTotalPages": 1,
        "resultCount": 2,
        "actualPageSize": 100,
        "rows": rows,
        "resultSignature": signature,
        "searchSession": session.to_dict(),
        "url": f"https://example.test/search?year={year}",
    }


class _PartitionAdapter:
    def __init__(self) -> None:
        self.search_years: list[str] = []

    def search(self, **kwargs: object) -> dict[str, object]:
        year = str(kwargs["year"])
        self.search_years.append(year)
        if year == "全部年份":
            rows = [
                {
                    "number": 1,
                    "policyId": "all-policy-1",
                    "title": "碳中和政策 1",
                    "url": "https://example.test/policy-1.html",
                },
                {
                    "number": 2,
                    "policyId": "all-policy-2",
                    "title": "碳中和政策 2",
                    "url": "https://example.test/policy-2.html",
                },
            ]
            signature = result_signature(rows)
            session = SearchSession(
                search_id="all-search",
                query="碳中和",
                match_mode="keyword",
                library="central",
                year="全部年份",
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                requested_page_size=100,
                actual_page_size=100,
                page=1,
                total_pages=2,
                result_count=4,
                row_count=2,
                grouping_mode="ungrouped",
                result_signature=signature,
                observed_at="2026-11-02T00:00:00Z",
                url="https://example.test/search?all",
                actual_year="全部年份",
                actual_province="全部省份",
                actual_timeliness="全部（仅记录元数据，不筛选）",
                reported_total_pages=1,
            )
            return {
                "page": 1,
                "totalPages": 2,
                "reportedTotalPages": 1,
                "resultCount": 4,
                "actualPageSize": 100,
                "rows": rows,
                "resultSignature": signature,
                "searchSession": session.to_dict(),
                "url": "https://example.test/search?all",
            }
        return _partition_state(
            year=year,
            search_id=f"search-{year}",
        )

    def next_page(self, _expected_page: int) -> dict[str, object]:
        raise ResultWindowTruncated("第 21 页被来源窗口拒绝")

    def available_filter_values(self, _key: str) -> list[str]:
        return ["2024", "2023"]


class ScanResumeTests(unittest.TestCase):
    def test_resumed_processing_reinitializes_selector_profile(self) -> None:
        class Adapter:
            selectors: dict[str, object] = {}

            def diagnose_current_page(self) -> dict[str, object]:
                self.selectors = {"detailTitle": ".title"}
                return {"detected": True, "profileId": "fixture"}

        class MissingAdapter:
            selectors: dict[str, object] = {}

            def diagnose_current_page(self) -> dict[str, object]:
                return {"detected": False, "profileId": ""}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = SingleTaskManager(
                root / "pkulaw.sqlite3",
                BrowserBridge(JsonLineLogger(root / "engine.jsonl")),
                JsonLineLogger(root / "manager.jsonl"),
                lambda _event, _data: None,
            )
            adapter = Adapter()

            manager._ensure_processing_selector_profile(  # noqa: SLF001
                adapter  # type: ignore[arg-type]
            )

            self.assertIn("detailTitle", adapter.selectors)
            with self.assertRaises(SelectorProfileMismatch):
                manager._ensure_processing_selector_profile(  # noqa: SLF001
                    MissingAdapter()  # type: ignore[arg-type]
                )

    def test_retry_rebinds_once_and_resumes_at_page_five(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="碳中和断点",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = SingleTaskConfig(
                    queries=("碳中和",),
                    match_mode="keyword",
                    library="central",
                    year_selection=YearSelection(
                        mode="single",
                        year=2024,
                    ),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                )
                controller = TaskControl(task_id)
                first = _ResumeAdapter(fail_page=5)

                with self.assertRaisesRegex(
                    RuntimeError,
                    "page 5 interrupted",
                ):
                    manager._collect(  # noqa: SLF001
                        task_id,
                        config,
                        first,  # type: ignore[arg-type]
                        store,
                        controller,
                    )

                journal = ScanJournal(
                    root
                    / "cache"
                    / "task-scans"
                    / f"{task_id}.jsonl",
                    task_id=task_id,
                    config_fingerprint=config_fingerprint(
                        manager._config_payload(config)  # noqa: SLF001
                    ),
                )
                self.assertEqual(
                    [record["page"] for record in journal.read()],
                    [1, 2, 3, 4],
                )

                second = _ResumeAdapter(
                    resumed_search_id="search-2",
                )
                rows, scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    second,  # type: ignore[arg-type]
                    store,
                    controller,
                )

                self.assertEqual(second.search_calls, 1)
                self.assertEqual(second.go_targets, [5])
                self.assertEqual(second.next_pages, [])
                self.assertEqual(len(rows), 450)
                self.assertEqual(len(scans), 5)
                self.assertEqual(
                    [record["page"] for record in journal.read()],
                    [1, 2, 3, 4, 5],
                )

                manager._seal_scan_journal(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                )
                third = _ResumeAdapter(
                    resumed_search_id="search-3",
                )
                restored_rows, restored_scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    third,  # type: ignore[arg-type]
                    store,
                    controller,
                )
                self.assertEqual(third.search_calls, 0)
                self.assertEqual(third.next_pages, [])
                self.assertEqual(third.go_targets, [])
                self.assertEqual(len(restored_rows), 450)
                self.assertEqual(len(restored_scans), 5)
            finally:
                store.close()

    def test_all_years_split_only_after_direct_window_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="碳中和年份拆分",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = SingleTaskConfig(
                    queries=("碳中和",),
                    match_mode="keyword",
                    library="central",
                    year_selection=YearSelection(mode="all"),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                )
                controller = TaskControl(task_id)
                first = _PartitionAdapter()

                with self.assertRaises(YearPartitionRequired) as context:
                    manager._collect(  # noqa: SLF001
                        task_id,
                        config,
                        first,  # type: ignore[arg-type]
                        store,
                        controller,
                    )
                self.assertEqual(context.exception.years, ("2024", "2023"))
                self.assertEqual(
                    context.exception.expected_result_count,
                    4,
                )

                store.update_task(
                    task_id,
                    checkpoint={
                        "yearPartition": context.exception.to_dict()
                    },
                )
                second = _PartitionAdapter()
                rows, scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    second,  # type: ignore[arg-type]
                    store,
                    controller,
                )

                self.assertEqual(second.search_years, ["2024", "2023"])
                self.assertEqual(len(rows), 4)
                self.assertEqual(len(scans), 2)
                checkpoint = store.get_task(task_id)["checkpoint"]
                self.assertEqual(
                    checkpoint["yearPartition"]["status"],
                    "complete",
                )
                self.assertEqual(
                    checkpoint["yearPartition"]["actualRowsRead"],
                    4,
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
