from __future__ import annotations

import tempfile
import threading
import time
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


def _renamed_state(
    page: int,
    search_id: str,
    *,
    prefix: str,
) -> dict[str, object]:
    state = _state(page, search_id)
    rows = [
        {
            **dict(row),
            "policyId": f"{prefix}-{row['policyId']}",
            "url": str(row["url"]).replace("policy-", f"{prefix}-policy-"),
        }
        for row in state["rows"]  # type: ignore[union-attr]
    ]
    signature = result_signature(rows)
    session_payload = dict(state["searchSession"])  # type: ignore[arg-type]
    session_payload.update(
        {
            "resultSignature": signature,
            "rowCount": len(rows),
        }
    )
    state.update(
        {
            "rows": rows,
            "resultSignature": signature,
            "searchSession": session_payload,
        }
    )
    return state


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
            raise RuntimeError(f"page {expected_page} interrupted")
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

    def test_resumed_processing_restores_search_url_context(self) -> None:
        class Adapter:
            selectors = {"detailTitle": ".title"}
            current_search_request = None
            current_search_session = None
            search_url = ""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            try:
                task = store.create_task(
                    name="恢复检索上下文",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                session_payload = _state(2, "search-resume")["searchSession"]
                store.update_task(
                    task_id,
                    checkpoint={"searchIdentity": session_payload},
                )
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(JsonLineLogger(root / "engine.jsonl")),
                    JsonLineLogger(root / "manager.jsonl"),
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
                adapter = Adapter()

                manager._restore_adapter_search_context(  # noqa: SLF001
                    adapter,  # type: ignore[arg-type]
                    store,
                    task_id,
                    config,
                )

                self.assertTrue(adapter.search_url.endswith("page=2"))
                self.assertEqual(
                    adapter.current_search_session.search_id,
                    "search-resume",
                )
                self.assertEqual(
                    adapter.current_search_request.query,
                    "碳中和",
                )
            finally:
                store.close()

    def test_repeated_retry_rebinds_each_search_session_segment(self) -> None:
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
                first = _ResumeAdapter(fail_page=3)

                with self.assertRaisesRegex(
                    RuntimeError,
                    "page 3 interrupted",
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
                    [1, 2],
                )

                second = _ResumeAdapter(
                    fail_page=4,
                    resumed_search_id="search-2",
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "page 4 interrupted",
                ):
                    manager._collect(  # noqa: SLF001
                        task_id,
                        config,
                        second,  # type: ignore[arg-type]
                        store,
                        controller,
                    )

                self.assertEqual(second.search_calls, 1)
                self.assertEqual(second.go_targets, [3])
                self.assertEqual(second.next_pages, [4])
                self.assertEqual(
                    [record["page"] for record in journal.read()],
                    [1, 2, 3],
                )

                third = _ResumeAdapter(
                    resumed_search_id="search-3",
                )
                rows, scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    third,  # type: ignore[arg-type]
                    store,
                    controller,
                )
                self.assertEqual(third.search_calls, 1)
                self.assertEqual(third.go_targets, [4])
                self.assertEqual(third.next_pages, [5])
                self.assertEqual(len(rows), 450)
                self.assertEqual(len(scans), 5)
                self.assertEqual(
                    [record["page"] for record in journal.read()],
                    [1, 2, 3, 4, 5],
                )
                final_resume = [
                    record["resumeEvidence"]
                    for record in journal.read()
                    if record.get("resumeEvidence")
                ][-1]
                self.assertEqual(final_resume["page"], 3)
                self.assertEqual(final_resume["searchId"], "search-2")
                self.assertEqual(
                    final_resume["resultSignature"],
                    _state(3, "search-2")["resultSignature"],
                )

                manager._seal_scan_journal(  # noqa: SLF001
                    store,
                    task_id,
                    config,
                )
                fourth = _ResumeAdapter(
                    resumed_search_id="search-4",
                )
                restored_rows, restored_scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    fourth,  # type: ignore[arg-type]
                    store,
                    controller,
                )
                self.assertEqual(fourth.search_calls, 0)
                self.assertEqual(fourth.next_pages, [])
                self.assertEqual(fourth.go_targets, [])
                self.assertEqual(len(restored_rows), 450)
                self.assertEqual(len(restored_scans), 5)
            finally:
                store.close()

    def test_changed_result_universe_restarts_scan_without_old_rows(
        self,
    ) -> None:
        class ChangedUniverseAdapter:
            def __init__(self) -> None:
                self.search_calls = 0
                self.next_pages: list[int] = []

            def search(self, **_kwargs: object) -> dict[str, object]:
                self.search_calls += 1
                return _renamed_state(
                    1,
                    f"search-new-{self.search_calls}",
                    prefix="new",
                )

            def next_page(self, expected_page: int) -> dict[str, object]:
                self.next_pages.append(expected_page)
                return _renamed_state(
                    expected_page,
                    "search-new-2",
                    prefix="new",
                )

            def go_to_page(self, target_page: int) -> dict[str, object]:
                return _renamed_state(
                    target_page,
                    "search-new-2",
                    prefix="new",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="新结果集重新扫描",
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
                journal_path = (
                    root
                    / "cache"
                    / "task-scans"
                    / f"{task_id}.jsonl"
                )
                journal = ScanJournal(
                    journal_path,
                    task_id=task_id,
                    config_fingerprint=config_fingerprint(
                        manager._config_payload(config)  # noqa: SLF001
                    ),
                )
                old_state = _state(1, "search-old")
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
                adapter = ChangedUniverseAdapter()

                rows, scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    adapter,  # type: ignore[arg-type]
                    store,
                    TaskControl(task_id),
                )

                self.assertEqual(adapter.search_calls, 2)
                self.assertEqual(adapter.next_pages, [2, 3, 4, 5])
                self.assertEqual(len(rows), 450)
                self.assertEqual(len(scans), 5)
                self.assertTrue(
                    all(
                        str(row["policyId"]).startswith("new-")
                        for row in rows
                    )
                )
                records = journal.read()
                self.assertEqual(
                    [record["page"] for record in records],
                    [1, 2, 3, 4, 5],
                )
                self.assertTrue(
                    all(
                        str(record["searchId"]).startswith("search-new-")
                        for record in records
                    )
                )
                archive_root = journal_path.parent / "archive"
                archived = list(archive_root.glob(f"{task_id}*.jsonl"))
                self.assertEqual(len(archived), 1)
                archived_record = ScanJournal(
                    archived[0],
                    task_id=task_id,
                    config_fingerprint=journal.config_fingerprint,
                ).read()[0]
                self.assertEqual(
                    archived_record["searchId"],
                    "search-old",
                )
            finally:
                store.close()

    def test_resume_waits_for_page_limit_before_advancing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="分页上限断点",
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
                    page_selection=PageSelection(
                        mode="limit",
                        max_pages=1,
                    ),
                    output_dir=(root / "output").resolve(),
                )
                state = _state(1, "search-limit")
                session = SearchSession.from_dict(state["searchSession"])
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
                journal.append(
                    {
                        "schemaVersion": 1,
                        "configFingerprint": journal.config_fingerprint,
                        "taskId": task_id,
                        "query": session.query,
                        "library": session.library,
                        "effectiveYear": session.year,
                        "page": 1,
                        "totalPages": session.total_pages,
                        "reportedTotalPages": (
                            session.reported_total_pages
                        ),
                        "resultCount": session.result_count,
                        "requestedPageSize": config.page_size,
                        "actualPageSize": session.actual_page_size,
                        "groupingMode": session.grouping_mode,
                        "searchId": session.search_id,
                        "resultSignature": session.result_signature,
                        "observedAt": session.observed_at,
                        "rows": state["rows"],
                    }
                )
                store.update_task(
                    task_id,
                    checkpoint={
                        "pageMode": "limit",
                        "pageSelection": {
                            "mode": "limit",
                            "maxPages": 1,
                        },
                        "pageLimitGate": True,
                        "pageLimitMaxPages": 1,
                    },
                )
                controller = TaskControl(task_id)
                adapter = _ResumeAdapter(
                    resumed_search_id="search-limit"
                )
                failure: list[BaseException] = []

                def run_collect() -> None:
                    worker_store = TaskStore(store.database_path)
                    try:
                        manager._collect(  # noqa: SLF001
                            task_id,
                            config,
                            adapter,  # type: ignore[arg-type]
                            worker_store,
                            controller,
                        )
                    except BaseException as exc:
                        failure.append(exc)
                    finally:
                        worker_store.close()

                worker = threading.Thread(target=run_collect)
                worker.start()
                deadline = time.monotonic() + 5
                waiting = False
                while time.monotonic() < deadline:
                    current = store.get_task(task_id)
                    if (
                        current
                        and current["status"] == "waiting_manual"
                        and (current.get("error") or {}).get("code")
                        == "PAGE_LIMIT_REACHED"
                    ):
                        waiting = True
                        break
                    time.sleep(0.02)
                if not waiting:
                    controller.request_stop()
                    worker.join(timeout=5)
                self.assertTrue(
                    waiting,
                    {
                        "failure": [repr(item) for item in failure],
                        "task": store.get_task(task_id),
                    },
                )
                current = store.get_task(task_id)
                checkpoint = dict((current or {}).get("checkpoint") or {})
                checkpoint.update(
                    {
                        "pageMode": "last",
                        "pageSelection": {"mode": "last"},
                        "pageLimitGate": False,
                        "pageLimitDecision": "continue_to_last",
                    }
                )
                store.update_task(task_id, checkpoint=checkpoint)
                controller.request_continue()
                worker.join(timeout=10)

                self.assertFalse(worker.is_alive())
                self.assertEqual(failure, [])
                self.assertEqual(adapter.go_targets, [2])
                self.assertGreaterEqual(len(adapter.next_pages), 3)
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

    def test_all_years_split_after_duplicate_beyond_reported_window(
        self,
    ) -> None:
        class WindowAdapter:
            def __init__(self) -> None:
                self.next_pages: list[int] = []

            @staticmethod
            def _window_state(page: int, rows_page: int) -> dict[str, object]:
                state = _state(rows_page, "search-window")
                state["page"] = page
                state["totalPages"] = 3
                state["reportedTotalPages"] = 1
                state["resultCount"] = 300
                state["actualYear"] = "全部年份"
                session = dict(state["searchSession"])  # type: ignore[arg-type]
                session.update(
                    {
                        "page": page,
                        "totalPages": 3,
                        "reportedTotalPages": 1,
                        "resultCount": 300,
                        "year": "全部年份",
                        "actualYear": "全部年份",
                    }
                )
                state["searchSession"] = session
                return state

            def search(self, **_kwargs: object) -> dict[str, object]:
                return self._window_state(1, 1)

            def next_page(self, expected_page: int) -> dict[str, object]:
                self.next_pages.append(expected_page)
                return self._window_state(
                    expected_page,
                    2 if expected_page == 3 else expected_page,
                )

            def available_filter_values(self, _key: str) -> list[str]:
                return ["2025", "2024"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="窗口扩展重复后按年份拆分",
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
                adapter = WindowAdapter()

                with self.assertRaises(YearPartitionRequired) as context:
                    manager._collect(  # noqa: SLF001
                        task_id,
                        config,
                        adapter,  # type: ignore[arg-type]
                        store,
                        TaskControl(task_id),
                    )

                self.assertEqual(adapter.next_pages, [2, 3])
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

    def test_year_window_plan_splits_only_oversized_year(self) -> None:
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
            manager = SingleTaskManager(
                root / "pkulaw.sqlite3",
                BrowserBridge(JsonLineLogger(root / "engine.jsonl")),
                JsonLineLogger(root / "manager.jsonl"),
                lambda _event, _data: None,
            )
            config = SingleTaskConfig(
                queries=("碳中和",),
                match_mode="keyword",
                library="local",
                year_selection=YearSelection(mode="all"),
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                page_size=100,
                page_selection=PageSelection(mode="last"),
                output_dir=(root / "output").resolve(),
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
            self.assertEqual(
                plan["provincePartitions"],
                {"2023": ["北京市", "天津市"]},
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
            self.assertTrue(
                all(
                    item.partition_key
                    for item in combinations
                    if item.province
                )
            )

    def test_year_window_scan_uses_province_combinations(self) -> None:
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
                            "label": "2024 (2)",
                            "count": 2,
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
                    name="年份内省市拆分",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                store.update_task(
                    task_id,
                    checkpoint={
                        "yearPartition": {
                            "status": "required",
                            "years": ["2024"],
                            "yearCounts": {"2024": 2},
                            "provincePartitions": {
                                "2024": ["北京市", "天津市"],
                            },
                            "partitionPageCapacity": 1,
                            "expectedResultCount": 2,
                            "query": "碳中和",
                            "library": "local",
                        }
                    },
                )
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = SingleTaskConfig(
                    queries=("碳中和",),
                    match_mode="keyword",
                    library="local",
                    year_selection=YearSelection(mode="all"),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                )
                adapter = ProvinceAdapter()

                rows, scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    adapter,  # type: ignore[arg-type]
                    store,
                    TaskControl(task_id),
                )

                self.assertEqual(
                    adapter.searches,
                    [
                        ("2024", "北京市"),
                        ("2024", "天津市"),
                    ],
                )
                self.assertEqual(
                    {row["policyId"] for row in rows},
                    {"bj-policy", "tj-policy"},
                )
                self.assertEqual(len(scans), 2)
                checkpoint = store.get_task(task_id)["checkpoint"]
                self.assertEqual(
                    checkpoint["yearPartition"]["actualUniqueResultCount"],
                    2,
                )
                records = manager._scan_journal(  # noqa: SLF001
                    task_id,
                    config,
                ).read()
                self.assertEqual(
                    {
                        record["partitionKey"]
                        for record in records
                    },
                    {
                        "year=2024|province=北京市",
                        "year=2024|province=天津市",
                    },
                )
            finally:
                store.close()

    def test_year_partition_accepts_verified_live_source_drift(self) -> None:
        class DriftAdapter:
            def available_filter_entries(
                self,
                key: str,
            ) -> list[dict[str, object]]:
                if key != "year":
                    return []
                return [
                    {
                        "value": "2024",
                        "label": "2024 (2)",
                        "count": 2,
                    },
                    {
                        "value": "2023",
                        "label": "2023 (2)",
                        "count": 2,
                    },
                ]

            def search(self, **kwargs: object) -> dict[str, object]:
                year = str(kwargs["year"])
                policy_ids = (
                    ["shared-policy", "new-2023"]
                    if year == "2023"
                    else ["shared-policy", "new-2024"]
                )
                rows = [
                    {
                        "number": index + 1,
                        "policyId": policy_id,
                        "title": f"{year} 政策 {index + 1}",
                        "url": (
                            "https://example.test/pkulaw/detail/"
                            f"{policy_id}.html"
                        ),
                    }
                    for index, policy_id in enumerate(policy_ids)
                ]
                signature = result_signature(rows)
                session = SearchSession(
                    search_id=f"search-{year}",
                    query="碳中和",
                    match_mode="keyword",
                    library="local",
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
                    "groupingMode": "ungrouped",
                    "actualYear": year,
                    "actualProvince": "全部省份",
                    "actualTimeliness": "全部（仅记录元数据，不筛选）",
                    "rows": rows,
                    "resultSignature": signature,
                    "searchSession": session.to_dict(),
                    "url": f"https://example.test/search?year={year}",
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="年份源漂移验证",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                store.update_task(
                    task_id,
                    checkpoint={
                        "yearPartition": {
                            "status": "required",
                            "years": ["2024", "2023"],
                            "yearCounts": {
                                "2024": 2,
                                "2023": 2,
                            },
                            "provincePartitions": {},
                            "expectedResultCount": 5,
                            "query": "碳中和",
                            "library": "local",
                        }
                    },
                )
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = SingleTaskConfig(
                    queries=("碳中和",),
                    match_mode="keyword",
                    library="local",
                    year_selection=YearSelection(mode="all"),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                )

                rows, scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    DriftAdapter(),  # type: ignore[arg-type]
                    store,
                    TaskControl(task_id),
                )

                self.assertEqual(len(scans), 2)
                self.assertEqual(len(rows), 3)
                partition = store.get_task(task_id)["checkpoint"][
                    "yearPartition"
                ]
                self.assertEqual(partition["status"], "complete")
                self.assertEqual(partition["sourceUniverseDrift"], 1)
                self.assertEqual(partition["crossYearDuplicateCount"], 1)
                self.assertEqual(
                    partition["actualUniqueResultCount"],
                    3,
                )
            finally:
                store.close()

    def test_live_year_count_drift_rescans_without_resetting_items(
        self,
    ) -> None:
        class ExistingItemDriftAdapter:
            search_calls = 0
            year_search_calls = 0
            current_search_session: SearchSession | None = None

            def available_filter_entries(
                self,
                key: str,
            ) -> list[dict[str, object]]:
                if key != "year":
                    return []
                return [
                    {
                        "value": "2024",
                        "label": "2024 (3)",
                        "count": 3,
                    }
                ]

            def search(self, **kwargs: object) -> dict[str, object]:
                self.search_calls += 1
                requested_year = str(kwargs.get("year") or "2024")
                if requested_year == "2024":
                    self.year_search_calls += 1
                    policy_ids = (
                        [
                            "archived-policy",
                            "new-policy-a",
                            "new-policy-b",
                        ]
                        if self.year_search_calls == 1
                        else [
                            "new-policy-a",
                            "new-policy-b",
                            "new-policy-c",
                        ]
                    )
                else:
                    policy_ids = [
                        "new-policy-a",
                        "new-policy-b",
                        "new-policy-c",
                    ]
                rows = [
                    {
                        "number": index + 1,
                        "policyId": policy_id,
                        "title": f"2024 政策 {index + 1}",
                        "url": (
                            "https://example.test/pkulaw/detail/"
                            f"{policy_id}.html"
                        ),
                    }
                    for index, policy_id in enumerate(policy_ids)
                ]
                signature = result_signature(rows)
                session = SearchSession(
                    search_id=f"search-{self.search_calls}",
                    query="碳中和",
                    match_mode="keyword",
                    library="local",
                    year=requested_year,
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    requested_page_size=100,
                    actual_page_size=100,
                    page=1,
                    total_pages=1,
                    result_count=3,
                    row_count=3,
                    grouping_mode="ungrouped",
                    result_signature=signature,
                    observed_at="2026-11-03T00:00:00Z",
                    url="https://example.test/search?year=2024",
                    actual_year=requested_year,
                    actual_province="全部省份",
                    actual_timeliness="全部（仅记录元数据，不筛选）",
                    reported_total_pages=1,
                )
                self.current_search_session = session
                return {
                    "page": 1,
                    "totalPages": 1,
                    "reportedTotalPages": 1,
                    "resultCount": 3,
                    "actualPageSize": 100,
                    "groupingMode": "ungrouped",
                    "actualYear": "2024",
                    "actualProvince": "全部省份",
                    "actualTimeliness": "全部（仅记录元数据，不筛选）",
                    "rows": rows,
                    "resultSignature": signature,
                    "searchSession": session.to_dict(),
                    "url": "https://example.test/search?year=2024",
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            logger = JsonLineLogger(root / "engine.jsonl")
            try:
                task = store.create_task(
                    name="已有任务项源漂移重扫",
                    mode="single",
                    task_input={"queries": ["碳中和"]},
                    output_dir=str(root / "output"),
                )
                task_id = str(task["id"])
                store.upsert_task_item(
                    task_id,
                    policy_key="id:archived-policy",
                    title="2024 政策 1",
                    status="completed",
                    source_type="北大法宝原件",
                    progress=1.0,
                    payload={
                        "candidates": [
                            {
                                "policyId": "archived-policy",
                                "title": "2024 政策 1",
                                "url": (
                                    "https://example.test/pkulaw/detail/"
                                    "archived-policy.html"
                                ),
                            }
                        ],
                        "identityStatus": "verified",
                        "downloadedFile": "already-downloaded.doc",
                    },
                    current_stage="completed",
                )
                store.update_task(
                    task_id,
                    checkpoint={
                        "yearPartition": {
                            "status": "required",
                            "years": ["2024"],
                            "yearCounts": {"2024": 2},
                            "provincePartitions": {},
                            "expectedResultCount": 2,
                            "query": "碳中和",
                            "library": "local",
                        }
                    },
                )
                manager = SingleTaskManager(
                    store.database_path,
                    BrowserBridge(logger),
                    logger,
                    lambda _event, _data: None,
                )
                config = SingleTaskConfig(
                    queries=("碳中和",),
                    match_mode="keyword",
                    library="local",
                    year_selection=YearSelection(mode="all"),
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=100,
                    page_selection=PageSelection(mode="last"),
                    output_dir=(root / "output").resolve(),
                )
                adapter = ExistingItemDriftAdapter()

                rows, _scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    adapter,  # type: ignore[arg-type]
                    store,
                    TaskControl(task_id),
                )
                self.assertEqual(adapter.search_calls, 4)
                self.assertEqual(
                    {
                        str(row["policyId"])
                        for row in rows
                    },
                    {
                        "archived-policy",
                        "new-policy-a",
                        "new-policy-b",
                        "new-policy-c",
                    },
                )
                checkpoint = store.get_task(task_id)["checkpoint"]
                partition = checkpoint["yearPartition"]
                self.assertEqual(partition["yearCounts"], {"2024": 3})
                self.assertEqual(
                    partition["expectedResultCount"],
                    3,
                )
                drift = checkpoint["scanUniverseChanges"][-1]
                self.assertEqual(drift["reason"], "year-result-count-drift")
                self.assertEqual(drift["downloadsStarted"], 0)
                self.assertEqual(drift["previousYearCounts"], {"2024": 2})
                self.assertEqual(drift["currentYearCounts"], {"2024": 3})
                self.assertIn(
                    "archived-policy",
                    partition["sourceDriftSupplementIdentitiesByYear"][
                        "2024"
                    ],
                )

                groups = [
                    {
                        "policyKey": f"id:{policy_id}",
                        "title": f"2024 政策 {index + 1}",
                        "candidates": [
                            {
                                "policyId": policy_id,
                                "title": f"2024 政策 {index + 1}",
                                "url": (
                                    "https://example.test/pkulaw/detail/"
                                    f"{policy_id}.html"
                                ),
                            }
                        ],
                        "identityStatus": "verified",
                    }
                    for index, policy_id in enumerate(
                        [
                            "archived-policy",
                            "new-policy-a",
                            "new-policy-b",
                            "new-policy-c",
                        ]
                    )
                ]
                manager._prepare_items(  # noqa: SLF001
                    store,
                    task_id,
                    groups,
                    match_mode="keyword",
                    queries=("碳中和",),
                )
                items = {
                    item["policyKey"]: item
                    for item in store.list_task_items(task_id)
                }
                self.assertEqual(len(items), 4)
                self.assertEqual(
                    items["id:archived-policy"]["status"],
                    "completed",
                )
                self.assertEqual(
                    items["id:new-policy-a"]["status"],
                    "pending",
                )
                self.assertEqual(
                    items["id:new-policy-b"]["status"],
                    "pending",
                )
                self.assertEqual(
                    items["id:new-policy-c"]["status"],
                    "pending",
                )

                resumed_rows, _resumed_scans = manager._collect(  # noqa: SLF001
                    task_id,
                    config,
                    adapter,  # type: ignore[arg-type]
                    store,
                    TaskControl(task_id),
                )
                self.assertEqual(
                    {
                        str(row["policyId"])
                        for row in resumed_rows
                    },
                    {
                        "archived-policy",
                        "new-policy-a",
                        "new-policy-b",
                        "new-policy-c",
                    },
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
