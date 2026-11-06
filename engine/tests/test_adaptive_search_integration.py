from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pkulaw_engine.batch_task import BatchTaskConfig, BatchTaskManager
from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import YearSelection
from pkulaw_engine.storage import TaskStore

from test_adaptive_batch_task import (
    ALL_YEARS,
    FakeAdapter,
)


class AdaptiveSearchIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "pkulaw.sqlite3"
        self.logger = JsonLineLogger(self.root / "engine.jsonl")
        self.store = TaskStore(self.database)
        self.task_id = str(
            self.store.create_task(
                name="自适应集成任务",
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

    def _config(self, library: str = "both") -> BatchTaskConfig:
        return BatchTaskConfig(
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
            catalog_format="xlsx",
            full_format="docx",
            search_strategy_version=2,
        )

    def _manager(self) -> BatchTaskManager:
        return BatchTaskManager(
            self.database,
            BrowserBridge(self.logger),
            self.logger,
            lambda _event, _data: None,
        )

    def _governor(self) -> SimpleNamespace:
        return SimpleNamespace(
            before_result_action=lambda _kind: None,
            after_result_action=lambda *args, **kwargs: None,
            note_captcha_cleared=lambda *args, **kwargs: False,
            snapshot=lambda: {
                "keyword_submissions": 0,
                "year_filter_switches": 0,
                "captcha_waits": 0,
                "released_to_boundary": False,
            },
        )

    def test_complete_window_prepares_downloadable_batches(self) -> None:
        adapter = FakeAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 1},
            ],
            complete_all_years=True,
            include_out_of_range_row=True,
        )
        manager = self._manager()
        with mock.patch(
            "pkulaw_engine.batch_task.SearchActionGovernor",
            return_value=self._governor(),
        ):
            scans = manager._prepare_batches(
                self.store,
                self.task_id,
                self._config(),
                adapter,
                TaskControl(self.task_id),
            )

        self.assertEqual(
            [call["year"] for call in adapter.search_calls],
            [ALL_YEARS, ALL_YEARS],
        )
        self.assertEqual(adapter.select_year_calls, [])
        self.assertGreaterEqual(len(scans), 2)
        self.assertEqual(
            len(self.store.list_task_batches(self.task_id)),
            2,
        )
        self.assertFalse(
            any(
                "-out" in str(row.get("policyId") or "")
                for batch in self.store.list_task_batches(self.task_id)
                for row in (batch.get("selected") or [])
            )
        )
        checkpoint = self.store.get_task(self.task_id)["checkpoint"]
        adaptive = checkpoint["adaptiveSearch"]
        self.assertEqual(
            sorted(adaptive["completedYears"]),
            ["2022", "2023"],
        )
        self.assertEqual(adaptive["missingYears"], [])

    def test_boundary_resume_continues_only_missing_scope(self) -> None:
        config = self._config(library="local")
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
                return_value=self._governor(),
            ),
        ):
            manager._run(
                self.task_id,
                config,
                TaskControl(self.task_id),
            )

        task = self.store.get_task(self.task_id)
        self.assertEqual(task["status"], "waiting_manual")
        self.assertEqual(task["error"]["code"], "CAPTURE_BOUNDARY_REACHED")
        adaptive = task["checkpoint"]["adaptiveSearch"]
        completed_before = set(adaptive["completedYears"])
        self.assertIn("2022", completed_before)
        self.assertIn("2023", set(adaptive["missingYears"]))
        self.assertGreater(
            adaptive["boundary"]["completedPolicyCount"],
            0,
        )

        manager.continue_capture_boundary(self.task_id)
        after = self.store.get_task(self.task_id)
        self.assertEqual(after["checkpoint"]["adaptiveSearch"]["fallbackMode"], "A")
        self.assertIn("confirmedAt", after["checkpoint"]["adaptiveSearch"]["boundary"])

        repeated = manager.continue_capture_boundary(self.task_id)
        self.assertEqual(
            repeated["task"]["checkpoint"]["adaptiveSearch"]["fallbackMode"],
            "A",
        )

        completed_adapter = FakeAdapter(
            year_entries=[
                {"value": "2022", "count": 1},
                {"value": "2023", "count": 1},
            ],
            truncated_year=None,
        )
        with (
            mock.patch(
                "pkulaw_engine.batch_task.PkulawAdapter",
                return_value=completed_adapter,
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
                return_value=self._governor(),
            ),
        ):
            manager._prepare_batches(
                self.store,
                self.task_id,
                config,
                completed_adapter,
                TaskControl(self.task_id),
            )

        resumed_task = self.store.get_task(self.task_id)
        resumed_adaptive = resumed_task["checkpoint"]["adaptiveSearch"]
        self.assertEqual(resumed_adaptive["missingYears"], [])
        self.assertIn("2023", resumed_adaptive["completedYears"])
        self.assertNotIn(
            "全部年份",
            [call["year"] for call in completed_adapter.search_calls],
        )
        self.assertIn(
            "2023",
            [call["year"] for call in completed_adapter.search_calls],
        )


if __name__ == "__main__":
    unittest.main()
