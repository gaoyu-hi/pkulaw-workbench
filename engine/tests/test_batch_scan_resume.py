from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pkulaw_engine.batch_task import BatchTaskConfig, BatchTaskManager
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.controls.task_control import TaskControl, TaskStopped
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


def _renamed_batch_state(
    page: int,
    search_id: str,
    *,
    prefix: str,
) -> dict[str, object]:
    state = _batch_state(page, search_id)
    rows = [
        {
            **dict(row),
            "policyId": f"{prefix}-{row['policyId']}",
            "url": str(row["url"]).replace(
                "batch-policy-",
                f"{prefix}-batch-policy-",
            ),
        }
        for row in state["rows"]  # type: ignore[union-attr]
    ]
    signature = result_signature(rows)
    session_payload = dict(state["searchSession"])  # type: ignore[arg-type]
    session_payload["resultSignature"] = signature
    state.update(
        {
            "rows": rows,
            "resultSignature": signature,
            "searchSession": session_payload,
        }
    )
    return state


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
    def test_restored_checkpoint_does_not_claim_live_search_session(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量检索上下文恢复",
                    mode="batch",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                session = SearchSession.from_dict(
                    _batch_state(1, "search-old")["searchSession"]
                )
                self.assertIsNotNone(session)
                store.update_task(
                    task_id,
                    checkpoint={
                        "searchIdentity": session.to_dict(),
                    },
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
                adapter = SimpleNamespace(
                    current_search_session=None,
                    current_search_request=None,
                    search_url="",
                )

                manager._restore_adapter_search_context(  # noqa: SLF001
                    adapter,  # type: ignore[arg-type]
                    store,
                    task_id,
                    config,
                )

                self.assertIsNone(adapter.current_search_session)
                self.assertIsNotNone(adapter.current_search_request)
            finally:
                store.close()

    def test_batch_retry_rebinds_each_search_session_segment(self) -> None:
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
                first = _BatchResumeAdapter(fail_page=3)

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

                journal = manager._scan_journal(  # noqa: SLF001
                    task_id,
                    config,
                )
                self.assertEqual(
                    [record["page"] for record in journal.read()],
                    [1, 2],
                )

                second = _BatchResumeAdapter(
                    fail_page=4,
                    search_id="search-2",
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "batch page 5 interrupted",
                ):
                    manager._prepare_batches(  # noqa: SLF001
                        store,
                        task_id,
                        config,
                        second,  # type: ignore[arg-type]
                        controller,
                    )

                self.assertEqual(second.search_calls, 1)
                self.assertEqual(second.go_targets, [3, 4])
                self.assertEqual(
                    [record["page"] for record in journal.read()],
                    [1, 2, 3],
                )

                third = _BatchResumeAdapter(search_id="search-3")
                scans = manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    third,  # type: ignore[arg-type]
                    controller,
                )
                self.assertEqual(third.search_calls, 1)
                self.assertEqual(third.go_targets, [4, 5])
                self.assertEqual(len(scans), 5)
                self.assertEqual(len(store.list_task_batches(task_id)), 5)
                self.assertTrue(journal.sealed)
                final_resume = [
                    record["resumeEvidence"]
                    for record in journal.read()
                    if record.get("resumeEvidence")
                ][-1]
                self.assertEqual(final_resume["page"], 3)
                self.assertEqual(final_resume["searchId"], "search-2")
                self.assertEqual(
                    final_resume["resultSignature"],
                    _batch_state(3, "search-2")["resultSignature"],
                )

                fourth = _BatchResumeAdapter(search_id="search-4")
                manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    fourth,  # type: ignore[arg-type]
                    controller,
                )
                self.assertEqual(fourth.search_calls, 0)
                self.assertEqual(fourth.go_targets, [])
            finally:
                store.close()

    def test_batch_changed_universe_restarts_without_old_rows(
        self,
    ) -> None:
        class ChangedBatchAdapter:
            def __init__(self) -> None:
                self.search_calls = 0
                self.go_targets: list[int] = []

            def search(self, **_kwargs: object) -> dict[str, object]:
                self.search_calls += 1
                return _renamed_batch_state(
                    1,
                    f"batch-new-{self.search_calls}",
                    prefix="new",
                )

            def go_to_page(self, target_page: int) -> dict[str, object]:
                self.go_targets.append(target_page)
                return _renamed_batch_state(
                    target_page,
                    "batch-new-2",
                    prefix="new",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量新结果集重扫",
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
                journal_path = (
                    root
                    / "cache"
                    / "task-scans"
                    / f"{task_id}.jsonl"
                )
                journal = manager._scan_journal(  # noqa: SLF001
                    task_id,
                    config,
                )
                old_state = _batch_state(1, "batch-old")
                old_session = SearchSession.from_dict(
                    old_state["searchSession"]  # type: ignore[arg-type]
                )
                self.assertIsNotNone(old_session)
                assert old_session is not None
                journal.append(
                    {
                        "schemaVersion": 1,
                        "configFingerprint": journal.config_fingerprint,
                        "taskId": task_id,
                        "query": old_session.query,
                        "library": old_session.library,
                        "effectiveYear": old_session.year,
                        "page": 1,
                        "totalPages": old_session.total_pages,
                        "reportedTotalPages": (
                            old_session.reported_total_pages
                        ),
                        "resultCount": old_session.result_count,
                        "requestedPageSize": config.page_size,
                        "actualPageSize": old_session.actual_page_size,
                        "groupingMode": old_session.grouping_mode,
                        "searchId": old_session.search_id,
                        "resultSignature": (
                            old_session.result_signature
                        ),
                        "observedAt": old_session.observed_at,
                        "rows": old_state["rows"],
                    }
                )
                adapter = ChangedBatchAdapter()

                scans = manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    adapter,  # type: ignore[arg-type]
                    TaskControl(task_id),
                )

                self.assertEqual(adapter.search_calls, 2)
                self.assertEqual(adapter.go_targets, [2, 3, 4, 5])
                self.assertEqual(len(scans), 5)
                records = journal.read()
                self.assertEqual(
                    [record["page"] for record in records],
                    [1, 2, 3, 4, 5],
                )
                self.assertTrue(
                    all(
                        str(record["searchId"]).startswith(
                            "batch-new-"
                        )
                        for record in records
                    )
                )
                batches = store.list_task_batches(task_id)
                self.assertEqual(len(batches), 5)
                archived = list(
                    (journal_path.parent / "archive").glob(
                        f"{task_id}*.jsonl"
                    )
                )
                self.assertEqual(len(archived), 1)
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

    def test_batch_all_years_split_after_duplicate_beyond_window(
        self,
    ) -> None:
        class WindowAdapter:
            def __init__(self) -> None:
                self.go_targets: list[int] = []

            @staticmethod
            def _window_state(page: int, rows_page: int) -> dict[str, object]:
                state = _batch_state(rows_page, "batch-window")
                state["page"] = page
                state["totalPages"] = 3
                state["reportedTotalPages"] = 1
                state["resultCount"] = 3
                state["actualYear"] = "全部年份"
                session = dict(state["searchSession"])  # type: ignore[arg-type]
                session.update(
                    {
                        "page": page,
                        "totalPages": 3,
                        "reportedTotalPages": 1,
                        "resultCount": 3,
                        "year": "全部年份",
                        "actualYear": "全部年份",
                    }
                )
                state["searchSession"] = session
                return state

            def search(self, **_kwargs: object) -> dict[str, object]:
                return self._window_state(1, 1)

            def go_to_page(self, target_page: int) -> dict[str, object]:
                self.go_targets.append(target_page)
                return self._window_state(
                    target_page,
                    2 if target_page == 3 else target_page,
                )

            def available_filter_values(self, _key: str) -> list[str]:
                return ["2025", "2024"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量窗口扩展重复后拆分",
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
                adapter = WindowAdapter()

                with self.assertRaises(YearPartitionRequired) as context:
                    manager._prepare_batches(  # noqa: SLF001
                        store,
                        task_id,
                        config,
                        adapter,  # type: ignore[arg-type]
                        TaskControl(task_id),
                    )

                self.assertEqual(adapter.go_targets, [2, 3])
                self.assertEqual(
                    context.exception.years,
                    ("2025", "2024"),
                )
                self.assertIsInstance(
                    context.exception.__cause__,
                    ResultWindowTruncated,
                )
            finally:
                store.close()

    def test_batch_year_window_plan_splits_only_oversized_year(
        self,
    ) -> None:
        class FacetAdapter:
            def __init__(self) -> None:
                self.searches: list[dict[str, object]] = []
                self.current_search_session = type(
                    "Session",
                    (),
                    {
                        "library": "central",
                        "year": "全部年份",
                        "province": "全部省份",
                    },
                )()

            def search(self, **kwargs: object) -> dict[str, object]:
                self.searches.append(kwargs)
                self.current_search_session = type(
                    "Session",
                    (),
                    {
                        "library": str(kwargs["library"]),
                        "year": str(kwargs["year"]),
                        "province": str(kwargs["province"]),
                    },
                )()
                session = SearchSession(
                    search_id="plan-context",
                    query=str(kwargs["keyword"]),
                    match_mode=str(kwargs["match_mode"]),
                    library=str(kwargs["library"]),
                    year=str(kwargs["year"]),
                    province=str(kwargs["province"]),
                    timeliness=str(kwargs["timeliness"]),
                    requested_page_size=int(kwargs["page_size"]),
                    actual_page_size=int(kwargs["page_size"]),
                    page=1,
                    total_pages=1,
                    result_count=0,
                    row_count=0,
                    grouping_mode="ungrouped",
                    result_signature=result_signature([]),
                    observed_at="2026-11-02T00:00:00Z",
                    url="https://example.test/search",
                    actual_year=str(kwargs["year"]),
                    actual_province=str(kwargs["province"]),
                    actual_timeliness=str(kwargs["timeliness"]),
                    reported_total_pages=1,
                )
                return {"searchSession": session.to_dict()}

            def available_filter_entries(
                self,
                key: str,
            ) -> list[dict[str, object]]:
                if key == "year":
                    return [
                        {
                            "value": "2024",
                            "label": "2024 (1937)",
                            "count": 1937,
                        },
                        {
                            "value": "2023",
                            "label": "2023 (3983)",
                            "count": 3983,
                        },
                    ]
                return [
                    {
                        "value": "北京市",
                        "label": "北京市 (115)",
                        "count": 115,
                    },
                    {
                        "value": "天津市",
                        "label": "天津市 (93)",
                        "count": 93,
                    },
                    {
                        "value": "北京市",
                        "label": "北京市 (115)",
                        "count": 115,
                    },
                    {
                        "value": "天津市",
                        "label": "天津市 (93)",
                        "count": 93,
                    },
                ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = BatchTaskManager(
                root / "pkulaw.sqlite3",
                BrowserBridge(JsonLineLogger(root / "engine.jsonl")),
                JsonLineLogger(root / "manager.jsonl"),
                lambda _event, _data: None,
            )
            config = BatchTaskConfig(
                queries=("碳中和",),
                match_mode="keyword",
                library="local",
                year_selection=YearSelection(mode="all"),
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                page_size=100,
                page_selection=PageSelection(mode="last"),
                output_dir=(root / "output").resolve(),
                catalog_format="xls",
                full_format="docx",
            )
            adapter = FacetAdapter()
            plan = manager._resolve_year_partition_plan(  # noqa: SLF001
                adapter,  # type: ignore[arg-type]
                config,
                {
                    "status": "required",
                    "years": ["2024", "2023"],
                    "expectedResultCount": 5920,
                    "query": "碳中和",
                    "library": "local",
                },
                {
                    "resultWindow": {
                        "reportedTotalPages": 20,
                    }
                },
            )

            self.assertEqual(plan["partitionPageCapacity"], 2000)
            self.assertEqual(
                [
                    (
                        item["library"],
                        item["year"],
                        item["province"],
                    )
                    for item in adapter.searches
                ],
                [("local", "全部年份", "全部省份")],
            )
            combinations = manager._year_partition_combinations(  # noqa: SLF001
                config,
                ["local"],
                ["2024", "2023"],
                scope_query="碳中和",
                scope_library="local",
                province_partitions=plan["provincePartitions"],
            )
            self.assertEqual(
                [(item.year, item.province) for item in combinations],
                [
                    ("2024", ""),
                    ("2023", "北京市"),
                    ("2023", "天津市"),
                ],
            )

    def test_batch_year_window_scan_uses_province_combinations(
        self,
    ) -> None:
        class ProvinceAdapter:
            def __init__(self) -> None:
                self.searches: list[tuple[str, str]] = []
                self.current_search_session = type(
                    "Session",
                    (),
                    {
                        "query": "碳中和",
                        "library": "local",
                        "year": "全部年份",
                        "province": "全部省份",
                    },
                )()

            def available_filter_entries(
                self,
                key: str,
            ) -> list[dict[str, object]]:
                if key == "year":
                    return [
                        {
                            "value": "2024",
                            "label": "2024 (3)",
                            "count": 3,
                        }
                    ]
                return [
                    {
                        "value": "北京市",
                        "label": "北京市 (1)",
                        "count": 1,
                    },
                    {
                        "value": "天津市",
                        "label": "天津市 (1)",
                        "count": 1,
                    },
                ]

            def search(self, **kwargs: object) -> dict[str, object]:
                province = str(kwargs["province"])
                year = str(kwargs["year"])
                self.searches.append((year, province))
                policy_id = "bj-policy" if province == "北京市" else "tj-policy"
                rows = [
                    {
                        "number": 1,
                        "policyId": policy_id,
                        "title": f"{province}碳中和政策",
                        "url": (
                            "https://example.test/pkulaw/detail/"
                            f"{policy_id}.html"
                        ),
                    }
                ]
                signature = result_signature(rows)
                session = SearchSession(
                    search_id=f"search-{policy_id}",
                    query="碳中和",
                    match_mode="keyword",
                    library="local",
                    year=year,
                    province=province,
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
                    url=f"https://example.test/search?province={province}",
                    actual_year=year,
                    actual_province=province,
                    actual_timeliness="全部（仅记录元数据，不筛选）",
                    reported_total_pages=1,
                )
                return {
                    "page": 1,
                    "totalPages": 1,
                    "reportedTotalPages": 1,
                    "resultCount": 1,
                    "actualPageSize": 100,
                    "groupingMode": "ungrouped",
                    "actualYear": year,
                    "actualProvince": province,
                    "actualTimeliness": "全部（仅记录元数据，不筛选）",
                    "rows": rows,
                    "resultSignature": signature,
                    "searchSession": session.to_dict(),
                    "url": f"https://example.test/search?province={province}",
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量年份内省市拆分",
                    mode="batch",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                store.update_task(
                    task_id,
                    checkpoint={
                        "scaleConfirmed": True,
                        "yearPartition": {
                            "status": "required",
                            "years": ["2024"],
                            "yearCounts": {"2024": 3},
                            "provincePartitions": {
                                "2024": ["北京市", "天津市"],
                            },
                            "partitionPageCapacity": 1,
                            "expectedResultCount": 3,
                            "query": "碳中和",
                            "library": "local",
                        },
                    },
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
                    library="local",
                    year_selection=YearSelection(mode="all"),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                    catalog_format="xls",
                    full_format="docx",
                )
                adapter = ProvinceAdapter()
                journal = manager._scan_journal(  # noqa: SLF001
                    task_id,
                    config,
                )
                fallback_row = {
                    "number": 3,
                    "policyId": "fallback-policy",
                    "title": "缺少制定机关但属于本年份的政策",
                    "url": (
                        "https://example.test/pkulaw/detail/"
                        "fallback-policy.html"
                    ),
                }
                journal.append(
                    {
                        "schemaVersion": 1,
                        "configFingerprint": journal.config_fingerprint,
                        "taskId": task_id,
                        "query": "碳中和",
                        "library": "local",
                        "effectiveYear": "2024",
                        "partitionKey": "",
                        "selectedProvince": "",
                        "selectedTimeliness": (
                            "全部（仅记录元数据，不筛选）"
                        ),
                        "page": 1,
                        "totalPages": 1,
                        "reportedTotalPages": 1,
                        "resultCount": 3,
                        "requestedPageSize": 100,
                        "actualPageSize": 100,
                        "groupingMode": "ungrouped",
                        "searchId": "fallback-search",
                        "resultSignature": result_signature(
                            [fallback_row]
                        ),
                        "observedAt": "2026-11-02T00:00:00Z",
                        "rows": [fallback_row],
                    }
                )

                scans = manager._prepare_batches(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                    adapter,  # type: ignore[arg-type]
                    TaskControl(task_id),
                )

                self.assertEqual(
                    adapter.searches,
                    [
                        ("2024", "北京市"),
                        ("2024", "天津市"),
                    ],
                )
                self.assertEqual(len(scans), 3)
                batches = store.list_task_batches(task_id)
                self.assertEqual(len(batches), 3)
                checkpoint = store.get_task(task_id)["checkpoint"]
                self.assertEqual(
                    checkpoint["yearPartition"]["actualUniqueResultCount"],
                    3,
                )
                self.assertEqual(
                    {
                        item["scopeKey"]
                        for item in checkpoint["retrievalContexts"]
                    },
                    {
                        "2024|year=2024|province=北京市",
                        "2024|year=2024|province=天津市",
                        "2024",
                    },
                )
            finally:
                store.close()

    def test_batch_range_starts_at_first_real_available_year(self) -> None:
        class RangeAdapter:
            def __init__(self) -> None:
                self.search_years: list[str] = []

            def available_filter_entries(
                self,
                key: str,
            ) -> list[dict[str, object]]:
                if key != "year":
                    return []
                return [
                    {"value": "2023", "label": "2023 (1)", "count": 1},
                    {"value": "2022", "label": "2022 (1)", "count": 1},
                ]

            def search(self, **kwargs: object) -> dict[str, object]:
                year = str(kwargs["year"])
                self.search_years.append(year)
                if year != "全部年份":
                    raise TaskStopped("batch year normalization reached")
                return _year_single_row_state(
                    year="全部年份",
                    search_id="batch-search-all-years",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="批量年份从真实起点开始",
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
                        mode="range",
                        start_year=1949,
                        end_year=2023,
                    ),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                    catalog_format="xls",
                    full_format="docx",
                )
                adapter = RangeAdapter()

                with self.assertRaises(TaskStopped):
                    manager._prepare_batches(  # noqa: SLF001
                        store,
                        task_id,
                        config,
                        adapter,  # type: ignore[arg-type]
                        TaskControl(task_id),
                    )

                self.assertEqual(
                    adapter.search_years[:2],
                    ["全部年份", "2022"],
                )
                checkpoint = store.get_task(task_id)["checkpoint"]
                self.assertEqual(
                    checkpoint["originalYearSelection"],
                    {
                        "mode": "range",
                        "year": None,
                        "startYear": 1949,
                        "endYear": 2023,
                    },
                )
                self.assertEqual(
                    checkpoint["effectiveYearSelection"],
                    {
                        "mode": "range",
                        "year": None,
                        "startYear": 2022,
                        "endYear": 2023,
                    },
                )
                self.assertTrue(
                    checkpoint["yearWindowAdjustment"]["adjusted"]
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
