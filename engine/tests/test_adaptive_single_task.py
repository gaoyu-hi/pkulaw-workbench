from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.capture_boundary import CaptureBoundaryRequired
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import SearchCombination, YearSelection
from pkulaw_engine.single_task import SingleTaskConfig, SingleTaskManager
from pkulaw_engine.sources.pkulaw import ManualActionRequired
from pkulaw_engine.sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
    result_signature,
)
from pkulaw_engine.storage import TaskStore


ALL_YEARS = "全部年份"


def _row(policy_id: str, year: str) -> dict[str, Any]:
    return {
        "number": int(policy_id.rsplit("-", 1)[-1]),
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


class FakeSingleAdapter:
    def __init__(
        self,
        *,
        year_entries: list[dict[str, Any]] | None = None,
        truncated_year: str | None = None,
        complete_all_years: bool = False,
    ) -> None:
        self.year_entries = year_entries or []
        self.truncated_year = truncated_year
        self.complete_all_years = complete_all_years
        self.search_calls: list[dict[str, Any]] = []
        self.select_year_calls: list[str] = []
        self.current_search_request: SearchRequestIdentity | None = None
        self.current_search_session: SearchSession | None = None

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(dict(kwargs))
        query = str(kwargs["keyword"])
        library = str(kwargs["library"])
        year = str(kwargs["year"])
        if self.complete_all_years and year == ALL_YEARS:
            rows = [
                dict(_row(f"{library}-{query}-1", "2022")),
                dict(_row(f"{library}-{query}-2", "2023")),
            ]
            result_count = len(rows)
            reported = 1
        elif year == ALL_YEARS:
            rows = [dict(_row(f"{library}-{query}-1", "2022"))]
            result_count = 201
            reported = 2
        elif self.truncated_year and year == self.truncated_year:
            rows = [dict(_row(f"{library}-{query}-{year}", year))]
            result_count = 201
            reported = 2
        else:
            rows = [dict(_row(f"{library}-{query}-{year}", year))]
            result_count = 1
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
        result_count = 201 if self.truncated_year == year else 1
        reported = 2 if result_count > 1 else 1
        state = _state(
            search_id=f"year-{year}",
            query=request.query,
            library=request.library,
            year=year,
            rows=rows,
            result_count=result_count,
            reported_total_pages=reported,
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
        self.current_search_session = SearchSession.from_dict(
            state["searchSession"]
        )
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
        session = self.current_search_session
        return _state(
            search_id=session.search_id,
            query=session.query,
            library=session.library,
            year=session.year,
            rows=[dict(_row("reuse-1", "2022"))],
            result_count=session.result_count,
            reported_total_pages=session.reported_total_pages or 1,
            page=session.page,
        )

    def available_filter_entries(self, key: str) -> list[dict[str, Any]]:
        if key != "year":
            return []
        return [dict(entry) for entry in self.year_entries]

    def go_to_page(self, target_page: int) -> dict[str, Any]:
        if self.current_search_request is None:
            raise RuntimeError("no request")
        if self.current_search_session is None:
            raise RuntimeError("no session")
        session = self.current_search_session
        return _state(
            search_id=session.search_id,
            query=self.current_search_request.query,
            library=self.current_search_request.library,
            year=self.current_search_request.year,
            rows=[
                dict(
                    _row(
                        f"{self.current_search_request.library}-"
                        f"{self.current_search_request.query}-page-{target_page}",
                        "2022",
                    )
                )
            ],
            result_count=session.result_count,
            reported_total_pages=session.reported_total_pages or 1,
            page=target_page,
        )

    def next_page(self, expected_page: int) -> dict[str, Any]:
        if self.current_search_request is None:
            raise RuntimeError("no request")
        if self.current_search_session is None:
            raise RuntimeError("no session")
        session = self.current_search_session
        state = _state(
            search_id=session.search_id,
            query=self.current_search_request.query,
            library=self.current_search_request.library,
            year=self.current_search_request.year,
            rows=[
                dict(
                    _row(
                        f"{self.current_search_request.library}-"
                        f"{self.current_search_request.query}-page-{expected_page}",
                        "2022",
                    )
                )
            ],
            result_count=session.result_count,
            reported_total_pages=session.reported_total_pages or 1,
            page=expected_page,
        )
        self._attach(
            state,
            self.current_search_request.query,
            self.current_search_request.library,
            self.current_search_request.year,
        )
        return state


class CaptchaReuseSingleAdapter(FakeSingleAdapter):
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


class AdaptiveSingleTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "pkulaw.sqlite3"
        self.logger = JsonLineLogger(self.root / "engine.jsonl")
        self.store = TaskStore(self.database)
        self.task_id = str(
            self.store.create_task(
                name="自适应逐篇任务",
                mode="single",
                task_input={"queries": ["碳中和"]},
                output_dir=str(self.root / "output"),
            )["id"]
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _config(self, *, library: str = "both") -> SingleTaskConfig:
        return SingleTaskConfig(
            queries=("碳中和",),
            match_mode="keyword",
            library=library,
            year_selection=YearSelection(
                mode="range",
                start_year=1949,
                end_year=2026,
            ),
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            page_size=100,
            page_selection=PageSelection(mode="last"),
            output_dir=(self.root / "output").resolve(),
            search_strategy_version=2,
        )

    def _manager(self) -> SingleTaskManager:
        return SingleTaskManager(
            self.database,
            BrowserBridge(self.logger),
            self.logger,
            lambda _event, _data: None,
        )

    def _run_collect(self, adapter: FakeSingleAdapter):
        return self._manager()._collect(
            self.task_id,
            self._config(),
            adapter,
            self.store,
            TaskControl(self.task_id),
        )

    def test_adaptive_single_total_updates_after_missing_years_append(
        self,
    ) -> None:
        adapter = FakeSingleAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 2},
                {"value": "2024", "count": 3},
            ]
        )
        with mock.patch(
            "pkulaw_engine.single_task.SearchActionGovernor",
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
            self._run_collect(adapter)
        checkpoint = self.store.get_task(self.task_id)["checkpoint"]
        self.assertGreater(int(checkpoint["combinationCount"]), 2)
        self.assertGreater(
            int(checkpoint["progressSnapshot"]["totalUnits"]),
            2,
        )

    def test_single_v2_range_submits_keyword_once_per_sublibrary(self) -> None:
        adapter = FakeSingleAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 1},
            ],
            complete_all_years=True,
        )
        with mock.patch(
            "pkulaw_engine.single_task.SearchActionGovernor",
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
            self._run_collect(adapter)
        self.assertEqual(
            [call["year"] for call in adapter.search_calls],
            [ALL_YEARS, ALL_YEARS],
        )
        self.assertEqual(adapter.select_year_calls, [])
        adaptive = self.store.get_task(self.task_id)["checkpoint"][
            "adaptiveSearch"
        ]
        self.assertEqual(adaptive["keywordSubmissions"], 0)
        self.assertEqual(adaptive["yearFilterSwitches"], 0)
        self.assertEqual(adaptive["captchaWaits"], 0)
        self.assertEqual(
            sorted(adaptive["completedYears"]),
            ["2022", "2023"],
        )
        self.assertEqual(adaptive["missingYears"], [])

    def test_single_v2_range_switches_missing_years_without_resubmit(self) -> None:
        adapter = FakeSingleAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 2},
                {"value": "2024", "count": 3},
            ]
        )
        with mock.patch(
            "pkulaw_engine.single_task.SearchActionGovernor",
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
            self._run_collect(adapter)
        self.assertEqual(len(adapter.search_calls), 4)
        self.assertGreaterEqual(len(adapter.select_year_calls), 1)
        self.assertTrue(
            all(call["year"] != ALL_YEARS for call in adapter.search_calls[2:])
        )
        adaptive = self.store.get_task(self.task_id)["checkpoint"][
            "adaptiveSearch"
        ]
        self.assertIn("keywordSubmissions", adaptive)
        self.assertIn("yearFilterSwitches", adaptive)
        self.assertIn("captchaWaits", adaptive)
        self.assertIn("completedYears", adaptive)
        self.assertIn("missingYears", adaptive)

    def test_single_v2_captcha_after_success_does_not_resubmit(self) -> None:
        adapter = CaptchaReuseSingleAdapter()
        manager = self._manager()
        with (
            mock.patch.object(
                manager,
                "_wait_for_manual",
                return_value=None,
            ),
            mock.patch(
                "pkulaw_engine.single_task.SearchActionGovernor",
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
            manager._collect(
                self.task_id,
                self._config(),
                adapter,
                self.store,
                TaskControl(self.task_id),
            )
        self.assertEqual(len(adapter.search_calls), 2)

    def test_single_v2_boundary_preserves_completed_items(self) -> None:
        adapter = FakeSingleAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 2},
            ],
            truncated_year="2023",
        )
        with (
            mock.patch(
                "pkulaw_engine.single_task.SearchActionGovernor",
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
            self.assertRaises(CaptureBoundaryRequired) as raised,
        ):
            self._run_collect(adapter)
        checkpoint = self.store.get_task(self.task_id)["checkpoint"]
        accepted = sum(
            max(0, int(item.get("rowsAccepted") or 0))
            for item in checkpoint.get("yearStats") or []
        )
        self.assertGreater(accepted, 0)
        self.assertEqual(
            raised.exception.boundary.completed_policy_count,
            4,
        )
        self.assertIn(
            "completedYears",
            raised.exception.adaptive_search,
        )
        self.assertIn(
            "missingYears",
            raised.exception.adaptive_search,
        )
        self.assertIn("scopeStates", raised.exception.adaptive_search)

    def test_single_v2_boundary_resume_processes_only_remaining_year(self) -> None:
        self.store.update_task(
            self.task_id,
            checkpoint={
                "adaptiveSearch": {
                    "fallbackMode": "A",
                    "boundary": {
                        "confirmedAt": "2026-11-06T00:00:00Z",
                        "remainingScopes": [
                            {
                                "query": "碳中和",
                                "library": "central",
                                "year": "2023",
                                "province": "全部省份",
                                "queryIndex": 0,
                                "libraryIndex": 0,
                            }
                        ],
                    },
                    "scopeStates": {
                        "碳中和|central": {
                            "entries": [
                                {"value": "2022", "count": 1},
                                {"value": "2023", "count": 1},
                            ],
                            "collected": {"2022": 1},
                        }
                    },
                }
            },
        )
        adapter = FakeSingleAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 1},
            ]
        )
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.single_task.SearchActionGovernor",
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
            manager._collect(
                self.task_id,
                self._config(library="central"),
                adapter,
                self.store,
                TaskControl(self.task_id),
            )
        self.assertEqual(
            [call["year"] for call in adapter.search_calls],
            ["2023"],
        )
        self.assertNotIn(ALL_YEARS, [call["year"] for call in adapter.search_calls])


if __name__ == "__main__":
    unittest.main()
