from __future__ import annotations

import unittest

from pkulaw_engine.selections.adaptive_years import (
    build_year_switch_combinations,
    plan_adaptive_years,
    publication_year_from_row,
    row_matches_year_selection,
)
from pkulaw_engine.selections.years import YearSelection


class AdaptiveYearsTests(unittest.TestCase):
    def test_plan_uses_real_years_and_only_missing_nonempty_years(self) -> None:
        selection = YearSelection(mode="range", start_year=1949, end_year=2023)
        plan = plan_adaptive_years(
            selection,
            [{"value": "2022", "count": 1}, {"value": "2023", "count": 2}],
            {"2022": 1},
        )
        self.assertEqual(plan.completed_years, ("2022",))
        self.assertEqual(plan.missing_years, ("2023",))
        self.assertEqual(plan.available_counts, {"2022": 1, "2023": 2})

    def test_requested_start_before_first_real_year_is_clipped(self) -> None:
        selection = YearSelection(mode="range", start_year=1949, end_year=2023)
        plan = plan_adaptive_years(
            selection,
            [{"value": "2022", "count": 1}, {"value": "2023", "count": 2}],
            {},
        )
        self.assertEqual(plan.missing_years, ("2022", "2023"))
        self.assertEqual(plan.completed_years, ())

    def test_zero_count_year_is_skipped(self) -> None:
        selection = YearSelection(mode="range", start_year=2022, end_year=2023)
        plan = plan_adaptive_years(
            selection,
            [{"value": "2022", "count": 1}, {"value": "2023", "count": 0}],
            {"2022": 1},
        )
        self.assertEqual(plan.completed_years, ("2022",))
        self.assertEqual(plan.missing_years, ())
        self.assertEqual(plan.available_counts, {"2022": 1})

    def test_malformed_count_is_unresolved(self) -> None:
        selection = YearSelection(mode="range", start_year=2022, end_year=2023)
        plan = plan_adaptive_years(
            selection,
            [{"value": "2022", "count": -1}],
            {},
        )
        self.assertEqual(plan.unresolved_years, ("2022",))
        self.assertEqual(plan.missing_years, ())
        self.assertEqual(plan.available_counts, {})

    def test_unknown_publication_date_is_not_treated_as_in_range(self) -> None:
        selection = YearSelection(mode="range", start_year=2020, end_year=2023)
        self.assertIsNone(publication_year_from_row({"publicationDate": ""}))
        self.assertFalse(row_matches_year_selection({"publicationDate": ""}, selection))

    def test_publication_year_parsing_and_range_filter(self) -> None:
        selection = YearSelection(mode="range", start_year=2020, end_year=2023)
        self.assertEqual(
            publication_year_from_row({"publicationDate": "2024-03-15"}),
            2024,
        )
        self.assertFalse(
            row_matches_year_selection({"publicationDate": "2024-03-15"}, selection)
        )
        self.assertTrue(
            row_matches_year_selection({"publicationDate": "2022-01-01"}, selection)
        )

    def test_missing_years_become_year_filter_actions(self) -> None:
        selection = YearSelection(mode="range", start_year=1949, end_year=2023)
        plan = plan_adaptive_years(
            selection,
            [{"value": "2022", "count": 1}, {"value": "2023", "count": 2}],
            {"2022": 1},
            query="碳中和",
            library="central",
        )
        actions = build_year_switch_combinations(plan, 0, 0, start_index=3)
        self.assertEqual([item.year for item in actions], ["2023"])
        self.assertEqual([item.action_kind for item in actions], ["year-filter-switch"])
        self.assertEqual(actions[0].query, "碳中和")
        self.assertEqual(actions[0].library, "central")


if __name__ == "__main__":
    unittest.main()
