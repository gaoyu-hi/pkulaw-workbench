from __future__ import annotations

import unittest
from datetime import date

from pkulaw_engine.selections.years import (
    EffectiveYearWindow,
    YearSelection,
    build_year_window_checkpoint,
    effective_year_window,
    expand_search_combinations,
    expand_search_combinations_for_year_windows,
    finalize_year_stats,
    mark_current_year_status,
    parse_year_selection,
)
from pkulaw_engine.storage import ValidationError


class YearSelectionTests(unittest.TestCase):
    def test_all_single_range_and_legacy_forms(self) -> None:
        all_years = parse_year_selection({"year": "全部年份"})
        self.assertEqual(all_years.mode, "all")
        self.assertEqual(all_years.years, ("全部年份",))

        single = parse_year_selection({"year": "2024"})
        self.assertEqual(single.mode, "single")
        self.assertEqual(single.years, ("2024",))

        structured = parse_year_selection(
            {
                "yearSelection": {
                    "mode": "range",
                    "startYear": 2018,
                    "endYear": 2021,
                }
            }
        )
        self.assertEqual(structured.mode, "range")
        self.assertEqual(structured.years, ("2018", "2019", "2020", "2021"))

    def test_year_boundaries_and_invalid_ranges(self) -> None:
        current = date.today().year
        self.assertEqual(
            YearSelection(mode="single", year=1949).years,
            ("1949",),
        )
        self.assertEqual(
            YearSelection(mode="single", year=1999).years,
            ("1999",),
        )
        self.assertEqual(
            YearSelection(mode="single", year=current).years,
            (str(current),),
        )
        with self.assertRaises(ValidationError) as reversed_context:
            YearSelection(mode="range", start_year=2021, end_year=2018)
        self.assertEqual(reversed_context.exception.code, "INVALID_YEAR_RANGE")
        with self.assertRaises(ValidationError) as old_context:
            YearSelection(mode="single", year=1948)
        self.assertEqual(old_context.exception.code, "YEAR_OUT_OF_RANGE")
        with self.assertRaises(ValidationError) as format_context:
            parse_year_selection({"year": "20x4"})
        self.assertEqual(format_context.exception.code, "INVALID_YEAR")

    def test_combinations_expand_query_library_year_in_order(self) -> None:
        selection = YearSelection(mode="range", start_year=2020, end_year=2021)
        combinations = expand_search_combinations(
            ["关键词"],
            ["central", "local"],
            selection,
        )
        self.assertEqual(
            [
                (item.query, item.library, item.year, item.combination_index)
                for item in combinations
            ],
            [
                ("关键词", "central", "2020", 1),
                ("关键词", "central", "2021", 2),
                ("关键词", "local", "2020", 3),
                ("关键词", "local", "2021", 4),
            ],
        )
        self.assertEqual(combinations[0].year_index, 0)
        self.assertEqual(combinations[2].library_index, 1)

    def test_range_starts_at_first_available_year(self) -> None:
        requested = YearSelection(
            mode="range",
            start_year=1949,
            end_year=2026,
        )

        window = effective_year_window(
            requested,
            ["2026", "2025", "2024", "2023", "2022"],
        )

        self.assertIsInstance(window, EffectiveYearWindow)
        self.assertEqual(window.selection.start_year, 2022)
        self.assertEqual(window.selection.end_year, 2026)
        self.assertTrue(window.adjusted)
        self.assertEqual(
            window.original,
            {"mode": "range", "year": None, "startYear": 1949, "endYear": 2026},
        )
        self.assertEqual(
            window.effective,
            {"mode": "range", "year": None, "startYear": 2022, "endYear": 2026},
        )
        self.assertEqual(
            window.available_years,
            ("2022", "2023", "2024", "2025", "2026"),
        )

    def test_year_window_fails_closed_without_a_real_overlap(self) -> None:
        with self.assertRaises(ValidationError) as empty_context:
            effective_year_window(YearSelection(mode="range", start_year=1949, end_year=2026), [])
        self.assertEqual(
            empty_context.exception.code,
            "YEAR_FILTER_UNAVAILABLE",
        )

        with self.assertRaises(ValidationError) as overlap_context:
            effective_year_window(
                YearSelection(mode="range", start_year=1949, end_year=2000),
                ["2022", "2023"],
            )
        self.assertEqual(
            overlap_context.exception.code,
            "YEAR_RANGE_UNAVAILABLE",
        )

        with self.assertRaises(ValidationError) as single_context:
            effective_year_window(
                YearSelection(mode="single", year=1999),
                ["2022", "2023"],
            )
        self.assertEqual(
            single_context.exception.code,
            "YEAR_FILTER_UNAVAILABLE",
        )
        self.assertEqual(
            single_context.exception.details,
            {
                "expected": "1999",
                "available": ["2022", "2023"],
            },
        )

    def test_year_window_ignores_invalid_values_and_caps_current_year(self) -> None:
        current = date.today().year
        all_years = effective_year_window(
            YearSelection(mode="all"),
            [str(current + 1), "20x4"],
        )
        self.assertEqual(all_years.selection.mode, "all")
        self.assertFalse(all_years.adjusted)

        capped = effective_year_window(
            YearSelection(
                mode="range",
                start_year=1949,
                end_year=current,
            ),
            [str(current + 1), str(current), "2022"],
        )
        self.assertEqual(capped.selection.start_year, 2022)
        self.assertEqual(capped.selection.end_year, current)

    def test_multiple_scope_summary_covers_every_effective_window(self) -> None:
        central = effective_year_window(
            YearSelection(mode="range", start_year=1949, end_year=2025),
            ["2022", "2023", "2024", "2025"],
        )
        local = effective_year_window(
            YearSelection(mode="range", start_year=1949, end_year=2025),
            ["2020", "2021", "2022", "2023", "2024"],
        )

        checkpoint = build_year_window_checkpoint(
            [
                (0, 0, "碳中和", "central", central),
                (0, 1, "碳中和", "local", local),
            ]
        )

        self.assertEqual(checkpoint["effectiveYearSelection"]["startYear"], 2020)
        self.assertEqual(checkpoint["effectiveYearSelection"]["endYear"], 2025)
        self.assertEqual(len(checkpoint["yearWindows"]), 2)

    def test_range_expansion_skips_years_missing_from_real_tree(self) -> None:
        window = effective_year_window(
            YearSelection(
                mode="range",
                start_year=2008,
                end_year=2026,
            ),
            ["2017", "2012"],
        )
        checkpoint = build_year_window_checkpoint(
            [(0, 0, "残疾人体育", "local", window)]
        )

        combinations = expand_search_combinations_for_year_windows(
            ["残疾人体育"],
            ["local"],
            checkpoint,
        )

        self.assertEqual(
            [combination.year for combination in combinations],
            ["2012", "2017"],
        )

    def test_year_status_pause_resume_and_finalize(self) -> None:
        checkpoint = {
            "currentQuery": "关键词",
            "currentLibrary": "central",
            "currentYear": "2021",
            "yearStats": [
                {
                    "query": "关键词",
                    "library": "central",
                    "year": "2020",
                    "status": "completed",
                    "resultCount": 2,
                },
                {
                    "query": "关键词",
                    "library": "central",
                    "year": "2021",
                    "status": "running",
                    "resultCount": 2,
                },
            ],
        }
        paused = mark_current_year_status(dict(checkpoint), "paused")
        self.assertEqual(paused["yearStats"][0]["status"], "completed")
        self.assertEqual(paused["yearStats"][1]["status"], "paused")
        resumed = mark_current_year_status(paused, "running")
        self.assertEqual(resumed["yearStats"][1]["status"], "running")
        finalized = finalize_year_stats(resumed)
        self.assertEqual(finalized["yearStats"][1]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
