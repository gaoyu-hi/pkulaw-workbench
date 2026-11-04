from __future__ import annotations

import unittest

from pkulaw_engine.selections.pagination import (
    PageSelection,
    page_signature,
    pagination_transition_error,
    parse_page_selection,
    restore_page_selection,
    should_pause_for_page_limit,
)
from pkulaw_engine.storage import ValidationError


class PageSelectionTests(unittest.TestCase):
    def test_default_and_structured_modes(self) -> None:
        self.assertEqual(parse_page_selection({}), PageSelection(mode="last"))
        self.assertEqual(
            parse_page_selection({"pageSelection": {"mode": "last"}}),
            PageSelection(mode="last"),
        )
        self.assertEqual(
            parse_page_selection(
                {"pageSelection": {"mode": "limit", "maxPages": 3}}
            ),
            PageSelection(mode="limit", max_pages=3),
        )

    def test_legacy_max_pages_is_treated_as_limit(self) -> None:
        selection = parse_page_selection({"maxPages": 5})
        self.assertEqual(selection.mode, "limit")
        self.assertEqual(selection.max_pages, 5)

    def test_limit_minimum_is_one(self) -> None:
        self.assertEqual(
            parse_page_selection(
                {"pageSelection": {"mode": "limit", "maxPages": 1}}
            ).max_pages,
            1,
        )
        for invalid in (0, -1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError) as context:
                    PageSelection(mode="limit", max_pages=invalid)
                self.assertEqual(context.exception.code, "INVALID_MAX_PAGES")

    def test_invalid_mode_and_boolean_are_rejected(self) -> None:
        for payload in (
            {"pageMode": "first"},
            {"pageSelection": {"mode": "unknown"}},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError) as context:
                    parse_page_selection(payload)
                self.assertEqual(context.exception.code, "INVALID_PAGE_MODE")
        with self.assertRaises(ValidationError) as context:
            PageSelection(mode="limit", max_pages=True)
        self.assertEqual(context.exception.code, "INVALID_MAX_PAGES")

    def test_continue_to_last_decision_survives_restart(self) -> None:
        configured = PageSelection(mode="limit", max_pages=2)
        restored = restore_page_selection(
            configured,
            {
                "pageMode": "last",
                "pageLimitDecision": "continue_to_last",
            },
        )
        self.assertEqual(restored, PageSelection(mode="last"))
        self.assertEqual(
            restore_page_selection(
                configured,
                {"pageMode": "last"},
            ),
            configured,
        )


class PaginationTransitionTests(unittest.TestCase):
    def test_page_limit_cases(self) -> None:
        cases = [
            (1, 1, 2, False),
            (1, 2, 2, False),
            (2, 2, 2, False),
            (1, 3, 2, False),
            (2, 3, 2, True),
            (3, 3, 2, True),
        ]
        for page, total_pages, max_pages, expected in cases:
            with self.subTest(
                page=page,
                total_pages=total_pages,
                max_pages=max_pages,
            ):
                self.assertEqual(
                    should_pause_for_page_limit(
                        page=page,
                        total_pages=total_pages,
                        max_pages=max_pages,
                    ),
                    expected,
                )

    def test_page_signature_prefers_stable_identity(self) -> None:
        self.assertEqual(
            page_signature(
                [
                    {"policyId": "A", "title": "标题 A"},
                    {"url": "https://example.test/B", "title": "标题 B"},
                ]
            ),
            ("A", "https://example.test/B"),
        )

    def test_transition_anomalies(self) -> None:
        self.assertIn(
            "分页未前进",
            pagination_transition_error(
                previous_page=2,
                next_page=2,
                previous_total_pages=3,
                next_total_pages=3,
                previous_signature=("A",),
                next_signature=("A",),
            )
            or "",
        )
        self.assertIn(
            "总页数异常下降",
            pagination_transition_error(
                previous_page=2,
                next_page=3,
                previous_total_pages=3,
                next_total_pages=2,
                previous_signature=("A",),
                next_signature=("B",),
            )
            or "",
        )
        self.assertIn(
            "结果签名",
            pagination_transition_error(
                previous_page=1,
                next_page=2,
                previous_total_pages=3,
                next_total_pages=3,
                previous_signature=("A", "B"),
                next_signature=("A", "B"),
            )
            or "",
        )
        self.assertIn(
            "总页数无效",
            pagination_transition_error(
                previous_page=1,
                next_page=2,
                previous_total_pages=3,
                next_total_pages=0,
                previous_signature=("A",),
                next_signature=("B",),
            )
            or "",
        )
        self.assertIn(
            "超出总页数",
            pagination_transition_error(
                previous_page=1,
                next_page=4,
                previous_total_pages=3,
                next_total_pages=3,
                previous_signature=("A",),
                next_signature=("B",),
            )
            or "",
        )


if __name__ == "__main__":
    unittest.main()
