from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from pkulaw_engine.sources.page_navigation import transition_next_page


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += max(0.01, seconds)


def _state(page: int, row_id: str) -> dict[str, Any]:
    return {
        "page": page,
        "totalPages": 3,
        "resultCount": 3,
        "rows": [{"number": 1, "policyId": row_id}],
        "searchId": "search-1",
        "queryValue": "人工智能",
        "library": "central",
        "searchType": "Title",
    }


class PageNavigationTests(unittest.TestCase):
    def test_next_page_clicks_once_and_requires_three_stable_samples(
        self,
    ) -> None:
        clock = FakeClock()
        tab = MagicMock()
        tab.run_js.return_value = {"ok": True}

        result = transition_next_page(
            tab,
            expected_page=2,
            selectors={"pagination": "ul", "nextPage": "a.next"},
            before_state=_state(1, "row-1"),
            read_state=lambda: _state(2, "row-2"),
            stable_samples=3,
            click_timeout_seconds=2,
            settle_timeout_seconds=2,
            interval_seconds=0.1,
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(result.stability_samples, 3)
        self.assertEqual(tab.run_js.call_count, 1)

    def test_slow_page_does_not_receive_a_second_click(self) -> None:
        clock = FakeClock()
        tab = MagicMock()
        tab.run_js.return_value = {"ok": True}
        states = [
            _state(1, "row-1"),
            _state(1, "row-1"),
            _state(2, "row-2"),
            _state(2, "row-2"),
            _state(2, "row-2"),
        ]

        result = transition_next_page(
            tab,
            expected_page=2,
            selectors={"pagination": "ul", "nextPage": "a.next"},
            before_state=_state(1, "row-1"),
            read_state=lambda: states.pop(0),
            stable_samples=3,
            click_timeout_seconds=3,
            settle_timeout_seconds=2,
            interval_seconds=0.1,
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(result.stability_samples, 3)
        self.assertEqual(tab.run_js.call_count, 1)

    def test_form_fallback_runs_only_after_unconfirmed_click(self) -> None:
        clock = FakeClock()
        tab = MagicMock()
        tab.run_js.side_effect = [
            {"ok": True},
            {"ok": True, "strategy": "right-form-submit"},
        ]
        page = 1

        def read_state() -> dict[str, Any]:
            nonlocal page
            if tab.run_js.call_count >= 2:
                page = 2
            return _state(page, f"row-{page}")

        result = transition_next_page(
            tab,
            expected_page=2,
            selectors={"pagination": "ul", "nextPage": "a.next"},
            before_state=_state(1, "row-1"),
            read_state=read_state,
            stable_samples=3,
            click_timeout_seconds=0.4,
            settle_timeout_seconds=2,
            interval_seconds=0.1,
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertTrue(result.used_form_fallback)
        self.assertEqual(tab.run_js.call_count, 2)


if __name__ == "__main__":
    unittest.main()
