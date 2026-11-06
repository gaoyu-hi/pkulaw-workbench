from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from typing import Any

from pkulaw_engine.batch_task import BatchTaskConfig, BatchTaskManager
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.capture_boundary import CaptureBoundaryRequired
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import YearSelection
from pkulaw_engine.selections.years import initial_adaptive_combinations
from pkulaw_engine.sources.pkulaw import LoginRequired, ManualActionRequired
from pkulaw_engine.sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
    result_signature,
)
from pkulaw_engine.storage import TaskStore


ALL_YEARS = "全部年份"


def _row(policy_id: str, year: str) -> dict[str, Any]:
    suffix = policy_id.rsplit("-", 1)[-1]
    number = int(suffix) if suffix.isdigit() else 1
    return {
        "number": number,
        "policyId": policy_id,
        "title": f"政策 {policy_id}",
        "url": f"https://example.test/{policy_id}.html",
        "publicationDate": f"{year}-01-01",
    }


def _session(
    *,
    search_id: str,
    query: str,
    library: str,
    year: str,
    rows: list[dict[str, Any]],
    result_count: int,
    reported_total_pages: int,
    page: int = 1,
) -> SearchSession:
    return SearchSession(
        search_id=search_id,
        query=query,
        match_mode="keyword",
        library=library,
        year=year,
        province="全部省份",
        timeliness="全部（仅记录元数据，不筛选）",
        requested_page_size=100,
        actual_page_size=100,
        page=page,
        total_pages=max(1, reported_total_pages),
        result_count=result_count,
        row_count=len(rows),
        grouping_mode="ungrouped",
        result_signature=result_signature(rows),
        observed_at="2026-11-06T00:00:00Z",
        url=f"https://example.test/search?page={page}",
        actual_year=year,
        actual_province="全部省份",
        actual_timeliness="全部（仅记录元数据，不筛选）",
        reported_total_pages=reported_total_pages,
    )


def _state(
    *,
    search_id: str,
    query: str,
    library: str,
    year: str,
    rows: list[dict[str, Any]],
    result_count: int,
    reported_total_pages: int,
    page: int = 1,
) -> dict[str, Any]:
    session = _session(
        search_id=search_id,
        query=query,
        library=library,
        year=year,
        rows=rows,
        result_count=result_count,
        reported_total_pages=reported_total_pages,
        page=page,
    )
    return {
        "page": page,
        "totalPages": max(1, reported_total_pages),
        "reportedTotalPages": reported_total_pages,
        "resultCount": result_count,
        "actualPageSize": 100,
        "rows": [dict(row) for row in rows],
        "resultSignature": result_signature(rows),
        "searchSession": session.to_dict(),
        "url": f"https://example.test/search?page={page}",
    }


