from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.batch_task import BatchTaskConfig, BatchTaskManager
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.scan_journal import ScanJournal, config_fingerprint
from pkulaw_engine.search_window import YearPartitionRequired
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import YearSelection
from pkulaw_engine.sources.search_transaction import (
    SearchSession,
    result_signature,
)
from pkulaw_engine.sources.pkulaw import ResultWindowTruncated
from pkulaw_engine.storage import TaskStore


def _batch_state(page: int, search_id: str) -> dict[str, object]:
    rows = [
        {
            "number": page,
            "policyId": f"batch-policy-{page}",
            "title": f"碳中和批次政策 {page}",
            "url": (
                "https://example.test/pkulaw/detail/"
                f"batch-policy-{page}.html"
            ),
        }
    ]
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
        result_count=5,
        row_count=1,
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
        "resultCount": 5,
        "actualPageSize": 100,
        "rows": rows,
        "resultSignature": signature,
        "searchSession": session.to_dict(),
        "url": f"https://example.test/search?page={page}",
    }


class _BatchResumeAdapter:
    def __init__(
        self,
        *,
        fail_page: int | None = None,
        search_id: str = "search-1",
    ) -> None:
        self.fail_page = fail_page
        self.search_id = search_id
        self.search_calls = 0
        self.go_targets: list[int] = []

    def search(self, **_kwargs: object) -> dict[str, object]:
        self.search_calls += 1
        return _batch_state(1, self.search_id)

    def go_to_page(self, target_page: int) -> dict[str, object]:
        self.go_targets.append(target_page)
        if self.fail_page == target_page:
            raise RuntimeError("batch page 5 interrupted")
        return _batch_state(target_page, self.search_id)


class _MissingIdBatchAdapter:
    def search(self, **_kwargs: object) -> dict[str, object]:
        rows = [
            {
                "number": 1,
                "title": "缺少稳定 ID 的碳中和政策",
                "url": "https://example.test/search/no-stable-id",
            }
        ]
        signature = result_signature(rows)
        session = SearchSession(
            search_id="search-missing-id",
            query="碳中和",
            match_mode="keyword",
            library="central",
            year="2024",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=100,
            actual_page_size=100,
            page=1,
            total_pages=1,
            result_count=1,
            row_count=1,
            grouping_mode="ungrouped",
            result_signature=signature,
            observed_at="2026-11-02T00:00:00Z",
            url="https://example.test/search/no-stable-id",
            actual_year="2024",
            actual_province="全部省份",
            actual_timeliness="全部（仅记录元数据，不筛选）",
            reported_total_pages=1,
        )
        return {
            "page": 1,
            "totalPages": 1,
            "reportedTotalPages": 1,
            "resultCount": 1,
            "actualPageSize": 100,
            "rows": rows,
            "resultSignature": signature,
            "searchSession": session.to_dict(),
            "url": "https://example.test/search/no-stable-id",
        }


def _year_single_row_state(
    *,
    year: str,
    search_id: str,
    reported_total_pages: int = 1,
    total_pages: int = 1,
    result_count: int = 1,
) -> dict[str, object]:
    rows = [
        {
            "number": 1,
            "policyId": f"{year}-partition-policy",
            "title": f"{year} 碳中和分区政策",
            "url": (
                "https://example.test/pkulaw/detail/"
                f"{year}-partition-policy.html"
            ),
        }
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
        total_pages=total_pages,
        result_count=result_count,
        row_count=1,
        grouping_mode="ungrouped",
        result_signature=signature,
        observed_at="2026-11-02T00:00:00Z",
        url=f"https://example.test/search?year={year}",
        actual_year=year,
        actual_province="全部省份",
        actual_timeliness="全部（仅记录元数据，不筛选）",
        reported_total_pages=reported_total_pages,
    )
    return {
        "page": 1,
        "totalPages": total_pages,
        "reportedTotalPages": reported_total_pages,
        "resultCount": result_count,
        "actualPageSize": 100,
        "rows": rows,
        "resultSignature": signature,
        "searchSession": session.to_dict(),
        "url": f"https://example.test/search?year={year}",
    }


