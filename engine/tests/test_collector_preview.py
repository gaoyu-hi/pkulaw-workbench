from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pkulaw_engine.collector_preview import (
    CollectorPreviewService,
    checkpoint_preview_snapshot,
    parse_preview_config,
)
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.single_task import SingleTaskManager
from pkulaw_engine.sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
)
from pkulaw_engine.storage import TaskStore, ValidationError


class FakePreviewAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        library = str(kwargs["library"])
        empty = str(kwargs["keyword"]) == "不存在"
        result_count = 0 if empty else {"central": 3, "local": 2}[library]
        total_pages = 1 if empty else {"central": 2, "local": 1}[library]
        rows = [
            {
                "policyId": f"{library}-{index}",
                "title": f"{library} 政策 {index}",
            }
            for index in range(min(result_count, 1))
        ]
        request = SearchRequestIdentity(
            query=str(kwargs["keyword"]),
            match_mode=str(kwargs["match_mode"]),
            library=library,
            year=str(kwargs["year"]),
            province=str(kwargs["province"]),
            timeliness=str(kwargs["timeliness"]),
            requested_page_size=int(kwargs["page_size"]),
        )
        session = SearchSession(
            search_id=f"search-{library}",
            query=request.query,
            match_mode=request.match_mode,
            library=library,
            year=request.year,
            province=request.province,
            timeliness=request.timeliness,
            requested_page_size=request.requested_page_size,
            actual_page_size=request.requested_page_size,
            page=1,
            total_pages=total_pages,
            result_count=result_count,
            row_count=len(rows),
            grouping_mode="ungrouped",
            result_signature=f"signature-{library}",
            observed_at="2026-11-02T08:00:00Z",
            url=f"https://example.test/{library}",
            actual_year=request.year,
            actual_province=request.province,
            actual_timeliness=request.timeliness,
        )
        return {
            "rows": rows,
            "page": 1,
            "totalPages": total_pages,
            "resultCount": result_count,
            "actualPageSize": request.requested_page_size,
            "groupingMode": "ungrouped",
            "actualYear": request.year,
            "actualProvince": request.province,
            "actualTimeliness": request.timeliness,
            "url": session.url,
            "searchSession": session.to_dict(),
        }


class WindowMismatchPreviewAdapter(FakePreviewAdapter):
    def search(self, **kwargs: object) -> dict[str, object]:
        state = super().search(**kwargs)
        state.update(
            {
                "resultCount": 17224,
                "totalPages": 173,
                "rows": [
                    {"policyId": "local-1", "title": "地方政策"}
                ],
            }
        )
        session = SearchSession.from_dict(state["searchSession"])
        assert session is not None
        session = SearchSession(
            search_id=session.search_id,
            query=session.query,
            match_mode=session.match_mode,
            library=session.library,
            year=session.year,
            province=session.province,
            timeliness=session.timeliness,
            requested_page_size=session.requested_page_size,
            actual_page_size=session.actual_page_size,
            page=session.page,
            total_pages=173,
            result_count=17224,
            row_count=100,
            grouping_mode=session.grouping_mode,
            result_signature="signature-local",
            observed_at=session.observed_at,
            url=session.url,
            actual_year=session.actual_year,
            actual_province=session.actual_province,
            actual_timeliness=session.actual_timeliness,
            reported_total_pages=20,
        )
        state["searchSession"] = session.to_dict()
        return state


class RangeAwarePreviewAdapter(FakePreviewAdapter):
    def available_filter_entries(
        self,
        key: str,
    ) -> list[dict[str, object]]:
        if key != "year":
            return []
        return [
            {"value": "2023", "label": "2023 (5)", "count": 5},
            {"value": "2022", "label": "2022 (4)", "count": 4},
        ]

    def search(self, **kwargs: object) -> dict[str, object]:
        if str(kwargs["year"]) == "1949":
            raise AssertionError("preview must inspect real years before scanning")
        return super().search(**kwargs)


class CollectorPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.bridge = MagicMock()
        self.logger = JsonLineLogger(self.root / "engine.jsonl")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_preview_summarizes_real_combinations_without_download_intent(self) -> None:
        adapter = FakePreviewAdapter()
        service = CollectorPreviewService(
            self.bridge,
            self.logger,
            adapter_factory=lambda _bridge, _logger: adapter,
        )

        result = service.preview(
            "batch",
            {
                "queries": ["碳中和"],
                "matchMode": "keyword",
                "library": "both",
                "yearSelection": {"mode": "all"},
                "province": "全部省份",
                "timeliness": "全部（仅记录元数据，不筛选）",
                "pageSize": 50,
            },
        )

        self.assertEqual(result["mode"], "batch")
        self.assertEqual(result["queryCount"], 1)
        self.assertEqual(result["libraryCount"], 2)
        self.assertEqual(result["combinationCount"], 2)
        self.assertEqual(result["totalResults"], 5)
        self.assertEqual(result["totalPages"], 3)
        self.assertEqual(result["estimatedBatches50"], 1)
        self.assertEqual(result["downloadsStarted"], 0)
        self.assertEqual(result["downloadIntentsCreated"], 0)
        self.assertEqual(result["tasksCreated"], 0)
        self.assertEqual(
            [combination["library"] for combination in result["combinations"]],
            ["central", "local"],
        )
        self.assertEqual(
            [call["library"] for call in adapter.calls],
            ["central", "local"],
        )
        self.bridge.create_download_intent.assert_not_called()

    def test_batch_rejects_single_exact_title_semantics(self) -> None:
        with self.assertRaises(ValidationError) as context:
            parse_preview_config(
                "batch",
                {
                    "queries": ["中华人民共和国国防动员法(2026修订)"],
                    "matchMode": "title",
                    "library": "central",
                },
            )

        self.assertEqual(context.exception.code, "BATCH_EXACT_TITLE_UNSUPPORTED")

    def test_single_allows_exact_title(self) -> None:
        config = parse_preview_config(
            "single",
            {
                "queries": ["中华人民共和国国防动员法(2026修订)"],
                "matchMode": "title",
                "library": "central",
            },
        )

        self.assertEqual(config.mode, "single")
        self.assertEqual(config.match_mode, "title")
        self.assertEqual(config.queries, ("中华人民共和国国防动员法(2026修订)",))

    def test_single_and_batch_allow_full_text_mode(self) -> None:
        for mode in ("single", "batch"):
            with self.subTest(mode=mode):
                config = parse_preview_config(
                    mode,
                    {
                        "queries": ["人工智能"],
                        "matchMode": "fulltext",
                        "library": "central",
                    },
                )
                self.assertEqual(config.match_mode, "fulltext")

    def test_preview_checkpoint_keeps_metrics_and_download_guards(self) -> None:
        checkpoint = checkpoint_preview_snapshot(
            {
                "previewSnapshot": {
                    "formFingerprint": "form-1",
                    "previewFingerprint": "preview-1",
                    "generatedAt": "2026-11-02T08:00:00Z",
                    "combinationCount": 6,
                    "totalResults": 216,
                    "totalPages": 9,
                    "estimatedBatches50": 5,
                    "originalYearSelection": {
                        "mode": "range",
                        "startYear": 1949,
                        "endYear": 2023,
                    },
                    "effectiveYearSelection": {
                        "mode": "range",
                        "startYear": 2022,
                        "endYear": 2023,
                    },
                    "yearWindowAdjustment": {
                        "adjusted": True,
                        "availableYears": ["2022", "2023"],
                    },
                    "downloadsStarted": 0,
                    "downloadIntentsCreated": 0,
                    "tasksCreated": 0,
                }
            }
        )

        self.assertEqual(checkpoint["previewFingerprint"], "preview-1")
        self.assertEqual(checkpoint["combinationCount"], 6)
        self.assertEqual(checkpoint["estimatedBatches50"], 5)
        self.assertEqual(
            checkpoint["effectiveYearSelection"]["startYear"],
            2022,
        )
        self.assertTrue(
            checkpoint["yearWindowAdjustment"]["adjusted"]
        )
        self.assertEqual(checkpoint["downloadsStarted"], 0)

    def test_zero_result_combination_reports_zero_pages(self) -> None:
        service = CollectorPreviewService(
            self.bridge,
            self.logger,
            adapter_factory=lambda _bridge, _logger: FakePreviewAdapter(),
        )

        result = service.preview(
            "single",
            {
                "queries": ["不存在"],
                "matchMode": "keyword",
                "library": "central",
                "pageSize": 20,
            },
        )

        self.assertEqual(result["combinations"][0]["resultCount"], 0)
        self.assertEqual(result["combinations"][0]["totalPages"], 0)

    def test_preview_exposes_result_window_mismatch_without_downloads(self) -> None:
        service = CollectorPreviewService(
            self.bridge,
            self.logger,
            adapter_factory=lambda _bridge, _logger: WindowMismatchPreviewAdapter(),
        )

        result = service.preview(
            "single",
            {
                "queries": ["碳中和"],
                "matchMode": "keyword",
                "library": "local",
                "pageSize": 100,
            },
        )

        combination = result["combinations"][0]
        self.assertEqual(result["windowStatus"], "probe_required")
        self.assertEqual(combination["reportedTotalPages"], 20)
        self.assertEqual(combination["expectedTotalPages"], 173)
        self.assertEqual(combination["accessibleCapacity"], 2000)
        self.assertEqual(combination["windowStatus"], "truncated")
        self.assertEqual(combination["totalPages"], 173)
        self.assertEqual(result["downloadIntentsCreated"], 0)

    def test_preview_starts_range_at_first_real_available_year(self) -> None:
        adapter = RangeAwarePreviewAdapter()
        service = CollectorPreviewService(
            self.bridge,
            self.logger,
            adapter_factory=lambda _bridge, _logger: adapter,
        )

        result = service.preview(
            "single",
            {
                "queries": ["碳中和"],
                "matchMode": "keyword",
                "library": "central",
                "yearSelection": {
                    "mode": "range",
                    "startYear": 1949,
                    "endYear": 2023,
                },
                "pageSize": 20,
            },
        )

        self.assertEqual(
            [call["year"] for call in adapter.calls],
            ["全部年份", "2022", "2023"],
        )
        self.assertEqual(
            result["originalYearSelection"],
            {
                "mode": "range",
                "year": None,
                "startYear": 1949,
                "endYear": 2023,
            },
        )
        self.assertEqual(
            result["effectiveYearSelection"],
            {
                "mode": "range",
                "year": None,
                "startYear": 2022,
                "endYear": 2023,
            },
        )
        self.assertTrue(result["yearWindowAdjustment"]["adjusted"])
        self.assertEqual(result["downloadsStarted"], 0)

    def test_single_task_checkpoint_persists_preview_before_work_starts(self) -> None:
        store = TaskStore(self.root / "checkpoint.sqlite3")
        try:
            task = store.create_task(
                name="预览断点",
                mode="single",
                task_input={
                    "queries": ["碳中和"],
                    "matchMode": "keyword",
                    "library": "central",
                    "pageSize": 20,
                    "outputDir": str(self.root / "output"),
                    "previewSnapshot": {
                        "formFingerprint": "form-1",
                        "previewFingerprint": "preview-1",
                        "generatedAt": "2026-11-02T08:00:00Z",
                        "combinationCount": 1,
                        "totalResults": 60,
                        "totalPages": 2,
                        "estimatedBatches50": 2,
                        "downloadsStarted": 99,
                        "downloadIntentsCreated": 99,
                        "tasksCreated": 99,
                    },
                },
                output_dir=str(self.root / "output"),
            )
            manager = SingleTaskManager(
                store.database_path,
                self.bridge,
                self.logger,
                lambda _event, _data: None,
            )
            config = manager._parse_config(task)
            controller = TaskControl(str(task["id"]))
            controller.request_stop()

            manager._run(str(task["id"]), config, controller)

            stopped = store.get_task(str(task["id"]))
            self.assertIsNotNone(stopped)
            self.assertEqual(stopped["status"], "stopped")
            snapshot = stopped["checkpoint"]["previewSnapshot"]
            self.assertEqual(snapshot["previewFingerprint"], "preview-1")
            self.assertEqual(snapshot["totalResults"], 60)
            self.assertEqual(snapshot["estimatedBatches50"], 2)
            self.assertEqual(snapshot["downloadsStarted"], 0)
            self.assertEqual(snapshot["downloadIntentsCreated"], 0)
            self.assertEqual(snapshot["tasksCreated"], 0)
        finally:
            store.close()