class FakeAdapter:
    def __init__(
        self,
        *,
        year_entries: list[dict[str, Any]] | None = None,
        truncated_year: str | None = None,
        complete_all_years: bool = False,
        include_out_of_range_row: bool = False,
    ) -> None:
        self.year_entries = year_entries or []
        self.truncated_year = truncated_year
        self.complete_all_years = complete_all_years
        self.include_out_of_range_row = include_out_of_range_row
        self.search_calls: list[dict[str, Any]] = []
        self.select_year_calls: list[str] = []
        self.current_search_request: SearchRequestIdentity | None = None
        self.current_search_session: SearchSession | None = None
        self.selectors: dict[str, Any] = {"__fake__": True}

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(dict(kwargs))
        query = str(kwargs["keyword"])
        library = str(kwargs["library"])
        year = str(kwargs["year"])
        if self.complete_all_years and kwargs["year"] == ALL_YEARS:
            rows = [
                dict(_row(f"{library}-{query}-1", "2022")),
                dict(_row(f"{library}-{query}-2", "2023")),
            ]
            if self.include_out_of_range_row:
                rows.append(
                    dict(_row(f"{library}-{query}-out", "1948"))
                )
        else:
            row_year = "2022" if kwargs["year"] == ALL_YEARS else year
            row_id = (
                f"{library}-{query}-1"
                if kwargs["year"] == ALL_YEARS
                else f"{library}-{query}-{year}-1"
            )
            rows = [dict(_row(row_id, row_year))]
        if kwargs["year"] == ALL_YEARS:
            if self.complete_all_years:
                result_count = len(rows)
                reported = 1
            else:
                result_count = 201
                reported = 2
        elif self.truncated_year and kwargs["year"] == self.truncated_year:
            result_count = 201
            reported = 2
        else:
            result_count = len(rows)
            reported = 1
        state = _state(
            search_id=f"search-{len(self.search_calls)}",
            query=query,
            library=library,
            year=year,
            rows=rows,
            result_count=result_count,
            reported_total_pages=reported,
        )
        self._attach(state, query, library, year)
        return state

    def select_year(self, *, year: str) -> dict[str, Any]:
        self.select_year_calls.append(year)
        request = self.current_search_request
        if request is None:
            raise RuntimeError("no request")
        rows = [dict(_row(f"{request.library}-{request.query}-{year}", year))]
        if self.truncated_year and year == self.truncated_year:
            result_count = 201
            reported_total_pages = 2
        else:
            result_count = len(rows)
            reported_total_pages = 1
        state = _state(
            search_id=f"year-{year}",
            query=request.query,
            library=request.library,
            year=year,
            rows=rows,
            result_count=result_count,
            reported_total_pages=reported_total_pages,
        )
        self._attach(state, request.query, request.library, year)
        return state

    def _attach(
        self,
        state: dict[str, Any],
        query: str,
        library: str,
        year: str,
    ) -> None:
        payload = state["searchSession"]
        self.current_search_session = SearchSession.from_dict(payload)
        self.current_search_request = SearchRequestIdentity(
            query=query,
            match_mode="keyword",
            library=library,
            year=year,
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=100,
        )

    def read_results(self) -> dict[str, Any]:
        if self.current_search_session is None:
            raise RuntimeError("no session")
        return _state(
            search_id=self.current_search_session.search_id,
            query=self.current_search_session.query,
            library=self.current_search_session.library,
            year=self.current_search_session.year,
            rows=[dict(_row("reuse-1", "2022"))],
            result_count=self.current_search_session.result_count,
            reported_total_pages=(
                self.current_search_session.reported_total_pages or 1
            ),
            page=self.current_search_session.page,
        )

    def available_filter_entries(self, key: str) -> list[dict[str, Any]]:
        if key != "year":
            return []
        return [dict(entry) for entry in self.year_entries]

    def go_to_page(self, target_page: int) -> dict[str, Any]:
        if self.current_search_request is None:
            raise RuntimeError("no request")
        reported = (
            self.current_search_session.reported_total_pages
            if self.current_search_session is not None
            else None
        ) or 1
        if self.current_search_session is None:
            raise RuntimeError("no session")
        return _state(
            search_id=self.current_search_session.search_id,
            query=self.current_search_request.query,
            library=self.current_search_request.library,
            year=self.current_search_request.year,
            rows=[
                dict(
                    _row(
                        f"{self.current_search_request.library}-"
                        f"{self.current_search_request.query}-"
                        f"page-{target_page}",
                        "2022",
                    )
                )
            ],
            result_count=self.current_search_session.result_count,
            reported_total_pages=reported,
            page=target_page,
        )


class CaptchaReuseAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(
            year_entries=[{"value": "2022", "count": 1}],
            complete_all_years=True,
        )
        self.first_search = True

    def search(self, **kwargs: Any) -> dict[str, Any]:
        if self.first_search:
            self.first_search = False
            state = super().search(**kwargs)
            raise ManualActionRequired("captcha before result confirmation")
        return super().search(**kwargs)


class LoginWaitAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(
            year_entries=[{"value": "2022", "count": 1}],
            complete_all_years=True,
        )
        self.first_search = True

    def search(self, **kwargs: Any) -> dict[str, Any]:
        if self.first_search:
            self.first_search = False
            raise LoginRequired("登录会话失效或需要机构登录")
        return super().search(**kwargs)


class CrossScopeBoundaryAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 2},
            ],
            truncated_year="2023",
            complete_all_years=True,
        )

    def select_year(self, *, year: str) -> dict[str, Any]:
        request = self.current_search_request
        if request is None:
            raise RuntimeError("no request")
        rows = [dict(_row("central-碳中和-2", "2023"))]
        state = _state(
            search_id=f"year-{year}",
            query=request.query,
            library=request.library,
            year=year,
            rows=rows,
            result_count=201,
            reported_total_pages=2,
        )
        self._attach(state, request.query, request.library, year)
        return state


class DuplicateYearSwitchAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 2},
            ]
        )

    def select_year(self, *, year: str) -> dict[str, Any]:
        if year != "2023":
            return super().select_year(year=year)
        request = self.current_search_request
        if request is None:
            raise RuntimeError("no request")
        policy_id = f"{request.library}-{request.query}-dup-2023"
        rows = [
            dict(_row(policy_id, "2023")),
            dict(_row(policy_id, "2023")),
        ]
        state = _state(
            search_id=f"year-{year}",
            query=request.query,
            library=request.library,
            year=year,
            rows=rows,
            result_count=len(rows),
            reported_total_pages=1,
        )
        self._attach(state, request.query, request.library, year)
        return state


class AdaptiveBatchTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "pkulaw.sqlite3"
        self.logger = JsonLineLogger(self.root / "engine.jsonl")
        self.store = TaskStore(self.database)
        self.task_id = str(
            self.store.create_task(
                name="自适应批量任务",
                mode="batch",
                task_input={"queries": ["碳中和"]},
                output_dir=str(self.root / "output"),
            )["id"]
        )
        self.store.update_task(
            self.task_id,
            checkpoint={"scaleConfirmed": True},
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _config(
        self,
        *,
        library: str = "both",
        year_selection: YearSelection | None = None,
    ) -> BatchTaskConfig:
        return BatchTaskConfig(
            queries=("碳中和",),
            match_mode="keyword",
            library=library,
            year_selection=year_selection
            or YearSelection(mode="range", start_year=1949, end_year=2026),
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            page_size=100,
            page_selection=PageSelection(mode="last"),
            output_dir=(self.root / "output").resolve(),
            catalog_format="xlsx",
            full_format="docx",
            search_strategy_version=2,
        )

    def _manager(self) -> BatchTaskManager:
        bridge = BrowserBridge(self.logger)
        manager = BatchTaskManager(
            self.database,
            bridge,
            self.logger,
            lambda _event, _data: None,
        )
        return manager

    def test_batch_v2_range_submits_keyword_once_per_sublibrary(self) -> None:
        adapter = FakeAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 1},
            ],
            complete_all_years=True,
        )
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.batch_task.SearchActionGovernor",
            return_value=SimpleNamespace(
                before_result_action=lambda _kind: None,
                after_result_action=lambda *a, **k: None,
                note_captcha_cleared=lambda *a, **k: False,
                snapshot=lambda: {
                    "keyword_submissions": 0,
                    "year_filter_switches": 0,
                    "captcha_waits": 0,
                    "released_to_boundary": False,
                },
            ),
        ):
            scans = manager._prepare_batches(
                self.store,
                self.task_id,
                self._config(),
                adapter,
                TaskControl(self.task_id),
            )
        self.assertEqual([call["year"] for call in adapter.search_calls], [ALL_YEARS, ALL_YEARS])
        self.assertEqual(adapter.select_year_calls, [])
        self.assertGreaterEqual(len(scans), 2)

    def test_batch_v2_range_switches_only_missing_years(self) -> None:
        adapter = FakeAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 2},
                {"value": "2024", "count": 3},
            ]
        )
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.batch_task.SearchActionGovernor",
            return_value=SimpleNamespace(
                before_result_action=lambda _kind: None,
                after_result_action=lambda *a, **k: None,
                note_captcha_cleared=lambda *a, **k: False,
                snapshot=lambda: {
                    "keyword_submissions": 0,
                    "year_filter_switches": 0,
                    "captcha_waits": 0,
                    "released_to_boundary": False,
                },
            ),
        ):
            manager._prepare_batches(
                self.store,
                self.task_id,
                self._config(),
                adapter,
                TaskControl(self.task_id),
            )
        self.assertEqual(len(adapter.search_calls), 4)
        self.assertEqual(
            sorted(set(adapter.select_year_calls)),
            ["2024"],
        )

    def test_batch_v2_zero_result_scope_does_not_switch_year(self) -> None:
        adapter = FakeAdapter(year_entries=[])
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.batch_task.SearchActionGovernor",
            return_value=SimpleNamespace(
                before_result_action=lambda _kind: None,
                after_result_action=lambda *a, **k: None,
                note_captcha_cleared=lambda *a, **k: False,
                snapshot=lambda: {
                    "keyword_submissions": 0,
                    "year_filter_switches": 0,
                    "captcha_waits": 0,
                    "released_to_boundary": False,
                },
            ),
        ):
            manager._prepare_batches(
                self.store,
                self.task_id,
                self._config(),
                adapter,
                TaskControl(self.task_id),
            )
        self.assertEqual(adapter.select_year_calls, [])

    def test_batch_v2_year_switch_coverage_counts_unique_policies(self) -> None:
        adapter = DuplicateYearSwitchAdapter()
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.batch_task.SearchActionGovernor",
            return_value=SimpleNamespace(
                before_result_action=lambda _kind: None,
                after_result_action=lambda *a, **k: None,
                note_captcha_cleared=lambda *a, **k: False,
                snapshot=lambda: {
                    "keyword_submissions": 0,
                    "year_filter_switches": 0,
                    "captcha_waits": 0,
                    "released_to_boundary": False,
                },
            ),
        ):
            manager._prepare_batches(
                self.store,
                self.task_id,
                self._config(),
                adapter,
                TaskControl(self.task_id),
            )
        checkpoint = self.store.get_task(self.task_id)["checkpoint"]
        adaptive = checkpoint["adaptiveSearch"]
        self.assertIn("2023", adaptive["missingYears"])
        self.assertNotIn("2023", adaptive["completedYears"])

    def test_batch_v2_boundary_records_remaining_scope(self) -> None:
        adapter = FakeAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 201},
            ],
            truncated_year="2023",
        )
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.batch_task.SearchActionGovernor",
            return_value=SimpleNamespace(
                before_result_action=lambda _kind: None,
                after_result_action=lambda *a, **k: None,
                note_captcha_cleared=lambda *a, **k: False,
                snapshot=lambda: {
                    "keyword_submissions": 0,
                    "year_filter_switches": 0,
                    "captcha_waits": 0,
                    "released_to_boundary": False,
                },
            ),
        ):
            with self.assertRaises(CaptureBoundaryRequired) as context:
                manager._prepare_batches(
                    self.store,
                    self.task_id,
                    self._config(),
                    adapter,
                    TaskControl(self.task_id),
                )
        self.assertEqual(context.exception.code, "CAPTURE_BOUNDARY_REACHED")
        self.assertFalse(self.store.list_task_batches(self.task_id))

    def test_batch_v2_captcha_reuses_successful_original_result(self) -> None:
        adapter = CaptchaReuseAdapter()
        manager = self._manager()
        combination = initial_adaptive_combinations(
            ("碳中和",),
            ("central",),
            YearSelection(mode="range", start_year=1949, end_year=2026),
        )[0]
        governor = SimpleNamespace(
            before_result_action=lambda _kind: None,
            after_result_action=lambda *a, **k: None,
            note_captcha_cleared=lambda *a, **k: False,
            snapshot=lambda: {
                "keyword_submissions": 0,
                "year_filter_switches": 0,
                "captcha_waits": 1,
                "released_to_boundary": False,
            },
        )
        with mock.patch.object(manager, "_wait_for_manual"):
            state = manager._run_adaptive_result_action(
                store=self.store,
                controller=TaskControl(self.task_id),
                task_id=self.task_id,
                config=self._config(library="central"),
                adapter=adapter,
                combination=combination,
                governor=governor,
            )
        self.assertEqual(len(adapter.search_calls), 1)
        self.assertIsNotNone(adapter.current_search_session)
        self.assertEqual(state["searchSession"]["query"], "碳中和")

    def test_batch_login_wait_does_not_count_as_captcha(self) -> None:
        adapter = LoginWaitAdapter()
        manager = self._manager()
        combination = initial_adaptive_combinations(
            ("碳中和",),
            ("central",),
            YearSelection(mode="range", start_year=1949, end_year=2026),
        )[0]
        captured_notes: list[float | None] = []
        governor = SimpleNamespace(
            before_result_action=lambda _kind: None,
            after_result_action=lambda *a, **k: None,
            note_captcha_cleared=lambda *a, **k: (
                captured_notes.append(a[0] if a else None),
                False,
            )[1],
            snapshot=lambda: {
                "keyword_submissions": 0,
                "year_filter_switches": 0,
                "captcha_waits": 0,
                "released_to_boundary": False,
            },
        )
        with mock.patch.object(manager, "_wait_for_manual"):
            state = manager._run_adaptive_result_action(
                store=self.store,
                controller=TaskControl(self.task_id),
                task_id=self.task_id,
                config=self._config(library="central"),
                adapter=adapter,
                combination=combination,
                governor=governor,
            )
        self.assertEqual(captured_notes, [])
        self.assertEqual(len(adapter.search_calls), 1)
        self.assertIsNotNone(state)

    def test_batch_v2_boundary_completed_count_is_unique_across_scopes(self) -> None:
        adapter = CrossScopeBoundaryAdapter()
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.batch_task.SearchActionGovernor",
            return_value=SimpleNamespace(
                before_result_action=lambda _kind: None,
                after_result_action=lambda *a, **k: None,
                note_captcha_cleared=lambda *a, **k: False,
                snapshot=lambda: {
                    "keyword_submissions": 0,
                    "year_filter_switches": 0,
                    "captcha_waits": 0,
                    "released_to_boundary": False,
                },
            ),
        ):
            with self.assertRaises(CaptureBoundaryRequired) as context:
                manager._prepare_batches(
                    self.store,
                    self.task_id,
                    self._config(library="central"),
                    adapter,
                    TaskControl(self.task_id),
                )
        self.assertEqual(context.exception.boundary.completed_policy_count, 2)

    def test_batch_v2_boundary_persists_waiting_manual_state(self) -> None:
        adapter = FakeAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 201},
            ],
            truncated_year="2023",
        )
        manager = self._manager()
        with (
            mock.patch(
                "pkulaw_engine.batch_task.PkulawAdapter",
                return_value=adapter,
            ),
            mock.patch.object(
                manager.browser_bridge,
                "state",
                return_value={
                    "captchaVisible": False,
                    "loginVisible": False,
                    "authenticated": True,
                },
            ),
            mock.patch(
                "pkulaw_engine.batch_task.SearchActionGovernor",
                return_value=SimpleNamespace(
                    before_result_action=lambda _kind: None,
                    after_result_action=lambda *a, **k: None,
                    note_captcha_cleared=lambda *a, **k: False,
                    snapshot=lambda: {
                        "keyword_submissions": 0,
                        "year_filter_switches": 0,
                        "captcha_waits": 0,
                        "released_to_boundary": False,
                    },
                ),
            ),
        ):
            manager._run(
                self.task_id,
                self._config(),
                TaskControl(self.task_id),
            )
        task = self.store.get_task(self.task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "waiting_manual")
        self.assertEqual(task["error"]["code"], "CAPTURE_BOUNDARY_REACHED")


if __name__ == "__main__":
    unittest.main()
