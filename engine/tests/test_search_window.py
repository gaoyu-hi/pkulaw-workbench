from __future__ import annotations

import unittest

from pkulaw_engine.search_window import (
    assess_result_window,
    plan_result_window_partitions,
)


class SearchWindowTests(unittest.TestCase):
    def test_real_carbon_neutrality_local_window_is_truncated(self) -> None:
        window = assess_result_window(
            result_count=17224,
            reported_total_pages=20,
            page_size=100,
        )

        self.assertEqual(window.expected_total_pages, 173)
        self.assertEqual(window.accessible_capacity, 2000)
        self.assertEqual(window.status, "truncated")
        self.assertEqual(window.code, "RESULT_WINDOW_TRUNCATED")

    def test_central_1837_results_fit_19_pages(self) -> None:
        window = assess_result_window(
            result_count=1837,
            reported_total_pages=19,
            page_size=100,
        )

        self.assertEqual(window.expected_total_pages, 19)
        self.assertEqual(window.expected_tail_rows, 37)
        self.assertEqual(window.status, "complete")
        self.assertEqual(window.code, "")

    def test_legal_page_21_extends_window_to_173_effective_pages(self) -> None:
        window = assess_result_window(
            result_count=17224,
            reported_total_pages=173,
            page_size=100,
        )

        self.assertEqual(window.expected_total_pages, 173)
        self.assertEqual(window.expected_tail_rows, 24)
        self.assertEqual(window.status, "complete")

    def test_zero_results_do_not_create_a_fake_page(self) -> None:
        window = assess_result_window(
            result_count=0,
            reported_total_pages=1,
            page_size=100,
        )

        self.assertEqual(window.expected_total_pages, 0)
        self.assertEqual(window.expected_tail_rows, 0)
        self.assertEqual(window.status, "no_result")
        self.assertEqual(window.code, "")

    def test_year_partitions_must_cover_every_source_result(self) -> None:
        plan = plan_result_window_partitions(
            result_count=17224,
            page_size=100,
            partitions=[
                {
                    "key": "2025",
                    "resultCount": 224,
                    "reportedTotalPages": 3,
                },
                {
                    "key": "2024",
                    "resultCount": 9000,
                    "reportedTotalPages": 90,
                },
                {
                    "key": "更早年份",
                    "resultCount": 8000,
                    "reportedTotalPages": 80,
                },
            ],
        )

        self.assertEqual(plan.status, "complete")
        self.assertEqual(plan.partition_count, 3)
        self.assertEqual(plan.total_partition_results, 17224)
        self.assertEqual(plan.code, "")

    def test_any_truncated_year_partition_fails_closed(self) -> None:
        plan = plan_result_window_partitions(
            result_count=17224,
            page_size=100,
            partitions=[
                {
                    "key": "2025",
                    "resultCount": 1200,
                    "reportedTotalPages": 10,
                },
                {
                    "key": "2024",
                    "resultCount": 16024,
                    "reportedTotalPages": 20,
                },
            ],
        )

        self.assertEqual(plan.status, "truncated")
        self.assertEqual(plan.code, "RESULT_WINDOW_TRUNCATED")

    def test_partition_count_mismatch_is_not_accepted(self) -> None:
        plan = plan_result_window_partitions(
            result_count=17224,
            page_size=100,
            partitions=[
                {
                    "key": "2025",
                    "resultCount": 224,
                    "reportedTotalPages": 3,
                },
                {
                    "key": "2024",
                    "resultCount": 8000,
                    "reportedTotalPages": 80,
                },
            ],
        )

        self.assertEqual(plan.status, "count_mismatch")
        self.assertEqual(
            plan.code,
            "RESULT_WINDOW_PARTITION_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
