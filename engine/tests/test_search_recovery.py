from __future__ import annotations

import unittest

from pkulaw_engine.search_recovery import (
    PendingSearchAction,
    pending_action_from_checkpoint,
    pending_action_to_checkpoint,
)
from pkulaw_engine.sources.search_transaction import SearchRequestIdentity


class PendingSearchActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = PendingSearchAction(
            action_id="action-1",
            kind="year-filter-switch",
            request=SearchRequestIdentity(
                query="人工智能",
                match_mode="keyword",
                library="central",
                year="2024",
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                requested_page_size=20,
            ),
            expected_page=1,
            previous_result_signature="previous-signature",
            target={"year": "2024"},
            attempt_count=1,
            created_at="2026-11-06T00:00:00Z",
        )

    def test_pending_action_round_trip_preserves_original_identity(self) -> None:
        restored = pending_action_from_checkpoint(
            pending_action_to_checkpoint(self.action)
        )

        self.assertEqual(restored, self.action)

    def test_malformed_checkpoint_returns_none(self) -> None:
        for payload in (
            None,
            {},
            {"kind": "next-page"},
            {"actionId": "", "kind": "next-page"},
            {
                "actionId": "action-1",
                "kind": "not-an-action",
                "request": self.action.request.to_dict(),
                "expectedPage": 1,
            },
            {
                "actionId": "action-1",
                "kind": "next-page",
                "request": {"query": "incomplete"},
                "expectedPage": 1,
            },
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(
                    pending_action_from_checkpoint(payload)
                )

    def test_checkpoint_uses_protocol_camel_case(self) -> None:
        payload = pending_action_to_checkpoint(self.action)

        self.assertEqual(payload["actionId"], "action-1")
        self.assertEqual(payload["expectedPage"], 1)
        self.assertEqual(payload["attemptCount"], 1)
        self.assertEqual(payload["request"]["query"], "人工智能")
        self.assertNotIn("action_id", payload)


if __name__ == "__main__":
    unittest.main()
