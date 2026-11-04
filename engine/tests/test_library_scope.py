from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.batch_task import BatchTaskConfig
from pkulaw_engine.collector_preview import parse_preview_config
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import YearSelection
from pkulaw_engine.single_task import SingleTaskConfig
from pkulaw_engine.sources.library_scope import (
    ALL_PROVINCES,
    province_for_library,
)


class LibraryScopeTests(unittest.TestCase):
    def test_province_is_removed_only_for_central_laws(self) -> None:
        self.assertEqual(
            province_for_library("central", "北京市"),
            ALL_PROVINCES,
        )
        self.assertEqual(
            province_for_library("local", "北京市"),
            "北京市",
        )
        self.assertEqual(
            province_for_library("central", ALL_PROVINCES),
            ALL_PROVINCES,
        )
        self.assertEqual(
            province_for_library("local", ""),
            ALL_PROVINCES,
        )

    def test_all_task_configs_expose_effective_province(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            single = SingleTaskConfig(
                queries=("碳中和",),
                match_mode="keyword",
                library="central",
                year_selection=YearSelection(mode="all"),
                province="北京市",
                timeliness="全部（仅记录元数据，不筛选）",
                page_size=20,
                page_selection=PageSelection(mode="last"),
                output_dir=output,
            )
            batch = BatchTaskConfig(
                queries=("碳中和",),
                match_mode="keyword",
                library="central",
                year_selection=YearSelection(mode="all"),
                province="北京市",
                timeliness="全部（仅记录元数据，不筛选）",
                page_size=50,
                page_selection=PageSelection(mode="last"),
                output_dir=output,
                catalog_format="xlsx",
                full_format="docx",
            )

            self.assertEqual(single.province_for("central"), ALL_PROVINCES)
            self.assertEqual(batch.province_for("central"), ALL_PROVINCES)

        preview = parse_preview_config(
            "single",
            {
                "queries": ["碳中和"],
                "library": "central",
                "province": "北京市",
            },
        )
        self.assertEqual(preview.province_for("central"), ALL_PROVINCES)


if __name__ == "__main__":
    unittest.main()