class _BatchPartitionAdapter:
    def __init__(self) -> None:
        self.search_years: list[str] = []

    def search(self, **kwargs: object) -> dict[str, object]:
        year = str(kwargs["year"])
        self.search_years.append(year)
        if year == "全部年份":
            return _year_single_row_state(
                year=year,
                search_id="all-years",
                reported_total_pages=1,
                total_pages=2,
                result_count=2,
            )
        return _year_single_row_state(
            year=year,
            search_id=f"search-{year}",
        )

    def go_to_page(self, _target_page: int) -> dict[str, object]:
        raise ResultWindowTruncated("第 2 页不被来源窗口接受")

    def available_filter_values(self, _key: str) -> list[str]:
        return ["2024", "2023"]


class BatchScanResumeTests(unittest.TestCase):
    def test_batch_retry_rebinds_once_and_restores_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量碳中和断点",
                    mode="batch",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                store.update_task(
                    task_id,
                    checkpoint={"scaleConfirmed": True},
                )
                manager = BatchTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = BatchTaskConfig(
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
                    catalog_format="xls",
                    full_format="docx",
                )
                controller = TaskControl(task_id)
                first = _BatchResumeAdapter(fail_page=5)

                with self.assertRaisesRegex(
                    RuntimeError,
                    "batch page 5 interrupted",
                ):
                    manager._prepare_batches(  # noqa: SLF001
                        store,
                        task_id,
                        config,
                        first,  # type: ignore[arg-type]
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

                second = _BatchResumeAdapter(search_id="search-2")
                scans = manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    second,  # type: ignore[arg-type]
                    controller,
                )

                self.assertEqual(second.search_calls, 1)
                self.assertEqual(second.go_targets, [5])
                self.assertEqual(len(scans), 5)
                self.assertEqual(len(store.list_task_batches(task_id)), 5)
                self.assertTrue(journal.sealed)

                third = _BatchResumeAdapter(search_id="search-3")
                manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    third,  # type: ignore[arg-type]
                    controller,
                )
                self.assertEqual(third.search_calls, 0)
                self.assertEqual(third.go_targets, [])
            finally:
                store.close()

    def test_batch_all_years_uses_partition_after_window_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量年份拆分",
                    mode="batch",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                store.update_task(
                    task_id,
                    checkpoint={"scaleConfirmed": True},
                )
                manager = BatchTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = BatchTaskConfig(
                    queries=("碳中和",),
                    match_mode="keyword",
                    library="central",
                    year_selection=YearSelection(mode="all"),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                    catalog_format="xls",
                    full_format="docx",
                )
                controller = TaskControl(task_id)
                first = _BatchPartitionAdapter()

                with self.assertRaises(YearPartitionRequired) as context:
                    manager._prepare_batches(  # noqa: SLF001
                        store,
                        task_id,
                        config,
                        first,  # type: ignore[arg-type]
                        controller,
                    )
                self.assertEqual(context.exception.years, ("2024", "2023"))
                self.assertEqual(
                    context.exception.expected_result_count,
                    2,
                )

                store.update_task(
                    task_id,
                    checkpoint={
                        "scaleConfirmed": True,
                        "yearPartition": context.exception.to_dict(),
                    },
                )
                second = _BatchPartitionAdapter()
                manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    second,  # type: ignore[arg-type]
                    controller,
                )

                self.assertEqual(second.search_years, ["2024", "2023"])
                self.assertEqual(len(store.list_task_batches(task_id)), 2)
                checkpoint = store.get_task(task_id)["checkpoint"]
                self.assertEqual(
                    checkpoint["yearPartition"]["actualRowsRead"],
                    2,
                )
            finally:
                store.close()

    def test_batch_missing_policy_id_becomes_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量缺少 ID",
                    mode="batch",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                store.update_task(
                    task_id,
                    checkpoint={"scaleConfirmed": True},
                )
                manager = BatchTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = BatchTaskConfig(
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
                    catalog_format="xls",
                    full_format="docx",
                )

                manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    _MissingIdBatchAdapter(),  # type: ignore[arg-type]
                    TaskControl(task_id),
                )

                self.assertEqual(store.list_task_batches(task_id), [])
                item = store.list_task_items(task_id)[0]
                self.assertEqual(item["status"], "pending_review")
                self.assertEqual(
                    item["error"]["code"],
                    "STABLE_POLICY_ID_MISSING",
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
